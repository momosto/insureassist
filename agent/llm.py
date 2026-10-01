"""Model access. `AnthropicLlm` calls Claude (Messages API, tool use, prompt caching); the offline planner and the
scripted test double implement the same `create()` interface, so the orchestrator never knows which one it has.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("insureassist.llm")


@dataclass
class LlmResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    model: str
    usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0})


class LlmUnavailable(Exception):
    """Timeout, rate limit, server error or refusal: the orchestrator falls back to the deterministic menu."""


class Llm(Protocol):
    model: str

    def create(self, system: str, tools: list[dict], messages: list[dict]) -> LlmResponse: ...


class AnthropicLlm:
    """Claude via the official SDK.

    - System prompt and tool definitions are frozen and marked for caching (prefix: tools -> system).
    - Server-side refusal fallback is opted into on the Claude API (`fallbacks: "default"`); a refusal that still
      comes back is treated as LlmUnavailable so the customer gets the menu, not an empty reply.
    - Earlier turns are sent as plain text only (no thinking/tool blocks), so history stays append-only.
    """

    def __init__(self, model: str, effort: str = "medium", use_fallbacks: bool = True, timeout: float = 30.0,
                 client: Any = None):
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic(timeout=timeout, max_retries=2)
        self.model = model
        self.effort = effort
        self.use_fallbacks = use_fallbacks

    def create(self, system: str, tools: list[dict], messages: list[dict]) -> LlmResponse:
        a = self._anthropic
        cached_tools = [dict(tl) for tl in tools]
        if cached_tools:
            cached_tools[-1]["cache_control"] = {"type": "ephemeral"}
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=cached_tools,
            messages=messages,
            output_config={"effort": self.effort},
        )
        try:
            if self.use_fallbacks:
                response = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                            fallbacks="default", **kwargs)
            else:
                response = self.client.messages.create(**kwargs)
        except a.RateLimitError as e:
            raise LlmUnavailable("rate limited") from e
        except a.APIStatusError as e:
            if e.status_code >= 500:
                raise LlmUnavailable(f"server error {e.status_code}") from e
            log.error("Claude request rejected: %s", e)
            raise LlmUnavailable(f"request rejected {e.status_code}") from e
        except a.APIConnectionError as e:
            raise LlmUnavailable("connection error") from e
        if response.stop_reason == "refusal":
            raise LlmUnavailable("model declined")
        usage = response.usage
        return LlmResponse(
            [b.model_dump(exclude_none=True) for b in response.content],
            response.stop_reason,
            response.model,
            {"input": usage.input_tokens or 0, "output": usage.output_tokens or 0,
             "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0},
        )

    def summarize(self, transcript: str, small_model: str) -> str:
        """Handoff summary for staff with the small model; falls back to the caller's deterministic summary on error."""
        response = self.client.messages.create(
            model=small_model, max_tokens=300,
            system="Summarise this customer-service chat for the human agent taking over: who, what they need, what "
                   "the assistant already did, and the next step. Three short sentences. Plain text.",
            messages=[{"role": "user", "content": transcript}],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()


class ScriptedLlm:
    """Test double: returns pre-written responses in order (the 'fake LLM' in docs/05)."""

    model = "scripted"

    def __init__(self, responses: list[LlmResponse | Exception]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, system: str, tools: list[dict], messages: list[dict]) -> LlmResponse:
        self.calls.append({"system": system, "tools": tools, "messages": [dict(m) for m in messages]})
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
