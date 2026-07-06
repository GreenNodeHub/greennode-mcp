"""Short-lived TTL cache for read-only discovery results (shared)."""

from __future__ import annotations

from cachetools import TTLCache
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


DEFAULT_MAXSIZE = 128


class DiscoveryCache:
    """One TTLCache per discovery tool. Lazy expiry; no background refresh.

    *ttl_config* maps tool name → TTL seconds (each product defines its own,
    tiered by how often the resource changes). A tool with no configured TTL
    is never cached (``get_or_fetch`` always calls ``fetch``).
    """

    def __init__(
        self,
        ttl_config: dict[str, int],
        maxsize: int = DEFAULT_MAXSIZE,
        timer=None,
    ):
        kwargs = {"timer": timer} if timer is not None else {}
        self._caches: dict[str, TTLCache] = {
            tool: TTLCache(maxsize=maxsize, ttl=ttl, **kwargs) for tool, ttl in ttl_config.items()
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
