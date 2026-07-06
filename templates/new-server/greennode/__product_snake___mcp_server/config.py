"""Configuration and region endpoint resolution for the {{PRODUCT}} MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from greennode.mcp_core.config import load_profile


Region = Literal["HCM-3", "HAN"]


# TODO: fill in the real {{PRODUCT}} API base URLs per region.
REGIONS: dict[str, dict[str, str]] = {
    "HCM-3": {"{{product}}": "https://{{product}}.api.vngcloud.vn"},
    "HAN": {"{{product}}": "https://{{product}}-han-1.api.vngcloud.vn"},
}


@dataclass
class {{Product}}Config:
    """Top-level {{PRODUCT}} configuration."""

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


def load_config(config_dir: Path) -> {{Product}}Config:
    """Load configuration from *config_dir* (credentials shared with greennode-cli)."""
    profile = load_profile(config_dir)
    return {{Product}}Config(
        client_id=profile.client_id,
        client_secret=profile.client_secret,
        default_region=profile.region,
        regions=REGIONS,
        project_id=profile.project_id,
    )
