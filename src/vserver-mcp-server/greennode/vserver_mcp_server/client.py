"""HTTP client for the vServer API (built on greennode.mcp_core)."""

from __future__ import annotations

from greennode.mcp_core.http import (
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RETRYABLE_STATUS_CODES,
    BaseClient,
)
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.config import VBACKUP_SERVICE, VSERVER_SERVICE, VserverConfig
from greennode.vserver_mcp_server.useragent import USER_AGENT
from typing import Any


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "RETRY_BASE_DELAY",
    "RETRYABLE_STATUS_CODES",
    "VbackupClient",
    "VserverClient",
]


class VserverClient(BaseClient):
    """Async client for the vServer API (retry + token refresh from BaseClient)."""

    def __init__(self, config: VserverConfig, token_manager: TokenManager) -> None:
        super().__init__(
            config, token_manager, default_service=VSERVER_SERVICE, user_agent=USER_AGENT
        )

    async def delete_with_body(
        self,
        path: str,
        region: str | None = None,
        json: Any = None,
    ) -> Any:
        """Send a DELETE request carrying a JSON body.

        Several vServer deletes take options in the body rather than the query
        string (e.g. ``DELETE /v2/{pid}/servers/{id}`` with
        ``{"deleteAllVolume": true}``, and the internal/external
        network-interface detaches).
        """
        return await self._request("DELETE", path, region=region, json=json)


class VbackupClient(BaseClient):
    """Async client for the vBackup gateway, which serves the snapshot schedules.

    Same IAM token and retry behaviour as :class:`VserverClient` — only the
    host differs, so the two are separate clients rather than one client with a
    per-call host argument.
    """

    def __init__(self, config: VserverConfig, token_manager: TokenManager) -> None:
        super().__init__(
            config, token_manager, default_service=VBACKUP_SERVICE, user_agent=USER_AGENT
        )
