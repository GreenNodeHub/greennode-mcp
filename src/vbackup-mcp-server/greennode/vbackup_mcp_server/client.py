"""HTTP client for the vBackup API (built on greennode.mcp_core)."""

from __future__ import annotations

from greennode.mcp_core.http import (
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RETRYABLE_STATUS_CODES,
    BaseClient,
)
from greennode.vbackup_mcp_server.auth import TokenManager
from greennode.vbackup_mcp_server.config import (
    VBACKUP_SERVICE,
    VDB_MEMORY_SERVICE,
    VDB_RELATIONAL_SERVICE,
    VMONITOR_SERVICE,
    VSERVER_SERVICE,
    VbackupConfig,
)
from greennode.vbackup_mcp_server.useragent import USER_AGENT


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "RETRY_BASE_DELAY",
    "RETRYABLE_STATUS_CODES",
    "VbackupClient",
]


class VbackupClient(BaseClient):
    """Async client for the vBackup API (retry + token refresh from BaseClient).

    One gateway per region serves the whole product, so unlike vServer there is
    no second service to route to and no project id to inject — every path is
    ``/v1/**`` and scoping travels in the token plus optional ``backendId`` /
    ``projectId`` query parameters.
    """

    def __init__(self, config: VbackupConfig, token_manager: TokenManager) -> None:
        super().__init__(
            config, token_manager, default_service=VBACKUP_SERVICE, user_agent=USER_AGENT
        )

    async def get_vserver(
        self, path: str, region: str | None = None, params: dict | None = None
    ) -> object:
        """GET against the **vServer** gateway instead of vBackup.

        The one place this package leaves its own product. vBackup records a
        `serverId` but nothing about the machine behind it, so describing a
        protected server — its name, state, flavour and image — means asking
        vServer. Unlike vBackup, that gateway is versioned ``/v2`` and carries
        the project id **in the path**.

        Kept to reads: this package must never mutate a vServer resource. That
        belongs to ``vserver-mcp-server``.
        """
        return await self._request(
            "GET", path, region=region, params=params, service=VSERVER_SERVICE
        )

    async def get_vdb(
        self, path: str, memory_engine: bool, region: str | None = None, params: dict | None = None
    ) -> object:
        """GET against the **vDB** gateway instead of vBackup.

        The third place this package leaves its own product, and the mirror of
        ``get_vserver``: vBackup can say which databases it already protects,
        but only vDB can say which databases exist at all — which is what a
        create needs in order to offer a choice.

        vDB splits its estate across two path prefixes by engine family rather
        than one gateway with a filter, so *memory_engine* selects between
        ``vdb-memory`` (Redis) and ``vdb-relational`` (PostgreSQL). The host is
        not region-scoped and resolves the project from the token.

        Kept to reads: this package must never mutate a vDB resource.
        """
        service = VDB_MEMORY_SERVICE if memory_engine else VDB_RELATIONAL_SERVICE
        return await self._request("GET", path, region=region, params=params, service=service)

    async def post_vmonitor(self, path: str, json: object, region: str | None = None) -> object:
        """POST against the **vMonitor** statistics API behind the Backup Center console.

        The second place this package leaves its own product, and the only one
        that reads time series. vBackup's own endpoints report the state right
        now; vMonitor is what answers "and how did it get there".

        The host is not region-scoped — one endpoint covers both regions and
        labels each series with the region it came from — so ``region`` only
        selects the (identical) base URL and never changes the result.
        """
        return await self._request(
            "POST", path, region=region, json=json, service=VMONITOR_SERVICE
        )
