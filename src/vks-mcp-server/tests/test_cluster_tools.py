"""Tests for cluster tools."""

from __future__ import annotations

import httpx
import json as _json
import pytest
import respx
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.cluster_handler import (
    ClusterHandler,
    _cluster_create_validate,
    _cluster_delete_dryrun,
    _cluster_get,
    _cluster_list,
)
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.models import (
    ClusterDetail,
    ClusterListData,
    CreateClusterComboDto,
    UpdateClusterDto,
)
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
VKS_BASE = "https://vks.api.vngcloud.vn"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(
            200,
            json={"accessToken": "mocked-token", "expiresIn": 1800},
        )
    )


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    token_manager = TokenManager(config)
    return VksClient(config, token_manager)


@pytest.fixture
def handler(config, client):
    return ClusterHandler(FastMCP("test"), config, client)


# ---------------------------------------------------------------------------
# _cluster_list
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_cluster_list(client):
    """list_clusters calls GET /v1/clusters and returns ClusterListData."""
    _mock_iam(respx.mock)
    items = [
        {
            "name": "my-cluster",
            "uid": "uid-abc",
            "status": "ACTIVE",
            "version": "1.28",
            "nodeCount": 3,
            "createdAt": "2024-01-15T10:00:00Z",
        }
    ]
    respx.get(f"{VKS_BASE}/v1/clusters").mock(
        return_value=httpx.Response(200, json={"items": items}),
    )
    result = await _cluster_list(client, {})
    assert isinstance(result, ClusterListData)
    assert result.clusters[0].name == "my-cluster"


@respx.mock
@pytest.mark.asyncio
async def test_cluster_list_with_pagination(client):
    """list_clusters passes page and pageSize as query params."""
    _mock_iam(respx.mock)
    respx.get(f"{VKS_BASE}/v1/clusters").mock(
        return_value=httpx.Response(200, json={"items": []}),
    )
    result = await _cluster_list(client, {"page": 2, "pageSize": 5})
    assert isinstance(result, ClusterListData)
    assert result.total == 0


# ---------------------------------------------------------------------------
# _cluster_delete_dryrun
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_cluster_delete_dryrun(client):
    """delete_cluster_dryrun fetches cluster + node-groups and warns user."""
    _mock_iam(respx.mock)
    cluster_id = "cid-xyz"
    cluster_data = {
        "uid": "cid-xyz",
        "name": "prod-cluster",
        "status": "ACTIVE",
        "version": "1.29",
        "nodeCount": 5,
    }
    node_groups = [
        {"uid": "ng-1", "name": "worker-ng", "nodeCount": 3},
        {"uid": "ng-2", "name": "gpu-ng", "nodeCount": 2},
    ]
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(200, json=cluster_data),
    )
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups").mock(
        return_value=httpx.Response(200, json={"items": node_groups}),
    )
    result = await _cluster_delete_dryrun(client, {"cluster_id": cluster_id})
    assert len(result) == 1
    text = result[0].text
    assert "YOU ARE ABOUT TO DELETE CLUSTER" in text


# ---------------------------------------------------------------------------
# _cluster_create_validate
# ---------------------------------------------------------------------------


_VALID_BODY = {
    "name": "mycluster01",
    "vpcId": "vpc-001",
    "networkType": "CILIUM_OVERLAY",
    "version": "1.28",
    "releaseChannel": "STABLE",
    "cidr": "10.96.0.0/16",
    "enablePrivateCluster": False,
}


def test_cluster_create_validate_valid():
    """A fully valid body returns 'valid'."""
    result = _cluster_create_validate({"body": _VALID_BODY})
    assert len(result) == 1
    assert result[0].text == "valid"


def test_cluster_create_validate_bad_name():
    """Invalid cluster name returns an error mentioning 'name'."""
    body = {**_VALID_BODY, "name": "BAD_NAME!"}
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "name" in text.lower()
    assert text != "valid"


def test_cluster_create_validate_missing_fields():
    """Missing vpcId returns an error mentioning 'vpcId'."""
    body = {k: v for k, v in _VALID_BODY.items() if k != "vpcId"}
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "vpcId" in text
    assert text != "valid"


