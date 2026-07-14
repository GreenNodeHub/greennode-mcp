"""Tests for discovery tools."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.discovery_handler import (
    _fetch_all_items,
    _flavor_list,
    _placementgroup_list,
    _quota_get,
    _require_project_id,
    _secgroup_list,
    _sshkey_list,
    _subnet_list,
    _suggest_group,
    _volumetype_list,
    _vpc_list,
)
from greennode.vks_mcp_server.models import (
    FlavorListData,
    PlacementGroupListData,
    QuotaData,
    SecgroupListData,
    SshKeyListData,
    SubnetListData,
    VolumeTypeListData,
    VpcListData,
)


VSERVER_BASE = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
PID = "pro-test-0001"


def _mock_iam(mock):
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VksClient(config, TokenManager(config))


@respx.mock
@pytest.mark.asyncio
async def test_vpc_list_returns_structured(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/networks").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {
                        "id": "net-1",
                        "displayName": "prod-vpc",
                        "cidr": "10.0.0.0/16",
                        "status": "ACTIVE",
                    }
                ],
                "totalItem": 1,
            },
        )
    )
    result = await _vpc_list(config, client, DiscoveryCache())
    assert isinstance(result, VpcListData)
    assert result.region  # region populated
    assert result.vpcs[0].id == "net-1"
    assert result.vpcs[0].name == "prod-vpc"


@respx.mock
@pytest.mark.asyncio
async def test_vpc_list_returns_only_active(config, client):
    """Non-ACTIVE VPCs are filtered out — they cannot host new clusters."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/networks").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {"id": "net-ok", "displayName": "prod", "status": "ACTIVE"},
                    {"id": "net-mid", "displayName": "half-baked", "status": "CREATING"},
                    {"id": "net-bad", "displayName": "gone", "status": "DELETING"},
                ]
            },
        )
    )
    result = await _vpc_list(config, client, DiscoveryCache())
    assert [v.id for v in result.vpcs] == ["net-ok"]


@respx.mock
@pytest.mark.asyncio
async def test_vpc_list_empty(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/networks").mock(
        return_value=httpx.Response(200, json={"listData": []})
    )
    result = await _vpc_list(config, client, DiscoveryCache())
    assert isinstance(result, VpcListData)
    assert result.vpcs == []


@respx.mock
@pytest.mark.asyncio
async def test_subnet_list_returns_structured(config, client):
    _mock_iam(respx.mock)
    vpc_id = "net-1"
    respx.get(f"{VSERVER_BASE}/v2/{PID}/networks/{vpc_id}/subnets").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"uuid": "sub-1", "name": "subnet-a", "cidr": "10.0.1.0/24", "status": "ACTIVE"}
            ],
        )
    )
    result = await _subnet_list(config, client, DiscoveryCache(), vpc_id=vpc_id)
    assert isinstance(result, SubnetListData)
    assert result.vpc_id == vpc_id
    assert result.subnets[0].id == "sub-1"
    assert result.subnets[0].name == "subnet-a"


