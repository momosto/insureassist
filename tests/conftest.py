import re

import pytest
from sqlalchemy import select

from agent import wiring
from core.backends import FixtureBackend
from core.config import Settings
from core.db import OutboundMessage, make_session_factory

FARAI = "263733456789"
TENDAI = "263772123456"
MAI_CHIPO = "263779000111"
UNKNOWN = "263771999888"


@pytest.fixture
def cfg() -> Settings:
    return Settings(llm_mode="offline", database_url="sqlite://", tool_transport="local", backend="fixtures",
                    redis_url=None, whatsapp_access_token=None)


@pytest.fixture
def make_agent(cfg):
    def build(llm=None, backend=None):
        backend = backend or FixtureBackend()
        sf = make_session_factory("sqlite://")
        agent = wiring.build(cfg, session_factory=sf, backend=backend)
        agent.simulate_payments = False
        if llm is not None:
            agent.llm = llm
        agent.backend = backend
        return agent
    return build


def sms_code(agent, wa_id: str) -> str:
    with agent.sf() as s:
        row = s.scalars(select(OutboundMessage).where(OutboundMessage.wa_id == wa_id, OutboundMessage.kind == "sms")
                        .order_by(OutboundMessage.id.desc())).first()
    return re.search(r"(?<!\d)(\d{6})(?!\d)", row.text).group(1)


def verify(agent, wa_id: str, first_message: str = "list my policies"):
    agent.handle(wa_id, first_message)
    return agent.handle(wa_id, sms_code(agent, wa_id))
