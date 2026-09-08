"""Package/header catalogue on the vLB gateway, plus the vServer discovery proxies."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import PROJECT, SUBNET, VLB_HCM3, VSERVER_HCM3, mock_iam, paging_env
from greennode.vlb_mcp_server.catalogue_handler import CatalogueHandler
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.discovery_handler import DiscoveryHandler
from greennode.vlb_mcp_server.models import (
    PackageListData,
    ServerListData,
    SubnetListData,
    VpcListData,
    ZoneListData,
)
from mcp.server.mcpserver import MCPServer


PACKAGES = [
    {
        "uuid": "lbp-small",
        "name": "Small",
        "type": "PACKAGE",
        "connectionNumber": 1000,
        "dataTransfer": 100,
        "mode": "ACTIVE",
        "lbType": "Layer 7",
        "displayLbType": "Application Load Balancer",
    }
]


@pytest.fixture
def catalogue(config, client, cache):
    return CatalogueHandler(MCPServer("t"), config, client, cache)


@pytest.fixture
def discovery(config, client, cache):
    return DiscoveryHandler(MCPServer("t"), config, client, cache)


# --- packages -----------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_packages_active_hits_packages_with_zone(catalogue):
    mock_iam(respx.mock)
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/packages").mock(
        return_value=httpx.Response(200, json=paging_env(PACKAGES))
    )
    result = await catalogue.list_load_balancer_packages(
        scope="active", zone_id="HCM03-1A", region="HCM-3", refresh=False
    )
    assert isinstance(result, PackageListData)
    assert result.packages[0].id == "lbp-small"
    assert result.packages[0].connection_number == 1000
    assert dict(route.calls[0].request.url.params) == {"zoneId": "HCM03-1A"}


@respx.mock
@pytest.mark.asyncio
async def test_list_packages_all_uses_all_packages_and_sends_no_zone(catalogue):
    """/all_packages takes no zoneId — sending one risks a 400 on an unfiltered catalogue."""
    mock_iam(respx.mock)
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/all_packages").mock(
        return_value=httpx.Response(200, json=paging_env(PACKAGES))
    )
    await catalogue.list_load_balancer_packages(
        scope="all", zone_id="HCM03-1A", region="HCM-3", refresh=False
    )
    assert route.called
    assert dict(route.calls[0].request.url.params) == {}


@respx.mock
@pytest.mark.asyncio
async def test_list_packages_omits_zone_when_not_given(catalogue):
    mock_iam(respx.mock)
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/packages").mock(
        return_value=httpx.Response(200, json=paging_env(PACKAGES))
    )
    await catalogue.list_load_balancer_packages(
        scope="active", zone_id=None, region="HCM-3", refresh=False
    )
    assert dict(route.calls[0].request.url.params) == {}


@respx.mock
@pytest.mark.asyncio
async def test_list_packages_is_cached_and_refresh_bypasses(catalogue):
    mock_iam(respx.mock)
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/packages").mock(
        return_value=httpx.Response(200, json=paging_env(PACKAGES))
    )
    args = {"scope": "active", "zone_id": None, "region": "HCM-3"}
    await catalogue.list_load_balancer_packages(refresh=False, **args)
    await catalogue.list_load_balancer_packages(refresh=False, **args)
    assert route.call_count == 1
    await catalogue.list_load_balancer_packages(refresh=True, **args)
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_package_scopes_are_cached_separately(catalogue):
    mock_iam(respx.mock)
    active = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/packages").mock(
        return_value=httpx.Response(200, json=paging_env(PACKAGES))
    )
    allp = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/all_packages").mock(
        return_value=httpx.Response(200, json=paging_env(PACKAGES))
    )
    await catalogue.list_load_balancer_packages(
        scope="active", zone_id=None, region="HCM-3", refresh=False
    )
    await catalogue.list_load_balancer_packages(
        scope="all", zone_id=None, region="HCM-3", refresh=False
    )
    assert active.called and allp.called


@respx.mock
@pytest.mark.asyncio
async def test_list_listener_headers(catalogue):
    mock_iam(respx.mock)
    respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/headers").mock(
        return_value=httpx.Response(200, json={"data": ["X-Forwarded-For", "X-Forwarded-Proto"]})
    )
    result = await catalogue.list_listener_headers(region="HCM-3", refresh=False)
    assert result.headers == ["X-Forwarded-For", "X-Forwarded-Proto"]


# --- vServer discovery proxies -------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_zones_hits_the_vserver_gateway(discovery):
    mock_iam(respx.mock)
    vserver = respx.get(f"{VSERVER_HCM3}/v1/{PROJECT}/zones").mock(
        return_value=httpx.Response(
            200, json={"data": [{"uuid": "HCM03-1A", "name": "HCM03-1A", "isEnabled": True}]}
        )
    )
    vlb = respx.get(f"{VLB_HCM3}/v1/{PROJECT}/zones").mock(return_value=httpx.Response(500))

    result = await discovery.list_zones(region="HCM-3", refresh=False)
    assert isinstance(result, ZoneListData)
    assert result.zones[0].id == "HCM03-1A"
    assert vserver.called and not vlb.called


@respx.mock
@pytest.mark.asyncio
async def test_list_vpcs(discovery):
    mock_iam(respx.mock)
    respx.get(f"{VSERVER_HCM3}/v2/{PROJECT}/networks").mock(
        return_value=httpx.Response(
            200,
            json=paging_env(
                [
                    {
                        "uuid": "vpc-1",
                        "displayName": "prod",
                        "cidr": "10.0.0.0/16",
                        "status": "ACTIVE",
                    }
                ]
            ),
        )
    )
    result = await discovery.list_vpcs(region="HCM-3", refresh=False)
    assert isinstance(result, VpcListData)
    assert (result.vpcs[0].id, result.vpcs[0].name) == ("vpc-1", "prod")


@respx.mock
@pytest.mark.asyncio
async def test_list_subnets(discovery):
    mock_iam(respx.mock)
    respx.get(f"{VSERVER_HCM3}/v2/{PROJECT}/networks/vpc-1/subnets").mock(
        return_value=httpx.Response(
            200,
            json=paging_env(
                [
                    {
                        "uuid": SUBNET,
                        "name": "app",
                        "cidr": "10.0.1.0/24",
                        "status": "ACTIVE",
                        "networkUuid": "vpc-1",
                    }
                ]
            ),
        )
    )
    result = await discovery.list_subnets(vpc_id="vpc-1", region="HCM-3", refresh=False)
    assert isinstance(result, SubnetListData)
    assert result.subnets[0].id == SUBNET
    assert result.vpc_id == "vpc-1"


@pytest.mark.asyncio
async def test_list_subnets_validates_the_vpc_id(discovery):
    with pytest.raises(ValueError, match="Invalid vpc_id"):
        await discovery.list_subnets(vpc_id="../secrets", region="HCM-3", refresh=False)


@respx.mock
@pytest.mark.asyncio
async def test_list_servers_exposes_the_private_ip_a_member_needs(discovery):
    mock_iam(respx.mock)
    respx.get(f"{VSERVER_HCM3}/v2/{PROJECT}/servers").mock(
        return_value=httpx.Response(
            200,
            json=paging_env(
                [
                    {
                        "uuid": "srv-1",
                        "name": "web-01",
                        "status": "ACTIVE",
                        "internalInterfaces": [{"fixedIp": "10.0.1.11", "floatingIp": ""}],
                    }
                ]
            ),
        )
    )
    result = await discovery.list_servers(region="HCM-3", refresh=False)
    assert isinstance(result, ServerListData)
    assert result.servers[0].private_ip == "10.0.1.11"


# --- registration & caching policy ---------------------------------------------


@pytest.mark.asyncio
async def test_catalogue_and_discovery_register_only_read_tools(catalogue, discovery):
    assert {t.name for t in await catalogue.mcp.list_tools()} == {
        "list_load_balancer_packages",
        "list_listener_headers",
    }
    assert {t.name for t in await discovery.mcp.list_tools()} == {
        "list_zones",
        "list_vpcs",
        "list_subnets",
        "list_servers",
    }


@respx.mock
@pytest.mark.asyncio
async def test_a_tool_without_a_ttl_is_never_cached(config, client):
    """DiscoveryCache with no TTLs must let every call through — the RMW invariant."""
    mock_iam(respx.mock)
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/headers").mock(
        return_value=httpx.Response(200, json={"data": ["X-Forwarded-For"]})
    )
    handler = CatalogueHandler(MCPServer("t"), config, client, DiscoveryCache(ttl_config={}))
    await handler.list_listener_headers(region="HCM-3", refresh=False)
    await handler.list_listener_headers(region="HCM-3", refresh=False)
    assert route.call_count == 2