@respx.mock
@pytest.mark.asyncio
async def test_subnet_list_returns_only_active(config, client):
    """Non-ACTIVE subnets are filtered out — nodes cannot join them."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/networks/net-1/subnets").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"uuid": "sub-ok", "name": "a", "status": "ACTIVE"},
                {"uuid": "sub-mid", "name": "b", "status": "CREATING"},
            ],
        )
    )
    result = await _subnet_list(config, client, DiscoveryCache(), vpc_id="net-1")
    assert [s.id for s in result.subnets] == ["sub-ok"]


@pytest.mark.asyncio
async def test_subnet_list_rejects_bad_vpc_id(config, client):
    with pytest.raises(ValueError):
        await _subnet_list(config, client, DiscoveryCache(), vpc_id="bad id/../x")


def test_suggest_group_classifies():
    assert _suggest_group({"cpu": 2, "memory": 4, "gpu": 1}) == "AI/GPU"
    assert _suggest_group({"cpu": 2, "memory": 4, "gpu": 0}) == "Dev/test"
    assert _suggest_group({"cpu": 8, "memory": 16, "gpu": 0}) == "Compute"
    assert _suggest_group({"cpu": 4, "memory": 32, "gpu": 0}) == "RAM cao"
    assert _suggest_group({"cpu": 4, "memory": 8, "gpu": 0}) == "Cân bằng"


FZ_PATH = f"{VSERVER_BASE}/v1/{PID}/flavor_zones/customs/clusters/master/false"


def _mock_flavor_zone(flavors):
    """Mock the two-step worker-flavor flow: flavor_zones (master=false) -> flavors."""
    respx.get(FZ_PATH).mock(
        return_value=httpx.Response(200, json={"listData": [{"id": "fz-1", "name": "z1"}]})
    )
    respx.get(f"{VSERVER_BASE}/v1/{PID}/fz-1/flavors").mock(
        return_value=httpx.Response(200, json={"listData": flavors})
    )


@respx.mock
@pytest.mark.asyncio
async def test_flavor_list_worker_flavors_by_zone(config, client):
    """Resolve the worker (master=false) flavor zone for the subnet's AZ, list its flavors."""
    _mock_iam(respx.mock)
    _mock_flavor_zone(
        [
            {
                "flavorId": "flv-1",
                "name": "2c_4g",
                "cpu": 2,
                "memory": 4,
                "gpu": 0,
                "remainingVms": 5,
            },
            {
                "flavorId": "flv-2",
                "name": "8c_16g",
                "cpu": 8,
                "memory": 16,
                "gpu": 0,
                "remainingVms": 3,
            },
        ]
    )
    result = await _flavor_list(config, client, DiscoveryCache(), zone="HCM03-1A")
    assert isinstance(result, FlavorListData)
    assert result.region == config.default_region
    assert result.zone == "HCM03-1A"
    assert result.need is None
    assert {f.id for f in result.flavors} == {"flv-1", "flv-2"}
    assert {f.group for f in result.flavors} == {"Dev/test", "Compute"}


@respx.mock
@pytest.mark.asyncio
async def test_flavor_list_excludes_sold_out(config, client):
    """Sold-out flavors (isSoldOut or remainingVms == 0) are filtered out."""
    _mock_iam(respx.mock)
    _mock_flavor_zone(
        [
            {"flavorId": "ok", "name": "a", "cpu": 2, "memory": 4, "gpu": 0, "remainingVms": 5},
            {
                "flavorId": "none-left",
                "name": "b",
                "cpu": 2,
                "memory": 4,
                "gpu": 0,
                "remainingVms": 0,
            },
            {
                "flavorId": "flagged",
                "name": "c",
                "cpu": 2,
                "memory": 4,
                "gpu": 0,
                "remainingVms": 9,
                "isSoldOut": True,
            },
        ]
    )
    result = await _flavor_list(config, client, DiscoveryCache(), zone="HCM03-1A")
    assert [f.id for f in result.flavors] == ["ok"]


@respx.mock
@pytest.mark.asyncio
async def test_flavor_list_filters_by_need(config, client):
    _mock_iam(respx.mock)
    _mock_flavor_zone(
        [
            {
                "flavorId": "flv-1",
                "name": "2c_4g",
                "cpu": 2,
                "memory": 4,
                "gpu": 0,
                "remainingVms": 5,
            },
            {
                "flavorId": "flv-2",
                "name": "8c_16g",
                "cpu": 8,
                "memory": 16,
                "gpu": 0,
                "remainingVms": 5,
            },
        ]
    )
    result = await _flavor_list(config, client, DiscoveryCache(), zone="HCM03-1A", need="Compute")
    assert result.need == "Compute"
    assert [f.id for f in result.flavors] == ["flv-2"]


@respx.mock
@pytest.mark.asyncio
async def test_sshkey_list_returns_structured(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/sshKeys").mock(
        return_value=httpx.Response(
            200,
            json={"listData": [{"id": "ssh-1", "name": "my-key"}], "totalItem": 1},
        )
    )
    result = await _sshkey_list(config, client, DiscoveryCache())
    assert isinstance(result, SshKeyListData)
    assert result.region == config.default_region  # echoes the region actually queried
    assert result.ssh_keys[0].id == "ssh-1"
    assert result.ssh_keys[0].name == "my-key"


