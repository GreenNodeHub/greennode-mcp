"""Global context for MCP tools -- set by server.py at startup."""

from __future__ import annotations

from greennode.vbackup_mcp_server.auth import TokenManager
from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import VbackupConfig


config: VbackupConfig | None = None
token_manager: TokenManager | None = None
client: VbackupClient | None = None
