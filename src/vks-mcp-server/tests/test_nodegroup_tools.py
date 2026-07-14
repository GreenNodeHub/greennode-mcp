"""Tests for node group tools."""

from __future__ import annotations

import httpx
import json as _json
import pytest
import respx
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.models import (
    CreateNodeGroupDto,
    NodeGroupDetail,
    NodeGroupListData,
    NodeGroupTaint,
    NodesData,
    UpdateNodeGroupDto,
    UpdateNodeGroupMetadataDto,
)
from greennode.vks_mcp_server.nodegroup_handler import (
    NodeGroupHandler,
    _nodegroup_delete_dryrun,
    _nodegroup_list,
)
from mcp.server.fastmcp import FastMCP


VKS_BASE = "https://vks.api.vngcloud.vn"
IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"


def _mock_iam(mock: respx.MockRouter) -> None:
    """Register a mock IAM token response."""
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


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_list(config, client):
    """list_nodegroups fetches node groups and cluster name, returns table."""
    _mock_iam(respx.mock)
    cluster_id = "cluster-abc123"
    ng_items = [
        {
            "name": "ng-default",
            "uid": "ng-uid-001",
            "status": "ACTIVE",
            "nodeCount": 3,
            "imageId": "img-001",
            "createdAt": "2024-01-15T10:00:00Z",
        },
        {
            "name": "ng-worker",
            "uid": "ng-uid-002",
            "status": "ACTIVE",
            "nodeCount": 5,
            "imageId": "img-002",
            "createdAt": "2024-01-16T12:00:00Z",
        },
    ]
    cluster_data = {"name": "my-cluster", "uid": cluster_id, "status": "ACTIVE"}
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups").mock(
        return_value=httpx.Response(200, json=ng_items),
    )
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(200, json=cluster_data),
    )
    result = await _nodegroup_list(config, client, cluster_id=cluster_id)
    assert isinstance(result, NodeGroupListData)
    assert result.cluster_name == "my-cluster"
    names = [ng.name for ng in result.node_groups]
    assert "ng-default" in names
    assert "ng-worker" in names


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_list_with_region(config, client):
    """list_nodegroups passes region parameter correctly."""
    _mock_iam(respx.mock)
    cluster_id = "cluster-xyz"
    ng_items = [
        {
            "name": "ng-han",
            "uid": "ng-han-001",
            "status": "ACTIVE",
            "nodeCount": 2,
            "imageId": "img-han",
            "createdAt": "2024-02-01T00:00:00Z",
        }
    ]
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups").mock(
        return_value=httpx.Response(200, json=ng_items),
    )
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(200, json={"name": "han-cluster", "uid": cluster_id}),
    )
    result = await _nodegroup_list(config, client, cluster_id=cluster_id, region="HCM-3")
    assert isinstance(result, NodeGroupListData)
    assert any(ng.name == "ng-han" for ng in result.node_groups)


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_list_cluster_fetch_fails(config, client):
    """list_nodegroups still works when cluster GET fails (falls back to cluster_id)."""
    _mock_iam(respx.mock)
    cluster_id = "cluster-fallback"
    ng_items = [
        {
            "name": "ng-one",
            "uid": "ng-one-001",
            "status": "ACTIVE",
            "nodeCount": 1,
            "imageId": "img-x",
            "createdAt": "2024-03-01T00:00:00Z",
        }
    ]
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups").mock(
        return_value=httpx.Response(200, json=ng_items),
    )
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(404, json={"message": "not found"}),
    )
    result = await _nodegroup_list(config, client, cluster_id=cluster_id)
    assert isinstance(result, NodeGroupListData)
    assert result.cluster_name == cluster_id
    assert any(ng.name == "ng-one" for ng in result.node_groups)


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_list_empty(config, client):
    """list_nodegroups returns empty message when no node groups."""
    _mock_iam(respx.mock)
    cluster_id = "cluster-empty"
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups").mock(
        return_value=httpx.Response(200, json=[]),
    )
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(200, json={"name": "empty-cluster", "uid": cluster_id}),
    )
    result = await _nodegroup_list(config, client, cluster_id=cluster_id)
    assert isinstance(result, NodeGroupListData)
    assert result.total == 0
    assert "No node groups found" in result.to_markdown()


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_delete_dryrun(config, client):
    """delete_nodegroup_dryrun returns warning with YOU ARE ABOUT TO DELETE NODE GROUP."""
    _mock_iam(respx.mock)
    cluster_id = "cluster-abc123"
    nodegroup_id = "ng-uid-001"
    ng_detail = {
        "name": "ng-default",
        "uid": "ng-uid-001",
        "status": "ACTIVE",
        "nodeCount": 3,
        "imageId": "img-001",
        "createdAt": "2024-01-15T10:00:00Z",
    }
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}").mock(
        return_value=httpx.Response(200, json=ng_detail),
    )
    result = await _nodegroup_delete_dryrun(
        config,
        client,
        cluster_id=cluster_id,
        nodegroup_id=nodegroup_id,
    )
    assert "YOU ARE ABOUT TO DELETE NODE GROUP" in result
    assert nodegroup_id in result
    assert cluster_id in result
    assert "IRREVERSIBLE" in result


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_delete_dryrun_includes_node_count(config, client):
    """delete_nodegroup_dryrun warning includes node count."""
    _mock_iam(respx.mock)
    cluster_id = "cluster-prod"
    nodegroup_id = "ng-prod-001"
    ng_detail = {
        "name": "ng-production",
        "uid": "ng-prod-001",
        "status": "ACTIVE",
        "nodeCount": 10,
        "imageId": "img-prod",
        "createdAt": "2024-01-01T00:00:00Z",
    }
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}").mock(
        return_value=httpx.Response(200, json=ng_detail),
    )
    result = await _nodegroup_delete_dryrun(
        config,
        client,
        cluster_id=cluster_id,
        nodegroup_id=nodegroup_id,
    )
    assert "10" in result
    assert "delete_nodegroup" in result


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_upgrade_version(config, client):
    """upgrade_nodegroup_version POSTs the target version and reports success."""
    _mock_iam(respx.mock)
    handler = NodeGroupHandler(FastMCP("test"), config, client, allow_write=True)
    cluster_id = "cid-1"
    nodegroup_id = "ng-1"
    route = respx.post(
        f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}/upgrade-version"
    ).mock(return_value=httpx.Response(200, json={"status": "UPGRADING"}))
    result = await handler.upgrade_nodegroup_version(
        cluster_id=cluster_id,
        nodegroup_id=nodegroup_id,
        kubernetes_version="v1.29.0",
        region=None,
    )
    assert "v1.29.0" in result
    assert route.called
    body = _json.loads(route.calls.last.request.content)
    assert body == {"kubernetesVersion": "v1.29.0"}


