"""Global context for MCP tools -- set by server.py at startup."""

from __future__ import annotations

from greennode.vlb_mcp_server.auth import TokenManager
from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import VlbConfig


config: VlbConfig | None = None
token_manager: TokenManager | None = None
client: VlbClient | None = None
