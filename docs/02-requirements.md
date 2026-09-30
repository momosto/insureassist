# InsureAssist — requirements

## 1. Scope

**In scope (intents):** identity verification; list policies; policy details and cover status; premium balance and arrears; pay premium (EcoCash); claim status; report a new claim (first notice of loss, draft only); loan summary, next instalment, settlement quote; pay loan instalment; product FAQs and "how do I…"; talk to a human; complaints (always to a human).
**Out of scope:** general chat, advice outside InsureHub products, underwriting decisions, claim approval, changing personal details (handoff), anything for a person other than the verified customer.

## 2. Personas

| Persona | Need |
|---|---|
| Farai, kombi owner, lapsed policy | "How much to get my cover back?" → pay by EcoCash at 21:00 |
| Chipo, funeral plan holder | report a death in the family; wants a person, not a bot |
| Mai Chipo, trader with a LendHub loan | "When is my next payment?" in Shona |
| Staff agent (contact centre) | take over conversations with context; see what the bot did |
| Compliance officer | prove what the bot said and did, and why |

## 3. Functional requirements

| ID | Story | Acceptance criteria |
|---|---|---|
| IA-01 | As a customer, I verify myself before seeing personal data | WhatsApp number must match a customer record **and** an OTP sent by SMS (simulated) is entered; 3 wrong OTPs → 30 min lock; session valid 30 min idle; public FAQs work without verification |
| IA-02 | As a customer, I ask about my policies in my own words and language | correct tool called; answer uses only tool data; replies in the customer's language (EN/SN/ND) |
| IA-03 | As a customer, I pay my premium in chat | bot states amount, policy and number to be charged → customer replies with a confirmation code → **the system** (not the model) initiates the payment via Payments with an idempotency key → EcoCash push → receipt after `payment.succeeded` |
| IA-04 | As a customer, I check a claim | status, last update, next step, and the assessor's requested documents (if any); never predicts approval |
| IA-05 | As a customer, I report a motor claim | collects date, place, description, photos (WhatsApp media) → creates a **draft** claim in InsureHub → reference number → ClaimGuard triage happens in the back office, never shown to the customer |
| IA-06 | As a customer, I check and pay my loan | loan summary, next instalment, settlement quote from LendHub; payment as in IA-03 |
| IA-07 | As a customer, I ask how things work | answers from the knowledge base via `search_help_articles`, with the article title cited; "I don't know" + handoff offer when nothing relevant is found |
| IA-08 | As a customer, I can always reach a human | "agent"/"munhu"/"umuntu" or equivalent → handoff; the bot confirms and stops replying |
| IA-09 | As a staff agent, I take over a conversation | inbox shows queue, transcript, AI summary, detected intent, sentiment, tool calls; agent replies through the same channel; hand back to the bot |
| IA-10 | As the business, we nudge customers proactively | `policy.lapsed` / `loan.arrears-changed` → WhatsApp **utility template** with reinstatement or payment instructions (opt-out honoured) |
| IA-11 | As compliance, I audit everything | per turn: input, output, model, prompt version, tool calls (redacted args), latency, tokens, guardrail verdicts, trace ID |

## 4. Guardrail requirements

| ID | Rule | Enforced by |
|---|---|---|
| G-01 | Off-topic requests are politely refused and redirected to supported services | topic classifier (small model) + system prompt + eval set |
| G-02 | The model can't choose the customer: tools take no `customer_id` argument; identity is injected from the session | MCP server design (ADR-0003) |
| G-03 | Money movement needs a confirmation code the customer types back, matching a pending intent (amount, reference, expiry 5 min) | code in the agent service (ADR-0004) |
| G-04 | Answers containing amounts, dates or statuses must come from tool results in the same turn | grounding check (deterministic compare of numbers in the reply vs tool outputs) |
| G-05 | No personal data of third parties, no full national IDs or card-like numbers in replies | output filter (regex + allowlist) |
| G-06 | Mandatory handoff: bereavement/funeral claims, complaints, legal threats, 2 failed tool attempts, negative sentiment trend, customer request | handoff policy module |
| G-07 | Instructions inside documents, images or tool outputs are treated as data, not commands | prompt structure (tool results in separate blocks), injection eval set |
| G-08 | Per-customer and global rate and token limits; menu fallback when the budget is exhausted | budget service |

## 5. Non-functional requirements

| Category | Requirement |
|---|---|
| Latency | first reply < 10 s for 95% (WhatsApp users expect fast replies); "typing…" indicator sent immediately |
| Quality | eval pass rate ≥ 90% overall and **100% on the safety set** (identity, confirmation, third-party data) before any release |
| Availability | 99% (demo); if the LLM is unavailable, fall back to menus |
| Cost | daily token budget; prompt caching on; tool outputs trimmed to needed fields |
| Privacy | transcripts retained 12 months (illustrative); redaction before storage and before sending to the LLM where the field isn't needed |
| Observability | OTel traces per turn: model call spans, tool spans (MCP), guardrail spans |
| Languages | English, Shona, Ndebele; eval set covers all three |

## Sources
- WhatsApp AI policy 2026: [respond.io](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban) · [Alibaba Cloud guide](https://www.alibabacloud.com/help/en/chatapp/use-cases/whatsapp-ai-policy-2026-guide)
- WhatsApp pricing and 24-hour window: [Meta docs](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
