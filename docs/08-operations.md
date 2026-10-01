# InsureAssist — operations runbook

Platform-wide runbooks (data breach, DLQ, AI budget, backups) live in `insurehub-platform/docs/runbooks/`.

## Components

| Component | Port | Notes |
|---|---|---|
| `insureassist-agent` | 8100 | WhatsApp webhook `/webhooks/whatsapp`, simulator `/chat`, inbox `/inbox`, events `/events`, health `/health/live` `/health/ready` |
| `insureassist-mcp` | 8101 | MCP Streamable HTTP `/mcp`; **never** exposed publicly (NetworkPolicy allows only the agent) |
| Redis | 6379 | sessions and pending payment intents (TTL 30 min) |
| PostgreSQL | 5432 | conversations, turns, handoffs, payment intents, outbound messages, MCP tool audit |

Key settings (`IA_*` env vars, see `core/config.py`): `IA_LLM_MODE` (`offline`/`anthropic`), `IA_DAILY_TOKEN_BUDGET`,
`IA_TOOL_TRANSPORT` (`mcp` in k8s), `IA_BACKEND` (`fixtures`/`http`). Secrets: `ANTHROPIC_API_KEY`,
`IA_DELEGATED_TOKEN_SECRET` (shared by agent and MCP server), `IA_WHATSAPP_APP_SECRET`, `IA_WHATSAPP_ACCESS_TOKEN`,
`IA_INBOX_PASSWORD`, `IA_EVENTS_SHARED_SECRET`.

## Switching the model on and off

- **Turn Claude off** (cost spike, provider incident, bad behaviour): set `IA_LLM_MODE=offline` and restart the agent. Customers get the deterministic assistant; nothing else changes.
- **Daily budget reached:** the agent falls back to the planner automatically and logs `budget: exhausted` in turn verdicts. Raise `IA_DAILY_TOKEN_BUDGET` only after checking spend (see `ai-budget.md`).

## Before any prompt or model change

1. Bump `PROMPT_VERSION` in `agent/prompts.py`.
2. `python -m evals.runner --write` (offline gate) and `python -m evals.runner --llm anthropic --write` (live).
3. Ship only if safety = 100%, overall ≥ 90%, languages ≥ 85%, and no set dropped more than 3 points. Link the report in the PR.

## Incidents

| Symptom | Check | Action |
|---|---|---|
| Bot replies "I couldn't get that information" | `select tool, outcome, count(*) from mcp_tool_audit where created_at > now() - interval '1 hour' group by 1,2;` | `backend_unavailable` → check InsureHub/LendHub/Payments health; `unauthorized` → the two services disagree on `IA_DELEGATED_TOKEN_SECRET` |
| Many handoffs with `repeated_tool_failures` | same query | as above; staff work the inbox meanwhile |
| WhatsApp messages not answered | agent logs for "bad signature" (401) | app secret rotated in Meta but not in the cluster |
| Receipts not sent | `select status, count(*) from payment_intents group by 1;` many `CONFIRMED` | `payment.succeeded` not reaching `/events` — check the Notifications bridge / signature secret |
| Suspected prompt-injection success | find the turn in the inbox (verdicts + tool calls) | add the message as a `safety` eval case, fix, re-run gates; if data was exposed follow `data-breach.md` (POTRAZ 24 h) |

## Data-subject requests

- **Access:** export the customer's conversations (`conversations.customer_ref`) and turns; transcripts are already redacted.
- **Erasure:** delete the customer's conversations, turns, handoffs and outbound messages; keep `payment_intents` (financial record) and `mcp_tool_audit` (redacted).
- **Opt-out of reminders:** customers type STOP; staff can insert into `opt_outs`.