@pytest.fixture
def handler(config, client):
    """Return a NodeGroupHandler wired to test config and client."""
    return NodeGroupHandler(FastMCP("test"), config, client)


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_list_nodes_structured_maps_ips(handler):
    """list_nodes returns NodesData with floating/fixed IP and ready/poc."""
    _mock_iam(respx.mock)
    cluster_id = "k8s-abc"
    nodegroup_id = "ng-1"
    nodes = {
        "items": [
            {
                "id": "node-1",
                "name": "worker-1",
                "status": "ACTIVE",
                "floatingIp": "1.2.3.4",
                "fixedIp": "10.0.0.5",
                "ready": True,
                "poc": False,
            }
        ]
    }
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}/nodes").mock(
        return_value=httpx.Response(200, json=nodes)
    )
    result = await handler.list_nodes(
        cluster_id=cluster_id, nodegroup_id=nodegroup_id, region=None
    )
    assert isinstance(result, NodesData)
    assert result.nodegroup_id == nodegroup_id
    assert len(result.nodes) == 1
    node = result.nodes[0]
    assert node.name == "worker-1"
    assert node.floating_ip == "1.2.3.4"
    assert node.fixed_ip == "10.0.0.5"
    assert node.ready == "True"
    assert node.poc == "False"


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_list_structured(handler):
    """list_nodegroups returns NodeGroupListData structured model."""
    _mock_iam(respx.mock)
    cluster_id = "k8s-abc"
    ng_items = [
        {
            "name": "ng-default",
            "uid": "ng-uid-001",
            "status": "ACTIVE",
            "nodeCount": 3,
            "imageId": "img-001",
            "createdAt": "2024-01-15T10:00:00Z",
        }
    ]
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups").mock(
        return_value=httpx.Response(200, json=ng_items),
    )
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}").mock(
        return_value=httpx.Response(200, json={"name": "my-cluster", "uid": cluster_id}),
    )
    result = await handler.list_nodegroups(cluster_id=cluster_id, region=None)
    assert isinstance(result, NodeGroupListData)
    assert result.cluster_name == "my-cluster"
    assert len(result.node_groups) == 1
    assert result.node_groups[0].name == "ng-default"


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_get_structured(handler):
    """get_nodegroup returns NodeGroupDetail structured model."""
    _mock_iam(respx.mock)
    cluster_id = "k8s-abc"
    nodegroup_id = "ng-1"
    ng_detail = {
        "name": "ng-default",
        "uid": "ng-1",
        "status": "ACTIVE",
        "nodeCount": 3,
        "imageId": "img-001",
        "flavorId": "flv-001",
        "createdAt": "2024-01-15T10:00:00Z",
    }
    respx.get(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}").mock(
        return_value=httpx.Response(200, json=ng_detail),
    )
    result = await handler.get_nodegroup(
        cluster_id=cluster_id, nodegroup_id=nodegroup_id, region=None
    )
    assert isinstance(result, NodeGroupDetail)
    assert result.id