@respx.mock
@pytest.mark.asyncio
async def test_sshkey_list_empty(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/sshKeys").mock(
        return_value=httpx.Response(200, json={"listData": []})
    )
    result = await _sshkey_list(config, client, DiscoveryCache())
    assert isinstance(result, SshKeyListData)
    assert result.ssh_keys == []


@respx.mock
@pytest.mark.asyncio
async def test_secgroup_list_returns_structured(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/secgroups").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {
                        "id": "secg-1",
                        "name": "default",
                        "description": "default sg",
                        "status": "ACTIVE",
                    }
                ]
            },
        )
    )
    result = await _secgroup_list(config, client, DiscoveryCache())
    assert isinstance(result, SecgroupListData)
    assert result.region == config.default_region  # echoes the region queried
    assert result.secgroups[0].model_dump() == {"id": "secg-1", "name": "default"}


@respx.mock
@pytest.mark.asyncio
async def test_secgroup_list_returns_only_active(config, client):
    """Non-ACTIVE security groups are filtered out."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/secgroups").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {"id": "sg-ok", "name": "a", "status": "ACTIVE"},
                    {"id": "sg-bad", "name": "b", "status": "CREATING"},
                ]
            },
        )
    )
    result = await _secgroup_list(config, client, DiscoveryCache())
    assert [g.id for g in result.secgroups] == ["sg-ok"]


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_uses_configured(config, client):
    """A configured project_id is returned without calling vServer."""
    config.project_id = "pro-test-0001"
    pid = await _require_project_id(config, client, region=None)
    assert pid == "pro-test-0001"


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_autodiscovers(config, client):
    """When project_id is unset, it is fetched from /v1/projects and cached."""
    config.project_id = None
    _mock_iam(respx.mock)
    route = respx.get(f"{VSERVER_BASE}/v1/projects").mock(
        return_value=httpx.Response(
            200, json={"projects": [{"projectId": "pro-disc-9999", "userId": "u1"}]}
        )
    )
    pid = await _require_project_id(config, client, region=None)
    assert pid == "pro-disc-9999"
    assert config.project_id == "pro-disc-9999"  # cached
    # second call must not hit the API again
    pid2 = await _require_project_id(config, client, region=None)
    assert pid2 == "pro-disc-9999"
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_no_project_errors(config, client):
    """An empty project list yields a clear error."""
    config.project_id = None
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v1/projects").mock(
        return_value=httpx.Response(200, json={"projects": []})
    )
    with pytest.raises(ValueError, match="project_id"):
        await _require_project_id(config, client, region=None)


# ---------------------------------------------------------------------------
# Cache-behaviour tests
# ---------------------------------------------------------------------------


@pytest.fixture
def cache():
    return DiscoveryCache()


@respx.mock
@pytest.mark.asyncio
async def test_vpc_list_caches_second_call(config, client, cache):
    _mock_iam(respx.mock)
    route = respx.get(f"{VSERVER_BASE}/v2/{PID}/networks").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {"id": "net-1", "displayName": "v", "cidr": "10.0.0.0/16", "status": "ACTIVE"}
                ]
            },
        )
    )
    r1 = await _vpc_list(config, client, cache)
    r2 = await _vpc_list(config, client, cache)
    assert r1 == r2
    assert route.call_count == 1  # second call served from cache


@respx.mock
@pytest.mark.asyncio
async def test_vpc_list_refresh_refetches(config, client, cache):
    _mock_iam(respx.mock)
    route = respx.get(f"{VSERVER_BASE}/v2/{PID}/networks").mock(
        return_value=httpx.Response(200, json={"listData": []})
    )
    await _vpc_list(config, client, cache)
    await _vpc_list(config, client, cache, refresh=True)
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_subnet_list_cache_keyed_by_vpc(config, client, cache):
    _mock_iam(respx.mock)
    r1 = respx.get(f"{VSERVER_BASE}/v2/{PID}/networks/net-1/subnets").mock(
        return_value=httpx.Response(
            200, json=[{"uuid": "s1", "name": "a", "cidr": "10.0.1.0/24", "status": "ACTIVE"}]
        )
    )
    r2 = respx.get(f"{VSERVER_BASE}/v2/{PID}/networks/net-2/subnets").mock(
        return_value=httpx.Response(
            200, json=[{"uuid": "s2", "name": "b", "cidr": "10.0.2.0/24", "status": "ACTIVE"}]
        )
    )
    await _subnet_list(config, client, cache, vpc_id="net-1")
    await _subnet_list(config, client, cache, vpc_id="net-1")  # cached
    await _subnet_list(config, client, cache, vpc_id="net-2")  # different key -> fetch
    assert r1.call_count == 1
    assert r2.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_flavor_list_cache_keyed_by_need(config, client, cache):
    _mock_iam(respx.mock)
    respx.get(FZ_PATH).mock(return_value=httpx.Response(200, json={"listData": [{"id": "fz-1"}]}))
    route = respx.get(f"{VSERVER_BASE}/v1/{PID}/fz-1/flavors").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {"flavorId": "f1", "cpu": 2, "memory": 4, "gpu": 0, "remainingVms": 5}
                ]
            },
        )
    )
    await _flavor_list(config, client, cache, zone="HCM03-1A")
    await _flavor_list(config, client, cache, zone="HCM03-1A")  # cached (need=None)
    await _flavor_list(config, client, cache, zone="HCM03-1A", need="Dev/test")  # different key
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_volumetype_list_picks_nvme_zone_only(config, client, cache):
    """Given a subnet's AZ zone, resolve the NVME volume-type-zone and list its types."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v1/{PID}/volume_type_zones").mock(
        return_value=httpx.Response(
            200,
            json={
                "volumeTypeZones": [
                    {"id": "vtz-ssd", "name": "SSD"},
                    {"id": "vtz-nvme", "name": "NVME"},
                ]
            },
        )
    )
    ssd_route = respx.get(f"{VSERVER_BASE}/v1/{PID}/vtz-ssd/volume_types").mock(
        return_value=httpx.Response(200, json={"volumeTypes": [{"id": "vt-ssd", "iops": 1}]})
    )
    respx.get(f"{VSERVER_BASE}/v1/{PID}/vtz-nvme/volume_types").mock(
        return_value=httpx.Response(
            200,
            json={
                "volumeTypes": [
                    {
                        "id": "vtype-3k",
                        "name": "3000",
                        "iops": 3000,
                        "minSize": 1,
                        "maxSize": 30000,
                    },
                    {"id": "vtype-5k", "name": "5000", "iops": 5000},
                ]
            },
        )
    )
    result = await _volumetype_list(config, client, cache, zone="HCM03-1A")
    assert isinstance(result, VolumeTypeListData)
    assert result.zone == "HCM03-1A"
    assert result.region == config.default_region
    assert not ssd_route.called  # SSD zone is skipped; NVME hardcoded
    assert [v.model_dump() for v in result.volume_types] == [
        {"id": "vtype-3k", "iops": 3000},
        {"id": "vtype-5k", "iops": 5000},
    ]


