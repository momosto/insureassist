# InsureAssist — agentic WhatsApp customer-service assistant

**Status:** 📝 Planned — build starts 2026-11-05 (3 weeks) · **Stack rotation slot:** Python
**Stack:** Python 3.12 · FastAPI · Anthropic Python SDK (Claude tool use) · **MCP Python SDK (FastMCP, Streamable HTTP)** · SQLAlchemy 2 + PostgreSQL · Redis · HTMX + Tailwind (staff inbox, web-chat simulator) · OpenTelemetry (GenAI conventions) · pytest · custom eval harness · GitHub Actions

**Author:** Simbarashe Nyamusa, Senior Software Engineer

InsureAssist lets InsureHub Group customers **check policies, pay premiums by EcoCash, follow up claims, report a new claim and check loan balances on WhatsApp**, in English, Shona or Ndebele. It is an **AI agent** built the way regulated businesses need it:

- it acts only through an **MCP server** of narrowly scoped tools over the core systems (InsureHub, Payments, LendHub, ClaimGuard);
- it **never sees or chooses whose data** it is working with — identity comes from a verified session, not from the model;
- **money moves need the customer's explicit confirmation**, checked by code rather than by the model;
- it **hands over to a human** when it should, with a summary;
- it is measured by an **evaluation suite in CI**, so every change is checked for accuracy, refusals and safety.

It is the working version of the *AI Virtual Care Centre* concept paper I wrote at TelOne.

> **Platform rule (researched):** since 15 Jan 2026 WhatsApp bans general-purpose AI assistants on the Business API, but allows business-specific customer-service bots. InsureAssist is deliberately scoped to InsureHub products and refuses off-topic requests ([respond.io](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban), [TechRadar](https://techradar.com/ai-platforms-assistants/meta-will-ban-rival-ai-chatbots-from-whatsapp)).

## Planning pack

| Document | Contents |
|---|---|
| [docs/01-concept-paper.md](docs/01-concept-paper.md) | business case: call-centre load, options, WhatsApp cost model, recommendation |
| [docs/02-requirements.md](docs/02-requirements.md) | intents, user stories, guardrail requirements, handoff rules, NFRs |
| [docs/03-architecture.md](docs/03-architecture.md) | C4, agent loop, MCP tool catalogue, identity & delegated auth, conversation flows |
| [docs/04-security-and-compliance.md](docs/04-security-and-compliance.md) | AI threat model (prompt injection, confused deputy, data leakage), OWASP LLM Top 10 mapping, data protection |
| [docs/05-test-and-evaluation-strategy.md](docs/05-test-and-evaluation-strategy.md) | eval datasets, graders, release gates, red-teaming |
| [docs/06-delivery-plan.md](docs/06-delivery-plan.md) | milestones, backlog, demo script |
| [docs/adr/](docs/adr/) | decisions |

## Planned layout

```
insureassist/
├── agent/                FastAPI app: WhatsApp webhook, web chat, agent loop, guardrails, sessions, staff inbox (HTMX)
├── mcp_server/           FastMCP server: tools over InsureHub, Payments, LendHub, ClaimGuard; delegated auth; audit
├── knowledge/            product FAQs & policy wordings (fictional) for the search_help_articles tool
├── evals/                datasets (YAML), graders, runner, reports
├── simulators/           fake WhatsApp Cloud API + OTP SMS for local/demo use
├── tests/                unit + integration tests (no live LLM calls)
└── docs/
```

## Demo (planned)

Public web-chat simulator at `assist.<domain>` (a WhatsApp-style UI; the real WhatsApp adapter is shown in the video with a test number). Demo customers match InsureHub's seed data (e.g. *Farai* — lapsed kombi policy).
