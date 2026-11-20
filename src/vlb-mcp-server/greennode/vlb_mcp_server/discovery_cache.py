"""Short-lived TTL cache for read-only vLB discovery results.

The tools **absent** from ``TTL_CONFIG`` matter as much as the ones present: a
tool with no configured TTL is never cached. Listeners, pools, members, L7
policies and health monitors are deliberately uncached because they are read
*inside* read-modify-write cycles (``attach_pool_members``,
``update_pool_health_monitor``) and while polling a 202. A 30-second-stale read
there means writing back a stale member list — a convenience turned into an
outage.
"""

from __future__ import annotations

from greennode.mcp_core.cache import DEFAULT_MAXSIZE
from greennode.mcp_core.cache import DiscoveryCache as _CoreDiscoveryCache
from greennode.mcp_core.http import current_identity


TTL_CONFIG: dict[str, int] = {
    # Catalogue — effectively immutable within a session.
    "list_load_balancer_packages": 3600,
    "list_listener_headers": 3600,
    "list_zones": 3600,
    "project_lookup": 3600,
    # Infrastructure the load balancer sits on; changes via another product.
    "list_vpcs": 120,
    "list_subnets": 120,
    "list_servers": 60,
    # vLB's own estate.
    "list_certificates": 60,
    "list_load_balancers": 30,
    "list_load_balancers_by_subnet": 30,
}


class DiscoveryCache(_CoreDiscoveryCache):
    """vLB discovery cache: core cache preconfigured with this package's TTLs.

    Every key is prefixed with the caller identity (hash of the passthrough
    user token, or 'service'), so under token passthrough one user's cached
    results can never be served to another.
    """

    def __init__(
        self, ttl_config: dict[str, int] | None = None, maxsize: int = DEFAULT_MAXSIZE, timer=None
    ):
        super().__init__(
            ttl_config if ttl_config is not None else TTL_CONFIG, maxsize=maxsize, timer=timer
        )

    async def get_or_fetch(self, tool, key, fetch, refresh=False):
        """Cache lookup with the caller identity baked into the key."""
        scoped_key = (current_identity(), key)
        return await super().get_or_fetch(tool, scoped_key, fetch, refresh)