@respx.mock
@pytest.mark.asyncio
async def test_volumetype_list_scopes_zones_by_subnet_zone(config, client, cache):
    """The volume_type_zones lookup is filtered by the subnet's zone uuid."""
    _mock_iam(respx.mock)
    route = respx.get(f"{VSERVER_BASE}/v1/{PID}/volume_type_zones").mock(
        return_value=httpx.Response(
            200, json={"volumeTypeZones": [{"id": "vtz-nvme", "name": "NVME"}]}
        )
    )
    respx.get(f"{VSERVER_BASE}/v1/{PID}/vtz-nvme/volume_types").mock(
        return_value=httpx.Response(200, json={"volumeTypes": []})
    )
    await _volumetype_list(config, client, cache, zone="HCM03-1B")
    assert route.calls.last.request.url.params.get("zoneId") == "HCM03-1B"


@respx.mock
@pytest.mark.asyncio
async def test_quota_get_returns_structured(config, client):
    """get_quota returns QuotaData from the VKS /v1/quota endpoint."""
    _mock_iam(respx.mock)
    respx.get("https://vks.api.vngcloud.vn/v1/quota").mock(
        return_value=httpx.Response(
            202,
            json={
                "maxClusters": 10,
                "numClusters": 3,
                "maxNodeGroupsPerCluster": 5,
                "maxNodesPerNodeGroup": 100,
            },
        )
    )
    result = await _quota_get(client, region="HCM-3")
    assert isinstance(result, QuotaData)
    assert result.region == "HCM-3"  # echoes the region queried
    assert result.max_clusters == 10
    assert result.num_clusters == 3


