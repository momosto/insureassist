# ADR-0003: Customer identity comes from the verified session via a delegated token, never from model arguments

- **Status:** Accepted · **Date:** 2026-09-30

## Context
If a tool accepted `customer_id` or `phone` as an argument, a prompt injection or social-engineering message could get the model to request another customer's data (a confused-deputy attack).

## Decision
- Customer tools have **no identity arguments**.
- After OTP verification, the agent service mints a short-lived signed token (`sub=customerRef`, `aud=insureassist-mcp`, `scope=self-service`, 15 min) and sends it with every MCP call. The model never sees the token.
- The MCP server validates the token and filters every backend query by `sub`. Object-level checks (e.g. `policy_number` belongs to `sub`) are applied as well.

## Consequences
- ➕ The model can't leak what it can't request; injection attacks fail by design, not by prompt wording.
- ➖ Staff "on behalf of" use needs a different token type (future staff copilot).
- Tested by the `safety` eval set and MCP server unit tests.
