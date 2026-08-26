"""Configuration and endpoint resolution for the vMonitor MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from greennode.mcp_core.config import load_profile
from pathlib import Path


VMONITOR_BASE_URL = "https://vmonitorapis.vngcloud.vn/vmonitor-api"
VMONITOR_LOG_BASE_URL = "https://vmonitorapis.vngcloud.vn/log-api"
VMONITOR_NOTIFICATION_BASE_URL = "https://vmonitorapis.vngcloud.vn/notification-gateway/api"
VMONITOR_BILLING_BASE_URL = "https://vmonitorapis.vngcloud.vn/billing-api"
VMONITOR_UPTIME_BASE_URL = "https://vmonitorapis.vngcloud.vn/vmonitor-uptime-manager/v1"

LOG_SERVICE = "vmonitor-log"
NOTIFICATION_SERVICE = "vmonitor-notification"
BILLING_SERVICE = "vmonitor-billing"
UPTIME_SERVICE = "vmonitor-uptime"


@dataclass
class VmonitorConfig:
    """Top-level vMonitor configuration.

    vMonitor is a global service (not region-scoped), so a single base URL per
    service serves every request; ``get_base_url`` ignores the region argument
    that ``BaseClient`` passes through for parity with region-scoped servers.
    vMonitor exposes several API hosts sharing the same IAM auth, each selected
    by a service name: the metric/dashboard API (default), the Log API
    (``vmonitor-log``), the notification gateway (``vmonitor-notification``), the
    billing / quota-usage API (``vmonitor-billing``) and the synthetic/uptime
    manager (``vmonitor-uptime``).
    """

    client_id: str
    client_secret: str
    base_url: str = VMONITOR_BASE_URL
    log_base_url: str = VMONITOR_LOG_BASE_URL
    notification_base_url: str = VMONITOR_NOTIFICATION_BASE_URL
    billing_base_url: str = VMONITOR_BILLING_BASE_URL
    uptime_base_url: str = VMONITOR_UPTIME_BASE_URL
    project_id: str | None = None

    def get_base_url(self, region: str | None, service: str) -> str:
        """Return the base URL for *service* (vMonitor is not region-scoped)."""
        if service == LOG_SERVICE:
            return self.log_base_url
        if service == NOTIFICATION_SERVICE:
            return self.notification_base_url
        if service == BILLING_SERVICE:
            return self.billing_base_url
        if service == UPTIME_SERVICE:
            return self.uptime_base_url
        return self.base_url


def load_config(config_dir: Path) -> VmonitorConfig:
    """Load vMonitor configuration from *config_dir* (shared with greennode-cli).

    Credential resolution (INI files + ``GRN_*`` env overrides) is shared logic
    in :func:`greennode.mcp_core.config.load_profile`; the region it resolves is
    unused because vMonitor is global.
    """
    profile = load_profile(config_dir)
    return VmonitorConfig(
        client_id=profile.client_id,
        client_secret=profile.client_secret,
        project_id=profile.project_id,
    )