@respx.mock
@pytest.mark.asyncio
async def test_placementgroup_list_returns_structured(config, client, cache):
    """list_placement_groups maps vServer serverGroups; uuid is the placementGroupId."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/serverGroups").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {
                        "uuid": "sg-uuid-1",
                        "name": "pg-web",
                        "policyId": "pol-1",
                        "policyName": "AFFINITY",
                        "description": "web tier",
                        "serverGroupId": 7,
                    }
                ],
                "totalItem": 1,
            },
        )
    )
    result = await _placementgroup_list(config, client, cache)
    assert isinstance(result, PlacementGroupListData)
    pg = result.placement_groups[0]
    assert pg.id == "sg-uuid-1"  # uuid, not the integer serverGroupId
    assert pg.name == "pg-web"
    assert pg.model_dump() == {"id": "sg-uuid-1", "name": "pg-web"}  # minimal projection


# ---------------------------------------------------------------------------
# Pagination safety net: _fetch_all_items
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_single_call_when_server_returns_all(config, client):
    """vServer's normal behaviour: one response holds everything (totalPage=0)."""
    _mock_iam(respx.mock)
    route = respx.get(f"{VSERVER_BASE}/v2/{PID}/things").mock(
        return_value=httpx.Response(
            200,
            json={"listData": [{"id": 1}, {"id": 2}], "totalItem": 2, "totalPage": 0},
        )
    )
    items = await _fetch_all_items(client, f"/v2/{PID}/things")
    assert [i["id"] for i in items] == [1, 2]
    assert route.call_count == 1  # no extra pages fetched


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_paginates_when_truncated(config, client):
    """If a response reports more items than returned, page through the rest."""
    _mock_iam(respx.mock)

    def responder(request):
        page = request.url.params.get("page")  # None on the initial unpaged call
        pages = {
            None: {"listData": [{"id": 1}, {"id": 2}], "totalItem": 5},  # looks truncated
            "1": {"listData": [{"id": 1}, {"id": 2}], "totalItem": 5},
            "2": {"listData": [{"id": 3}, {"id": 4}], "totalItem": 5},
            "3": {"listData": [{"id": 5}], "totalItem": 5},
        }
        return httpx.Response(200, json=pages[page])

    # first (unpaged) call also looks truncated -> triggers pagination from page 1
    respx.get(f"{VSERVER_BASE}/v2/{PID}/things").mock(side_effect=responder)
    items = await _fetch_all_items(client, f"/v2/{PID}/things", page_size=2)
    assert [i["id"] for i in items] == [1, 2, 3, 4, 5]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_handles_bare_list(config, client):
    """A non-enveloped (bare array) response is returned as-is."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v2/{PID}/things").mock(
        return_value=httpx.Response(200, json=[{"id": "a"}])
    )
    items = await _fetch_all_items(client, f"/v2/{PID}/things")
    assert items == [{"id": "a"}]


# ---------------------------------------------------------------------------
# project_id is region-scoped
# ---------------------------------------------------------------------------

HAN_VSERVER = "https://han-1.api.vngcloud.vn/vserver/vserver-gateway"


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_is_per_region(config, client):
    """Each region has its own project_id; the configured one is HCM-3-only."""
    _mock_iam(respx.mock)
    config.project_id = "pro-hcm"  # configured value belongs to the default region (HCM-3)
    respx.get(f"{HAN_VSERVER}/v1/projects").mock(
        return_value=httpx.Response(200, json={"projects": [{"projectId": "pro-han"}]})
    )
    # default region → configured value, no fetch
    assert await _require_project_id(config, client) == "pro-hcm"
    # HAN → fetched from the HAN endpoint, not the HCM-3 configured id
    assert await _require_project_id(config, client, region="HAN") == "pro-han"


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_caches_per_region(config, client):
    """A region's project_id is fetched once, then cached (no configured default)."""
    _mock_iam(respx.mock)
    config.project_id = None
    route = respx.get(f"{HAN_VSERVER}/v1/projects").mock(
        return_value=httpx.Response(200, json={"projects": [{"projectId": "pro-han"}]})
    )
    assert await _require_project_id(config, client, region="HAN") == "pro-han"
    assert await _require_project_id(config, client, region="HAN") == "pro-han"
    assert route.call_count == 1  # cached, not refetched


