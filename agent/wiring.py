"""Builds the Agent from settings (composition root)."""
from __future__ import annotations

import httpx

from agent.llm import AnthropicLlm
from agent.orchestrator import Agent, Messenger
from agent.planner import OfflinePlanner
from agent.sessions import MemoryStore, RedisStore
from agent.tool_client import LocalToolClient, McpToolClient
from core.backends import FixtureBackend, HttpBackend
from core.config import Settings
from core.db import db_audit_sink, make_session_factory
from mcp_server.tools import ToolService


def build(cfg: Settings, session_factory=None, backend=None) -> Agent:
    sf = session_factory or make_session_factory(cfg.database_url)
    if backend is None:
        backend = (HttpBackend(cfg.insurehub_url, cfg.lendhub_url, cfg.payments_url, cfg.lendhub_channel_user,
                               cfg.lendhub_channel_password) if cfg.backend == "http" else FixtureBackend())
    tools = (McpToolClient(cfg.mcp_url) if cfg.tool_transport == "mcp"
             else LocalToolClient(ToolService(backend, audit=db_audit_sink(sf)), cfg.delegated_token_secret))
    summarizer = None
    if cfg.llm_mode == "anthropic":
        llm = AnthropicLlm(cfg.main_model, cfg.effort, cfg.use_server_fallbacks)
        summarizer = lambda transcript: llm.summarize(transcript, cfg.small_model)  # noqa: E731
    else:
        llm = OfflinePlanner()
    store = RedisStore(cfg.redis_url) if cfg.redis_url else MemoryStore()
    messenger = Messenger(sf, cfg, http_post=httpx.post if cfg.whatsapp_access_token else None)
    return Agent(cfg, store, llm, tools, payments=backend, directory=backend, session_factory=sf, messenger=messenger,
                 simulate_payments=isinstance(backend, FixtureBackend), summarizer=summarizer)
