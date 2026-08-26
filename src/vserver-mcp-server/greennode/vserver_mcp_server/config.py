"""Configuration loading and region endpoint resolution for the vServer MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from greennode.mcp_core.config import load_profile
from pathlib import Path
from typing import Literal


Region = Literal["HCM-3", "HAN"]

VSERVER_SERVICE = "vserver"
VBACKUP_SERVICE = "vbackup"


@dataclass(frozen=True)
class RegionEndpoints:
    """Endpoints for a single vServer region.

    Snapshot *schedules* live on a second gateway (vBackup) with its own host:
    the vServer gateway serves the snapshot points, but the policies those
    points are taken under are only listed there.
    """

    vserver: str
    vbackup: str


REGIONS: dict[str, RegionEndpoints] = {
    "HCM-3": RegionEndpoints(
        vserver="https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway",
        vbackup="https://hcm-3.console.greennode.ai/vserver/vbackup-gateway",
    ),
    "HAN": RegionEndpoints(
        vserver="https://han-1.api.vngcloud.vn/vserver/vserver-gateway",
        vbackup="https://han-1.console.greennode.ai/vserver/vbackup-gateway",
    ),
}


@dataclass
class VserverConfig:
    """Top-level vServer configuration."""

    client_id: str
    client_secret: str
    default_region: str
    regions: dict[str, RegionEndpoints]
    project_id: str | None = None
    project_id_by_region: dict[tuple[str, str], str] = field(default_factory=dict)

    def get_endpoints(self, region: str | None = None) -> RegionEndpoints:
        """Return endpoints for the given region.

        Falls back to *default_region* when *region* is ``None``.
        Raises ``ValueError`` when the resolved region is not configured.
        """
        resolved = region if region is not None else self.default_region
        if resolved not in self.regions:
            raise ValueError(
                f"Region '{resolved}' does not exist in configuration. "
                f"Valid regions: {list(self.regions.keys())}"
            )
        return self.regions[resolved]

    def get_base_url(self, region: str | None, service: str) -> str:
        """Return the base URL for *service* in *region* (required by BaseClient)."""
        endpoints = self.get_endpoints(region)
        if service == VBACKUP_SERVICE:
            return endpoints.vbackup
        return endpoints.vserver


def load_config(config_dir: Path) -> VserverConfig:
    """Load vServer configuration from *config_dir*.

    Credentials/region/project resolution (INI files + ``GRN_*`` env overrides)
    is shared logic in :func:`greennode.mcp_core.config.load_profile`; this
    wrapper adds the vServer region endpoints.
    """
    profile = load_profile(config_dir)
    return VserverConfig(
        client_id=profile.client_id,
        client_secret=profile.client_secret,
        default_region=profile.region,
        regions=REGIONS,
        project_id=profile.project_id,
    )
