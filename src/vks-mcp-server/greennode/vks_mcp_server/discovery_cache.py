"""Short-lived TTL cache for read-only vServer discovery results."""

from __future__ import annotations

from greennode.mcp_core.cache import DEFAULT_MAXSIZE
from greennode.mcp_core.cache import DiscoveryCache as _CoreDiscoveryCache


# Per-tool TTL in seconds, tiered by how often the resource changes.
TTL_CONFIG: dict[str, int] = {
    "list_flavors": 1800,
    "list_volume_types": 1800,
    "list_cluster_versions": 1800,
    "list_vpcs": 120,
    "list_subnets": 120,
    "list_security_groups": 120,
    "list_placement_groups": 120,
    "list_ssh_keys": 30,
}


class DiscoveryCache(_CoreDiscoveryCache):
    """VKS discovery cache: core cache preconfigured with this package's TTLs."""

    def __init__(
        self, ttl_config: dict[str, int] | None = None, maxsize: int = DEFAULT_MAXSIZE, timer=None
    ):
        super().__init__(
            ttl_config if ttl_config is not None else TTL_CONFIG, maxsize=maxsize, timer=timer
        )
