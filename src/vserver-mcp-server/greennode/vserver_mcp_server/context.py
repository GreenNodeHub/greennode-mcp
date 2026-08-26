"""Global context for MCP tools -- set by server.py at startup."""

from __future__ import annotations

from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import VserverConfig


config: VserverConfig | None = None
token_manager: TokenManager | None = None
client: VserverClient | None = None
