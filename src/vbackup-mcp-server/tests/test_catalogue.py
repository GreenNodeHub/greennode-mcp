"""Catalogue tools: backends, platform configuration, protected servers.

Backup destinations moved to ``test_destinations.py`` with their handler.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import API_BASE, HAN_BASE, RAW_CONFIGURATION, envelope, mock_iam
from greennode.vbackup_mcp_server.catalogue_handler import CatalogueHandler
from greennode.vbackup_mcp_server.models import BackendListData
from mcp.server.mcpserver import MCPServer


@pytest.fixture
def handler(config, client, cache):
    return CatalogueHandler(MCPServer("test"), config, client, cache)


@pytest.fixture
def handler_uncached(config, client, no_cache):
    return CatalogueHandler(MCPServer("test"), config, client, no_cache)


@pytest.mark.asyncio
async def test_tools_registered_read_only(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    for name in ("list_backends", "get_configuration", "list_protected_servers"):
        assert name in tools
        assert tools[name].annotations.read_only_hint is True


@respx.mock
@pytest.mark.asyncio
async def test_list_backends_structured(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backends").mock(
        return_value=httpx.Response(200, json=envelope([{"id": "be-0001", "name": "HCM-03"}]))
    )
    result = await handler.list_backends(region="HCM-3", refresh=False)
    assert isinstance(result, BackendListData)
    assert [(b.id, b.name) for b in result.backends] == [("be-0001", "HCM-03")]


@respx.mock
@pytest.mark.asyncio
async def test_backends_cached_and_refresh_bypasses(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/backends").mock(
        return_value=httpx.Response(200, json=envelope([{"id": "be-0001", "name": "HCM-03"}]))
    )
    await handler.list_backends(region="HCM-3", refresh=False)
    await handler.list_backends(region="HCM-3", refresh=False)
    assert route.call_count == 1
    await handler.list_backends(region="HCM-3", refresh=True)
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_regions_cached_separately(handler):
    """A cache keyed without the region would serve HCM-3 results for HAN."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backends").mock(
        return_value=httpx.Response(200, json=envelope([{"id": "be-0001", "name": "HCM-03"}]))
    )
    respx.get(f"{HAN_BASE}/v1/backends").mock(
        return_value=httpx.Response(200, json=envelope([{"id": "be-0002", "name": "HAN-01"}]))
    )
    hcm = await handler.list_backends(region="HCM-3", refresh=False)
    han = await handler.list_backends(region="HAN", refresh=False)
    assert hcm.backends[0].name == "HCM-03"
    assert han.backends[0].name == "HAN-01"


@respx.mock
@pytest.mark.asyncio
async def test_configuration_reports_only_open_hours(handler_uncached):
    """Hours the platform disabled must not be offered for a new policy."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/configurations").mock(
        return_value=httpx.Response(200, json=RAW_CONFIGURATION)
    )
    result = await handler_uncached.get_configuration(region="HCM-3", refresh=False)
    assert result.backup_policy_hours == ["1:00", "13:00"]
    assert "00:00" not in result.backup_policy_hours
    assert "12:00" not in result.backup_policy_hours


@respx.mock
@pytest.mark.asyncio
async def test_configuration_keeps_backup_and_snapshot_limits_apart(handler_uncached):
    """Validating a backup policy against snapshot limits would allow 1h intervals."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/configurations").mock(
        return_value=httpx.Response(200, json=RAW_CONFIGURATION)
    )
    result = await handler_uncached.get_configuration(region="HCM-3", refresh=False)
    assert result.backup_policy_hourly_intervals == [4, 6, 8, 12]
    assert result.snapshot_policy_hourly_intervals == [1, 2, 4, 6, 8, 12]
    assert result.backup_policy_retention_limits.daily == 30000
    assert result.snapshot_policy_retention_limits.daily == 64
    assert result.allowed_backup_server_status == ["ACTIVE", "STOPPED"]


@respx.mock
@pytest.mark.asyncio
async def test_protected_servers_reads_the_ids_key(handler_uncached):
    """This endpoint answers {"ids": [...]}, not the usual items envelope."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instances/protected-servers").mock(
        return_value=httpx.Response(200, json={"ids": ["ins-0001", "ins-0002"]})
    )
    result = await handler_uncached.list_protected_servers(
        region="HCM-3", backend_id=None, refresh=False
    )
    assert result.total == 2
    assert result.server_ids == ["ins-0001", "ins-0002"]


@respx.mock
@pytest.mark.asyncio
async def test_malformed_backend_id_rejected(handler_uncached):
    with pytest.raises(ValueError, match="backend_id"):
        await handler_uncached.list_protected_servers(
            region="HCM-3", backend_id="../etc", refresh=False
        )