def test_cluster_create_validate_missing_network_type():
    """Missing networkType returns error."""
    body = {k: v for k, v in _VALID_BODY.items() if k != "networkType"}
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "networkType" in text
    assert text != "valid"


def test_cluster_create_validate_cilium_overlay_needs_cidr():
    """CILIUM_OVERLAY networkType without cidr returns an error."""
    body = {k: v for k, v in _VALID_BODY.items() if k != "cidr"}
    assert body["networkType"] == "CILIUM_OVERLAY"
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "cidr" in text


def test_cluster_create_validate_invalid_network_type():
    """An unknown networkType (e.g. legacy CALICO) is rejected."""
    body = {**_VALID_BODY, "networkType": "CALICO"}
    body.pop("cidr", None)
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "networkType" in text
    assert text != "valid"


def test_cluster_create_validate_missing_enable_private_cluster():
    """Missing enablePrivateCluster is reported."""
    body = {k: v for k, v in _VALID_BODY.items() if k != "enablePrivateCluster"}
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "enablePrivateCluster" in text


def test_cluster_create_validate_cilium_native_routing_needs_secondary_subnets():
    """CILIUM_NATIVE_ROUTING without secondarySubnets returns an error."""
    body = {
        **_VALID_BODY,
        "networkType": "CILIUM_NATIVE_ROUTING",
    }
    body.pop("cidr", None)
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "secondarySubnets" in text


def test_cluster_create_validate_tigera_valid():
    """A TIGERA cluster with cidr is valid (TIGERA replaces the legacy CALICO)."""
    body = {**_VALID_BODY, "networkType": "TIGERA"}
    result = _cluster_create_validate({"body": body})
    assert result[0].text == "valid"


def test_cluster_create_validate_tigera_needs_cidr():
    """TIGERA without cidr is rejected."""
    body = {k: v for k, v in _VALID_BODY.items() if k != "cidr"}
    body["networkType"] = "TIGERA"
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert "cidr" in text
    assert text != "valid"


# ---------------------------------------------------------------------------
# configure_auto_healing
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_cluster_auto_healing_config(config, client):
    """configure_auto_healing PATCHes only provided fields and reports success."""
    _mock_iam(respx.mock)
    handler = ClusterHandler(FastMCP("test"), config, client, allow_write=True)
    cluster_id = "cid-1"
    route = respx.patch(f"{VKS_BASE}/v1/clusters/{cluster_id}/auto-healing-config").mock(
        return_value=httpx.Response(200, json={"enableAutoHealing": True})
    )
    result = await handler.configure_auto_healing(
        cluster_id=cluster_id,
        enable_auto_healing=True,
        max_unhealthy=None,
        unhealthy_range=None,
        timeout_unhealthy=30,
        region=None,
    )
    assert "updated successfully" in result
    assert route.called
    body = _json.loads(route.calls.last.request.content)
    assert body["enableAutoHealing"] is True
    assert body["timeoutUnhealthy"] == 30
    assert "maxUnhealthy" not in body


# ---------------------------------------------------------------------------
# Structured return tests (Task 4)
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_cluster_list_structured(client):
    """_cluster_list returns ClusterListData with ClusterSummary items."""
    _mock_iam(respx.mock)
    items = [
        {
            "uid": "k8s-abc",
            "name": "prod-cluster",
            "status": "ACTIVE",
            "version": "1.29",
            "nodeCount": 3,
            "createdAt": "2024-01-15T10:00:00Z",
        }
    ]
    respx.get(f"{VKS_BASE}/v1/clusters").mock(
        return_value=httpx.Response(200, json={"items": items}),
    )
    result = await _cluster_list(client, {})
    assert isinstance(result, ClusterListData)
    assert result.clusters[0].id == "k8s-abc"
    assert result.clusters[0].name == "prod-cluster"


