"""One customer turn, end to end (docs/03-architecture.md §2).

dedupe → session → (handoff mode? stop) → pending payment confirmation (code, not model) → identity/OTP →
mandatory handoff → input guardrails → budget → agent loop (model ⇄ tools, max N calls) → output guardrails →
confirmation code appended by code → audit → send.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select

from agent import guardrails
from agent.llm import Llm, LlmUnavailable
from agent.planner import OfflinePlanner, classify, t
from agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from agent.sessions import PendingPayment, Session, SessionStore
from agent.tool_client import ToolClient
from core import tokens
from core.backends import Backend, BackendError
from core.config import Settings
from core.db import Conversation, Handoff, OutboundMessage, OptOut, PaymentIntent, ProcessedMessage, Turn, utcnow
from mcp_server.tools import PREPARE_TOOLS, PUBLIC_TOOLS, TOOL_DEFINITIONS, redact

log = logging.getLogger("insureassist.agent")

PERSONAL_INTENTS = {"policies", "balance", "pay_premium", "new_claim", "claim_status", "loan", "pay_loan", "settle"}

MSG = {
    "otp_sent": {"en": "To protect your account I've sent a 6-digit code by SMS to the number ending {last4}. Please reply with it.",
                 "sn": "Kuchengetedza account yako, ndatumira kodhi ine manhamba 6 neSMS kunhamba inopera ne{last4}. Ndapota ndipindure nayo.",
                 "nd": "Ukuvikela i-account yakho, ngithumele ikhodi yezinombolo ezi-6 nge-SMS enombolweni ephela ngo-{last4}. Ngicela uphendule ngayo."},
    "otp_sms": {"en": "InsureHub: your InsureAssist code is {code}. It expires in 5 minutes. Never share it with anyone.",
                "sn": "InsureHub: kodhi yako yeInsureAssist ndeiyi: {code}. Inopera mumaminitsi 5. Usaipa munhu.",
                "nd": "InsureHub: ikhodi yakho ye-InsureAssist ngu-{code}. Iphela emizuzwini emi-5. Ungayiniki muntu."},
    "otp_ok": {"en": "Thanks {name}, you're verified.", "sn": "Ndatenda {name}, wasimbiswa.", "nd": "Siyabonga {name}, usuqinisekisiwe."},
    "otp_wrong": {"en": "That code is not right. {left} attempt(s) left.", "sn": "Kodhi iyoyo haina kunaka. Wasara nemikana {left}.",
                  "nd": "Leyo khodi ayiqondanga. Usele lamathuba angu-{left}."},
    "otp_locked": {"en": "Too many wrong codes. For your safety, please try again in {minutes} minutes or type *agent*.",
                   "sn": "Makanganisa kakawanda. Edzazve mushure memaminitsi {minutes}, kana nyora *munhu*.",
                   "nd": "Amaphutha manengi. Zama futhi ngemva kwemizuzu engu-{minutes}, kumbe ubhale *umuntu*."},
    "unknown_number": {"en": "I can't find an InsureHub profile for this WhatsApp number, so I can't share account details. "
                             "General questions are welcome, or type *agent*.",
                       "sn": "Handisi kuwana profile yeInsureHub yenhamba iyi, saka handigone kupa ruzivo rwe account. "
                             "Mibvunzo yakajairika inogamuchirwa, kana nyora *munhu*.",
                       "nd": "Angitholi i-profile ye-InsureHub yale nombolo, ngakho angeke ngabelana ngolwazi lwe-account. "
                             "Imibuzo ejwayelekileyo yamukelekile, kumbe ubhale *umuntu*."},
    "confirm": {"en": "To confirm, reply with code *{code}* within {minutes} minutes. Reply *cancel* to stop. "
                      "We will never ask for your EcoCash PIN in this chat.",
                "sn": "Kuti usimbise, pindura nekodhi *{code}* mukati memaminitsi {minutes}. Nyora *cancel* kumisa. "
                      "Hatimbokumbiri PIN yako yeEcoCash muchat muno.",
                "nd": "Ukuqinisekisa, phendula ngekhodi *{code}* phakathi kwemizuzu engu-{minutes}. Bhala *cancel* ukumisa. "
                      "Kasisoze sakucela i-PIN ye-EcoCash kule ngxoxo."},
    "pay_started": {"en": "Done — check your phone and approve the EcoCash prompt for {ccy} {amount} with your PIN. "
                          "I'll send a receipt when it goes through.",
                    "sn": "Zvaitwa — tarisa foni yako ugamuchire EcoCash ye{ccy} {amount} nePIN yako. Ndichatumira risiti kana zvapera.",
                    "nd": "Kwenziwe — khangela ifoni yakho uvume i-EcoCash ye-{ccy} {amount} nge-PIN yakho. Ngizathumela irisidi."},
    "code_wrong": {"en": "That code doesn't match. Please reply with the code shown above, or *cancel*.",
                   "sn": "Kodhi iyoyo hainyatsoenderana. Pindura nekodhi iri pamusoro, kana *cancel*.",
                   "nd": "Leyo khodi kayifanani. Phendula ngekhodi engenhla, kumbe *cancel*."},
    "code_expired": {"en": "That payment request expired, so nothing was charged. Ask again whenever you're ready.",
                     "sn": "Chikumbiro chekubhadhara chapera nguva, hapana chabhadharwa. Bvunzazve kana wagadzirira.",
                     "nd": "Isicelo sokubhadala siphelelwe yisikhathi, akukho okubhadaliweyo. Buza futhi nxa usulungile."},
    "pay_cancelled": {"en": "Cancelled — nothing was charged.", "sn": "Zvamiswa — hapana chabhadharwa.",
                      "nd": "Kumisiwe — akukho okubhadaliweyo."},
    "pay_failed": {"en": "Sorry, I couldn't start the EcoCash payment. Nothing was charged. Please try later or type *agent*.",
                   "sn": "Ndine urombo, handina kukwanisa kutanga EcoCash. Hapana chabhadharwa. Edza gare gare kana nyora *munhu*.",
                   "nd": "Uxolo, angikwazanga ukuqala i-EcoCash. Akukho okubhadaliweyo. Zama ngemva kumbe ubhale *umuntu*."},
    "receipt": {"en": "Receipt: {ccy} {amount} received for {ref} (EcoCash ref {provider}). Thank you!",
                "sn": "Risiti: {ccy} {amount} yagamuchirwa ye{ref} (EcoCash ref {provider}). Tatenda!",
                "nd": "Irisidi: {ccy} {amount} yamukelwe ye-{ref} (EcoCash ref {provider}). Siyabonga!"},
    "scope": {"en": "I can only help with InsureHub policies, claims, payments and loans.",
              "sn": "Ndinogona kubatsira nezveInsureHub chete: mapolicy, ma-claim, kubhadhara nezvikwereti.",
              "nd": "Ngingasiza ngezinto ze-InsureHub kuphela: ama-policy, ama-claim, ukubhadala lezikweleti."},
    "safety": {"en": "I can only discuss the account of the verified customer I'm chatting with, and I can't change how I work. "
                     "If someone else needs help, they can message us from their own number.",
               "sn": "Ndinogona kutaura nezve account yemunhu wandiri kutaura naye chete. Kana mumwe munhu achida rubatsiro, "
                     "ngaatitumire meseji kubva panhamba yake.",
               "nd": "Ngingakhuluma nge-account yomuntu engikhuluma laye kuphela. Nxa omunye umuntu efuna uncedo, "
                     "kasithumelele umlayezo enombolweni yakhe."},
    "budget": {"en": "I'm getting a lot of messages right now, so here's the quick menu.",
               "sn": "Ndiri kugamuchira mameseji mazhinji izvozvi, saka heino menu.",
               "nd": "Ngamukela imilayezo eminengi khathesi, ngakho nansi i-menu."},
    "opted_out": {"en": "You won't receive reminders from us any more. You can still chat with us here.",
                  "sn": "Hauchazogamuchira zviyeuchidzo. Unogona kuramba uchitaura nesu pano.",
                  "nd": "Kawusayikuthola izikhumbuzo. Usengakhuluma lathi lapha."},
}


def msg(key: str, lang: str, **kw: Any) -> str:
    return MSG[key].get(lang, MSG[key]["en"]).format(**kw)


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _mask_codes(text: str) -> str:
    """OTPs and confirmation codes never reach stored transcripts."""
    return re.sub(r"\b\d{4,6}\b", "[code]", text) if re.fullmatch(r"\s*\d{4,6}\s*", text) else text


@dataclass
class TurnResult:
    replies: list[str]
    intent: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    verdicts: dict[str, Any] = field(default_factory=dict)
    handoff: str | None = None
    model: str | None = None


class Messenger:
    """Sends messages: records every outbound message and, when a WhatsApp token is configured, calls the Cloud API."""

    def __init__(self, session_factory, settings: Settings, http_post: Callable[..., Any] | None = None):
        self.sf = session_factory
        self.cfg = settings
        self.http_post = http_post

    def send(self, wa_id: str, text: str, kind: str = "text", template: str | None = None) -> None:
        with self.sf() as s:
            s.add(OutboundMessage(wa_id=wa_id, kind=kind, template=template, text=text))
            s.commit()
        if kind != "sms" and self.cfg.whatsapp_access_token and self.cfg.whatsapp_phone_number_id and self.http_post:
            url = f"https://graph.facebook.com/v21.0/{self.cfg.whatsapp_phone_number_id}/messages"
            body = {"messaging_product": "whatsapp", "to": wa_id, "type": "text", "text": {"body": text}}
            if kind == "template":
                body = {"messaging_product": "whatsapp", "to": wa_id, "type": "template",
                        "template": {"name": template, "language": {"code": "en"},
                                     "components": [{"type": "body", "parameters": [{"type": "text", "text": text}]}]}}
            try:
                self.http_post(url, json=body, headers={"Authorization": f"Bearer {self.cfg.whatsapp_access_token}"})
            except Exception:  # never lose the turn because the channel is down; outbox keeps the record
                log.exception("WhatsApp send failed for %s", wa_id[-4:])


class Agent:
    def __init__(self, cfg: Settings, store: SessionStore, llm: Llm, tools: ToolClient, payments: Backend,
                 directory: Backend, session_factory, messenger: Messenger, simulate_payments: bool = False,
                 summarizer: Callable[[str], str] | None = None):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.fallback = OfflinePlanner()
        self.tools = tools
        self.payments = payments
        self.directory = directory  # phone → customer lookup for verification
        self.sf = session_factory
        self.messenger = messenger
        self.simulate_payments = simulate_payments
        self.summarizer = summarizer
        self.tokens_used_today = 0
        self._token_day = date.today()
        self._lock = threading.Lock()

    # ================================================================================================ entry point
    def handle(self, wa_id: str, text: str, channel: str = "web", message_id: str | None = None,
               media_ids: list[str] | None = None) -> TurnResult:
        started = time.perf_counter()
        text = (text or "").strip()[:2000]
        if message_id and not self._first_time(message_id):
            return TurnResult([])  # duplicate webhook delivery
        session = self._load(wa_id)
        conversation = self._conversation(session, channel)
        if conversation.status == "CLOSED":
            conversation = self._conversation(session, channel, force_new=True)
        self._record(conversation.id, "customer", _mask_codes(text))

        result = self._turn(session, conversation, text, media_ids or [])

        for reply in result.replies:
            self.messenger.send(wa_id, reply)
        self._audit_turn(conversation.id, result, int((time.perf_counter() - started) * 1000), session.language)
        self.store.save(session, self.cfg.session_idle_minutes * 60)
        return result

    # ================================================================================================ the turn
    def _turn(self, session: Session, conversation: Conversation, text: str, media: list[str]) -> TurnResult:
        lang = session.language = guardrails.detect_language(text, session.language)

        if text.upper() in ("STOP", "UNSUBSCRIBE", "MISA"):
            self._opt_out(session.wa_id)
            return TurnResult([msg("opted_out", lang)], intent="opt_out")

        if conversation.status in ("HANDOFF", "HUMAN"):
            return TurnResult([], intent="with_human")  # the bot stops replying; staff see it in the inbox

        # ---- payment confirmation: deterministic code path, never the model (ADR-0004)
        if session.pending:
            handled = self._confirmation(session, conversation, text)
            if handled:
                return handled

        # ---- identity (IA-01)
        if session.state == "AWAITING_OTP" and re.fullmatch(r"\s*\d{6}\s*", text):
            return self._verify_otp(session, conversation, text)
        if session.state == "VERIFIED" and not session.verified:
            session.state, session.token = "NEW", None  # delegated token expired; re-verify on next personal request

        # ---- mandatory handoff (G-06) before anything else the model could do
        reason = guardrails.handoff_reason(text)
        if guardrails.is_negative(text):
            session.negative_turns += 1
            if session.negative_turns >= 2:
                reason = reason or "negative_sentiment"
        if reason:
            return self._handoff(session, conversation, reason, classify(text))

        # ---- input guardrails (G-01, G-07)
        verdict = guardrails.check_input(text)
        if not verdict.allowed:
            key = "scope" if verdict.category == "off_topic" else "safety"
            reply = msg(key, lang) + ("\n" + t("menu", lang) if key == "scope" else "")
            return TurnResult([reply], intent=verdict.category, verdicts={"input": verdict.category, "signals": verdict.signals})

        intent = classify(text)
        if intent in PERSONAL_INTENTS and not session.verified:
            return self._start_verification(session, text, intent)

        # ---- budget (G-08)
        llm = self.llm
        verdicts: dict[str, Any] = {"input": "ok"}
        if not self._within_budget(session):
            llm = self.fallback
            verdicts["budget"] = "exhausted"
        return self._agent_loop(session, conversation, text, intent, llm, verdicts, media)

    # ================================================================================================ agent loop
    def _agent_loop(self, session: Session, conversation: Conversation, text: str, intent: str, llm: Llm,
                    verdicts: dict[str, Any], media: list[str]) -> TurnResult:
        lang = session.language
        context = (f"<context>language={lang}; name={session.first_name or ''}; "
                   f"verified={'yes' if session.verified else 'no'}</context>")
        user_text = text + (f"\n[customer attached {len(media)} photo(s): {', '.join(media)}]" if media else "")
        messages = list(session.history) + [{"role": "user", "content": [{"type": "text", "text": context},
                                                                         {"type": "text", "text": user_text}]}]
        tools = [d for d in TOOL_DEFINITIONS if session.verified or d["name"] in PUBLIC_TOOLS]
        calls: list[dict] = []
        sources: list[str] = [text]
        prepared: dict | None = None
        handoff_reason: str | None = None
        model_used = getattr(llm, "model", "?")
        reply = ""
        try:
            for _ in range(self.cfg.max_tool_calls_per_turn + 1):
                response = llm.create(SYSTEM_PROMPT, tools, messages)
                model_used = response.model
                self._count_tokens(response.usage)
                uses = [b for b in response.content if b.get("type") == "tool_use"]
                if response.stop_reason != "tool_use" or not uses:
                    reply = "\n".join(b["text"] for b in response.content if b.get("type") == "text").strip()
                    break
                if len(calls) + len(uses) > self.cfg.max_tool_calls_per_turn:
                    verdicts["max_tool_calls"] = True
                    raise LlmUnavailable("tool call limit reached")
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for use in uses:
                    output = self.tools.call(use["name"], use.get("input") or {}, session.token, conversation.id)
                    is_error = "error" in output
                    calls.append({"tool": use["name"], "args": redact(use.get("input") or {}),
                                  "outcome": output.get("error", "ok")})
                    if is_error and output.get("error") not in ("not_yours", "bad_amount", "over_limit", "wrong_line"):
                        session.tool_failures += 1
                    if use["name"] == "request_human" and not is_error:
                        handoff_reason = output.get("reason", "model_request")
                    if use["name"] in PREPARE_TOOLS and not is_error:
                        prepared = output
                    sources.append(json.dumps(output))
                    # tool output is data: it goes back as a tool_result block, never as instructions
                    results.append({"type": "tool_result", "tool_use_id": use["id"], "content": json.dumps(output),
                                    **({"is_error": True} if is_error else {})})
                messages.append({"role": "user", "content": results})
                if handoff_reason:
                    break
            else:
                raise LlmUnavailable("no final answer")
        except LlmUnavailable as e:
            log.warning("LLM unavailable (%s); using the deterministic fallback", e)
            verdicts["llm"] = f"fallback:{e}"
            if llm is not self.fallback:
                return self._agent_loop(session, conversation, text, intent, self.fallback, verdicts, media)
            reply = t("menu", lang)

        if handoff_reason:
            return self._handoff(session, conversation, handoff_reason, intent, calls)
        if session.tool_failures >= 2:
            session.tool_failures = 0
            return self._handoff(session, conversation, "repeated_tool_failures", intent, calls)

        out = guardrails.check_output(reply, sources)
        verdicts["output"] = "ok" if out.ok else out.issues
        if not out.ok:
            # the model said something the tools don't support: answer deterministically from fresh tool calls instead
            log.warning("Output guardrail blocked a reply: %s", out.issues)
            verdicts["output_blocked"] = out.issues
            if llm is not self.fallback:
                return self._agent_loop(session, conversation, text, intent, self.fallback, verdicts, media)
            reply = t("dont_know", lang)
        else:
            reply = out.text

        replies = [reply] if reply else []
        if prepared:
            replies.append(self._create_intent(session, conversation, prepared))
        session.history = (session.history + [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply or "(no reply)"},
        ])[-20:]
        return TurnResult(replies, intent=intent, tool_calls=calls, verdicts=verdicts, model=model_used)

    # ================================================================================================ identity
    def _start_verification(self, session: Session, text: str, intent: str) -> TurnResult:
        lang = session.language
        if session.locked_until > time.time():
            minutes = int((session.locked_until - time.time()) / 60) + 1
            return TurnResult([msg("otp_locked", lang, minutes=minutes)], intent="locked")
        customer = self.directory.find_customer(session.wa_id)
        if customer is None:
            return TurnResult([msg("unknown_number", lang)], intent="unknown_customer")
        code = f"{secrets.randbelow(1_000_000):06d}"
        session.state = "AWAITING_OTP"
        session.otp_hash = _hash(code)
        session.otp_expires_at = time.time() + 300
        session.otp_attempts = 0
        session.customer_ref = customer.customer_ref
        session.first_name = customer.first_name
        session.parked_text = text
        self.messenger.send(session.wa_id, msg("otp_sms", lang, code=code), kind="sms")
        return TurnResult([msg("otp_sent", lang, last4=session.wa_id[-4:])], intent="verify")

    def _verify_otp(self, session: Session, conversation: Conversation, text: str) -> TurnResult:
        lang = session.language
        if session.locked_until > time.time():
            return TurnResult([msg("otp_locked", lang, minutes=self.cfg.otp_lock_minutes)], intent="locked")
        if time.time() > session.otp_expires_at or not secrets.compare_digest(_hash(text.strip()), session.otp_hash or ""):
            session.otp_attempts += 1
            left = self.cfg.otp_max_attempts - session.otp_attempts
            if left <= 0:
                session.locked_until = time.time() + self.cfg.otp_lock_minutes * 60
                session.state, session.otp_hash = "NEW", None
                return TurnResult([msg("otp_locked", lang, minutes=self.cfg.otp_lock_minutes)], intent="locked",
                                  verdicts={"otp": "locked"})
            return TurnResult([msg("otp_wrong", lang, left=left)], intent="verify", verdicts={"otp": "wrong"})
        session.state = "VERIFIED"
        session.otp_hash = None
        session.token = tokens.mint(session.customer_ref, session.wa_id, self.cfg.delegated_token_secret,
                                    self.cfg.delegated_token_minutes)
        session.token_expires_at = time.time() + self.cfg.delegated_token_minutes * 60
        with self.sf() as s:
            conv = s.get(Conversation, conversation.id)
            conv.customer_ref = session.customer_ref
            s.commit()
        greeting = msg("otp_ok", lang, name=session.first_name)
        parked, session.parked_text = session.parked_text, None
        if parked:
            follow = self._turn(session, conversation, parked, [])
            return TurnResult([greeting] + follow.replies, follow.intent, follow.tool_calls,
                              {"otp": "ok", **follow.verdicts}, follow.handoff, follow.model)
        return TurnResult([greeting + " " + t("menu", lang)], intent="verified", verdicts={"otp": "ok"})

    # ================================================================================================ payments
    def _create_intent(self, session: Session, conversation: Conversation, prepared: dict) -> str:
        code = f"{secrets.randbelow(10_000):04d}"
        intent_id = secrets.token_hex(8)
        session.pending = PendingPayment(intent_id, prepared["kind"], prepared["reference"], prepared["currency"],
                                         prepared["amount"], prepared["description"], code,
                                         time.time() + self.cfg.confirmation_minutes * 60)
        with self.sf() as s:
            s.add(PaymentIntent(id=intent_id, conversation_id=conversation.id, customer_ref=session.customer_ref,
                                wa_id=session.wa_id, kind=prepared["kind"], reference=prepared["reference"],
                                currency=prepared["currency"], amount=prepared["amount"], status="PENDING"))
            s.commit()
        return msg("confirm", session.language, code=code, minutes=self.cfg.confirmation_minutes)

    def _confirmation(self, session: Session, conversation: Conversation, text: str) -> TurnResult | None:
        p = session.pending
        lang = session.language
        lowered = text.strip().lower()
        if lowered in ("cancel", "no", "stop", "aiwa", "kwete", "cha", "hatshi"):
            self._intent_status(p.intent_id, "CANCELLED")
            session.pending = None
            return TurnResult([msg("pay_cancelled", lang)], intent="payment_cancelled")
        code = re.fullmatch(r"\s*\*?(\d{4})\*?\s*", text)
        if not code:
            return None  # not about the payment; the pending intent stays until it expires
        if time.time() > p.expires_at:
            self._intent_status(p.intent_id, "EXPIRED")
            session.pending = None
            return TurnResult([msg("code_expired", lang)], intent="payment_expired", verdicts={"confirmation": "expired"})
        if not secrets.compare_digest(code.group(1), p.code):
            p.attempts += 1
            if p.attempts >= 3:
                self._intent_status(p.intent_id, "CANCELLED")
                session.pending = None
                return TurnResult([msg("pay_cancelled", lang)], intent="payment_cancelled", verdicts={"confirmation": "too_many"})
            return TurnResult([msg("code_wrong", lang)], intent="payment_confirm", verdicts={"confirmation": "wrong"})
        session.pending = None
        try:
            started = self.payments.start_payment(session.customer_ref, p.kind, p.reference, Decimal(p.amount),
                                                  p.currency, session.wa_id, idempotency_key=p.intent_id)
        except BackendError:
            self._intent_status(p.intent_id, "FAILED")
            return TurnResult([msg("pay_failed", lang)], intent="payment_failed", verdicts={"confirmation": "ok", "payment": "failed"})
        self._intent_status(p.intent_id, "CONFIRMED", payment_id=started.payment_id)
        if self.simulate_payments:
            threading.Timer(2.0, self.payment_succeeded, kwargs={
                "payment_id": started.payment_id, "reference": p.reference, "amount": p.amount, "currency": p.currency,
                "provider_reference": "MP" + secrets.token_hex(4).upper()}).start()
        return TurnResult([msg("pay_started", lang, ccy=p.currency, amount=p.amount)], intent="payment_confirmed",
                          tool_calls=[{"tool": "payments.start", "args": {"reference": p.reference, "amount": p.amount},
                                       "outcome": started.status}], verdicts={"confirmation": "ok"})

    def _intent_status(self, intent_id: str, status: str, payment_id: str | None = None) -> None:
        with self.sf() as s:
            row = s.get(PaymentIntent, intent_id)
            if row:
                row.status = status
                if payment_id:
                    row.payment_id = payment_id
                    row.confirmed_at = utcnow()
                s.commit()

    def payment_succeeded(self, payment_id: str | None, reference: str, amount: str, currency: str,
                          provider_reference: str) -> bool:
        """`payment.succeeded` from the bus or the simulator → receipt, even after the session has ended (§8)."""
        with self.sf() as s:
            q = select(PaymentIntent).where(PaymentIntent.status == "CONFIRMED")
            q = q.where(PaymentIntent.payment_id == payment_id) if payment_id else q.where(PaymentIntent.reference == reference)
            row = s.scalars(q.order_by(PaymentIntent.created_at.desc())).first()
            if not row or row.receipt_sent:
                return False
            row.status, row.receipt_sent = "SUCCEEDED", True
            s.commit()
            wa_id, lang = row.wa_id, "en"
            conv = s.get(Conversation, row.conversation_id)
            if conv:
                lang = conv.language
        self.messenger.send(wa_id, msg("receipt", lang, ccy=currency, amount=amount, ref=reference, provider=provider_reference))
        return True

    # ================================================================================================ handoff
    def _handoff(self, session: Session, conversation: Conversation, reason: str, intent: str | None,
                 calls: list[dict] | None = None) -> TurnResult:
        transcript = self._transcript(conversation.id)
        summary = None
        if self.summarizer:
            try:
                summary = self.summarizer(transcript)
            except Exception:
                log.exception("Summary model failed; using the deterministic summary")
        if not summary:
            last = [line for line in transcript.splitlines() if line.startswith("customer:")][-3:]
            summary = (f"Customer {session.first_name or 'unverified'} ({'verified' if session.verified else 'not verified'}"
                       f", {session.customer_ref or 'no profile'}). Reason: {reason.replace('_', ' ')}. Intent: {intent}. "
                       f"Recent messages: " + " | ".join(line[9:].strip() for line in last))
        with self.sf() as s:
            conv = s.get(Conversation, conversation.id)
            conv.status = "HANDOFF"
            s.add(Handoff(conversation_id=conversation.id, reason=reason, summary=summary, intent=intent,
                          sentiment="negative" if session.negative_turns else "neutral"))
            s.commit()
        conversation.status = "HANDOFF"
        return TurnResult([t("handoff", session.language)], intent=intent, tool_calls=calls or [],
                          verdicts={"handoff": reason}, handoff=reason)

    def staff_reply(self, conversation_id: str, staff: str, text: str) -> None:
        with self.sf() as s:
            conv = s.get(Conversation, conversation_id)
            conv.status = "HUMAN"
            for h in s.scalars(select(Handoff).where(Handoff.conversation_id == conversation_id, Handoff.status == "QUEUED")):
                h.status, h.assignee = "ASSIGNED", staff
            s.add(Turn(conversation_id=conversation_id, role="agent", text=text, author=staff))
            conv.updated_at = utcnow()
            s.commit()
            wa_id = conv.wa_id
        self.messenger.send(wa_id, text)

    def hand_back(self, conversation_id: str, staff: str) -> None:
        with self.sf() as s:
            conv = s.get(Conversation, conversation_id)
            conv.status = "BOT"
            for h in s.scalars(select(Handoff).where(Handoff.conversation_id == conversation_id,
                                                     Handoff.status.in_(["QUEUED", "ASSIGNED"]))):
                h.status, h.closed_at = "RETURNED", utcnow()
            s.add(Turn(conversation_id=conversation_id, role="system", text=f"Handed back to the assistant by {staff}"))
            s.commit()

    def callback_request(self, wa_id: str, reason: str, channel: str, customer_ref: str | None = None) -> str:
        """A call-back asked for on another channel (USSD *263# option 5) becomes a ticket in this inbox."""
        with self.sf() as s:
            conv = Conversation(channel=channel, wa_id=wa_id, customer_ref=customer_ref, status="HANDOFF")
            s.add(conv)
            s.flush()
            s.add(Turn(conversation_id=conv.id, role="system", text=f"Call-back requested on {channel.upper()} about: {reason}"))
            s.add(Handoff(conversation_id=conv.id, reason="callback_request",
                          summary=f"Customer {customer_ref or 'unverified'} asked on {channel.upper()} to be called back "
                                  f"about {reason}. Call …{wa_id[-4:]} within 1 working day.", intent=reason))
            s.commit()
            return conv.id

    # ================================================================================================ proactive (IA-10)
    def nudge(self, wa_id: str, template: str, text: str) -> bool:
        with self.sf() as s:
            if s.get(OptOut, wa_id):
                return False
        self.messenger.send(wa_id, text + "\nReply STOP to stop reminders.", kind="template", template=template)
        return True

    # ================================================================================================ plumbing
    def _load(self, wa_id: str) -> Session:
        session = self.store.get(wa_id) or Session(wa_id=wa_id)
        today = date.today().isoformat()
        if session.day != today:
            session.day, session.turns_today = today, 0
        session.turns_today += 1
        session.last_seen = time.time()
        return session

    def _conversation(self, session: Session, channel: str, force_new: bool = False) -> Conversation:
        with self.sf() as s:
            conv = None if force_new or not session.conversation_id else s.get(Conversation, session.conversation_id)
            if conv is None:
                conv = Conversation(channel=channel, wa_id=session.wa_id, customer_ref=session.customer_ref,
                                    language=session.language)
                s.add(conv)
                s.commit()
                session.conversation_id = conv.id
            return conv

    def _first_time(self, message_id: str) -> bool:
        with self.sf() as s:
            if s.get(ProcessedMessage, message_id):
                return False
            s.add(ProcessedMessage(message_id=message_id))
            s.commit()
            return True

    def _record(self, conversation_id: str, role: str, text: str, **kw: Any) -> None:
        with self.sf() as s:
            s.add(Turn(conversation_id=conversation_id, role=role, text=guardrails.check_output(text, [text]).text, **kw))
            conv = s.get(Conversation, conversation_id)
            conv.updated_at = utcnow()
            s.commit()

    def _audit_turn(self, conversation_id: str, r: TurnResult, latency_ms: int, language: str) -> None:
        with self.sf() as s:
            conv = s.get(Conversation, conversation_id)
            for reply in r.replies:
                s.add(Turn(conversation_id=conversation_id, role="assistant", text=reply, intent=r.intent, model=r.model,
                           prompt_version=PROMPT_VERSION, verdicts=r.verdicts, tool_calls=r.tool_calls,
                           latency_ms=latency_ms))
            if not r.replies and r.intent != "with_human":
                s.add(Turn(conversation_id=conversation_id, role="system", text="(no reply)", intent=r.intent,
                           verdicts=r.verdicts))
            if conv:
                conv.language = language
            s.commit()

    def _transcript(self, conversation_id: str) -> str:
        with self.sf() as s:
            turns = s.scalars(select(Turn).where(Turn.conversation_id == conversation_id).order_by(Turn.id)).all()
            return "\n".join(f"{t_.role}: {t_.text}" for t_ in turns[-20:])

    def _opt_out(self, wa_id: str) -> None:
        with self.sf() as s:
            if not s.get(OptOut, wa_id):
                s.add(OptOut(wa_id=wa_id))
                s.commit()

    def _within_budget(self, session: Session) -> bool:
        with self._lock:
            if self._token_day != date.today():
                self._token_day, self.tokens_used_today = date.today(), 0
            return (self.tokens_used_today < self.cfg.daily_token_budget
                    and session.turns_today <= self.cfg.max_turns_per_customer_per_day)

    def _count_tokens(self, usage: dict[str, int]) -> None:
        with self._lock:
            self.tokens_used_today += usage.get("input", 0) + usage.get("output", 0)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
