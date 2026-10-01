# InsureAssist — requirements traceability & implementation report

**Version:** 0.1.0 · **Date:** 2026-10-01 · **Build:** 66 pytest tests green · offline eval 82/82 (safety 100%) · Compose stack (agent + MCP server + Redis + PostgreSQL) verified end to end

✅ done and tested · 🟡 done with a documented simplification · ⏳ deferred.

## 1. Functional requirements

| ID | Requirement | Implementation | Verified by | Status |
|---|---|---|---|---|
| IA-01 | Verify by phone + SMS OTP; 3 wrong → 30 min lock; 30 min idle; FAQs without verification | `Agent._start_verification/_verify_otp`, `Session`, delegated token `core/tokens.py` | `test_otp_lockout_after_three_wrong_codes`, eval `safe-020`, `safe-015`, `core-019` | ✅ (SMS simulated) |
| IA-02 | Policies in own words and language | tools `list_my_policies`, `get_policy`, `get_premium_balance`; EN/SN/ND templates and prompt | evals `core-*`, `languages` (100%) | ✅ |
| IA-03 | Pay premium: confirm with a code, system initiates payment with idempotency key | prepare tools + `Agent._create_intent/_confirmation` (ADR-0004), `PaymentIntent` audit | `test_confirmation_code_is_generated_by_code_and_never_shown_to_the_model`, evals `core-002`, `safe-008..012` | ✅ |
| IA-04 | Claim status, next step, missing documents; never predicts approval | `get_claim_status`, banned-promise output filter | evals `core-004..006`, `safe-018`, `ground-006/007` | ✅ |
| IA-05 | Report motor claim (draft) | `start_motor_claim` (draft only; ClaimGuard triage stays back-office) | `test_motor_claim_only_on_a_motor_policy…`, eval `core-015/016` | 🟡 WhatsApp media IDs are passed through; media download/upload to InsureHub awaits its draft-claim endpoint |
| IA-06 | Loan summary, settlement, pay loan | `get_loan_summary`, `get_settlement_quote`, `prepare_loan_payment`; `HttpBackend` uses LendHub's real channel endpoints | evals `core-007..010`, `lang-001/002` | ✅ |
| IA-07 | Knowledge-base answers citing the article; "don't know" + handoff | `core/knowledge.py` (BM25 + title boost), 9 fictional articles | evals `core-011..014`, `test_knowledge_search_prefers_the_topical_article` | ✅ |
| IA-08 | "agent/munhu/umuntu" → human; bot stops replying | `guardrails.HANDOFF_RULES`, conversation status `HANDOFF/HUMAN` | evals `hand-001/002/009`, `lang-005/008` | ✅ |
| IA-09 | Staff inbox: queue, transcript, summary, intent, sentiment, tool calls; reply; hand back | `/inbox` (HTMX, HTTP Basic), `Agent.staff_reply/hand_back`, summary by Haiku or deterministic | `test_staff_inbox_takeover_and_hand_back` | ✅ (Basic auth until Keycloak) |
| IA-10 | Proactive nudges from `policy.lapsed` / `loan.arrears-changed`, opt-out honoured | `POST /events` (Integrations HMAC scheme), `Agent.nudge`, `OptOut`, STOP keyword | `test_events_need_the_group_signature_and_respect_opt_out` | 🟡 HTTP intake built; direct RabbitMQ consumer not yet (events can be bridged by Notifications) |
| IA-11 | Audit per turn: input, output, model, prompt version, tool calls (redacted), latency, tokens, verdicts | `Turn` rows, `mcp_tool_audit` (MCP side), redaction | `test_every_call_is_audited_with_redacted_arguments`, `test_transcripts_never_store_codes_or_national_ids` | ✅ (trace ID: see NFR) |

## 2. Guardrails

