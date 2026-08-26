"""Configuration and region endpoint resolution for the vBackup MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from greennode.mcp_core.config import load_profile
from pathlib import Path
from typing import Literal


Region = Literal["HCM-3", "HAN"]

VBACKUP_SERVICE = "vbackup"

VMONITOR_SERVICE = "vmonitor"
"""The vMonitor statistics API behind the Backup Center console.

A single host serving BOTH regions — unlike every other endpoint this package
calls, it is not region-scoped, and its series carry the region as a dimension
instead. Both region keys therefore map to the same URL, so ``get_base_url``
keeps working unchanged.
"""

VDB_RELATIONAL_SERVICE = "vdb-relational"
"""The vDB gateway for relational engines (PostgreSQL), read to plan a database backup.

Like vMonitor and unlike everything else here, the host is **not
region-scoped** — one gateway answers for the whole account and resolves the
project from the token — so both region keys map to the same URL.
"""

VDB_MEMORY_SERVICE = "vdb-memory"
"""The vDB gateway for in-memory engines (Redis). Same host, different path prefix."""

VSERVER_SERVICE = "vserver"
"""The vServer gateway, reached only to describe the source server behind a backup.

Note the ``/vserver/vserver-gateway`` path. The shorter ``/vserver-gateway``
spelling answers in HCM-3 but **404s in HAN**, so it must not be used; this is
the same spelling ``vserver-mcp-server`` routes to.
"""


REGIONS: dict[str, dict[str, str]] = {
    "HCM-3": {
        VBACKUP_SERVICE: "https://hcm-3.api.vngcloud.vn/vbackup-gateway",
        VSERVER_SERVICE: "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway",
        VMONITOR_SERVICE: "https://backupcenter.console.greennode.ai/vmonitor-api",
        VDB_RELATIONAL_SERVICE: "https://vdb-gateway.vngcloud.vn/vdb-relational",
        VDB_MEMORY_SERVICE: "https://vdb-gateway.vngcloud.vn/vdb-memory",
    },
    "HAN": {
        VBACKUP_SERVICE: "https://han-1.api.vngcloud.vn/vbackup-gateway",
        VSERVER_SERVICE: "https://han-1.api.vngcloud.vn/vserver/vserver-gateway",
        VMONITOR_SERVICE: "https://backupcenter.console.greennode.ai/vmonitor-api",
        VDB_RELATIONAL_SERVICE: "https://vdb-gateway.vngcloud.vn/vdb-relational",
        VDB_MEMORY_SERVICE: "https://vdb-gateway.vngcloud.vn/vdb-memory",
    },
}


@dataclass
class VbackupConfig:
    """Top-level vBackup configuration."""

    client_id: str
    client_secret: str
    default_region: str
    regions: dict[str, dict[str, str]]
    project_id: str | None = None

    def get_base_url(self, region: str | None, service: str) -> str:
        """Return the base URL for *service* in *region* (required by BaseClient)."""
        resolved = region if region is not None else self.default_region
        if resolved not in self.regions:
            raise ValueError(
                f"Region '{resolved}' does not exist in configuration. "
                f"Valid regions: {list(self.regions.keys())}"
            )
        return self.regions[resolved][service]


def load_config(config_dir: Path) -> VbackupConfig:
    """Load configuration from *config_dir* (credentials shared with greennode-cli)."""
    profile = load_profile(config_dir)
    return VbackupConfig(
        client_id=profile.client_id,
        client_secret=profile.client_secret,
        default_region=profile.region,
        regions=REGIONS,
        project_id=profile.project_id,
    )