# ---------------------------------------------------------------------------
# Write tool DTO tests (Task 9)
# ---------------------------------------------------------------------------


@pytest.fixture
def handler_write(config, client):
    """Return a NodeGroupHandler with allow_write=True."""
    return NodeGroupHandler(FastMCP("test-write"), config, client, allow_write=True)


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_create_accepts_dto(handler_write, respx_mock):
    """create_nodegroup accepts a CreateNodeGroupDto and sends correct wire body."""
    _mock_iam(respx_mock)
    cluster_id = "k8s-abc"
    ng_response = {
        "uid": "ng-new-001",
        "name": "new-ng",
        "status": "CREATING",
    }
    respx_mock.post(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups").mock(
        return_value=httpx.Response(200, json=ng_response)
    )
    dto = CreateNodeGroupDto(
        name="new-ng",
        flavorId="flav-001",
        diskSize=100,
        diskType="SSD",
        numNodes=2,
        securityGroups=["sg-001"],
        sshKeyId="key-001",
        os="rocky",
    )
    result = await handler_write.create_nodegroup(cluster_id=cluster_id, body=dto, region=None)
    sent = _json.loads(respx_mock.calls.last.request.content)
    assert sent["name"] == "new-ng"
    assert sent["flavorId"] == "flav-001"
    assert sent["diskSize"] == 100
    # os must be top-level, NOT nested inside upgradeConfig
    assert sent["os"] == "rocky"
    assert "os" not in sent["upgradeConfig"]
    assert "new-ng" in result


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_update_accepts_dto(handler_write, respx_mock):
    """update_nodegroup accepts an UpdateNodeGroupDto and sends correct wire body."""
    _mock_iam(respx_mock)
    cluster_id = "k8s-abc"
    nodegroup_id = "ng-001"
    ng_response = {
        "uid": nodegroup_id,
        "name": "ng-updated",
        "status": "ACTIVE",
    }
    respx_mock.put(f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}").mock(
        return_value=httpx.Response(200, json=ng_response)
    )
    dto = UpdateNodeGroupDto(numNodes=5)
    result = await handler_write.update_nodegroup(
        cluster_id=cluster_id, nodegroup_id=nodegroup_id, body=dto, region=None
    )
    sent = _json.loads(respx_mock.calls.last.request.content)
    assert sent["numNodes"] == 5
    assert "labels" not in sent  # exclude_none should drop unset fields
    assert "ng-updated" in result or nodegroup_id in result


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_update_empty_body_guarded(handler_write, respx_mock):
    """update_nodegroup with an all-empty body returns a guard message, no HTTP call."""
    _mock_iam(respx_mock)
    route = respx_mock.put(f"{VKS_BASE}/v1/clusters/k8s-abc/node-groups/ng-001").mock(
        return_value=httpx.Response(200, json={})
    )
    result = await handler_write.update_nodegroup(
        cluster_id="k8s-abc", nodegroup_id="ng-001", body=UpdateNodeGroupDto(), region=None
    )
    assert not route.called
    assert "nothing to update" in result.lower()


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_update_metadata_sends_patch(handler_write, respx_mock):
    """update_nodegroup_metadata PATCHes /metadata with labels, tags, and taints."""
    _mock_iam(respx_mock)
    cluster_id = "k8s-abc"
    nodegroup_id = "ng-001"
    route = respx_mock.patch(
        f"{VKS_BASE}/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}/metadata"
    ).mock(return_value=httpx.Response(200, json={"uid": nodegroup_id}))
    dto = UpdateNodeGroupMetadataDto(
        labels={"team": "core"},
        tags={"env": "prod"},
        taints=[NodeGroupTaint(key="gpu", value="true", effect="NoSchedule")],
    )
    result = await handler_write.update_nodegroup_metadata(
        cluster_id=cluster_id, nodegroup_id=nodegroup_id, body=dto, region=None
    )
    assert route.called
    sent = _json.loads(route.calls.last.request.content)
    assert sent["labels"] == {"team": "core"}
    assert sent["tags"] == {"env": "prod"}
    assert sent["taints"][0]["effect"] == "NoSchedule"
    assert nodegroup_id in result


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_update_metadata_empty_body_guarded(handler_write, respx_mock):
    """update_nodegroup_metadata with an empty body returns a guard message, no HTTP call."""
    _mock_iam(respx_mock)
    route = respx_mock.patch(f"{VKS_BASE}/v1/clusters/k8s-abc/node-groups/ng-001/metadata").mock(
        return_value=httpx.Response(200, json={})
    )
    result = await handler_write.update_nodegroup_metadata(
        cluster_id="k8s-abc",
        nodegroup_id="ng-001",
        body=UpdateNodeGroupMetadataDto(),
        region=None,
    )
    assert not route.called
    assert "nothing to update" in result.lower()


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_update_metadata_not_registered_without_write(handler, respx_mock):
    """Metadata tool is a write op: not registered on a read-only handler."""
    tool_names = {t.name for t in await handler.mcp.list_tools()}
    assert "update_nodegroup_metadata" not in tool_names