| ID | Rule | Implementation | Verified by | Status |
|---|---|---|---|---|
| G-01 | Off-topic refused, menu offered | `check_input` (scope lists) + system prompt | `scope` set 100% | ✅ (keyword lists; a Haiku classifier is the planned upgrade) |
| G-02 | Model can't choose the customer | no customer-ID arguments (rejected if sent), token-scoped `Caller` | `test_tools_never_accept_a_customer_identifier`, real MCP round trip | ✅ |
| G-03 | Money needs a typed code matching a pending intent (5 min) | `Agent._confirmation` (code path, not model) | `safe-008..012`, `test_expired_confirmation_charges_nothing` | ✅ |
| G-04 | Amounts/dates/statuses grounded in this turn's tool results | `check_output` grounding; on failure the turn is re-answered deterministically | `test_grounding_blocks…`, `test_hallucinated_amount_is_replaced…` | ✅ |
| G-05 | No third-party data, IDs or card numbers in replies | output filter + redaction in transcripts and audit | `safe-013`, `test_output_filter…` | ✅ |
| G-06 | Mandatory handoff (bereavement, complaint, legal, 2 failures, negative trend, request) | `HANDOFF_RULES`, failure counter, sentiment counter | `handoff` set 100% | ✅ |
| G-07 | Instructions in documents/tool output are data | tool results only as `tool_result` blocks; injection signals refused | `safe-005..007`, `safe-016` | ✅ |
| G-08 | Rate/token limits, menu fallback | `_within_budget` → deterministic planner | code review | 🟡 no dedicated test yet |

## 3. Non-functional requirements

| NFR | Evidence | Status |
|---|---|---|
| Quality gates (safety 100%, overall ≥ 90%, languages ≥ 85%) | `evals/reports/2026-10-01-offline.md`: 82/82 | ✅ offline · ⏳ the live-Claude run needs an API key and spends tokens — run `python -m evals.runner --llm anthropic --write` (CI does it nightly when the secret exists) |
| Latency < 10 s p95 | offline p95 per case 0.06 s; live model latency not yet measured | ⏳ with the live eval |
| LLM outage → menus | `LlmUnavailable` → planner; refusal stop reason → planner | `test_llm_outage_falls_back_to_the_planner` ✅ |
| Cost control | offline default for the public demo; prompt caching on system + tools; daily token budget | ✅ |
| Privacy | redaction before storage; national IDs never sent to tools/LLM by the agent; transcripts in PostgreSQL | ✅ (12-month deletion job ⏳) |
| Observability | per-turn audit rows; OTel GenAI spans | ⏳ OTel spans not yet emitted (platform phase 2) |
| Languages EN/SN/ND | templates + detection; eval set | ✅ (translations need native-speaker review before real use) |

## 4. Deviations from the planning pack

| Planned | Built | Why |
|---|---|---|
| Python 3.12 | code runs on 3.11+; image uses 3.12 | local toolchain was 3.11 |
| Pending intents in Redis written by the MCP server | intents created by the agent after a successful `prepare_*` result; Redis (or memory) via the session | keeps the MCP server stateless; the model still never sees the code (ADR-0006) |
| MCP SDK latest | pinned `mcp<2` (1.30) | 2.x reorganised the server package; upgrade tracked |
| Topic classifier with a small model | deterministic keyword guard | reproducible evals at zero cost; small-model classifier is an additive later step |
| Testcontainers for Redis/Postgres | in-memory store + SQLite in tests; Redis/Postgres exercised on the Compose stack | faster suite; the stores are thin |

## 5. Backlog (v0.2)

1. Run the live-Claude eval and publish the report; add LLM-as-judge tone rubric with 30 human-labelled examples.
2. RabbitMQ consumer for `payment.succeeded`, `policy.lapsed`, `loan.arrears-changed`.
3. InsureHub channel endpoints (`/api/channels/*`) → switch `IA_BACKEND=http`; media download for FNOL photos.
4. OTel GenAI spans; dashboards for containment and handoff rates.
5. Transcript retention job; data-subject export from the inbox.
6. Native-speaker review of Shona and isiNdebele templates; grow eval sets to the targets in docs/05 (140 cases).
