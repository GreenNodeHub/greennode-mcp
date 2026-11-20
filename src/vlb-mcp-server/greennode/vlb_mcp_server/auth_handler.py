"""Authentication handler for the vLB MCP Server."""

from __future__ import annotations

from greennode.mcp_core.http import user_token_var
from greennode.vlb_mcp_server.auth import TokenManager
from greennode.vlb_mcp_server.config import VLB_SERVICE, VlbConfig
from greennode.vlb_mcp_server.tool_annotations import READ


class AuthHandler:
    """Register and serve authentication-related MCP tools.

    ``expose_service_token`` must be False on the HTTP transport. There the
    server is shared, and the service-account token carries wider rights than
    a caller's own token. Returning it would let any caller lift those rights
    and would put the secret into the model transcript.
    """

    def __init__(
        self,
        mcp,
        config: VlbConfig,
        token_manager: TokenManager,
        expose_service_token: bool = True,
    ):
        self.mcp = mcp
        self.config = config
        self.token_manager = token_manager
        self.expose_service_token = expose_service_token
        self.mcp.tool(name="get_access_token", annotations=READ)(self.get_access_token)

    async def get_access_token(self) -> str:
        """Retrieves the current access token for vLB API calls. Returns the token, default region, and endpoint URL. Over the HTTP transport this is the caller's own token; the shared service-account token is never returned there."""
        region = self.config.default_region
        endpoint = self.config.get_base_url(region, VLB_SERVICE)

        user_token = user_token_var.get()
        if user_token:
            return (
                f"access_token: {user_token}"
                f"\nregion: {region}"
                f"\nvlb_endpoint: {endpoint}"
                f"\nauth_mode: passthrough (the caller's own token)"
            )

        if not self.expose_service_token:
            raise ValueError(
                "The service-account token is not available over the HTTP transport. "
                "Send your own IAM token in the Authorization header to get it back here."
            )

        token = await self.token_manager.get_token()
        return (
            f"access_token: {token}"
            f"\nregion: {region}"
            f"\nvlb_endpoint: {endpoint}"
            f"\nauth_mode: client_credentials (auto-refresh)"
        )