@pytest.mark.asyncio
async def test_create_nodegroup_description_has_zone_chained_workflow(handler_write):
    """create_nodegroup's ## Workflow teaches the zone-scoped discovery chain.

    The subnet must be picked first — its zone.uuid scopes both list_flavors and
    list_volume_types. The old flat list (which even pointed at
    list_cluster_versions, a create_cluster concern) must be gone.
    """
    tools = await handler_write.mcp.list_tools()
    desc = next(t.description for t in tools if t.name == "create_nodegroup")
    assert "## Workflow" in desc
    for name in (
        "get_cluster",
        "list_subnets",
        "list_flavors",
        "list_volume_types",
        "list_ssh_keys",
        "get_creation_guide",  # cross-ref to the on-demand guide tool
    ):
        assert name in desc, f"{name} missing from create_nodegroup description"
    # subnet first: its zone scopes flavors and volume types
    assert desc.index("list_subnets") < desc.index("list_flavors")
    assert desc.index("list_subnets") < desc.index("list_volume_types")
    assert "zone" in desc
    assert "list_cluster_versions" not in desc  # create_cluster concern, not nodegroup


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_list_fetches_all_pages(config, client):
    """VKS enforces paging (default pageSize=10) and quota allows 20 node groups
    per cluster — the list must page through, never truncating."""
    _mock_iam(respx.mock)

    def item(i):
        return {"name": f"ng{i}", "uid": f"ng-{i}", "status": "ACTIVE"}

    def responder(request):
        page = int(request.url.params.get("page", 0))
        size = int(request.url.params.get("pageSize", 10))
        all_items = [item(i) for i in range(12)]
        chunk = all_items[page * size : (page + 1) * size]
        return httpx.Response(
            200, json={"items": chunk, "total": 12, "page": page, "pageSize": size}
        )

    respx.get(f"{VKS_BASE}/v1/clusters/k8s-abc/node-groups").mock(side_effect=responder)
    respx.get(f"{VKS_BASE}/v1/clusters/k8s-abc").mock(
        return_value=httpx.Response(200, json={"name": "my-cluster"})
    )
    result = await _nodegroup_list(config, client, cluster_id="k8s-abc")
    assert len(result.node_groups) == 12
    assert result.node_groups[-1].name == "ng11"


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_outputs_echo_region(config, client, handler):
    """NodeGroupListData echoes the resolved region so wrong-region results are visible."""
    _mock_iam(respx.mock)
    respx.get(f"{VKS_BASE}/v1/clusters/k8s-abc/node-groups").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )
    respx.get(f"{VKS_BASE}/v1/clusters/k8s-abc").mock(
        return_value=httpx.Response(200, json={"name": "my-cluster"})
    )
    result = await _nodegroup_list(config, client, cluster_id="k8s-abc")
    assert result.region == config.default_region


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_create_error_teaches_the_guide(handler_write, respx_mock):
    """A failed create points the agent at get_creation_guide — error-driven
    guidance, so an agent that skipped the guide learns about it exactly when
    it goes wrong."""
    _mock_iam(respx_mock)
    respx_mock.post(f"{VKS_BASE}/v1/clusters/k8s-abc/node-groups").mock(
        return_value=httpx.Response(400, json={"message": "subnetId is invalid"})
    )
    dto = CreateNodeGroupDto(
        name="new-ng",
        flavorId="flav-001",
        diskSize=100,
        diskType="vtype-001",
        numNodes=1,
        sshKeyId="ssh-001",
    )
    with pytest.raises(RuntimeError, match="get_creation_guide"):
        await handler_write.create_nodegroup(cluster_id="k8s-abc", body=dto, region=None)


