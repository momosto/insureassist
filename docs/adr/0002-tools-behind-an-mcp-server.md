# ADR-0002: Expose core-system actions to the agent through a dedicated MCP server

- **Status:** Accepted · **Date:** 2026-09-30

## Context
The agent needs about 11 tools across InsureHub, LendHub, Payments and a knowledge base. They could be plain Python functions in the agent, or a separate server speaking the Model Context Protocol.

## Options
1. In-process tool functions: least code, but tools are tied to one agent and one model SDK, and security logic is mixed with conversation logic.
2. **Separate MCP server (Python SDK / FastMCP, Streamable HTTP):** a standard protocol; reusable by other clients (a staff copilot, other models); its own auth, audit and deployment.
3. A bespoke REST "tools API": works, but reinvents a standard the industry has adopted.

## Decision
Option 2. The MCP server is cluster-internal, requires a signed delegated token per call (ADR-0003), and writes an audit row per call.

## Consequences
- ➕ Shows the integration standard employers are adopting (MCP is now under the Linux Foundation and supported by OpenAI, Google, Microsoft and AWS).
- ➕ Security boundary is explicit and testable on its own.
- ➖ One more service (~100 MB RAM) and a network hop per tool call; acceptable.

## Sources
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) · [MCP specification](https://modelcontextprotocol.io/)
- [Wikipedia — Model Context Protocol](https://en.wikipedia.org/wiki/Model_Context_Protocol)
- [DEV — MCP grows up: enterprise adoption 2026](https://dev.to/lusivision/mcp-grows-up-what-enterprise-adoption-means-in-2026-1875)
