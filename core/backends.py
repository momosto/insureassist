"""Core-system access used by the tool service (MCP server).

`FixtureBackend` mirrors the InsureHub and LendHub demo seeds so the assistant runs on its own (demo, tests, evals).
`HttpBackend` talks to the real services: LendHub's channel endpoints exist today; InsureHub's channel endpoints are
specified in insurehub/NEXT_STEPS.md ("Ecosystem work") and are called here with that contract.
"""
from __future__ import annotations

import copy
import itertools
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

import httpx


class BackendError(Exception):
    """A core system failed or rejected the call (shown to the customer as an apology, never as raw text)."""


@dataclass
class Customer:
    customer_ref: str
    first_name: str
    msisdn: str
    language: str = "en"


@dataclass
class Policy:
    number: str
    line: str  # MOTOR | FUNERAL | HOME
    description: str
    status: str  # ACTIVE | LAPSED | ...
    currency: str
    monthly_premium: Decimal
    sum_insured: Decimal
    arrears: Decimal
    next_due: date | None


@dataclass
class Claim:
    number: str
    policy_number: str
    status: str
    last_update: str
    next_step: str
    missing_documents: list[str] = field(default_factory=list)


@dataclass
class Loan:
    loan_id: str
    loan_number: str
    product: str
    currency: str
    status: str
    principal: Decimal
    outstanding_principal: Decimal
    arrears: Decimal
    days_past_due: int
    next_due_date: date | None
    next_amount_due: Decimal
    settlement_amount: Decimal


@dataclass
class PaymentStarted:
    payment_id: str
    status: str


class Backend(Protocol):
    def find_customer(self, msisdn: str) -> Customer | None: ...
    def policies(self, customer_ref: str) -> list[Policy]: ...
    def claims(self, customer_ref: str) -> list[Claim]: ...
    def create_draft_claim(self, customer_ref: str, policy_number: str, incident_date: str, location: str,
                           description: str, media_ids: list[str]) -> str: ...
    def loans(self, customer_ref: str) -> list[Loan]: ...
    def start_payment(self, customer_ref: str, kind: str, reference: str, amount: Decimal, currency: str,
                      msisdn: str, idempotency_key: str) -> PaymentStarted: ...


# ------------------------------------------------------------------------------------------------ fixtures

def _seed(today: date) -> dict:
    d = Decimal
    return {
        "customers": {
            "263772123456": Customer("IH-CUS-0001", "Tendai", "263772123456", "en"),
            "263733456789": Customer("IH-CUS-0003", "Farai", "263733456789", "en"),
            "263788112233": Customer("IH-CUS-0004", "Chipo", "263788112233", "sn"),
            "263712987654": Customer("IH-CUS-0002", "Nyasha", "263712987654", "en"),
            "263774556677": Customer("IH-CUS-0005", "Tatenda", "263774556677", "nd"),
            "263779000111": Customer("CUS-001001", "Mai Chipo", "263779000111", "sn"),
        },
        "policies": {
            "IH-CUS-0001": [
                Policy("MOT-2026-000101", "MOTOR", "Toyota Hilux 2.8 GD-6 AEZ 4521 - Comprehensive", "ACTIVE", "USD",
                       d("94.50"), d("28000.00"), d("0.00"), today + timedelta(days=12)),
                Policy("FUN-2026-000102", "FUNERAL", "Standard funeral plan - 5 lives", "ACTIVE", "USD",
                       d("18.00"), d("5000.00"), d("0.00"), today + timedelta(days=9)),
            ],
            "IH-CUS-0003": [
                Policy("MOT-2026-000301", "MOTOR", "Toyota HiAce Quantum ACF 7788 - Comprehensive (kombi)", "LAPSED", "USD",
                       d("62.40"), d("9000.00"), d("187.20"), today - timedelta(days=71)),
            ],
            "IH-CUS-0004": [
                Policy("FUN-2026-000401", "FUNERAL", "Premium funeral plan - 3 lives", "ACTIVE", "ZWG",
                       d("965.00"), d("268000.00"), d("0.00"), today + timedelta(days=5)),
            ],
            "IH-CUS-0002": [
                Policy("HOM-2026-000201", "HOME", "Home cover - Borrowdale, Harare", "ACTIVE", "USD",
                       d("71.25"), d("141000.00"), d("0.00"), today + timedelta(days=20)),
            ],
            "IH-CUS-0005": [
                Policy("MOT-2026-000501", "MOTOR", "Honda Fit AFH 2290 - Third Party Only", "ACTIVE", "ZWG",
                       d("320.00"), d("0.00"), d("320.00"), today - timedelta(days=4)),
            ],
            "CUS-001001": [],
        },
        "claims": {
            "IH-CUS-0001": [Claim("CLM-2026-000111", "MOT-2026-000101", "UNDER_REVIEW",
                                  "Assessor booked at Zimoco panel beaters for Thursday.",
                                  "The assessor inspects the vehicle, then we confirm the repair authority.", [])],
            "IH-CUS-0002": [Claim("CLM-2026-000211", "HOM-2026-000201", "APPROVED", "Approved less USD 200 excess.",
                                  "Payment is released within 5 working days.", [])],
            "IH-CUS-0004": [Claim("CLM-2026-000411", "FUN-2026-000401", "PAID", "Paid via RTGS to the nominated account.",
                                  "Nothing further is needed.", [])],
            "IH-CUS-0005": [Claim("CLM-2026-000511", "MOT-2026-000501", "REJECTED",
                                  "Third-party-only cover does not include own damage or theft of personal items.",
                                  "You may appeal in writing within 30 days.", [])],
        },
        "loans": {
            "CUS-001001": [Loan("lh-0001", "LN-2610-000001", "TRADER", "USD", "ACTIVE", d("600.00"), d("554.88"),
                                d("0.00"), 0, today + timedelta(days=3), d("57.03"), d("566.43"))],
        },
    }


