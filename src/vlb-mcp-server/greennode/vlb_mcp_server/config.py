"""Configuration and region endpoint resolution for the vLB MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from greennode.mcp_core.config import load_profile
from pathlib import Path
from typing import Literal


Region = Literal["HCM-3", "HAN"]

VLB_SERVICE = "vlb"
"""The vLB gateway. Every path is ``/v2/{projectId}/...`` and region-scoped."""

VSERVER_SERVICE = "vserver"
"""The vServer gateway, reached read-only for project, zone, VPC/subnet and server lookups.

vLB has no project-listing endpoint of its own, and a load balancer must be
placed on a vServer subnet, so this package cannot resolve its own scope or
offer a placement choice without it.

Note the ``/vserver/vserver-gateway`` path. The shorter ``/vserver-gateway``
spelling answers in HCM-3 but **404s in HAN**, so it must not be used; this is
the same spelling ``vserver-mcp-server`` and ``vbackup-mcp-server`` route to.
"""


REGIONS: dict[str, dict[str, str]] = {
    "HCM-3": {
        VLB_SERVICE: "https://hcm-3.api.vngcloud.vn/vserver/vlb-gateway",
        VSERVER_SERVICE: "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway",
    },
    "HAN": {
        VLB_SERVICE: "https://han-1.api.vngcloud.vn/vserver/vlb-gateway",
        VSERVER_SERVICE: "https://han-1.api.vngcloud.vn/vserver/vserver-gateway",
    },
}


@dataclass
class VlbConfig:
    """Top-level vLB configuration."""

    client_id: str
    client_secret: str
    default_region: str
    regions: dict[str, dict[str, str]]
    project_id: str | None = None
    project_id_by_region: dict[tuple[str, str], str] = field(default_factory=dict)

    def get_base_url(self, region: str | None, service: str) -> str:
        """Return the base URL for *service* in *region* (required by BaseClient)."""
        resolved = region if region is not None else self.default_region
        if resolved not in self.regions:
            raise ValueError(
                f"Region '{resolved}' does not exist in configuration. "
                f"Valid regions: {list(self.regions.keys())}"
            )
        return self.regions[resolved][service]


def load_config(config_dir: Path) -> VlbConfig:
    """Load vLB configuration from *config_dir*.

    Credentials/region/project resolution (INI files + ``GRN_*`` env overrides)
    is shared logic in :func:`greennode.mcp_core.config.load_profile`; this
    wrapper adds the vLB region endpoints.
    """
    profile = load_profile(config_dir)
    return VlbConfig(
        client_id=profile.client_id,
        client_secret=profile.client_secret,
        default_region=profile.region,
        regions=REGIONS,
        project_id=profile.project_id,
    )
