"""Configuration loading and region endpoint resolution for GreenNode MCP Server."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
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

    Reads ``credentials`` and ``config`` INI files under *config_dir*.
    The profile is selected by the ``GRN_PROFILE`` environment variable
    (default: ``"default"``).

    Environment variable overrides (highest priority):
    - ``GRN_CLIENT_ID`` overrides ``client_id`` from credentials file
    - ``GRN_CLIENT_SECRET`` overrides ``client_secret`` from credentials file
    - ``GRN_DEFAULT_REGION`` overrides ``region`` from config file

    Raises ``FileNotFoundError`` if the credentials file is missing (and
    no env var overrides are set) and ``ValueError`` if required fields
    are absent.
    """
    profile = os.environ.get("GRN_PROFILE", "default")

    credentials_path = config_dir / "credentials"
    config_path = config_dir / "config"

    # --- Credentials ---
    env_client_id = os.environ.get("GRN_CLIENT_ID")
    env_client_secret = os.environ.get("GRN_CLIENT_SECRET")

    client_id: str | None = env_client_id
    client_secret: str | None = env_client_secret

    if not (client_id and client_secret):
        # Need to read from file
        if not credentials_path.exists():
            if client_id and client_secret:
                pass  # both provided via env, skip file
            else:
                raise FileNotFoundError(
                    f"Credentials file not found: {credentials_path}"
                    ". Run 'grn configure' to set up authentication credentials."
                )

        cred_parser = configparser.ConfigParser()
        cred_parser.read(credentials_path)

        if not cred_parser.has_section(profile):
            raise ValueError(f"Credentials file missing section [{profile}]: {credentials_path}")

        if not client_id:
            client_id = cred_parser.get(profile, "client_id", fallback=None)
        if not client_secret:
            client_secret = cred_parser.get(profile, "client_secret", fallback=None)

    if not client_id or not client_secret:
        raise ValueError(
            "Credentials must include client_id and client_secret. "
            "Obtain them from VNG Cloud IAM Portal > Service Accounts."
        )

    # --- Region ---
    env_region = os.environ.get("GRN_DEFAULT_REGION")
    default_region = env_region or "HCM-3"

    if not env_region and config_path.exists():
        cfg_parser = configparser.ConfigParser()
        cfg_parser.read(config_path)
        if cfg_parser.has_section(profile):
            default_region = cfg_parser.get(profile, "region", fallback=default_region)

    # --- Project ID ---
    project_id = os.environ.get("GRN_PROJECT_ID")
    if not project_id and config_path.exists():
        cfg_parser = configparser.ConfigParser()
        cfg_parser.read(config_path)
        if cfg_parser.has_section(profile):
            project_id = cfg_parser.get(profile, "project_id", fallback=None)

    return VksConfig(
        client_id=client_id,
        client_secret=client_secret,
        default_region=default_region,
        regions=REGIONS,
        project_id=project_id,
    )
