"""The MCP tool catalogue (docs/03-architecture.md §3), as plain Python so it can be tested without a transport.

Rules that make the tools hard to misuse (OWASP LLM06 "excessive agency"):
- no tool takes a customer identifier; the customer comes from the verified delegated token (ADR-0003);
- policy/loan arguments are checked against the customer's own records ("not_yours" otherwise);
- money tools only *prepare* a payment; there is no execute tool (ADR-0004);
- results are trimmed to the fields the assistant needs (no national IDs, no addresses).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from core.backends import Backend, BackendError
from core.knowledge import KnowledgeBase

log = logging.getLogger("insureassist.tools")


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Caller:
    """Who the tool runs for, taken from the validated delegated token. `None` customer = public tools only."""
    customer_ref: str | None
    msisdn: str | None
    conversation_id: str | None = None


def _s(schema_props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": schema_props, "required": required, "additionalProperties": False}


# Stable order and wording: tool definitions are part of the cached prompt prefix.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {"name": "list_my_policies",
     "description": "List the verified customer's insurance policies with status, monthly premium, arrears and next due date.",
     "input_schema": _s({}, [])},
    {"name": "get_policy",
     "description": "Details of one of the customer's policies: cover description, sum insured, status, arrears.",
     "input_schema": _s({"policy_number": {"type": "string", "description": "e.g. MOT-2026-000301"}}, ["policy_number"])},
    {"name": "get_premium_balance",
     "description": "Amount needed to clear arrears / reinstate one of the customer's policies, and the regular premium.",
     "input_schema": _s({"policy_number": {"type": "string"}}, ["policy_number"])},
    {"name": "prepare_premium_payment",
     "description": ("Prepare (do NOT charge) an EcoCash premium payment for one of the customer's policies. "
                     "If amount is omitted, the arrears (or one month's premium when nothing is overdue) is used. "
                     "The system then asks the customer to confirm with a code; you never execute payments."),
     "input_schema": _s({"policy_number": {"type": "string"}, "amount": {"type": "number"}}, ["policy_number"])},
    {"name": "get_claim_status",
     "description": "Status, last update, next step and missing documents for the customer's claims (all, or one claim).",
     "input_schema": _s({"claim_number": {"type": "string"}}, [])},
    {"name": "start_motor_claim",
     "description": ("Create a DRAFT motor claim (first notice of loss) on one of the customer's motor policies. "
                     "Only call after the customer has given the date, place and what happened."),
     "input_schema": _s({"policy_number": {"type": "string"}, "incident_date": {"type": "string", "description": "YYYY-MM-DD"},
                         "location": {"type": "string"}, "description": {"type": "string"},
                         "media_ids": {"type": "array", "items": {"type": "string"}}},
                        ["policy_number", "incident_date", "location", "description"])},
    {"name": "get_loan_summary",
     "description": "The customer's InsureHub Microfinance loans: balance, arrears, next instalment date and amount.",
     "input_schema": _s({}, [])},
    {"name": "get_settlement_quote",
     "description": "Amount to settle one of the customer's loans in full today.",
     "input_schema": _s({"loan_number": {"type": "string"}}, ["loan_number"])},
    {"name": "prepare_loan_payment",
     "description": ("Prepare (do NOT charge) an EcoCash loan repayment. If amount is omitted, the next amount due is used. "
                     "The system asks the customer to confirm with a code."),
     "input_schema": _s({"loan_number": {"type": "string"}, "amount": {"type": "number"}}, ["loan_number"])},
    {"name": "search_help_articles",
     "description": "Search InsureHub's help articles (products, how-to, claims documents, waiting periods, complaints).",
     "input_schema": _s({"query": {"type": "string"}}, ["query"])},
    {"name": "request_human",
     "description": ("Hand the conversation to a human agent. Use for complaints, bereavement, legal matters, changes to "
                     "personal details, or when the customer asks for a person or you cannot help."),
     "input_schema": _s({"reason": {"type": "string"}}, ["reason"])},
]

PUBLIC_TOOLS = {"search_help_articles", "request_human"}
TOOL_NAMES = [t["name"] for t in TOOL_DEFINITIONS]

PREPARE_TOOLS = {"prepare_premium_payment", "prepare_loan_payment"}
MAX_SELF_SERVICE_PAYMENT = {"USD": Decimal("2000"), "ZWG": Decimal("60000")}


def _money(v: Decimal) -> str:
    return f"{v:.2f}"


def redact(value: Any) -> Any:
    """Audit redaction: national-ID-like and card-like strings never reach logs."""
    if isinstance(value, str):
        value = re.sub(r"\b\d{2}-?\d{6,7}-?[A-Za-z]-?\d{2}\b", "[NATIONAL-ID]", value)
        value = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[CARD]", value)
        return value[:300]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class ToolService:
    def __init__(self, backend: Backend, kb: KnowledgeBase | None = None,
                 audit: Callable[[dict], None] | None = None):
        self.backend = backend
        self.kb = kb or KnowledgeBase()
        self.audit = audit or (lambda record: log.info("tool_audit %s", record))

    # ---------------------------------------------------------------------------------------------- dispatch
    def call(self, name: str, args: dict[str, Any] | None, caller: Caller) -> dict[str, Any]:
        args = dict(args or {})
        started = time.perf_counter()
        outcome = "ok"
        try:
            if name not in TOOL_NAMES:
                raise ToolError("unknown_tool", f"No tool named {name}")
            if name not in PUBLIC_TOOLS and not caller.customer_ref:
                raise ToolError("not_verified", "The customer must be verified before personal data can be used")
            if any(k in args for k in ("customer_id", "customer_ref", "msisdn", "national_id")):
                raise ToolError("bad_arguments", "Tools never take a customer identifier")
            result = getattr(self, name)(caller, **args)
            return result
        except ToolError as e:
            outcome = e.code
            raise
        except BackendError as e:
            outcome = "backend_unavailable"
            raise ToolError("backend_unavailable", "The core system did not respond; try again later") from e
        except TypeError as e:
            outcome = "bad_arguments"
            raise ToolError("bad_arguments", str(e)) from e
        finally:
            self.audit({"tool": name, "customer_ref": caller.customer_ref, "conversation_id": caller.conversation_id,
                        "args": redact(args), "outcome": outcome,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1)})

    # ---------------------------------------------------------------------------------------------- helpers
    def _policy(self, caller: Caller, policy_number: str):
        number = policy_number.strip().upper()
        for p in self.backend.policies(caller.customer_ref):
            if p.number == number:
                return p
        raise ToolError("not_yours", f"Policy {number} is not one of this customer's policies")

    def _loan(self, caller: Caller, loan_number: str):
        number = loan_number.strip().upper()
        for loan in self.backend.loans(caller.customer_ref):
            if loan.loan_number == number:
                return loan
        raise ToolError("not_yours", f"Loan {number} is not one of this customer's loans")

    @staticmethod
    def _amount(raw: Any, default: Decimal, currency: str) -> Decimal:
        if raw is None:
            amount = default
        else:
            try:
                amount = Decimal(str(raw)).quantize(Decimal("0.01"))
            except InvalidOperation as e:
                raise ToolError("bad_arguments", "Amount must be a number") from e
        if amount <= 0:
            raise ToolError("bad_amount", "Amount must be more than zero")
        if amount > MAX_SELF_SERVICE_PAYMENT[currency]:
            raise ToolError("over_limit", f"Payments above {currency} {MAX_SELF_SERVICE_PAYMENT[currency]} need a branch or agent")
        return amount

    @staticmethod
    def _policy_view(p) -> dict:
        return {"policy_number": p.number, "line": p.line, "description": p.description, "status": p.status,
                "currency": p.currency, "monthly_premium": _money(p.monthly_premium), "arrears": _money(p.arrears),
                "next_due": p.next_due.isoformat() if p.next_due else None}

    # ---------------------------------------------------------------------------------------------- tools
    def list_my_policies(self, caller: Caller) -> dict:
        return {"policies": [self._policy_view(p) for p in self.backend.policies(caller.customer_ref)]}

    def get_policy(self, caller: Caller, policy_number: str) -> dict:
        p = self._policy(caller, policy_number)
        return {**self._policy_view(p), "sum_insured": _money(p.sum_insured)}

    def get_premium_balance(self, caller: Caller, policy_number: str) -> dict:
        p = self._policy(caller, policy_number)
        return {"policy_number": p.number, "status": p.status, "currency": p.currency, "arrears": _money(p.arrears),
                "amount_to_reinstate": _money(p.arrears) if p.status == "LAPSED" else "0.00",
                "monthly_premium": _money(p.monthly_premium),
                "next_due": p.next_due.isoformat() if p.next_due else None}

    def prepare_premium_payment(self, caller: Caller, policy_number: str, amount: Any = None) -> dict:
        p = self._policy(caller, policy_number)
        default = p.arrears if p.arrears > 0 else p.monthly_premium
        value = self._amount(amount, default, p.currency)
        return {"prepared": True, "kind": "premium", "reference": p.number, "currency": p.currency,
                "amount": _money(value), "description": p.description,
                "msisdn_masked": "*****" + (caller.msisdn or "")[-4:],
                "note": "Not charged yet. The system will ask the customer to confirm with a code."}

    def get_claim_status(self, caller: Caller, claim_number: str | None = None) -> dict:
        claims = self.backend.claims(caller.customer_ref)
        if claim_number:
            number = claim_number.strip().upper()
            claims = [c for c in claims if c.number == number]
            if not claims:
                raise ToolError("not_yours", f"Claim {number} is not one of this customer's claims")
        return {"claims": [{"claim_number": c.number, "policy_number": c.policy_number, "status": c.status,
                            "last_update": c.last_update, "next_step": c.next_step,
                            "missing_documents": c.missing_documents} for c in claims]}

    def start_motor_claim(self, caller: Caller, policy_number: str, incident_date: str, location: str, description: str,
                          media_ids: list[str] | None = None) -> dict:
        p = self._policy(caller, policy_number)
        if p.line != "MOTOR":
            raise ToolError("wrong_line", "Motor claims can only be started on a motor policy")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", incident_date or ""):
            raise ToolError("bad_arguments", "incident_date must be YYYY-MM-DD")
        number = self.backend.create_draft_claim(caller.customer_ref, p.number, incident_date, location.strip()[:200],
                                                 description.strip()[:2000], list(media_ids or []))
        return {"draft_claim_number": number, "policy_number": p.number,
                "next_step": "A claims officer will contact you within 2 working days. Keep the police report reference."}

    def get_loan_summary(self, caller: Caller) -> dict:
        return {"loans": [{"loan_number": loan.loan_number, "product": loan.product, "status": loan.status, "currency": loan.currency,
                           "principal": _money(loan.principal), "outstanding_principal": _money(loan.outstanding_principal),
                           "arrears": _money(loan.arrears), "days_past_due": loan.days_past_due,
                           "next_due_date": loan.next_due_date.isoformat() if loan.next_due_date else None,
                           "next_amount_due": _money(loan.next_amount_due)} for loan in self.backend.loans(caller.customer_ref)]}

    def get_settlement_quote(self, caller: Caller, loan_number: str) -> dict:
        loan = self._loan(caller, loan_number)
        return {"loan_number": loan.loan_number, "currency": loan.currency, "settlement_amount": _money(loan.settlement_amount),
                "valid_for": "today only"}

    def prepare_loan_payment(self, caller: Caller, loan_number: str, amount: Any = None) -> dict:
        loan = self._loan(caller, loan_number)
        default = loan.arrears + loan.next_amount_due if loan.arrears > 0 else loan.next_amount_due
        value = self._amount(amount, default, loan.currency)
        return {"prepared": True, "kind": "loan", "reference": loan.loan_number, "currency": loan.currency,
                "amount": _money(value), "description": f"{loan.product} loan {loan.loan_number}",
                "msisdn_masked": "*****" + (caller.msisdn or "")[-4:],
                "note": "Not charged yet. The system will ask the customer to confirm with a code."}

    def search_help_articles(self, caller: Caller, query: str) -> dict:
        hits = self.kb.search(query)
        return {"results": [{"title": p.title, "text": p.text, "article": p.slug} for p, _ in hits]}

    def request_human(self, caller: Caller, reason: str) -> dict:
        return {"handoff": True, "reason": reason[:200]}
