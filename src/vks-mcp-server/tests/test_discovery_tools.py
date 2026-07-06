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
    assert result.vpcs[0].cidr == "10.0.0.0/16"
    assert result.vpcs[0].status == "ACTIVE"


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
    assert result.subnets[0].cidr == "10.0.1.0/24"


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


@respx.mock
@pytest.mark.asyncio
async def test_flavor_list_returns_structured(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v1/{PID}/flavors/customs/clusters").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "flavorId": "flv-1",
                    "name": "2c_4g",
                    "cpu": 2,
                    "memory": 4,
                    "gpu": 0,
                    "group": "standard",
                },
                {
                    "flavorId": "flv-2",
                    "name": "8c_16g",
                    "cpu": 8,
                    "memory": 16,
                    "gpu": 0,
                    "group": "standard",
                },
            ],
        )
    )
    result = await _flavor_list(config, client, DiscoveryCache())
    assert isinstance(result, FlavorListData)
    assert result.need is None
    ids = [f.id for f in result.flavors]
    assert "flv-1" in ids
    assert "flv-2" in ids
    groups = [f.group for f in result.flavors]
    assert "Dev/test" in groups
    assert "Compute" in groups


@respx.mock
@pytest.mark.asyncio
async def test_flavor_list_filters_by_need(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v1/{PID}/flavors/customs/clusters").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"flavorId": "flv-1", "name": "2c_4g", "cpu": 2, "memory": 4, "gpu": 0},
                {"flavorId": "flv-2", "name": "8c_16g", "cpu": 8, "memory": 16, "gpu": 0},
            ],
        )
    )
    result = await _flavor_list(config, client, DiscoveryCache(), need="Compute")
    assert isinstance(result, FlavorListData)
    assert result.need == "Compute"
    assert len(result.flavors) == 1
    assert result.flavors[0].id == "flv-2"
    assert result.flavors[0].name == "8c_16g"


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
    assert result.secgroups[0].id == "secg-1"
    assert result.secgroups[0].name == "default"
    assert result.secgroups[0].description == "default sg"
    assert result.secgroups[0].status == "ACTIVE"


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
    route = respx.get(f"{VSERVER_BASE}/v1/{PID}/flavors/customs/clusters").mock(
        return_value=httpx.Response(
            200, json=[{"flavorId": "f1", "name": "2c_4g", "cpu": 2, "memory": 4, "gpu": 0}]
        )
    )
    await _flavor_list(config, client, cache)
    await _flavor_list(config, client, cache)  # cached (need=None)
    await _flavor_list(config, client, cache, need="Dev/test")  # different key
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_volumetype_list_two_step_fetch(config, client, cache):
    """list_volume_types fetches type zones, then volume types per zone, tagged by name."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v1/{PID}/volume_type_zones").mock(
        return_value=httpx.Response(
            200,
            json={
                "volumeTypeZones": [
                    {"id": "vtz-ssd", "name": "SSD", "zone": "HCM03-1A"},
                    {"id": "vtz-nvme", "name": "NVMe", "zone": "HCM03-1A"},
                ]
            },
        )
    )
    respx.get(f"{VSERVER_BASE}/v1/{PID}/vtz-ssd/volume_types").mock(
        return_value=httpx.Response(
            200,
            json={
                "volumeTypes": [
                    {"id": "vt-1", "name": "ssd-io1", "iops": 3000, "minSize": 20, "maxSize": 2000}
                ]
            },
        )
    )
    respx.get(f"{VSERVER_BASE}/v1/{PID}/vtz-nvme/volume_types").mock(
        return_value=httpx.Response(
            200,
            json={"volumeTypes": [{"id": "vt-2", "name": "nvme-io1", "iops": 8000}]},
        )
    )
    result = await _volumetype_list(config, client, cache)
    assert isinstance(result, VolumeTypeListData)
    assert {v.id for v in result.volume_types} == {"vt-1", "vt-2"}
    by_id = {v.id: v for v in result.volume_types}
    assert by_id["vt-1"].type_zone == "SSD"
    assert by_id["vt-2"].type_zone == "NVMe"


@respx.mock
@pytest.mark.asyncio
async def test_volumetype_list_filters_by_type_name(config, client, cache):
    """type_name filter only fetches the matching type zone (case-insensitive)."""
    _mock_iam(respx.mock)
    respx.get(f"{VSERVER_BASE}/v1/{PID}/volume_type_zones").mock(
        return_value=httpx.Response(
            200,
            json={
                "volumeTypeZones": [
                    {"id": "vtz-ssd", "name": "SSD"},
                    {"id": "vtz-nvme", "name": "NVMe"},
                ]
            },
        )
    )
    ssd_route = respx.get(f"{VSERVER_BASE}/v1/{PID}/vtz-ssd/volume_types").mock(
        return_value=httpx.Response(200, json={"volumeTypes": [{"id": "vt-1", "name": "ssd"}]})
    )
    nvme_route = respx.get(f"{VSERVER_BASE}/v1/{PID}/vtz-nvme/volume_types").mock(
        return_value=httpx.Response(200, json={"volumeTypes": [{"id": "vt-2"}]})
    )
    result = await _volumetype_list(config, client, cache, type_name="ssd")
    assert ssd_route.called
    assert not nvme_route.called
    assert [v.id for v in result.volume_types] == ["vt-1"]


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
    result = await _quota_get(client)
    assert isinstance(result, QuotaData)
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
    assert pg.policy == "AFFINITY"
