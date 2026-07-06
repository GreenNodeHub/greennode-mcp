"""Authentication handler for GreenNode MCP Server."""

from __future__ import annotations

from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.config import VksConfig


class AuthHandler:
    """Register and serve authentication-related MCP tools."""

    def __init__(self, mcp, config: VksConfig, token_manager: TokenManager):
        self.mcp = mcp
        self.config = config
        self.token_manager = token_manager
        self.mcp.tool(name="get_access_token")(self.get_access_token)

    async def get_access_token(self) -> str:
        """Retrieves the current access token for VKS/vServer API calls. Returns the token, default region, endpoint URLs. Token auto-refreshes via client credentials."""
        token = await self.token_manager.get_token()
        region = self.config.default_region
        endpoints = self.config.get_endpoints(region)

        return (
            f"access_token: {token}"
            f"\nregion: {region}"
            f"\nvks_endpoint: {endpoints.vks}"
            f"\nvserver_endpoint: {endpoints.vserver}"
            f"\nauth_mode: client_credentials (auto-refresh)"
        )
