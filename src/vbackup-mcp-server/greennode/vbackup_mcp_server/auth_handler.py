"""Authentication handler for the vBackup MCP Server."""

from __future__ import annotations

from greennode.vbackup_mcp_server.auth import TokenManager
from greennode.vbackup_mcp_server.config import VBACKUP_SERVICE, VbackupConfig
from greennode.vbackup_mcp_server.tool_annotations import READ


class AuthHandler:
    """Register and serve authentication-related MCP tools."""

    def __init__(self, mcp, config: VbackupConfig, token_manager: TokenManager):
        self.mcp = mcp
        self.config = config
        self.token_manager = token_manager
        self.mcp.tool(name="get_access_token", annotations=READ)(self.get_access_token)

    async def get_access_token(self) -> str:
        """Retrieves the current access token for vBackup API calls. Returns the token, default region, and endpoint URL. Token auto-refreshes via client credentials."""
        token = await self.token_manager.get_token()
        region = self.config.default_region
        endpoint = self.config.get_base_url(region, VBACKUP_SERVICE)

        return (
            f"access_token: {token}"
            f"\nregion: {region}"
            f"\nvbackup_endpoint: {endpoint}"
            f"\nauth_mode: client_credentials (auto-refresh)"
        )
