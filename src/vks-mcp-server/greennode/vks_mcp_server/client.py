"""HTTP client for the VKS + vServer APIs (built on greennode.mcp_core)."""

from __future__ import annotations

from greennode.mcp_core.http import (
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RETRYABLE_STATUS_CODES,
    BaseClient,
)
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.config import VksConfig
from greennode.vks_mcp_server.useragent import USER_AGENT
from typing import Any


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "RETRY_BASE_DELAY",
    "RETRYABLE_STATUS_CODES",
    "VksClient",
]


class VksClient(BaseClient):
    """Async client for the VKS API, with vServer access for discovery."""

    def __init__(self, config: VksConfig, token_manager: TokenManager) -> None:
        super().__init__(config, token_manager, default_service="vks", user_agent=USER_AGENT)

    async def vserver_get(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a GET request to the vServer API (not the VKS API)."""
        return await self._request("GET", path, region=region, params=params, service="vserver")
