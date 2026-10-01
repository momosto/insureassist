import time

import pytest

from core import tokens
from core.backends import FixtureBackend
from mcp_server.server import caller_from_headers
from mcp_server.tools import Caller, ToolError, ToolService, redact

SECRET = "test-secret-that-is-long-enough-for-hs256"
FARAI = Caller("IH-CUS-0003", "263733456789")
TENDAI = Caller("IH-CUS-0001", "263772123456")


@pytest.fixture
def service():
    audits = []
    svc = ToolService(FixtureBackend(), audit=audits.append)
    svc.audits = audits
    return svc


def test_tools_only_return_the_callers_own_data(service):
    policies = service.call("list_my_policies", {}, FARAI)["policies"]
    assert [p["policy_number"] for p in policies] == ["MOT-2026-000301"]
    with pytest.raises(ToolError) as e:
        service.call("get_policy", {"policy_number": "MOT-2026-000301"}, TENDAI)
    assert e.value.code == "not_yours"


def test_personal_tools_need_a_verified_customer_but_help_articles_do_not(service):
    with pytest.raises(ToolError) as e:
        service.call("list_my_policies", {}, Caller(None, None))
    assert e.value.code == "not_verified"
    assert service.call("search_help_articles", {"query": "waiting period funeral"}, Caller(None, None))["results"]


def test_tools_never_accept_a_customer_identifier(service):
    with pytest.raises(ToolError) as e:
        service.call("list_my_policies", {"customer_ref": "IH-CUS-0001"}, FARAI)
    assert e.value.code == "bad_arguments"


def test_prepare_payment_does_not_charge_and_respects_limits(service):
    backend = service.backend
    prepared = service.call("prepare_premium_payment", {"policy_number": "mot-2026-000301"}, FARAI)
    assert prepared["amount"] == "187.20" and prepared["prepared"] is True
    assert backend.payments == []
    with pytest.raises(ToolError) as e:
        service.call("prepare_premium_payment", {"policy_number": "MOT-2026-000301", "amount": 9000}, FARAI)
    assert e.value.code == "over_limit"
    with pytest.raises(ToolError):
        service.call("prepare_premium_payment", {"policy_number": "MOT-2026-000301", "amount": -5}, FARAI)


def test_motor_claim_only_on_a_motor_policy_and_creates_a_draft(service):
    with pytest.raises(ToolError) as e:
        service.call("start_motor_claim", {"policy_number": "FUN-2026-000102", "incident_date": "2026-09-30",
                                           "location": "Harare", "description": "x"}, TENDAI)
    assert e.value.code == "wrong_line"
    r = service.call("start_motor_claim", {"policy_number": "MOT-2026-000101", "incident_date": "2026-09-30",
                                           "location": "Samora Machel Ave", "description": "rear-ended"}, TENDAI)
    assert r["draft_claim_number"].startswith("DRF-")


def test_every_call_is_audited_with_redacted_arguments(service):
    service.call("search_help_articles", {"query": "my id is 63-123456A78 card 4111 1111 1111 1111"}, FARAI)
    record = service.audits[-1]
    assert record["tool"] == "search_help_articles" and record["outcome"] == "ok"
    assert "63-123456A78" not in str(record["args"]) and "4111" not in str(record["args"])
    with pytest.raises(ToolError):
        service.call("get_policy", {"policy_number": "X"}, FARAI)
    assert service.audits[-1]["outcome"] == "not_yours"


def test_backend_outage_becomes_a_tool_error():
    svc = ToolService(FixtureBackend(fail_tools={"policies"}), audit=lambda r: None)
    with pytest.raises(ToolError) as e:
        svc.call("list_my_policies", {}, FARAI)
    assert e.value.code == "backend_unavailable"


def test_delegated_token_rules():
    token = tokens.mint("IH-CUS-0003", "263733456789", SECRET, minutes=15)
    caller = caller_from_headers({"authorization": f"Bearer {token}"}, SECRET, "list_my_policies")
    assert caller.customer_ref == "IH-CUS-0003"
    expired = tokens.mint("IH-CUS-0003", "263733456789", SECRET, minutes=1, now=time.time() - 3600)
    for bad in (None, "garbage", expired, tokens.mint("IH-CUS-0003", "x", "another-secret-long-enough-for-hs256")):
        with pytest.raises(ToolError) as e:
            caller_from_headers({"authorization": f"Bearer {bad}"} if bad else {}, SECRET, "list_my_policies")
        assert e.value.code == "unauthorized"
    # public tools work without a token
    assert caller_from_headers({}, SECRET, "search_help_articles").customer_ref is None


def test_redaction_helper():
    assert redact({"q": "id 63-123456A78"}) == {"q": "id [NATIONAL-ID]"}
