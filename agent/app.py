"""InsureAssist agent service: WhatsApp webhook, web-chat simulator, event intake and the staff inbox (HTMX).

    uvicorn agent.app:create_app --factory --port 8100
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from agent import wiring
from agent.orchestrator import Agent
from core.config import Settings, settings
from core.db import Conversation, Handoff, OutboundMessage, ToolAudit, Turn

log = logging.getLogger("insureassist.app")
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))  # autoescape on: model output is never HTML

DEMO_CUSTOMERS = [
    ("263733456789", "Farai — lapsed kombi policy"),
    ("263772123456", "Tendai — Hilux claim under review"),
    ("263779000111", "Mai Chipo — trader loan (Shona)"),
    ("263788112233", "Chipo — funeral plan in ZWG"),
    ("263774556677", "Tatenda — ZWG motor in arrears (Ndebele)"),
    ("263771999888", "Unknown number"),
]


def verify_meta_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def verify_group_signature(secret: str, body: bytes, header: str | None, tolerance: int = 300) -> bool:
    """Same scheme as InsureHub Integrations: X-Signature: t=<unix>,v1=hex(HMAC(secret, t + '.' + body))."""
    if not header:
        return False
    parts = dict(p.strip().split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


def create_app(cfg: Settings | None = None, agent: Agent | None = None) -> FastAPI:
    cfg = cfg or settings()
    agent = agent or wiring.build(cfg)
    app = FastAPI(title="InsureAssist", version="0.1.0",
                  description="Scoped WhatsApp customer-service agent for InsureHub Group (fictional).")
    app.state.agent = agent
    basic = HTTPBasic()
    inbox_password = cfg.inbox_password

    def staff(creds: HTTPBasicCredentials = Depends(basic)) -> str:
        if not secrets.compare_digest(creds.password.encode(), inbox_password.encode()):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})
        return creds.username

    # ------------------------------------------------------------------------------------------- health
    @app.get("/health/live", include_in_schema=False)
    def live() -> dict:
        return {"status": "UP"}

    @app.get("/health/ready", include_in_schema=False)
    def ready() -> dict:
        with agent.sf() as s:
            s.execute(select(1))
        return {"status": "UP", "llm": cfg.llm_mode, "tools": cfg.tool_transport, "backend": cfg.backend}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/chat")

    # ------------------------------------------------------------------------------------------- web-chat simulator
    @app.get("/chat", response_class=HTMLResponse)
    def chat(request: Request, wa_id: str = DEMO_CUSTOMERS[0][0]):
        return TEMPLATES.TemplateResponse(request, "chat.html", {"customers": DEMO_CUSTOMERS, "wa_id": wa_id,
                                                                 "mode": cfg.llm_mode})

    @app.post("/chat/send", response_class=HTMLResponse)
    def chat_send(request: Request, wa_id: str = Form(...), text: str = Form("")):
        if text.strip():
            agent.handle(wa_id, text, channel="web")
        return thread(request, wa_id)

    @app.get("/chat/thread", response_class=HTMLResponse)
    def thread(request: Request, wa_id: str):
        with agent.sf() as s:
            inbound = s.execute(select(Turn.text, Turn.created_at).join(Conversation)
                                .where(Conversation.wa_id == wa_id, Turn.role == "customer")).all()
            outbound = s.execute(select(OutboundMessage.text, OutboundMessage.created_at, OutboundMessage.kind)
                                 .where(OutboundMessage.wa_id == wa_id)).all()
        items = [{"who": "customer", "text": r.text, "at": r.created_at.replace(tzinfo=None)} for r in inbound]
        items += [{"who": "sms" if r.kind == "sms" else ("template" if r.kind == "template" else "bot"),
                   "text": r.text, "at": r.created_at.replace(tzinfo=None)} for r in outbound]
        items.sort(key=lambda i: i["at"])
        show_sms = cfg.demo_show_otp
        return TEMPLATES.TemplateResponse(request, "_thread.html", {"items": [i for i in items if show_sms or i["who"] != "sms"]})

    @app.post("/chat/reset")
    def chat_reset(wa_id: str = Form(...)):
        agent.store.delete(wa_id)
        with agent.sf() as s:
            for c in s.scalars(select(Conversation).where(Conversation.wa_id == wa_id, Conversation.status != "CLOSED")):
                c.status = "CLOSED"
            s.commit()
        return RedirectResponse(f"/chat?wa_id={wa_id}", status_code=303)

    # ------------------------------------------------------------------------------------------- WhatsApp Cloud API
    @app.get("/webhooks/whatsapp", response_class=PlainTextResponse)
    def wa_verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and secrets.compare_digest(q.get("hub.verify_token", ""), cfg.whatsapp_verify_token):
            return q.get("hub.challenge", "")
        raise HTTPException(403, "verification failed")

    @app.post("/webhooks/whatsapp")
    async def wa_webhook(request: Request, tasks: BackgroundTasks):
        body = await request.body()
        if not verify_meta_signature(cfg.whatsapp_app_secret, body, request.headers.get("x-hub-signature-256")):
            raise HTTPException(401, "bad signature")
        payload = json.loads(body)
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for m in change.get("value", {}).get("messages", []):
                    text = m.get("text", {}).get("body", "") if m.get("type") == "text" else m.get("image", {}).get("caption", "")
                    media = [m["image"]["id"]] if m.get("type") == "image" else []
                    # reply asynchronously so Meta gets its 200 quickly; dedupe on the WhatsApp message id
                    tasks.add_task(agent.handle, m["from"], text, "whatsapp", m.get("id"), media)
        return {"status": "accepted"}

    # ------------------------------------------------------------------------------------------- group events
    @app.post("/events")
    async def events(request: Request):
        body = await request.body()
        if not verify_group_signature(cfg.events_shared_secret, body, request.headers.get("x-signature")):
            raise HTTPException(401, "bad signature")
        return handle_event(agent, json.loads(body))

    # ------------------------------------------------------------------------------------------- staff inbox (IA-09)
    @app.get("/inbox", response_class=HTMLResponse)
    def inbox(request: Request, user: str = Depends(staff)):
        with agent.sf() as s:
            queue = s.execute(select(Handoff, Conversation).join(Conversation, Handoff.conversation_id == Conversation.id)
                              .where(Handoff.status.in_(["QUEUED", "ASSIGNED"])).order_by(Handoff.created_at)).all()
            stats = {
                "conversations": s.scalar(select(func.count()).select_from(Conversation)),
                "handoffs": s.scalar(select(func.count()).select_from(Handoff)),
                "tool_calls": s.scalar(select(func.count()).select_from(ToolAudit)),
            }
        return TEMPLATES.TemplateResponse(request, "inbox.html", {"queue": queue, "user": user, "stats": stats})

    @app.get("/inbox/{conversation_id}", response_class=HTMLResponse)
    def conversation(request: Request, conversation_id: str, user: str = Depends(staff)):
        with agent.sf() as s:
            conv = s.get(Conversation, conversation_id)
            if not conv:
                raise HTTPException(404)
            turns = s.scalars(select(Turn).where(Turn.conversation_id == conversation_id).order_by(Turn.id)).all()
            handoffs = s.scalars(select(Handoff).where(Handoff.conversation_id == conversation_id)
                                 .order_by(Handoff.created_at)).all()
        return TEMPLATES.TemplateResponse(request, "conversation.html", {"conv": conv, "turns": turns,
                                                                         "handoffs": handoffs, "user": user})

    @app.post("/inbox/{conversation_id}/reply")
    def reply(conversation_id: str, text: str = Form(...), user: str = Depends(staff)):
        agent.staff_reply(conversation_id, user, text.strip()[:2000])
        return RedirectResponse(f"/inbox/{conversation_id}", status_code=303)

    @app.post("/inbox/{conversation_id}/handback")
    def handback(conversation_id: str, user: str = Depends(staff)):
        agent.hand_back(conversation_id, user)
        return RedirectResponse("/inbox", status_code=303)

    return app


def handle_event(agent: Agent, envelope: dict[str, Any]) -> dict:
    """Envelope as published on insurehub.events: {messageId, type, payload (JSON string or object), occurredAt}."""
    message_id = envelope.get("messageId")
    if message_id and not agent._first_time(f"event:{message_id}"):
        return {"status": "duplicate"}
    kind = envelope.get("type", "")
    payload = envelope.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if kind in ("PaymentSucceeded", "payment.succeeded"):
        sent = agent.payment_succeeded(payload.get("paymentId"), payload.get("policyNumber", ""), str(payload.get("amount")),
                                       payload.get("currency", ""), payload.get("providerReference", ""))
        return {"status": "receipt_sent" if sent else "ignored"}
    if kind in ("PolicyLapsed", "policy.lapsed"):
        sent = agent.nudge(payload["msisdn"], "policy_lapsed_reminder",
                           f"InsureHub: your policy {payload['policyNumber']} has lapsed, so you are not covered. Paying "
                           f"{payload.get('currency', 'USD')} {payload.get('arrears')} reinstates it. Reply *pay* to pay by EcoCash.")
        return {"status": "nudged" if sent else "opted_out"}
    if kind in ("LoanArrearsChanged", "loan.arrears-changed"):
        if payload.get("toBucket") == "CURRENT":
            return {"status": "ignored"}
        sent = agent.nudge(payload["msisdn"], "loan_arrears_reminder",
                           f"InsureHub Microfinance: loan {payload['loanNumber']} is {payload.get('dpd')} days overdue "
                           f"({payload.get('currency', 'USD')} {payload.get('arrearsAmount')}). Reply *pay loan* to pay by "
                           f"EcoCash, or *agent* if you need help.")
        return {"status": "nudged" if sent else "opted_out"}
    return {"status": "ignored"}

