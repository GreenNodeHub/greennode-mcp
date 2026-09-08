"""HTTP client for the vLB API (built on greennode.mcp_core)."""

from __future__ import annotations

from greennode.mcp_core.http import (
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RETRYABLE_STATUS_CODES,
    BaseClient,
)
from greennode.vlb_mcp_server.auth import TokenManager
from greennode.vlb_mcp_server.config import VLB_SERVICE, VSERVER_SERVICE, VlbConfig
from greennode.vlb_mcp_server.useragent import USER_AGENT
from typing import Any


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "RETRY_BASE_DELAY",
    "RETRYABLE_STATUS_CODES",
    "VlbClient",
]


class VlbClient(BaseClient):
    """Async client for the vLB API (retry + token refresh from BaseClient).

    Most mutations answer ``202 Accepted`` (deletes, rebalancing) or ``200`` with
    no content (listener/pool/member/policy updates), which ``BaseClient``
    surfaces as ``None`` — callers must never index into the result of a write.
    """

    def __init__(self, config: VlbConfig, token_manager: TokenManager) -> None:
        super().__init__(config, token_manager, default_service=VLB_SERVICE, user_agent=USER_AGENT)

    async def get_vserver(
        self, path: str, region: str | None = None, params: dict | None = None
    ) -> Any:
        """GET against the **vServer** gateway instead of vLB.

        The one place this package leaves its own product, and it is not
        optional: vLB exposes no project-listing endpoint, so even resolving
        ``/v2/{projectId}/...`` goes through here. The same gateway answers the
        zone, VPC, subnet and server lookups a placement decision needs.

        Kept to reads: this package must never mutate a vServer resource. That
        belongs to ``vserver-mcp-server``.
        """
        return await self._request(
            "GET", path, region=region, params=params, service=VSERVER_SERVICE
        )