# ---------------------------------------------------------------------------
# _resolve_zone_context: derive (region, zone) from cluster_id + subnet_id
# ---------------------------------------------------------------------------

VKS_BASE_HCM = "https://vks.api.vngcloud.vn"
VKS_BASE_HAN = "https://vks-han-1.api.vngcloud.vn"
VSERVER_BASE_HAN = "https://han-1.api.vngcloud.vn/vserver/vserver-gateway"


def _mock_cluster(base, cluster_id, vpc_id, status=200):
    body = {"uid": cluster_id, "vpcId": vpc_id} if status == 200 else {"message": "not found"}
    return respx.get(f"{base}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(status, json=body)
    )


def _mock_subnets(vserver_base, vpc_id, subnets):
    return respx.get(f"{vserver_base}/v2/{PID}/networks/{vpc_id}/subnets").mock(
        return_value=httpx.Response(200, json={"listData": subnets, "totalItem": len(subnets)})
    )


@respx.mock
@pytest.mark.asyncio
async def test_resolve_zone_context_default_region(config, client):
    """cluster in the default region: one locate call, zone read off the subnet."""
    from greennode.vks_mcp_server.discovery_handler import _resolve_zone_context

    _mock_iam(respx.mock)
    _mock_cluster(VKS_BASE_HCM, "k8s-abc", "net-1")
    _mock_subnets(
        VSERVER_BASE,
        "net-1",
        [
            {
                "uuid": "sub-1",
                "name": "s1",
                "status": "ACTIVE",
                "zone": {"uuid": "HCM03-1A", "name": "1A"},
            }
        ],
    )
    region, zone = await _resolve_zone_context(
        config, client, DiscoveryCache(), cluster_id="k8s-abc", subnet_id="sub-1"
    )
    assert region == "HCM-3"
    assert zone == "HCM03-1A"


@respx.mock
@pytest.mark.asyncio
async def test_resolve_zone_context_locates_other_region(config, client):
    """cluster missing in the default region: fall through to HAN and use its endpoints."""
    from greennode.vks_mcp_server.discovery_handler import _resolve_zone_context

    _mock_iam(respx.mock)
    _mock_cluster(VKS_BASE_HCM, "k8s-han", "x", status=404)
    _mock_cluster(VKS_BASE_HAN, "k8s-han", "net-9")
    respx.get(f"{VSERVER_BASE_HAN}/v1/projects").mock(
        return_value=httpx.Response(200, json=[{"projectId": PID}])
    )
    _mock_subnets(
        VSERVER_BASE_HAN,
        "net-9",
        [
            {
                "uuid": "sub-9",
                "name": "s9",
                "status": "ACTIVE",
                "zone": {"uuid": "HAN01-1A", "name": "1A"},
            }
        ],
    )
    region, zone = await _resolve_zone_context(
        config, client, DiscoveryCache(), cluster_id="k8s-han", subnet_id="sub-9"
    )
    assert region == "HAN"
    assert zone == "HAN01-1A"


@respx.mock
@pytest.mark.asyncio
async def test_resolve_zone_context_cluster_nowhere(config, client):
    """cluster in no region: a clear error naming the regions tried."""
    from greennode.vks_mcp_server.discovery_handler import _resolve_zone_context

    _mock_iam(respx.mock)
    _mock_cluster(VKS_BASE_HCM, "k8s-ghost", "x", status=404)
    _mock_cluster(VKS_BASE_HAN, "k8s-ghost", "x", status=404)
    with pytest.raises(ValueError, match="HCM-3.*HAN|HAN.*HCM-3"):
        await _resolve_zone_context(
            config, client, DiscoveryCache(), cluster_id="k8s-ghost", subnet_id="sub-1"
        )


