# ADR-0004: Money movement is prepared by the model but executed only by code after explicit customer confirmation

- **Status:** Accepted · **Date:** 2026-09-30

## Context
An agent that can "pay" is useful, but an LLM must not be the last line of defence before charging someone's EcoCash wallet.

## Decision
- Tools only **prepare** payments (`prepare_premium_payment`, `prepare_loan_payment`). They create a pending intent in Redis: amount, currency, reference, msisdn, a 4-digit code and a 5-minute expiry.
- The bot shows the details and asks the customer to reply with the code.
- The **agent service** (deterministic code, outside the model loop) matches the next inbound message against the pending intent, then calls Payments with an `Idempotency-Key` equal to the intent ID.
- No tool exists that executes a payment.

## Consequences
- ➕ Hallucinated or injected "payments" are impossible; double charges are prevented by idempotency.
- ➕ Clear audit trail: intent → confirmation message → payment ID → provider result.
- ➖ One extra message for the customer; acceptable (and familiar from USSD/EcoCash flows).
