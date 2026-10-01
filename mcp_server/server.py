"""InsureAssist MCP server (FastMCP, Streamable HTTP) — the tool layer over InsureHub, LendHub and Payments.

Every call must carry `Authorization: Bearer <delegated token>` minted by the agent service after OTP verification;
public tools (help articles, request_human) also work without one. Not routed publicly: cluster-internal only.

    python -m mcp_server.server        # serves http://0.0.0.0:8101/mcp
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from core.backends import FixtureBackend, HttpBackend
from core.config import settings
from core.db import db_audit_sink, make_session_factory
from core.tokens import TokenError, verify
from mcp_server.tools import PUBLIC_TOOLS, TOOL_DEFINITIONS, Caller, ToolError, ToolService

log = logging.getLogger("insureassist.mcp")
DESCRIPTIONS = {t["name"]: t["description"] for t in TOOL_DEFINITIONS}


def build_service() -> ToolService:
    cfg = settings()
    backend = (HttpBackend(cfg.insurehub_url, cfg.lendhub_url, cfg.payments_url, cfg.lendhub_channel_user,
                           cfg.lendhub_channel_password) if cfg.backend == "http" else FixtureBackend())
    return ToolService(backend, audit=db_audit_sink(make_session_factory(cfg.database_url)))


def caller_from_headers(headers: Any, secret: str, tool: str) -> Caller:
    auth = headers.get("authorization") if headers else None
    token = auth.split(" ", 1)[1] if auth and auth.lower().startswith("bearer ") else None
    conversation = headers.get("x-conversation-id") if headers else None
    if token is None and tool in PUBLIC_TOOLS:
        return Caller(None, None, conversation)
    try:
        claims = verify(token, secret)
    except TokenError as e:
        raise ToolError("unauthorized", f"Delegated token rejected: {e}") from e
    return Caller(claims["sub"], claims.get("msisdn"), conversation)


def create_server(service: ToolService | None = None) -> FastMCP:
    service = service or build_service()
    secret = settings().delegated_token_secret
    mcp = FastMCP("insureassist-tools", host="0.0.0.0", port=8101, stateless_http=True, json_response=True)

    def run(ctx: Context, name: str, **args: Any) -> str:
        request = ctx.request_context.request
        try:
            caller = caller_from_headers(getattr(request, "headers", None), secret, name)
            return json.dumps(service.call(name, {k: v for k, v in args.items() if v is not None}, caller))
        except ToolError as e:
            return json.dumps({"error": e.code, "message": e.message})

    @mcp.tool(description=DESCRIPTIONS["list_my_policies"])
    def list_my_policies(ctx: Context) -> str:
        return run(ctx, "list_my_policies")

    @mcp.tool(description=DESCRIPTIONS["get_policy"])
    def get_policy(policy_number: str, ctx: Context) -> str:
        return run(ctx, "get_policy", policy_number=policy_number)

    @mcp.tool(description=DESCRIPTIONS["get_premium_balance"])
    def get_premium_balance(policy_number: str, ctx: Context) -> str:
        return run(ctx, "get_premium_balance", policy_number=policy_number)

    @mcp.tool(description=DESCRIPTIONS["prepare_premium_payment"])
    def prepare_premium_payment(policy_number: str, ctx: Context, amount: float | None = None) -> str:
        return run(ctx, "prepare_premium_payment", policy_number=policy_number, amount=amount)

    @mcp.tool(description=DESCRIPTIONS["get_claim_status"])
    def get_claim_status(ctx: Context, claim_number: str | None = None) -> str:
        return run(ctx, "get_claim_status", claim_number=claim_number)

    @mcp.tool(description=DESCRIPTIONS["start_motor_claim"])
    def start_motor_claim(policy_number: str, incident_date: str, location: str, description: str, ctx: Context,
                          media_ids: list[str] | None = None) -> str:
        return run(ctx, "start_motor_claim", policy_number=policy_number, incident_date=incident_date,
                   location=location, description=description, media_ids=media_ids)

    @mcp.tool(description=DESCRIPTIONS["get_loan_summary"])
    def get_loan_summary(ctx: Context) -> str:
        return run(ctx, "get_loan_summary")

    @mcp.tool(description=DESCRIPTIONS["get_settlement_quote"])
    def get_settlement_quote(loan_number: str, ctx: Context) -> str:
        return run(ctx, "get_settlement_quote", loan_number=loan_number)

    @mcp.tool(description=DESCRIPTIONS["prepare_loan_payment"])
    def prepare_loan_payment(loan_number: str, ctx: Context, amount: float | None = None) -> str:
        return run(ctx, "prepare_loan_payment", loan_number=loan_number, amount=amount)

    @mcp.tool(description=DESCRIPTIONS["search_help_articles"])
    def search_help_articles(query: str, ctx: Context) -> str:
        return run(ctx, "search_help_articles", query=query)

    @mcp.tool(description=DESCRIPTIONS["request_human"])
    def request_human(reason: str, ctx: Context) -> str:
        return run(ctx, "request_human", reason=reason)

    return mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    create_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