@respx.mock
@pytest.mark.asyncio
async def test_resolve_zone_context_subnet_not_in_vpc(config, client):
    """subnet not in the cluster's VPC: actionable error pointing at list_subnets."""
    from greennode.vks_mcp_server.discovery_handler import _resolve_zone_context

    _mock_iam(respx.mock)
    _mock_cluster(VKS_BASE_HCM, "k8s-abc", "net-1")
    _mock_subnets(
        VSERVER_BASE,
        "net-1",
        [
            {
                "uuid": "sub-other",
                "name": "s1",
                "status": "ACTIVE",
                "zone": {"uuid": "HCM03-1A", "name": "1A"},
            }
        ],
    )
    with pytest.raises(ValueError, match="list_subnets"):
        await _resolve_zone_context(
            config, client, DiscoveryCache(), cluster_id="k8s-abc", subnet_id="sub-wrong"
        )


# ---------------------------------------------------------------------------
# Cache isolation under token passthrough (per-identity keys)
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_discovery_cache_isolated_per_identity(config, client):
    """Two different user tokens must never share cached discovery results —
    user B seeing user A's VPC list would be a cross-tenant leak."""
    from greennode.mcp_core.http import user_token_var

    _mock_iam(respx.mock)
    # passthrough identities never use the configured (service-account) pid —
    # they resolve their own via /v1/projects
    respx.get(f"{VSERVER_BASE}/v1/projects").mock(
        return_value=httpx.Response(200, json=[{"projectId": PID}])
    )
    route = respx.get(f"{VSERVER_BASE}/v2/{PID}/networks").mock(
        return_value=httpx.Response(
            200, json=[{"id": "net-1", "displayName": "a", "status": "ACTIVE"}]
        )
    )
    cache = DiscoveryCache()
    t = user_token_var.set("token-user-a")
    try:
        await _vpc_list(config, client, cache)
        await _vpc_list(config, client, cache)  # same user: cached
        assert route.call_count == 1
    finally:
        user_token_var.reset(t)
    t = user_token_var.set("token-user-b")
    try:
        await _vpc_list(config, client, cache)  # different user: MUST refetch
        assert route.call_count == 2
    finally:
        user_token_var.reset(t)


@respx.mock
@pytest.mark.asyncio
async def test_project_id_isolated_per_identity(config, client):
    """project_id differs per user — user B must not inherit user A's project."""
    from greennode.mcp_core.http import user_token_var

    _mock_iam(respx.mock)
    config.project_id = ""  # no configured pid: always discovered
    calls = []

    def responder(request):
        calls.append(1)
        pid = f"pro-user-{len(calls)}"
        return httpx.Response(200, json=[{"projectId": pid}])

    respx.get(f"{VSERVER_BASE}/v1/projects").mock(side_effect=responder)
    t = user_token_var.set("token-user-a")
    try:
        pid_a = await _require_project_id(config, client)
        assert await _require_project_id(config, client) == pid_a  # cached per user
    finally:
        user_token_var.reset(t)
    t = user_token_var.set("token-user-b")
    try:
        pid_b = await _require_project_id(config, client)
    finally:
        user_token_var.reset(t)
    assert pid_a != pid_b
    assert len(calls) == 2


@respx.mock
@pytest.mark.asyncio
async def test_passthrough_ignores_configured_service_project_id(config, client):
    """The env/file project_id belongs to the SERVICE ACCOUNT — a passthrough
    user must never silently use it."""
    from greennode.mcp_core.http import user_token_var

    _mock_iam(respx.mock)
    config.project_id = "pro-service-account"
    respx.get(f"{VSERVER_BASE}/v1/projects").mock(
        return_value=httpx.Response(200, json=[{"projectId": "pro-of-the-user"}])
    )
    t = user_token_var.set("token-user-a")
    try:
        assert await _require_project_id(config, client) == "pro-of-the-user"
    finally:
        user_token_var.reset(t)