@respx.mock
@pytest.mark.asyncio
async def test_cluster_get_structured(client):
    """_cluster_get returns ClusterDetail with correct fields."""
    _mock_iam(respx.mock)
    cluster_id = "k8s-abc"
    cluster_data = {
        "uid": cluster_id,
        "name": "prod-cluster",
        "status": "ACTIVE",
        "version": "1.29",
        "networkType": "CILIUM_OVERLAY",
        "vpcId": "vpc-001",
    }
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(200, json=cluster_data),
    )
    result = await _cluster_get(client, {"cluster_id": cluster_id})
    assert isinstance(result, ClusterDetail)
    assert result.id == cluster_id
    assert result.name == "prod-cluster"


# ---------------------------------------------------------------------------
# Write tool DTO tests (Task 9)
# ---------------------------------------------------------------------------


@pytest.fixture
def handler_write(config, client):
    """Return a ClusterHandler with allow_write=True."""
    return ClusterHandler(FastMCP("test-write"), config, client, allow_write=True)


@respx.mock
@pytest.mark.asyncio
async def test_cluster_create_accepts_dto(handler_write, respx_mock):
    """create_cluster accepts a CreateClusterComboDto and sends correct wire body."""
    _mock_iam(respx_mock)
    cluster_response = {
        "uid": "k8s-new-001",
        "name": "demo",
        "status": "CREATING",
        "version": "v1.29.0",
        "networkType": "CILIUM_NATIVE_ROUTING",
        "vpcId": "net-1",
        "enablePrivateCluster": False,
    }
    respx_mock.post(f"{VKS_BASE}/v1/clusters").mock(
        return_value=httpx.Response(200, json=cluster_response)
    )
    dto = CreateClusterComboDto(
        name="demo",
        version="v1.29.0",
        networkType="CILIUM_NATIVE_ROUTING",
        vpcId="net-1",
        enablePrivateCluster=False,
        secondarySubnets=["sub-1"],
    )
    result = await handler_write.create_cluster(body=dto, region=None)
    sent = _json.loads(respx_mock.calls.last.request.content)
    assert sent["name"] == "demo"
    assert sent["networkType"] == "CILIUM_NATIVE_ROUTING"
    assert "demo" in result


@respx.mock
@pytest.mark.asyncio
async def test_cluster_update_accepts_dto(handler_write, respx_mock):
    """update_cluster accepts an UpdateClusterDto and sends correct wire body."""
    _mock_iam(respx_mock)
    cluster_id = "k8s-abc"
    cluster_response = {
        "uid": cluster_id,
        "name": "renamed-cluster",
        "status": "ACTIVE",
        "version": "1.29",
    }
    respx_mock.put(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(200, json=cluster_response)
    )
    dto = UpdateClusterDto(version="v1.29.0", whitelistNodeCIDRs=["10.0.0.0/8"])
    result = await handler_write.update_cluster(cluster_id=cluster_id, body=dto, region=None)
    sent = _json.loads(respx_mock.calls.last.request.content)
    assert sent["version"] == "v1.29.0"
    assert sent["whitelistNodeCIDRs"] == ["10.0.0.0/8"]
    assert "enabledLoadBalancerPlugin" not in sent  # exclude_none drops unset toggles
    assert "renamed-cluster" in result


@respx.mock
@pytest.mark.asyncio
async def test_cluster_create_validate_accepts_dto(handler_write):
    """validate_cluster_create accepts a CreateClusterComboDto and returns 'valid'."""
    dto = CreateClusterComboDto(
        name="mycluster01",
        version="1.28",
        networkType="CILIUM_OVERLAY",
        vpcId="vpc-001",
        enablePrivateCluster=False,
        cidr="10.96.0.0/16",
    )
    result = handler_write.validate_cluster_create(body=dto)
    assert result == "valid"


def test_create_cluster_dto_rejects_nodegroups():
    """The deprecated nodeGroups array is rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        CreateClusterComboDto(
            name="mycluster01",
            version="1.28",
            networkType="CILIUM_OVERLAY",
            vpcId="vpc-001",
            enablePrivateCluster=False,
            cidr="10.96.0.0/16",
            nodeGroups=[{"name": "ng1"}],
        )