class FixtureBackend:
    """In-memory core systems seeded like InsureHub/LendHub demos. Thread-safe; `reset()` restores the seed."""

    def __init__(self, today: date | None = None, fail_tools: set[str] | None = None):
        self._today = today or date.today()
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self.fail_tools = fail_tools or set()
        self.payments: list[dict] = []
        self.draft_claims: list[dict] = []
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._data = _seed(self._today)
            self.payments.clear()
            self.draft_claims.clear()

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail_tools:
            raise BackendError(f"{name} unavailable (simulated outage)")

    def find_customer(self, msisdn: str) -> Customer | None:
        return copy.deepcopy(self._data["customers"].get(msisdn))

    def policies(self, customer_ref: str) -> list[Policy]:
        self._maybe_fail("policies")
        return copy.deepcopy(self._data["policies"].get(customer_ref, []))

    def claims(self, customer_ref: str) -> list[Claim]:
        self._maybe_fail("claims")
        return copy.deepcopy(self._data["claims"].get(customer_ref, []))

    def create_draft_claim(self, customer_ref, policy_number, incident_date, location, description, media_ids) -> str:
        self._maybe_fail("claims")
        with self._lock:
            number = f"DRF-2026-{next(self._seq):05d}"
            self.draft_claims.append({"number": number, "customer_ref": customer_ref, "policy_number": policy_number,
                                      "incident_date": incident_date, "location": location,
                                      "description": description, "media_ids": media_ids})
            return number

    def loans(self, customer_ref: str) -> list[Loan]:
        self._maybe_fail("loans")
        return copy.deepcopy(self._data["loans"].get(customer_ref, []))

    def start_payment(self, customer_ref, kind, reference, amount, currency, msisdn, idempotency_key) -> PaymentStarted:
        self._maybe_fail("payments")
        with self._lock:
            for p in self.payments:
                if p["idempotency_key"] == idempotency_key:
                    return PaymentStarted(p["payment_id"], "PENDING")
            payment_id = f"PAY-{next(self._seq):06d}"
            self.payments.append({"payment_id": payment_id, "customer_ref": customer_ref, "kind": kind,
                                  "reference": reference, "amount": amount, "currency": currency, "msisdn": msisdn,
                                  "idempotency_key": idempotency_key})
            return PaymentStarted(payment_id, "PENDING")


# ------------------------------------------------------------------------------------------------ real services

