# ADR-0001: Build a scoped customer-service assistant, not a general-purpose chatbot

- **Status:** Accepted · **Date:** 2026-09-30

## Context
Meta's WhatsApp Business policy bans general-purpose AI assistants from 15 January 2026 but allows business-specific bots for support, bookings, tracking and notifications. Regulated financial services also need predictable, auditable answers.

## Options
1. General LLM chat with some tools: flexible, but violates platform policy and invites misuse.
2. **Scoped assistant:** fixed intents, a topic guard, refusal of off-topic requests, actions only via tools.
3. Pure menu bot: compliant and predictable, but poor with free text and mixed languages.

## Decision
Option 2, with Option 3 kept as the fallback path.

## Consequences
- ➕ Policy-compliant; smaller attack surface; easier to evaluate.
- ➖ Some customers will ask things we refuse; mitigated by a friendly redirect plus a human option.
- The `scope` eval set enforces this decision on every release.

## Sources
- [respond.io — WhatsApp 2026 AI policy explained](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban)
- [TechRadar — Meta bans rival AI chatbots from WhatsApp](https://techradar.com/ai-platforms-assistants/meta-will-ban-rival-ai-chatbots-from-whatsapp)
