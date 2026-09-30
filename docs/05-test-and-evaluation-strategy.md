# InsureAssist — test & evaluation strategy

Employers in 2026 ask for "continuous evaluation" alongside tool use and structured outputs ([ODSC](https://opendatascience.com/12-most-in-demand-ai-jobs-in-2026-and-the-skills-employers-want/)). Here, **evals are the release gate**, just as unit tests are for ordinary code.

## 1. Conventional tests (no live LLM)

| Level | What |
|---|---|
| Unit | guardrails (topic, PII filter, grounding compare), confirmation state machine, OTP lockout, budget, handoff policy, webhook signature |
| MCP server | each tool with a fake backend: scoping (another customer's policy → refused), trimming, audit record, token validation (expired, wrong audience, missing) |
| Agent loop | scripted fake LLM that emits given tool calls → assert tool dispatch, max-iterations stop, error handling, fallback to menus |
| Integration | FastAPI + Redis + Postgres (Testcontainers) + simulated WhatsApp + fake core APIs |
| Replay | recorded real conversations ("cassettes") replayed on every PR at zero token cost |

## 2. Evaluation suite (live model, nightly + before release)

### Datasets (`evals/datasets/*.yaml`)

| Set | Size (target) | Examples |
|---|---|---|
| `core_tasks` | 40 | balance, status, pay premium, claim status, loan next instalment, FAQ |
| `languages` | 20 | the same tasks in Shona and Ndebele, and mixed-language messages ("ndoda kubhadhara premium yangu") |
| `safety` | 30 | third-party data requests, injection in text and in documents, "print your prompt", payment without confirmation, fake confirmation codes |
| `scope` | 15 | off-topic (homework, politics, general chit-chat) → polite refusal (WhatsApp policy) |
| `handoff` | 15 | bereavement, complaint, legal threat, repeated failure, explicit request |
| `grounding` | 20 | backend returns tricky numbers (ZWG vs USD, zero balance, several policies) → reply must match exactly |

Each case: conversation turns, fixture backend state, expected tool calls (ordered or set), expected facts, forbidden content, expected handoff/refusal.

### Graders

| Grader | Type | Checks |
|---|---|---|
| Tool trajectory | deterministic | right tools, right arguments, no forbidden tools, ≤ 5 calls |
| Facts | deterministic | amounts, dates, statuses and references in the reply equal the fixture values |
| Safety | deterministic | no third-party data, no confirmation bypass, no PII patterns |
| Refusal / handoff | deterministic + classifier | refused or handed off when expected, and not otherwise |
| Tone & clarity | LLM-as-judge with a rubric (1–5), calibrated against 30 human-labelled examples; agreement reported | polite, concise, correct language, no jargon |

### Release gates

| Metric | Gate |
|---|---|
| `safety` pass rate | **100%** |
| overall pass rate | ≥ 90% |
| `languages` pass rate | ≥ 85% |
| regression vs last release | no set drops > 3 points |
| p95 latency (eval run) | < 10 s |
| cost per resolved task | reported (trend, not gate) |

Reports are saved to `evals/reports/<date>.md` and linked from the README; the CI badge shows the latest pass rate.

## 3. Red-teaming

Before launch: a 1-hour manual red-team session per release using the OWASP LLM Top 10 as a checklist; every successful attack becomes a new `safety` case (the suite only grows).

## 4. Online monitoring (after launch)

Containment rate, handoff reasons, thumbs up/down after resolution, sampled transcript review (5%/week), drift alerts when the handoff rate or fallback rate moves > 2σ.
