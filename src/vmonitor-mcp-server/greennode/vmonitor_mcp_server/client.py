"""HTTP client for the vMonitor API (built on greennode.mcp_core)."""

from __future__ import annotations

from greennode.mcp_core.http import (
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RETRYABLE_STATUS_CODES,
    BaseClient,
)
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.config import (
    BILLING_SERVICE,
    LOG_SERVICE,
    NOTIFICATION_SERVICE,
    UPTIME_SERVICE,
    VmonitorConfig,
)
from greennode.vmonitor_mcp_server.useragent import USER_AGENT


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "RETRY_BASE_DELAY",
    "RETRYABLE_STATUS_CODES",
    "VmonitorBillingClient",
    "VmonitorClient",
    "VmonitorLogClient",
    "VmonitorNotificationClient",
    "VmonitorUptimeClient",
]


class VmonitorClient(BaseClient):
    """Async client for the vMonitor API (retry + token refresh from BaseClient)."""

    def __init__(self, config: VmonitorConfig, token_manager: TokenManager) -> None:
        super().__init__(config, token_manager, default_service="vmonitor", user_agent=USER_AGENT)


class VmonitorLogClient(BaseClient):
    """Async client for the vMonitor Log API.

    Same IAM auth as ``VmonitorClient`` but a different base URL, selected via the
    ``vmonitor-log`` default service (resolved by ``VmonitorConfig.get_base_url``).
    """

    def __init__(self, config: VmonitorConfig, token_manager: TokenManager) -> None:
        super().__init__(config, token_manager, default_service=LOG_SERVICE, user_agent=USER_AGENT)


class VmonitorNotificationClient(BaseClient):
    """Async client for the vMonitor notification gateway.

    Same IAM auth, base URL selected via the ``vmonitor-notification`` service.
    """

    def __init__(self, config: VmonitorConfig, token_manager: TokenManager) -> None:
        super().__init__(
            config, token_manager, default_service=NOTIFICATION_SERVICE, user_agent=USER_AGENT
        )


class VmonitorBillingClient(BaseClient):
    """Async client for the vMonitor billing / quota-usage API.

    Same IAM auth, base URL selected via the ``vmonitor-billing`` service.
    """

    def __init__(self, config: VmonitorConfig, token_manager: TokenManager) -> None:
        super().__init__(
            config, token_manager, default_service=BILLING_SERVICE, user_agent=USER_AGENT
        )


class VmonitorUptimeClient(BaseClient):
    """Async client for the vMonitor synthetic / uptime manager.

    Same IAM auth, base URL selected via the ``vmonitor-uptime`` service.
    """

    def __init__(self, config: VmonitorConfig, token_manager: TokenManager) -> None:
        super().__init__(
            config, token_manager, default_service=UPTIME_SERVICE, user_agent=USER_AGENT
        )