@respx.mock
@pytest.mark.asyncio
async def test_nodegroup_delete_force(handler_write, respx_mock):
    """force_delete=True sends forceDelete=true to the API (CLI --force-delete
    parity) — the escalation when a normal delete is stuck."""
    _mock_iam(respx_mock)
    route = respx_mock.delete(f"{VKS_BASE}/v1/clusters/k8s-abc/node-groups/ng-001").mock(
        return_value=httpx.Response(202)
    )
    await handler_write.delete_nodegroup(
        cluster_id="k8s-abc", nodegroup_id="ng-001", force_delete=True, region=None
    )
    assert route.calls.last.request.url.params.get("forceDelete") == "true"

    # force_delete=False: no forceDelete param at all
    await handler_write.delete_nodegroup(
        cluster_id="k8s-abc", nodegroup_id="ng-001", force_delete=False, region=None
    )
    assert "forceDelete" not in route.calls.last.request.url.params


# ---------------------------------------------------------------------------
# validate_nodegroup_create — local rules + discovery cross-checks
# ---------------------------------------------------------------------------

VS_BASE = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
_PID = "pro-test-0001"


def _valid_ng_body(**over):
    body = {
        "name": "web-workers",
        "flavorId": "flav-ok",
        "diskType": "vtype-ok",
        "diskSize": 100,
        "numNodes": 2,
        "sshKeyId": "ssh-ok",
        "subnetId": "sub-ok",
    }
    body.update(over)
    return CreateNodeGroupDto(**body)


