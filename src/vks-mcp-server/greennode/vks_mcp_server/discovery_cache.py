"""Short-lived TTL cache for read-only vServer discovery results."""

from __future__ import annotations

from cachetools import TTLCache
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


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

DEFAULT_MAXSIZE = 128


class DiscoveryCache:
    """One TTLCache per discovery tool. Lazy expiry; no background refresh.

    A tool with no configured TTL is never cached (``get_or_fetch`` always
    calls ``fetch``).
    """

    def __init__(
        self, ttl_config: dict[str, int] | None = None, maxsize: int = DEFAULT_MAXSIZE, timer=None
    ):
        cfg = ttl_config if ttl_config is not None else TTL_CONFIG
        kwargs = {"timer": timer} if timer is not None else {}
        self._caches: dict[str, TTLCache] = {
            tool: TTLCache(maxsize=maxsize, ttl=ttl, **kwargs) for tool, ttl in cfg.items()
        }

    async def get_or_fetch(
        self,
        tool: str,
        key: Hashable,
        fetch: Callable[[], Awaitable[Any]],
        refresh: bool = False,
    ) -> Any:
        """Return the cached value for *key*, else await *fetch* and store it.

        ``refresh=True`` bypasses the lookup and overwrites the stored value.
        """
        cache = self._caches.get(tool)
        if cache is None:
            return await fetch()
        if not refresh:
            try:
                return cache[key]
            except KeyError:
                pass
        value = await fetch()
        cache[key] = value
        return value

    def invalidate(self, tool: str | None = None) -> None:
        """Clear one tool's cache, or all caches when *tool* is None."""
        if tool is None:
            for cache in self._caches.values():
                cache.clear()
        elif tool in self._caches:
            self._caches[tool].clear()
