"""Deterministic planner with the same tool-use interface as Claude.

Used (1) as the public demo's "model" so the site costs nothing to run, (2) as the menu fallback when Claude is
unavailable, refuses, or the budget is spent (architecture §8), and (3) by CI evals so the safety gate is
reproducible. It does exactly what the guardrails expect of a well-behaved model: it only states facts that come
from tool results, prepares (never executes) payments, and offers a human when it cannot help.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from agent.llm import LlmResponse

# ------------------------------------------------------------------------------------------- localised phrases
T = {
    "menu": {
        "en": ("I can help with: 1) your policies and what you owe, 2) paying a premium by EcoCash, 3) claim status, "
               "4) reporting a motor accident, 5) your loan balance and repayments, 6) how things work. "
               "Type *agent* any time to talk to a person."),
        "sn": ("Ndinogona kukubatsira ne: 1) mapolicy ako nemari yaunokwereta, 2) kubhadhara premium neEcoCash, "
               "3) chimiro che claim, 4) kuzivisa tsaona yemotokari, 5) chikwereti chako nekubhadhara, 6) mashandiro "
               "ezvinhu. Nyora *munhu* kuti utaure nemunhu."),
        "nd": ("Ngingakusiza nge: 1) ama-policy akho lemali okweletayo, 2) ukubhadala i-premium nge-EcoCash, "
               "3) isimo se-claim, 4) ukubika ingozi yemota, 5) isikweleti sakho lokubhadala, 6) indlela izinto "
               "ezisebenza ngayo. Bhala *umuntu* ukukhuluma lomuntu."),
    },
    "greeting": {"en": "Hello{name}! I'm InsureAssist, the InsureHub WhatsApp assistant.",
                 "sn": "Mhoro{name}! Ndini InsureAssist, mubatsiri weInsureHub paWhatsApp.",
                 "nd": "Sawubona{name}! NginguInsureAssist, umsizi weInsureHub ku-WhatsApp."},
    "no_policies": {"en": "I can't find any insurance policies on your profile.",
                    "sn": "Handisi kuwana mapolicy ane chekuita newe.",
                    "nd": "Angitholi ama-policy akho."},
    "policies_head": {"en": "Your policies:", "sn": "Mapolicy ako:", "nd": "Ama-policy akho:"},
    "balance": {"en": "{desc} ({ref}) is {status}. Arrears: {ccy} {arrears}. Monthly premium: {ccy} {premium}.",
                "sn": "{desc} ({ref}) iri {status}. Mari yakasara: {ccy} {arrears}. Premium pamwedzi: {ccy} {premium}.",
                "nd": "{desc} ({ref}) i-{status}. Imali esalayo: {ccy} {arrears}. I-premium ngenyanga: {ccy} {premium}."},
    "reinstate": {"en": " Paying {ccy} {amount} reinstates your cover from the day we receive it. Reply *pay* to pay by EcoCash.",
                  "sn": " Ukabhadhara {ccy} {amount} cover yako inodzoka kubva zuva ratagamuchira mari. Nyora *pay* kuti ubhadhare neEcoCash.",
                  "nd": " Ungabhadala {ccy} {amount} i-cover yakho iyabuya kusukela ngelanga esiyamukela ngalo imali. Bhala *pay* ukubhadala nge-EcoCash."},
    "which_policy": {"en": "Which policy? {options}", "sn": "Ndeipi policy? {options}", "nd": "Yiphi i-policy? {options}"},
    "prepared": {"en": "Ready to pay {ccy} {amount} for {desc} ({ref}) from EcoCash number {msisdn}.",
                 "sn": "Takagadzirira kubhadhara {ccy} {amount} ye{desc} ({ref}) kubva paEcoCash {msisdn}.",
                 "nd": "Sesilungele ukubhadala {ccy} {amount} ye-{desc} ({ref}) kusuka ku-EcoCash {msisdn}."},
    "claims_none": {"en": "You have no claims with us.", "sn": "Hauna ma-claim.", "nd": "Awulama-claim."},
    "claim": {"en": "Claim {ref} on {policy}: {status}. Latest: {update} Next: {next}",
              "sn": "Claim {ref} pa{policy}: {status}. Zvichangoitika: {update} Zvinotevera: {next}",
              "nd": "I-claim {ref} ku-{policy}: {status}. Okwakamuva: {update} Okulandelayo: {next}"},
    "claim_docs": {"en": " Still needed: {docs}.", "sn": " Zvichiri kudiwa: {docs}.", "nd": " Okusadingeka: {docs}."},
    "claim_details": {"en": ("I'm sorry to hear about the accident. To start your claim, tell me: the date (YYYY-MM-DD), "
                             "where it happened, and what happened. You can also send photos of the damage."),
                      "sn": ("Ndine urombo nezvetsaona. Kuti titange claim, ndiudze: zuva (YYYY-MM-DD), pazvakaitikira, "
                             "uye zvakaitika. Unogona kutumirawo mifananidzo."),
                      "nd": ("Ngiyaxolisa ngengozi. Ukuqala i-claim, ngitshela: ilanga (YYYY-MM-DD), lapho okwenzakala "
                             "khona, lokwenzakeleyo. Ungathumela lezithombe.")},
    "claim_started": {"en": "Your draft claim {ref} on {policy} is lodged. {next}",
                      "sn": "Claim yako {ref} pa{policy} yanyoreswa. Mukuru wema-claim achakubata mukati memazuva 2 ebasa.",
                      "nd": "I-claim yakho {ref} ku-{policy} isibhalisiwe. Isisebenzi sama-claim sizakuthinta phakathi kwamalanga 2 omsebenzi."},
    "no_loans": {"en": "I can't find any loans on your profile.", "sn": "Handisi kuwana chikwereti chako.",
                 "nd": "Angitholi isikweleti sakho."},
    "loan": {"en": "Loan {ref} ({product}): outstanding {ccy} {outstanding}. Next instalment {ccy} {next} due {due}.",
             "sn": "Chikwereti {ref} ({product}): chasara {ccy} {outstanding}. Kubhadhara kunotevera {ccy} {next} pa{due}.",
             "nd": "Isikweleti {ref} ({product}): esisalayo {ccy} {outstanding}. Okulandelayo {ccy} {next} ngomhla {due}."},
    "loan_arrears": {"en": " Overdue: {ccy} {arrears} ({dpd} days).", "sn": " Zvakasarira: {ccy} {arrears} (mazuva {dpd}).",
                     "nd": " Okusele: {ccy} {arrears} (amalanga {dpd})."},
    "settlement": {"en": "To settle {ref} in full today: {ccy} {amount} (valid today only; future interest is not charged).",
                   "sn": "Kupedza {ref} nhasi: {ccy} {amount} (nhasi chete).",
                   "nd": "Ukuqeda {ref} lamuhla: {ccy} {amount} (lamuhla kuphela)."},
    "faq": {"en": "From our help article \"{title}\": {text}", "sn": "Kubva pachinyorwa \"{title}\": {text}",
            "nd": "Kusuka embhalweni \"{title}\": {text}"},
    "dont_know": {"en": "I'm not sure about that one. Would you like me to connect you to a person? Type *agent*.",
                  "sn": "Handina chokwadi nazvo. Unoda kutaura nemunhu here? Nyora *munhu*.",
                  "nd": "Kangiqinisekanga ngalokho. Ufuna ukukhuluma lomuntu? Bhala *umuntu*."},
    "handoff": {"en": "I'm connecting you to a member of our team. They'll reply here shortly.",
                "sn": "Ndiri kukubatanidza nemumwe wevashandi vedu. Vachakupindura pano munguva pfupi.",
                "nd": "Ngikuxhumanisa lelungu lethimba lethu. Bazakuphendula lapha masinyane."},
    "tool_error": {"en": "Sorry, I couldn't get that information right now. Please try again in a few minutes, or type *agent*.",
                   "sn": "Ndine urombo, handina kukwanisa kuwana ruzivo irworwo izvozvi. Edza zvakare, kana nyora *munhu*.",
                   "nd": "Uxolo, angikwazanga ukuthola lolo lwazi khathesi. Zama futhi, kumbe ubhale *umuntu*."},
    "not_yours": {"en": "I can only help with your own policies, claims and loans.",
                  "sn": "Ndinogona kubatsira nemapolicy, ma-claim nezvikwereti zvako chete.",
                  "nd": "Ngingasiza ngama-policy, ama-claim lezikweleti zakho kuphela."},
}

STATUS_WORDS = {"LAPSED": {"en": "lapsed (no cover)", "sn": "yakadzima (hapana cover)", "nd": "ivaliwe (ayikho i-cover)"},
                "ACTIVE": {"en": "active", "sn": "iri kushanda", "nd": "iyasebenza"}}


def t(key: str, lang: str, **kw: Any) -> str:
    variants = T[key]
    return variants.get(lang, variants["en"]).format(**kw)


def status_word(status: str, lang: str) -> str:
    return STATUS_WORDS.get(status, {}).get(lang, status.replace("_", " ").lower())


# ------------------------------------------------------------------------------------------- intent

def classify(text: str) -> str:
    s = text.lower()
    has = lambda pattern: re.search(pattern, s) is not None  # noqa: E731
    pay = has(r"\b(pay|repay|bhadhara|kubhadhara|bhadala|ukubhadala)\b") or has(r"\bpay\b")
    loanish = has(r"\b(loan|instal|chikwereti)") or has(r"sikweleti")
    settle = has(r"\b(settle|settlement|pay ?off|payoff|close my loan)\b")
    # "how do I pay my premium?" is a how-to question; "what is my balance?" is about the customer's own account
    personal = has(r"\bmy (?:\w+ )?(?:polic|claim|loan|balance|premium|cover|instal|payment|plan|arrears|account|funeral|kombi)")
    procedural = has(r"^(how do i|how can i|how does|what documents|which documents|what happens|do you|can i|is there)")
    question = has(r"^(what is|what's|what are|when (?:do|does|is|will)|why|does)")
    if not has(r"\bhow much\b") and (procedural or (question and not personal)):
        return "faq"
    if has(r"\bhow much\b") and not settle and not (pay and loanish):
        return "loan" if loanish else "balance"
    if pay and loanish:
        return "pay_loan"
    if settle:
        return "settle"
    if loanish or has(r"\bnext (?:payment|instalment)\b"):
        return "loan"
    if pay:
        return "pay_premium"
    if has(r"\b(owe|balance|arrear|how much|marii|lingakanani|reinstat|cover back|lapsed)\b"):
        return "balance"
    if has(r"\b(accident|crash|crashed|hit my|was hit|rear-?ended|report (?:a |an )?(?:new )?claim|start (?:a )?claim|"
           r"tsaona|ingozi|ndarohwa)\b"):
        return "new_claim"
    if has(r"\bclaims?\b|\bclm-") or has(r"\b(assessor|panel beater)\b"):
        return "claim_status"
    if has(r"\b(polic(?:y|ies)|my cover|my insurance|my plan|mapolicy|ama-?policy)\b"):
        return "policies"
    if has(r"^\s*(hi|hello|hey|good (?:morning|afternoon|evening)|mhoro|mhoroi|mangwanani|masikati|sawubona|"
           r"salibonani|menu|start)\b[\s!.?]*$"):
        return "greeting"
    return "faq"


def _policy_number(text: str) -> str | None:
    m = re.search(r"\b(?:MOT|FUN|HOM)-\d{4}-\d{6}\b", text.upper())
    return m.group(0) if m else None


def _loan_number(text: str) -> str | None:
    m = re.search(r"\bLN-\d{4}-\d{6}\b", text.upper())
    return m.group(0) if m else None


def _amount(text: str) -> float | None:
    m = re.search(r"(?:USD|ZWG|US\$|\$)\s?(\d+(?:\.\d{1,2})?)", text, re.I) or re.search(r"\bpay (\d+(?:\.\d{1,2})?)\b", text, re.I)
    return float(m.group(1)) if m else None


def _incident(text: str, today: date) -> tuple[str | None, str | None]:
    d = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    when = d.group(0) if d else None
    lowered = text.lower()
    if when is None and re.search(r"\b(yesterday|nezuro|izolo)\b", lowered):
        when = (today - timedelta(days=1)).isoformat()
    elif when is None and re.search(r"\b(today|this morning|nhasi|lamuhla)\b", lowered):
        when = today.isoformat()
    loc = re.search(r"\b(?:at|in|near|on|along)\s+((?:the\s+)?[A-Z0-9][\w'./-]*(?:\s+(?:[A-Z0-9][\w'./-]*|and|&|/|Rd|St|Ave|road|street|avenue))*)", text)
    return when, (loc.group(1).strip(" .,") if loc else None)


# ------------------------------------------------------------------------------------------- planner

@dataclass
class Context:
    language: str
    name: str | None
    verified: bool


def parse_context(blocks: list[dict]) -> tuple[Context, str]:
    ctx = Context("en", None, False)
    text_parts = []
    for b in blocks:
        if b.get("type") != "text":
            continue
        m = re.match(r"<context>(.*)</context>", b["text"], re.S)
        if m:
            fields = {k.strip(): v for k, v in (kv.split("=", 1) for kv in m.group(1).split(";") if "=" in kv)}
            ctx = Context(fields.get("language", "en").strip(), (fields.get("name") or "").strip() or None,
                          fields.get("verified", "no").strip() == "yes")
        else:
            text_parts.append(b["text"])
    return ctx, "\n".join(text_parts)


class OfflinePlanner:
    """Implements the LLM interface: `create(system, tools, messages) -> LlmResponse`."""

    model = "offline-planner-v1"

    def __init__(self, today: date | None = None):
        self._today = today

    def create(self, system: str, tools: list[dict], messages: list[dict]) -> LlmResponse:
        # the customer's message for this turn = the last user message that is not only tool results
        start = max(i for i, m in enumerate(messages) if m["role"] == "user" and not _only_tool_results(m))
        content = messages[start]["content"]
        ctx, text = parse_context(content if isinstance(content, list) else [{"type": "text", "text": content}])
        done: list[tuple[str, dict, dict | None, bool]] = []  # name, input, result, is_error
        results = {}
        for m in messages[start + 1:]:
            if m["role"] == "user":
                for b in m["content"]:
                    if b.get("type") == "tool_result":
                        results[b["tool_use_id"]] = b
        for m in messages[start + 1:]:
            if m["role"] == "assistant" and isinstance(m["content"], list):
                for b in m["content"]:
                    if b.get("type") == "tool_use":
                        r = results.get(b["id"])
                        payload = _json(r["content"]) if r else None
                        done.append((b["name"], b["input"], payload, bool(r and (r.get("is_error") or "error" in (payload or {})))))
        available = {tl["name"] for tl in tools}
        step = self._next(classify(text), text, ctx, done, available)
        if isinstance(step, tuple):
            name, args = step
            return LlmResponse([{"type": "tool_use", "id": f"toolu_offline_{len(done) + 1}", "name": name, "input": args}],
                               "tool_use", self.model)
        return LlmResponse([{"type": "text", "text": step}], "end_turn", self.model)

    # returns ("tool", args) for the next call or the final text
    def _next(self, intent: str, text: str, ctx: Context, done: list, available: set[str]):
        lang = ctx.language
        called = {d[0]: d for d in done}
        if done and done[-1][3]:
            error = (done[-1][2] or {}).get("error")
            return t("not_yours", lang) if error == "not_yours" else t("tool_error", lang)
        today = self._today or date.today()

        def need(tool: str, args: dict | None = None):
            return (tool, args or {}) if tool in available else None

        if intent == "greeting":
            return t("greeting", lang, name=f" {ctx.name}" if ctx.name else "") + " " + t("menu", lang)

        explicit = _policy_number(text)
        if intent in ("balance", "pay_premium") and explicit and "list_my_policies" not in called:
            tool = "get_premium_balance" if intent == "balance" else "prepare_premium_payment"
            if tool not in called:
                args = {"policy_number": explicit}
                if intent == "pay_premium" and _amount(text) is not None:
                    args["amount"] = _amount(text)
                return need(tool, args)
            r = called[tool][2]
            if intent == "balance":
                msg = t("balance", lang, desc=r["policy_number"], ref=r["policy_number"], status=status_word(r["status"], lang),
                        ccy=r["currency"], arrears=r["arrears"], premium=r["monthly_premium"])
                if r["status"] == "LAPSED" and float(r["amount_to_reinstate"]) > 0:
                    msg += t("reinstate", lang, ccy=r["currency"], amount=r["amount_to_reinstate"])
                return msg
            return t("prepared", lang, ccy=r["currency"], amount=r["amount"], desc=r["description"], ref=r["reference"],
                     msisdn=r["msisdn_masked"])

        if intent in ("policies", "balance", "pay_premium", "new_claim"):
            if "list_my_policies" not in called:
                return need("list_my_policies") or self._faq(text, ctx, called, available)
            policies = called["list_my_policies"][2]["policies"]
            if not policies:
                return t("no_policies", lang)
            if intent == "policies":
                lines = [f"• {p['description']} ({p['policy_number']}): {status_word(p['status'], lang)}, "
                         f"{p['currency']} {p['monthly_premium']}/month" for p in policies]
                return t("policies_head", lang) + "\n" + "\n".join(lines)
            if intent == "new_claim":
                motor = [p for p in policies if p["line"] == "MOTOR"]
                when, where = _incident(text, today)
                if not motor:
                    return t("no_policies", lang)
                if "start_motor_claim" in called:
                    r = called["start_motor_claim"][2]
                    return t("claim_started", lang, ref=r["draft_claim_number"], policy=r["policy_number"], next=r["next_step"])
                if not (when and where):
                    return t("claim_details", lang)
                chosen = _policy_number(text) or motor[0]["policy_number"]
                return need("start_motor_claim", {"policy_number": chosen, "incident_date": when, "location": where,
                                                  "description": text.strip()[:500]})
            chosen = _policy_number(text)
            if not chosen:
                owing = [p for p in policies if float(p["arrears"]) > 0 or p["status"] == "LAPSED"]
                pool = owing or policies
                if len(pool) > 1:
                    return t("which_policy", lang, options=", ".join(f"{p['policy_number']} ({p['description']})" for p in pool))
                chosen = pool[0]["policy_number"]
            if intent == "balance":
                if "get_premium_balance" not in called:
                    return need("get_premium_balance", {"policy_number": chosen})
                b = called["get_premium_balance"][2]
                desc = next((p["description"] for p in policies if p["policy_number"] == b["policy_number"]), b["policy_number"])
                msg = t("balance", lang, desc=desc, ref=b["policy_number"], status=status_word(b["status"], lang),
                        ccy=b["currency"], arrears=b["arrears"], premium=b["monthly_premium"])
                if b["status"] == "LAPSED" and float(b["amount_to_reinstate"]) > 0:
                    msg += t("reinstate", lang, ccy=b["currency"], amount=b["amount_to_reinstate"])
                return msg
            # pay_premium
            if "prepare_premium_payment" not in called:
                args = {"policy_number": chosen}
                if _amount(text) is not None:
                    args["amount"] = _amount(text)
                return need("prepare_premium_payment", args)
            p = called["prepare_premium_payment"][2]
            return t("prepared", lang, ccy=p["currency"], amount=p["amount"], desc=p["description"], ref=p["reference"],
                     msisdn=p["msisdn_masked"])

        if intent in ("loan", "pay_loan", "settle"):
            if "get_loan_summary" not in called:
                return need("get_loan_summary") or self._faq(text, ctx, called, available)
            loans = called["get_loan_summary"][2]["loans"]
            if not loans:
                return t("no_loans", lang)
            chosen = _loan_number(text) or loans[0]["loan_number"]
            if intent == "loan":
                out = []
                for loan in loans:
                    line = t("loan", lang, ref=loan["loan_number"], product=loan["product"], ccy=loan["currency"],
                             outstanding=loan["outstanding_principal"], next=loan["next_amount_due"], due=loan["next_due_date"])
                    if float(loan["arrears"]) > 0:
                        line += t("loan_arrears", lang, ccy=loan["currency"], arrears=loan["arrears"], dpd=loan["days_past_due"])
                    out.append(line)
                return "\n".join(out)
            if intent == "settle":
                if "get_settlement_quote" not in called:
                    return need("get_settlement_quote", {"loan_number": chosen})
                q = called["get_settlement_quote"][2]
                return t("settlement", lang, ref=q["loan_number"], ccy=q["currency"], amount=q["settlement_amount"])
            if "prepare_loan_payment" not in called:
                args = {"loan_number": chosen}
                if _amount(text) is not None:
                    args["amount"] = _amount(text)
                return need("prepare_loan_payment", args)
            p = called["prepare_loan_payment"][2]
            return t("prepared", lang, ccy=p["currency"], amount=p["amount"], desc=p["description"], ref=p["reference"],
                     msisdn=p["msisdn_masked"])

        if intent == "claim_status":
            if "get_claim_status" not in called:
                claim = re.search(r"\bCLM-\d{4}-\d{6}\b", text.upper())
                return need("get_claim_status", {"claim_number": claim.group(0)} if claim else {}) or self._faq(text, ctx, called, available)
            claims = called["get_claim_status"][2]["claims"]
            if not claims:
                return t("claims_none", lang)
            out = []
            for c in claims:
                line = t("claim", lang, ref=c["claim_number"], policy=c["policy_number"], status=status_word(c["status"], lang),
                         update=c["last_update"], next=c["next_step"])
                if c["missing_documents"]:
                    line += t("claim_docs", lang, docs=", ".join(c["missing_documents"]))
                out.append(line)
            return "\n".join(out)

        return self._faq(text, ctx, called, available)

    def _faq(self, text: str, ctx: Context, called: dict, available: set[str]):
        if "search_help_articles" not in called:
            if "search_help_articles" not in available:
                return t("menu", ctx.language)
            return ("search_help_articles", {"query": text[:200]})
        hits = called["search_help_articles"][2]["results"]
        if not hits:
            return t("dont_know", ctx.language)
        return t("faq", ctx.language, title=hits[0]["title"], text=hits[0]["text"])


def _only_tool_results(message: dict) -> bool:
    c = message["content"]
    return isinstance(c, list) and bool(c) and all(b.get("type") == "tool_result" for b in c)


def _json(content: Any) -> dict | None:
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None
