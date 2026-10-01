# Changelog

## [0.1.0] — 2026-10-01

### Added
- MCP server (FastMCP, Streamable HTTP) with 11 customer-scoped tools over InsureHub, LendHub and Payments; delegated-token authorisation; per-call audit with redaction.
- Agent service (FastAPI): OTP verification, Claude tool-use loop with prompt caching and server-side refusal fallback, deterministic planner fallback, guardrails (scope, injection, third-party, grounding, PII, banned promises, budgets), mandatory handoffs, payment confirmation by code with idempotent execution and receipts.
- Channels: WhatsApp Cloud API webhook (signature-verified, deduplicated), web-chat simulator, signed event intake for receipts and proactive nudges with opt-out.
- Staff inbox (HTMX): queue, transcript with tool calls and guardrail verdicts, summary, reply, hand back.
- Knowledge base (9 fictional articles) with BM25 search.
- Evaluation suite: 82 cases across core tasks, languages (EN/SN/ND), safety, scope, handoff and grounding; deterministic graders; release gates.
- Tests: 66 (tools/tokens, guardrails, agent loop with a scripted model, HTTP surface, real MCP transport, eval gate).
- Delivery: Dockerfile, Compose (agent + MCP + Redis + PostgreSQL), Kustomize with NetworkPolicy, GitHub Actions (tests, offline gate, nightly live eval, multi-arch image, Trivy).
