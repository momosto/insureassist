"""How the agent reaches the tools: in-process (demo, tests) or over MCP Streamable HTTP (deployed).

Both paths validate the delegated token with the same code (`caller_from_headers`), so tests of the local path
exercise the real authorisation rules.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from mcp_server.server import caller_from_headers
from mcp_server.tools import ToolError, ToolService


class ToolClient(Protocol):
    def call(self, name: str, args: dict[str, Any], token: str | None, conversation_id: str | None) -> dict[str, Any]: ...


class LocalToolClient:
    def __init__(self, service: ToolService, secret: str):
        self.service = service
        self.secret = secret

    def call(self, name, args, token, conversation_id):
        headers = {"x-conversation-id": conversation_id or ""}
        if token:
            headers["authorization"] = f"Bearer {token}"
        try:
            caller = caller_from_headers(headers, self.secret, name)
            return self.service.call(name, args, caller)
        except ToolError as e:
            return {"error": e.code, "message": e.message}


class McpToolClient:
    """MCP client over Streamable HTTP. One short session per call keeps the agent stateless and horizontally scalable."""

    def __init__(self, url: str, timeout: float = 15.0):
        self.url = url
        self.timeout = timeout

    def call(self, name, args, token, conversation_id):
        return asyncio.run(self._call(name, args, token, conversation_id))

    async def _call(self, name, args, token, conversation_id):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"X-Conversation-Id": conversation_id or ""}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with streamablehttp_client(self.url, headers=headers, timeout=self.timeout) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, args)
        except Exception as e:  # transport failures become a tool error the orchestrator can count
            return {"error": "backend_unavailable", "message": f"MCP server unreachable: {type(e).__name__}"}
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            return json.loads(text)
        except ValueError:
            return {"error": "bad_tool_output", "message": text[:200]}
