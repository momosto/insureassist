"""HTTP surface: WhatsApp webhook signatures, event intake, staff inbox, and a real MCP-over-HTTP round trip."""
import hashlib
import hmac
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from agent.app import create_app
from agent.tool_client import McpToolClient
from core import tokens
from core.backends import FixtureBackend
from core.db import Conversation, OutboundMessage
from mcp_server.server import create_server
from mcp_server.tools import ToolService
from tests.conftest import FARAI, TENDAI


@pytest.fixture
def client(cfg, make_agent):
    agent = make_agent()
    return TestClient(create_app(cfg, agent)), agent


def meta_sig(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def group_sig(secret, body):
    t = int(time.time())
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


def wa_payload(frm, text, mid):
    return json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": frm, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}).encode()


def test_whatsapp_webhook_requires_a_valid_signature_and_dedupes(client, cfg):
    c, agent = client
    body = wa_payload(TENDAI, "hello", "wamid.A")
    assert c.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": "sha256=00"}).status_code == 401
    for _ in range(2):
        assert c.post("/webhooks/whatsapp", content=body,
                      headers={"x-hub-signature-256": meta_sig(cfg.whatsapp_app_secret, body)}).status_code == 200
    with agent.sf() as s:
        assert s.query(OutboundMessage).filter_by(wa_id=TENDAI).count() == 1


def test_whatsapp_verification_handshake(client, cfg):
    c, _ = client
    ok = c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": cfg.whatsapp_verify_token,
                                             "hub.challenge": "42"})
    assert ok.text == "42"
    assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 403


def test_events_need_the_group_signature_and_respect_opt_out(client, cfg):
    c, agent = client
    event = json.dumps({"messageId": "e1", "type": "LoanArrearsChanged", "payload": json.dumps(
        {"msisdn": "263779000111", "loanNumber": "LN-2610-000001", "dpd": 8, "toBucket": "DPD_1_30",
         "arrearsAmount": 57.03, "currency": "USD"})}).encode()
    assert c.post("/events", content=event, headers={"x-signature": "t=1,v1=00"}).status_code == 401
    assert c.post("/events", content=event, headers={"x-signature": group_sig(cfg.events_shared_secret, event)}).json() == {"status": "nudged"}
    assert c.post("/events", content=event, headers={"x-signature": group_sig(cfg.events_shared_secret, event)}).json() == {"status": "duplicate"}
    agent.handle("263779000111", "STOP")
    again = event.replace(b'"e1"', b'"e2"')
    assert c.post("/events", content=again, headers={"x-signature": group_sig(cfg.events_shared_secret, again)}).json() == {"status": "opted_out"}


def test_staff_inbox_takeover_and_hand_back(client):
    c, agent = client
    agent.handle(FARAI, "I want to make a complaint")
    assert c.get("/inbox").status_code == 401
    auth = ("rudo", "Demo123!")
    page = c.get("/inbox", auth=auth)
    assert page.status_code == 200 and "complaint" in page.text
    with agent.sf() as s:
        conv_id = s.query(Conversation).filter_by(wa_id=FARAI).one().id
    c.post(f"/inbox/{conv_id}/reply", data={"text": "Hi Farai, Rudo here. How can I help?"}, auth=auth)
    assert agent.handle(FARAI, "thanks Rudo").replies == []  # a human is handling it
    c.post(f"/inbox/{conv_id}/handback", auth=auth)
    assert agent.handle(FARAI, "hello").replies
    # model output is escaped in the inbox (LLM05)
    agent.handle(FARAI, "<script>alert(1)</script> agent")
    assert "<script>alert(1)</script>" not in c.get(f"/inbox/{conv_id}", auth=auth).text


def test_web_chat_simulator_renders_a_thread(client):
    c, _ = client
    assert c.get("/chat").status_code == 200
    r = c.post("/chat/send", data={"wa_id": TENDAI, "text": "hello"})
    assert "InsureAssist" in r.text
    assert c.get("/health/ready").json()["status"] == "UP"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_mcp_round_trip_enforces_the_delegated_token(cfg):
    service = ToolService(FixtureBackend(), audit=lambda r: None)
    app = create_server(service).streamable_http_app()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        client = McpToolClient(f"http://127.0.0.1:{port}/mcp")
        token = tokens.mint("IH-CUS-0003", FARAI, cfg.delegated_token_secret)
        ok = client.call("get_premium_balance", {"policy_number": "MOT-2026-000301"}, token, "conv1")
        assert ok["arrears"] == "187.20"
        assert client.call("list_my_policies", {}, None, "conv1")["error"] == "unauthorized"
        other = client.call("get_policy", {"policy_number": "MOT-2026-000101"}, token, "conv1")
        assert other["error"] == "not_yours"
        assert client.call("search_help_articles", {"query": "waiting period"}, None, "conv1")["results"]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
