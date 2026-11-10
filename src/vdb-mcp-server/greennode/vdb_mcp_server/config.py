"""Configuration and service endpoint resolution for the vDB MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from greennode.mcp_core.config import load_profile
from pathlib import Path
from typing import Literal


Region = Literal["HCM-3", "HAN"]
"""Kept only to satisfy ``BaseClient.get_base_url(region, service)``.

vDB tools deliberately expose no ``region`` parameter -- see ``REGIONS`` below.
"""

GATEWAY_URL_ENV = "GRN_VDB_GATEWAY_URL"
"""Environment variable overriding the gateway host for every service.

Point it at a non-production gateway to live-probe a family against staging
before it reaches production. Unset, every call goes to the production
gateway below: the module constants never read the environment, so a stray
variable cannot leak a test host into a production run's URL table.
"""

VDB_GATEWAY = "https://vdb-gateway.vngcloud.vn"
"""The single vDB gateway host (the production default).

Unlike vServer or vBackup there is **no per-region host**: one gateway answers
for the whole account and resolves the project from the token. Verified in
production and already relied on by ``vbackup-mcp-server``, which reads vDB
through the same two prefixes.

Note the OpenAPI document spells this `https:/vdb-gateway.vngcloud.vn` with a
single slash. That is a typo in the spec, not an endpoint.

Override for staging or local testing via ``GRN_VDB_GATEWAY_URL`` — see
``GATEWAY_URL_ENV``.
"""

VDB_RELATIONAL_SERVICE = "vdb-relational"
"""Relational engines: MySQL and standalone PostgreSQL. Paths are ``/v1/**``."""

VDB_MEMORY_SERVICE = "vdb-memory"
"""MemoryStore (Redis). Paths are ``/v1/**``, but they are NOT the relational
paths with a different prefix -- the two families disagree on method and shape
for the same business operation. See the package CLAUDE.md."""

VDB_POSTGRESQL_SERVICE = "vdb-postgresql"
"""PostgreSQL Cluster. Paths are ``/v1/**``.

This family has no list and no get-by-id endpoint of its own: its clusters are
enumerated by the *relational* instance listing, which returns them mixed in
with relational instances.
"""

VDB_KAFKA_SERVICE = "vdb-kafka"
"""Kafka. Unversioned paths (``/clusters``, not ``/v1/clusters``) and four
different response shapes -- it shares no response handling with the others."""


VDB_SERVICES = (
    VDB_RELATIONAL_SERVICE,
    VDB_MEMORY_SERVICE,
    VDB_POSTGRESQL_SERVICE,
    VDB_KAFKA_SERVICE,
)
"""Every vDB service prefix the one gateway fans out to."""


def _regions_for(gateway: str) -> dict[str, dict[str, str]]:
    """Map both region keys onto the same per-service URLs on *gateway*.

    ``BaseClient`` resolves every call through ``get_base_url(region, service)``,
    so the region axis has to exist; for vDB it simply has no effect (see the
    ``Region`` literal above). Both region keys therefore resolve to the same
    URL on purpose. Building the table here means a future region-scoped vDB
    host is a data change in this one function and nothing else.
    """
    per_region = {service: f"{gateway}/{service}" for service in VDB_SERVICES}
    return {region: dict(per_region) for region in ("HCM-3", "HAN")}


REGIONS = _regions_for(VDB_GATEWAY)
"""The production URL table — see :func:`_regions_for`."""


@dataclass
class VdbConfig:
    """Top-level vDB configuration."""

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


def load_config(config_dir: Path) -> VdbConfig:
    """Load configuration from *config_dir* (credentials shared with greennode-cli).

    ``GRN_VDB_GATEWAY_URL`` (see ``GATEWAY_URL_ENV``) overrides the gateway
    host for every service; a trailing slash is trimmed. Unset, the production
    gateway answers.
    """
    profile = load_profile(config_dir)
    gateway = os.environ.get(GATEWAY_URL_ENV, "").rstrip("/") or VDB_GATEWAY
    return VdbConfig(
        client_id=profile.client_id,
        client_secret=profile.client_secret,
        default_region=profile.region,
        regions=_regions_for(gateway),
        project_id=profile.project_id,
    )
