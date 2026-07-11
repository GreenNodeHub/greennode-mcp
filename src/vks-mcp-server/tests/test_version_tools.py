"""Tests for version and image tools."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.models import VersionsData
from greennode.vks_mcp_server.version_handler import VersionHandler, _cluster_versions_list


VKS_BASE = "https://vks.api.vngcloud.vn"
IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(
            200,
            json={"accessToken": "mocked-token", "expiresIn": 1800},
        )
    )


@pytest.fixture
def config_and_client(sample_config):
    config = load_config(sample_config)
    token_manager = TokenManager(config)
    client = VksClient(config, token_manager)
    return config, client


CLUSTER_VERSIONS_RESPONSE = {
    "items": [
        {"version": "v1.29.0", "stage": "STABLE", "deprecatedAt": None, "enable": True},
        {"version": "v1.28.0", "stage": "STABLE", "deprecatedAt": "2024-06-01", "enable": True},
        {"version": "v1.27.0", "stage": "STABLE", "deprecatedAt": None, "enable": False},
    ]
}


@respx.mock
@pytest.mark.asyncio
async def test_cluster_versions_list(config_and_client):
    """Enabled versions appear; disabled versions do not; recommendation is marked."""
    config, client = config_and_client
    _mock_iam(respx.mock)
    respx.get(f"{VKS_BASE}/v1/cluster-versions").mock(
        return_value=httpx.Response(200, json=CLUSTER_VERSIONS_RESPONSE),
    )
    result = await _cluster_versions_list(config, client, region=None)
    assert isinstance(result, VersionsData)
    version_strings = [v.version for v in result.versions]
    assert "v1.29.0" in version_strings
    assert "v1.28.0" in version_strings
    assert "v1.27.0" not in version_strings
    assert any(v.recommended for v in result.versions)
    assert result.recommended


CLUSTER_VERSIONS_BARE_ARRAY = [
    {"version": "v1.30.0", "stage": "STABLE", "deprecatedAt": None, "enable": True},
    {"version": "v1.29.0", "stage": "RAPID", "deprecatedAt": None, "enable": True},
]


@respx.mock
@pytest.mark.asyncio
async def test_cluster_versions_list_bare_array(config_and_client):
    """A top-level array response must not crash with 'list' object has no attribute 'get'."""
    config, client = config_and_client
    _mock_iam(respx.mock)
    respx.get(f"{VKS_BASE}/v1/cluster-versions").mock(
        return_value=httpx.Response(200, json=CLUSTER_VERSIONS_BARE_ARRAY),
    )
    result = await _cluster_versions_list(config, client, region=None)
    assert isinstance(result, VersionsData)
    version_strings = [v.version for v in result.versions]
    assert "v1.30.0" in version_strings
    assert "v1.29.0" in version_strings


# ---------------------------------------------------------------------------
# Cache call-count tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_mcp():
    """Minimal stub for the FastMCP server object used by VersionHandler."""

    class _FakeMcp:
        def tool(self, *, name, annotations=None):
            def decorator(fn):
                return fn

            return decorator

    return _FakeMcp()


@respx.mock
@pytest.mark.asyncio
async def test_cluster_versions_list_cached(config_and_client, mock_mcp):
    """Two successive calls hit the VKS endpoint only once (cache hit on second call)."""
    config, client = config_and_client
    _mock_iam(respx.mock)
    versions_route = respx.get(f"{VKS_BASE}/v1/cluster-versions").mock(
        return_value=httpx.Response(200, json=CLUSTER_VERSIONS_RESPONSE),
    )

    cache = DiscoveryCache()
    handler = VersionHandler(mock_mcp, config, client, cache)

    result1 = await handler.list_cluster_versions(region=None, refresh=False)
    result2 = await handler.list_cluster_versions(region=None, refresh=False)

    assert versions_route.call_count == 1, (
        f"Expected 1 VKS call (cache hit on second), got {versions_route.call_count}"
    )
    assert isinstance(result1, VersionsData)
    assert any(v.recommended for v in result1.versions)
    assert result1.recommended
    assert result1 == result2


@respx.mock
@pytest.mark.asyncio
async def test_cluster_versions_list_refresh(config_and_client, mock_mcp):
    """refresh=True bypasses the cache and hits the endpoint a second time."""
    config, client = config_and_client
    _mock_iam(respx.mock)
    versions_route = respx.get(f"{VKS_BASE}/v1/cluster-versions").mock(
        return_value=httpx.Response(200, json=CLUSTER_VERSIONS_RESPONSE),
    )

    cache = DiscoveryCache()
    handler = VersionHandler(mock_mcp, config, client, cache)

    await handler.list_cluster_versions(region=None, refresh=False)
    await handler.list_cluster_versions(region=None, refresh=True)

    assert versions_route.call_count == 2, (
        f"Expected 2 VKS calls (refresh bypasses cache), got {versions_route.call_count}"
    )


@respx.mock
@pytest.mark.asyncio
async def test_cluster_versions_list_structured(config_and_client, mock_mcp):
    """list_cluster_versions returns VersionsData with recommended marked."""
    config, client = config_and_client
    _mock_iam(respx.mock)
    respx.get(f"{VKS_BASE}/v1/cluster-versions").mock(
        return_value=httpx.Response(200, json=CLUSTER_VERSIONS_RESPONSE),
    )

    cache = DiscoveryCache()
    handler = VersionHandler(mock_mcp, config, client, cache)

    result = await handler.list_cluster_versions(region=None, refresh=False)
    assert isinstance(result, VersionsData)
    assert any(v.recommended for v in result.versions)
    assert result.recommended
