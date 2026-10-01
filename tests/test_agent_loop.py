"""The orchestrator against a scripted fake model: tool dispatch, limits, fallbacks, guardrails, confirmation by code."""
import json
import re
import time

from agent.llm import LlmResponse, LlmUnavailable, ScriptedLlm
from core.db import Handoff, PaymentIntent, Turn
from tests.conftest import FARAI, TENDAI, UNKNOWN, verify


def tool(name, args=None, i=1):
    return LlmResponse([{"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": args or {}}], "tool_use", "scripted")


def text(t):
    return LlmResponse([{"type": "text", "text": t}], "end_turn", "scripted")


def test_tool_results_go_back_as_tool_result_blocks_and_answer_is_grounded(make_agent):
    llm = ScriptedLlm([tool("get_premium_balance", {"policy_number": "MOT-2026-000301"}),
                       text("Farai, you owe USD 187.20 on MOT-2026-000301.")])
    agent = make_agent(llm)
    r = verify(agent, FARAI, "how much do I owe on MOT-2026-000301")
    assert "USD 187.20" in r.replies[-1]
    second_call = llm.calls[1]["messages"]
    result_block = second_call[-1]["content"][0]
    assert result_block["type"] == "tool_result" and json.loads(result_block["content"])["arrears"] == "187.20"
    # system prompt and tool list are identical on every call (cache-friendly prefix)
    assert llm.calls[0]["system"] == llm.calls[1]["system"] and llm.calls[0]["tools"] == llm.calls[1]["tools"]


def test_hallucinated_amount_is_replaced_by_the_deterministic_answer(make_agent):
    llm = ScriptedLlm([tool("get_premium_balance", {"policy_number": "MOT-2026-000301"}),
                       text("You owe USD 99.00, pay by Friday.")])
    agent = make_agent(llm)
    r = verify(agent, FARAI, "how much do I owe on MOT-2026-000301")
    assert "99.00" not in r.replies[-1] and "187.20" in r.replies[-1]
    assert any(i.startswith("ungrounded_amount") for i in r.verdicts["output_blocked"])


def test_llm_outage_falls_back_to_the_planner(make_agent):
    agent = make_agent(ScriptedLlm([LlmUnavailable("timeout")] * 5))
    r = verify(agent, FARAI, "what policies do I have")
    assert "MOT-2026-000301" in r.replies[-1]
    assert r.verdicts["llm"].startswith("fallback")


def test_tool_call_limit_stops_a_looping_model(make_agent):
    agent = make_agent(ScriptedLlm([tool("list_my_policies", i=i) for i in range(10)]))
    r = verify(agent, FARAI, "what policies do I have")
    assert r.verdicts.get("max_tool_calls") is True
    assert "MOT-2026-000301" in r.replies[-1]  # deterministic fallback answered


def test_model_requested_handoff_creates_a_queue_item_and_silences_the_bot(make_agent):
    agent = make_agent(ScriptedLlm([tool("request_human", {"reason": "customer wants to change beneficiary"})]))
    r = agent.handle(UNKNOWN, "Can I talk about my beneficiaries")
    assert r.handoff == "customer wants to change beneficiary"
    with agent.sf() as s:
        assert s.query(Handoff).count() == 1
    assert agent.handle(UNKNOWN, "hello?").replies == []


def test_confirmation_code_is_generated_by_code_and_never_shown_to_the_model(make_agent):
    llm = ScriptedLlm([tool("prepare_premium_payment", {"policy_number": "MOT-2026-000301"}),
                       text("Ready to pay USD 187.20 for MOT-2026-000301.")])
    agent = make_agent(llm)
    r = verify(agent, FARAI, "pay MOT-2026-000301")
    code = re.search(r"\*(\d{4})\*", r.replies[-1]).group(1)
    assert all(code not in json.dumps(c["messages"]) for c in llm.calls)
    assert agent.backend.payments == []
    r = agent.handle(FARAI, code)
    assert r.intent == "payment_confirmed" and len(agent.backend.payments) == 1
    assert agent.backend.payments[0]["idempotency_key"] == agent.backend.payments[0]["idempotency_key"]
    with agent.sf() as s:
        assert s.query(PaymentIntent).one().status == "CONFIRMED"


def test_expired_confirmation_charges_nothing(make_agent):
    agent = make_agent()
    verify(agent, FARAI, "pay my premium")
    session = agent.store.get(FARAI)
    session.pending.expires_at = time.time() - 1
    agent.store.save(session, 600)
    r = agent.handle(FARAI, session.pending.code)
    assert r.intent == "payment_expired" and agent.backend.payments == []


def test_receipt_is_sent_once_when_payment_succeeds(make_agent):
    agent = make_agent()
    r = verify(agent, FARAI, "pay my premium")
    code = re.search(r"\*(\d{4})\*", r.replies[-1]).group(1)
    agent.handle(FARAI, code)
    payment_id = agent.backend.payments[0]["payment_id"]
    assert agent.payment_succeeded(payment_id, "MOT-2026-000301", "187.20", "USD", "MP123")
    assert not agent.payment_succeeded(payment_id, "MOT-2026-000301", "187.20", "USD", "MP123")


def test_otp_lockout_after_three_wrong_codes(make_agent):
    agent = make_agent()
    agent.handle(TENDAI, "list my policies")
    for wrong in ("111111", "222222", "333333"):
        r = agent.handle(TENDAI, wrong)
    assert r.verdicts == {"otp": "locked"}
    assert "Too many" in agent.handle(TENDAI, "list my policies").replies[0]


def test_duplicate_webhook_message_is_processed_once(make_agent):
    agent = make_agent()
    first = agent.handle(TENDAI, "hello", message_id="wamid.1")
    second = agent.handle(TENDAI, "hello", message_id="wamid.1")
    assert first.replies and second.replies == []


def test_transcripts_never_store_codes_or_national_ids(make_agent):
    agent = make_agent()
    verify(agent, FARAI, "list my policies")
    agent.handle(FARAI, "for your records my id is 63-123456A78")
    with agent.sf() as s:
        stored = " ".join(t.text for t in s.query(Turn).all())
    assert "63-123456A78" not in stored
    assert not re.search(r"(?<![\d-])\d{6}(?![\d-])", stored)  # no OTPs (policy numbers contain 6 digits)


def test_unverified_sessions_only_get_public_tools(make_agent):
    llm = ScriptedLlm([text("Here is how to pay a premium.")])
    agent = make_agent(llm)
    agent.handle(UNKNOWN, "how do I pay a premium?")
    assert {t["name"] for t in llm.calls[0]["tools"]} == {"search_help_articles", "request_human"}
