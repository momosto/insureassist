# ADR-0006: A deterministic planner behind the same interface as Claude (fallback, public demo, eval baseline)

- **Status:** Accepted · **Date:** 2026-10-01

## Context
The architecture needs a menu fallback when the LLM is down, refuses, or the budget is spent (§8). The public demo
must not run up an AI bill. And CI needs an eval run that is reproducible and free, so the safety gate can block
every pull request, not just nightly.

## Decision
- `agent/planner.py` implements the same `create(system, tools, messages)` interface as `AnthropicLlm`: it reads the
  customer's message, emits `tool_use` blocks, and composes the final answer **only from tool results**, in EN/SN/ND.
- The orchestrator uses it (a) as `IA_LLM_MODE=offline` for the public simulator, (b) automatically when Claude is
  unavailable, refuses, loops past the tool-call limit, or the budget is exhausted, and (c) to re-answer a turn whose
  model reply failed the grounding/PII/promise checks.
- The eval suite runs offline on every PR (gate) and with Claude nightly; the same datasets and graders are used.
- Payment intents are created by the agent service after a successful `prepare_*` tool result, so the MCP server stays
  stateless and the confirmation code never enters the model context (refines ADR-0004).

## Consequences
- ➕ Guardrails, tools, identity and payment flows are tested end to end at zero token cost; outages degrade gracefully.
- ➕ The offline score is a floor: a model change can't ship if it does worse than the deterministic baseline.
- ➖ The planner is rule-based and narrow. A 100% offline score proves the system's safety mechanics, **not** the
  model's conversational quality; that needs the live eval and the tone rubric (backlog).
