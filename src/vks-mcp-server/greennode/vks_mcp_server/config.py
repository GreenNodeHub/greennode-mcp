"""Configuration loading and region endpoint resolution for GreenNode MCP Server."""

from __future__ import annotations

from dataclasses import dataclass, field
from greennode.mcp_core.config import load_profile
from pathlib import Path
from typing import Literal


Region = Literal["HCM-3", "HAN"]


@dataclass(frozen=True)
class RegionEndpoints:
    """Endpoints for a single VKS region."""

    vks: str
    vserver: str


@dataclass
class VksConfig:
    """Top-level VKS configuration."""

    client_id: str
    client_secret: str
    default_region: str
    regions: dict[str, RegionEndpoints]
    project_id: str | None = None
    # vServer project_id is region-scoped; resolved lazily per region (see
    # discovery_handler._require_project_id). Not loaded from config.
    project_id_by_region: dict[str, str] = field(default_factory=dict)

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
        """Return the base URL for *service* ("vks" or "vserver") in *region*."""
        endpoints = self.get_endpoints(region)
        return endpoints.vserver if service == "vserver" else endpoints.vks


REGIONS: dict[str, RegionEndpoints] = {
    "HCM-3": RegionEndpoints(
        vks="https://vks.api.vngcloud.vn",
        vserver="https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway",
    ),
    "HAN": RegionEndpoints(
        vks="https://vks-han-1.api.vngcloud.vn",
        vserver="https://han-1.api.vngcloud.vn/vserver/vserver-gateway",
    ),
}


def load_config(config_dir: Path) -> VksConfig:
    """Load VKS configuration from *config_dir*.

    Credentials/region/project resolution (INI files + ``GRN_*`` env overrides)
    is shared logic in :func:`greennode.mcp_core.config.load_profile`; this
    wrapper adds the VKS/vServer region endpoints.
    """
    profile = load_profile(config_dir)
    return VksConfig(
        client_id=profile.client_id,
        client_secret=profile.client_secret,
        default_region=profile.region,
        regions=REGIONS,
        project_id=profile.project_id,
    )