class HttpBackend:
    """Real core APIs with service credentials; customer scoping travels as X-On-Behalf-Of (architecture §4)."""

    def __init__(self, insurehub_url: str, lendhub_url: str, payments_url: str, lendhub_user: str, lendhub_password: str,
                 timeout: float = 10.0):
        self._ih = httpx.Client(base_url=insurehub_url, timeout=timeout)
        self._lh = httpx.Client(base_url=lendhub_url, timeout=timeout)
        self._pay = httpx.Client(base_url=payments_url, timeout=timeout)
        self._lh_user, self._lh_password = lendhub_user, lendhub_password
        self._lh_token: str | None = None

    def _lendhub(self, method: str, path: str, **kw) -> httpx.Response:
        if not self._lh_token:
            r = self._lh.post("/api/v1/auth/login", json={"username": self._lh_user, "password": self._lh_password})
            r.raise_for_status()
            self._lh_token = r.json()["accessToken"]
        r = self._lh.request(method, path, headers={"Authorization": f"Bearer {self._lh_token}", **kw.pop("headers", {})}, **kw)
        if r.status_code == 401:
            self._lh_token = None
            return self._lendhub(method, path, **kw)
        return r

    def find_customer(self, msisdn: str) -> Customer | None:
        try:
            r = self._ih.get("/api/channels/customers", params={"msisdn": msisdn})
            if r.status_code == 200 and r.json():
                c = r.json()[0]
                return Customer(c["customerRef"], c["firstName"], msisdn)
            r = self._lendhub("GET", "/api/v1/customers", params={"msisdn": msisdn})
            if r.status_code == 200 and r.json():
                c = r.json()[0]
                return Customer(c["customerRef"], c["firstName"], msisdn)
        except httpx.HTTPError as e:
            raise BackendError(str(e)) from e
        return None

    def policies(self, customer_ref: str) -> list[Policy]:
        try:
            r = self._ih.get("/api/channels/policies", headers={"X-On-Behalf-Of": customer_ref})
            if r.status_code == 404:
                return []
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(str(e)) from e
        return [Policy(p["policyNumber"], p["line"], p["description"], p["status"], p["currency"],
                       Decimal(str(p["monthlyPremium"])), Decimal(str(p["sumInsured"])), Decimal(str(p.get("arrears", 0))),
                       date.fromisoformat(p["nextDue"]) if p.get("nextDue") else None) for p in r.json()]

    def claims(self, customer_ref: str) -> list[Claim]:
        try:
            r = self._ih.get("/api/channels/claims", headers={"X-On-Behalf-Of": customer_ref})
            if r.status_code == 404:
                return []
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(str(e)) from e
        return [Claim(c["claimNumber"], c["policyNumber"], c["status"], c.get("lastUpdate", ""), c.get("nextStep", ""),
                      c.get("missingDocuments", [])) for c in r.json()]

    def create_draft_claim(self, customer_ref, policy_number, incident_date, location, description, media_ids) -> str:
        try:
            r = self._ih.post("/api/channels/claims/drafts", headers={"X-On-Behalf-Of": customer_ref},
                              json={"policyNumber": policy_number, "incidentDate": incident_date, "location": location,
                                    "description": description, "mediaIds": media_ids})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(str(e)) from e
        return r.json()["draftNumber"]

    def loans(self, customer_ref: str) -> list[Loan]:
        try:
            r = self._lendhub("GET", f"/api/v1/customers/{customer_ref}/loans")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(str(e)) from e
        return [Loan(loan["loanId"], loan["loanNumber"], loan["productCode"], loan["currency"], loan["status"], Decimal(str(loan["principal"])),
                     Decimal(str(loan["outstandingPrincipal"])), Decimal(str(loan["arrearsAmount"])), loan["daysPastDue"],
                     date.fromisoformat(loan["nextDueDate"]) if loan.get("nextDueDate") else None,
                     Decimal(str(loan["nextAmountDue"])), Decimal(str(loan["settlementAmount"]))) for loan in r.json()]

    def start_payment(self, customer_ref, kind, reference, amount, currency, msisdn, idempotency_key) -> PaymentStarted:
        try:
            if kind == "loan":
                loan = next(x for x in self.loans(customer_ref) if x.loan_number == reference)
                r = self._lendhub("POST", f"/api/v1/loans/{loan.loan_id}/repayment-requests",
                                  headers={"Idempotency-Key": idempotency_key}, json={"amount": float(amount), "msisdn": msisdn})
                r.raise_for_status()
                return PaymentStarted(str(r.json().get("paymentId")), str(r.json().get("status")))
            r = self._pay.post("/payments", headers={"Idempotency-Key": idempotency_key},
                               json={"policyNumber": reference, "amount": float(amount), "currency": currency,
                                     "method": "EcoCash", "msisdn": msisdn})
            r.raise_for_status()
            return PaymentStarted(str(r.json()["id"]), str(r.json()["status"]))
        except (httpx.HTTPError, StopIteration) as e:
            raise BackendError(str(e)) from e
