"""Profile/credential loading shared by all GreenNode MCP servers.

Reads the ``credentials`` and ``config`` INI files under a config directory
(``~/.greennode``, shared with greennode-cli) with ``GRN_*`` environment
variable overrides. Product servers wrap :class:`ProfileSettings` in their own
config dataclass and add product endpoints (region → base URLs).
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


#: Preferred config directory name — where ``grn configure`` writes.
DEFAULT_CONFIG_DIR_NAME = ".greennode"
#: Pre-rename config directory name, kept as a read-only fallback.
LEGACY_CONFIG_DIR_NAME = ".greenode"


def resolve_config_dir(home: Path | None = None) -> Path:
    """Return the config directory to READ credentials/config from.

    Prefers ``~/.greennode``; falls back to the pre-rename ``~/.greenode`` only
    when the preferred directory is absent but the legacy one exists — so
    installs made before the greennode-cli rename keep working until the user
    re-runs ``grn configure``. When neither exists, returns the preferred
    ``~/.greennode`` (its non-existence surfaces as a normal "no credentials"
    error). Mirrors greennode-cli's ``effectiveConfigDir``.
    """
    base = home or Path.home()
    preferred = base / DEFAULT_CONFIG_DIR_NAME
    if preferred.exists():
        return preferred
    legacy = base / LEGACY_CONFIG_DIR_NAME
    if legacy.exists():
        return legacy
    return preferred


@dataclass
class ProfileSettings:
    """Credentials and defaults resolved from env + profile files."""

    client_id: str
    client_secret: str
    region: str
    project_id: str | None = None


def load_profile(config_dir: Path, default_region: str = "HCM-3") -> ProfileSettings:
    """Load credentials/region/project from *config_dir*.

    Reads ``credentials`` and ``config`` INI files under *config_dir*. The
    profile is selected by the ``GRN_PROFILE`` environment variable
    (default: ``"default"``).

    Environment variable overrides (highest priority):
    - ``GRN_CLIENT_ID`` overrides ``client_id`` from the credentials file
    - ``GRN_CLIENT_SECRET`` overrides ``client_secret`` from the credentials file
    - ``GRN_DEFAULT_REGION`` overrides ``region`` from the config file
    - ``GRN_PROJECT_ID`` overrides ``project_id`` from the config file

    Raises ``FileNotFoundError`` if the credentials file is missing (and no
    env var overrides are set) and ``ValueError`` if required fields are absent.
    """
    profile = os.environ.get("GRN_PROFILE", "default")

    credentials_path = config_dir / "credentials"
    config_path = config_dir / "config"

    # --- Credentials ---
    client_id: str | None = os.environ.get("GRN_CLIENT_ID")
    client_secret: str | None = os.environ.get("GRN_CLIENT_SECRET")

    if not (client_id and client_secret):
        if not credentials_path.exists():
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
            "Obtain them from GreenNode IAM Portal > Service Accounts."
        )

    # --- Region ---
    env_region = os.environ.get("GRN_DEFAULT_REGION")
    region = env_region or default_region

    if not env_region and config_path.exists():
        cfg_parser = configparser.ConfigParser()
        cfg_parser.read(config_path)
        if cfg_parser.has_section(profile):
            region = cfg_parser.get(profile, "region", fallback=region)

    # --- Project ID ---
    project_id = os.environ.get("GRN_PROJECT_ID")
    if not project_id and config_path.exists():
        cfg_parser = configparser.ConfigParser()
        cfg_parser.read(config_path)
        if cfg_parser.has_section(profile):
            project_id = cfg_parser.get(profile, "project_id", fallback=None)

    return ProfileSettings(
        client_id=client_id,
        client_secret=client_secret,
        region=region,
        project_id=project_id,
    )