def _mock_validation_chain(respx_mock):
    """Cluster + full zone-scoped discovery chain, all healthy."""
    respx_mock.get(f"{VKS_BASE}/v1/clusters/k8s-abc").mock(
        return_value=httpx.Response(200, json={"uid": "k8s-abc", "vpcId": "net-1"})
    )
    respx_mock.get(f"{VS_BASE}/v2/{_PID}/networks/net-1/subnets").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {
                        "uuid": "sub-ok",
                        "name": "s1",
                        "status": "ACTIVE",
                        "zone": {"uuid": "HCM03-1A", "name": "1A"},
                    }
                ],
                "totalItem": 1,
            },
        )
    )
    respx_mock.get(f"{VS_BASE}/v1/{_PID}/flavor_zones/families").mock(
        return_value=httpx.Response(
            200,
            json=[{"key": "general-purpose", "condition": {"codes": ["code-s2"]}}],
        )
    )
    respx_mock.get(
        f"{VS_BASE}/v1/{_PID}/flavors/families/general-purpose/platforms/code-s2"
        "/clusters/master/false"
    ).mock(
        return_value=httpx.Response(
            200, json={"listData": [{"flavorId": "flav-ok", "name": "s1", "cpu": 2, "memory": 4}]}
        )
    )
    respx_mock.get(f"{VS_BASE}/v1/{_PID}/volume_type_zones").mock(
        return_value=httpx.Response(
            200,
            json={"listData": [{"id": "vtz-1", "name": "NVME"}, {"id": "vtz-ssd", "name": "SSD"}]},
        )
    )
    respx_mock.get(f"{VS_BASE}/v1/{_PID}/vtz-1/volume_types").mock(
        return_value=httpx.Response(
            200, json={"listData": [{"id": "vtype-ok", "name": "3000", "iops": 3000}]}
        )
    )
    respx_mock.get(f"{VS_BASE}/v1/{_PID}/vtz-ssd/volume_types").mock(
        return_value=httpx.Response(200, json={"listData": [{"id": "vt-ssd-1", "iops": 1000}]})
    )
    respx_mock.get(f"{VS_BASE}/v2/{_PID}/sshKeys").mock(
        return_value=httpx.Response(
            200, json={"listData": [{"id": "ssh-ok", "name": "k"}], "totalItem": 1}
        )
    )


@pytest.fixture
def validate_handler(config, client):
    from greennode.vks_mcp_server.discovery_cache import DiscoveryCache

    return NodeGroupHandler(FastMCP("t-val"), config, client, cache=DiscoveryCache())


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_create_valid(validate_handler, respx_mock):
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc", body=_valid_ng_body()
    )
    assert result == "valid"


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_create_bad_name(validate_handler, respx_mock):
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc", body=_valid_ng_body(name="Bad_Name_Way_Too_Long!")
    )
    assert result != "valid" and "name" in result.lower()


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_create_subnet_not_in_vpc(validate_handler, respx_mock):
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc", body=_valid_ng_body(subnetId="sub-elsewhere")
    )
    assert result != "valid" and "list_subnets" in result


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_create_flavor_not_in_zone(validate_handler, respx_mock):
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc", body=_valid_ng_body(flavorId="flav-ghost")
    )
    assert result != "valid" and "flavorId" in result and "list_flavors" in result


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_create_bad_disktype(validate_handler, respx_mock):
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc", body=_valid_ng_body(diskType="SSD")
    )
    assert result != "valid" and "diskType" in result and "list_volume_types" in result


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_create_unknown_ssh_key(validate_handler, respx_mock):
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc", body=_valid_ng_body(sshKeyId="ssh-ghost")
    )
    assert result != "valid" and "sshKeyId" in result and "list_ssh_keys" in result


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_create_collects_multiple_errors(validate_handler, respx_mock):
    """One pass reports every problem — not just the first."""
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc",
        body=_valid_ng_body(flavorId="flav-ghost", diskType="SSD", sshKeyId="ssh-ghost"),
    )
    assert "flavorId" in result and "diskType" in result and "sshKeyId" in result


@respx.mock
@pytest.mark.asyncio
async def test_validate_nodegroup_accepts_ssd_disktype(validate_handler, respx_mock):
    """A user who explicitly chose an SSD tier must not be flagged invalid."""
    _mock_iam(respx_mock)
    _mock_validation_chain(respx_mock)
    result = await validate_handler.validate_nodegroup_create(
        cluster_id="k8s-abc", body=_valid_ng_body(diskType="vt-ssd-1")
    )
    assert result == "valid"
