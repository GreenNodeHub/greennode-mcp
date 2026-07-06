"""Tests for the discovery TTL cache."""

from __future__ import annotations

import pytest
from greennode.vks_mcp_server.discovery_cache import TTL_CONFIG, DiscoveryCache


@pytest.mark.asyncio
async def test_hit_returns_cached_without_refetch():
    cache = DiscoveryCache()
    calls = []

    async def fetch():
        calls.append(1)
        return f"result-{len(calls)}"

    k = ("list_vpcs", "HCM-3", "pro-1")
    first = await cache.get_or_fetch("list_vpcs", k, fetch)
    second = await cache.get_or_fetch("list_vpcs", k, fetch)
    assert first == "result-1"
    assert second == "result-1"  # served from cache
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_refresh_bypasses_and_overwrites():
    cache = DiscoveryCache()
    calls = []

    async def fetch():
        calls.append(1)
        return f"result-{len(calls)}"

    k = ("list_vpcs", "HCM-3", "pro-1")
    await cache.get_or_fetch("list_vpcs", k, fetch)
    refreshed = await cache.get_or_fetch("list_vpcs", k, fetch, refresh=True)
    assert refreshed == "result-2"
    assert len(calls) == 2
    # the refreshed value is now cached
    again = await cache.get_or_fetch("list_vpcs", k, fetch)
    assert again == "result-2"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_distinct_keys_do_not_collide():
    cache = DiscoveryCache()

    async def fetch_a():
        return "A"

    async def fetch_b():
        return "B"

    assert (
        await cache.get_or_fetch(
            "list_subnets", ("list_subnets", "HCM-3", "pro-1", "net-1"), fetch_a
        )
        == "A"
    )
    assert (
        await cache.get_or_fetch(
            "list_subnets", ("list_subnets", "HCM-3", "pro-1", "net-2"), fetch_b
        )
        == "B"
    )


@pytest.mark.asyncio
async def test_ttl_expiry_refetches():
    clock = {"t": 1000.0}
    cache = DiscoveryCache(ttl_config={"list_vpcs": 100}, timer=lambda: clock["t"])
    calls = []

    async def fetch():
        calls.append(1)
        return len(calls)

    k = ("list_vpcs", "HCM-3", "pro-1")
    assert await cache.get_or_fetch("list_vpcs", k, fetch) == 1
    clock["t"] += 50
    assert await cache.get_or_fetch("list_vpcs", k, fetch) == 1  # still fresh
    clock["t"] += 60  # now 110s elapsed > 100s ttl
    assert await cache.get_or_fetch("list_vpcs", k, fetch) == 2  # expired -> refetch


@pytest.mark.asyncio
async def test_unknown_tool_always_fetches():
    cache = DiscoveryCache()
    calls = []

    async def fetch():
        calls.append(1)
        return "x"

    await cache.get_or_fetch("not_a_tool", ("k",), fetch)
    await cache.get_or_fetch("not_a_tool", ("k",), fetch)
    assert len(calls) == 2  # no cache for unknown tool


def test_ttl_config_has_all_discovery_tools():
    for tool in [
        "list_vpcs",
        "list_subnets",
        "list_flavors",
        "list_ssh_keys",
        "list_security_groups",
    ]:
        assert tool in TTL_CONFIG
