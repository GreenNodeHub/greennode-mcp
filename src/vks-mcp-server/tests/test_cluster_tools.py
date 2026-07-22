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
async def test_cluster_list_empty(client):
    """An empty region returns an empty ClusterListData without looping."""
    _mock_iam(respx.mock)
    route = respx.get(f"{VKS_BASE}/v1/clusters").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0}),
    )
    result = await _cluster_list(client, {})
    assert isinstance(result, ClusterListData)
    assert result.total == 0
    assert route.call_count == 1


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


def test_cluster_create_validate_cilium_native_routing_valid_without_secondary_subnets():
    """secondarySubnets is a NODE-GROUP concern (each group sets its own at
    creation) — a CILIUM_NATIVE_ROUTING cluster body without it is valid."""
    body = {
        **_VALID_BODY,
        "networkType": "CILIUM_NATIVE_ROUTING",
    }
    body.pop("cidr", None)
    result = _cluster_create_validate({"body": body})
    assert result[0].text == "valid"


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


@respx.mock
@pytest.mark.asyncio
async def test_cluster_list_fetches_all_pages(client):
    """The VKS backend defaults to pageSize=10 and enforces it — a bare call must
    page through until every cluster is collected, never truncating."""
    _mock_iam(respx.mock)

    def item(i):
        return {"name": f"c{i}", "uid": f"uid-{i}", "status": "ACTIVE", "version": "1.28"}

    def responder(request):
        page = int(request.url.params.get("page", 0))
        size = int(request.url.params.get("pageSize", 10))
        all_items = [item(i) for i in range(12)]
        chunk = all_items[page * size : (page + 1) * size]
        return httpx.Response(
            200, json={"items": chunk, "total": 12, "page": page, "pageSize": size}
        )

    respx.get(f"{VKS_BASE}/v1/clusters").mock(side_effect=responder)
    result = await _cluster_list(client, {})
    assert len(result.clusters) == 12
    assert result.clusters[-1].name == "c11"


def test_update_cluster_dto_all_fields_optional():
    """The API no longer requires version/whitelistNodeCIDRs — partial updates
    (e.g. toggling one plugin) must construct without them."""
    from greennode.vks_mcp_server.models import UpdateClusterDto

    dto = UpdateClusterDto(enabledLoadBalancerPlugin=True)
    assert dto.model_dump(exclude_none=True) == {"enabledLoadBalancerPlugin": True}
    UpdateClusterDto(version="1.29")  # version alone is also a valid update
    UpdateClusterDto(whitelistNodeCIDRs=["10.0.0.0/8"])


@respx.mock
@pytest.mark.asyncio
async def test_update_cluster_rejects_empty_body(config, client, respx_mock):
    """An empty partial-update body is a no-op — reject it without calling the API."""
    from greennode.vks_mcp_server.models import UpdateClusterDto
    from mcp.server.fastmcp import FastMCP

    _mock_iam(respx_mock)
    handler = ClusterHandler(FastMCP("t"), config, client, allow_write=True)
    route = respx_mock.put(f"{VKS_BASE}/v1/clusters/k8s-abc").mock(
        return_value=httpx.Response(202, json={})
    )
    result = await handler.update_cluster(
        cluster_id="k8s-abc", body=UpdateClusterDto(), region=None
    )
    assert not route.called
    assert "nothing to update" in result.lower()


@respx.mock
@pytest.mark.asyncio
async def test_configure_auto_upgrade_handles_empty_202(config, client, respx_mock):
    """The auto-upgrade endpoint answers 202 with an empty body — the tool must
    succeed with a clean message (no crash, no trailing 'None')."""
    _mock_iam(respx_mock)
    handler = ClusterHandler(FastMCP("t"), config, client, allow_write=True)
    respx_mock.put(f"{VKS_BASE}/v1/clusters/k8s-abc/auto-upgrade-config").mock(
        return_value=httpx.Response(202)
    )
    result = await handler.configure_auto_upgrade(
        cluster_id="k8s-abc", weekdays="Mon,Wed", time="03:00", region=None
    )
    assert "updated successfully" in result
    assert "None" not in result


# ---------------------------------------------------------------------------
# kubeconfig extraction (the API wraps the YAML in a JSON envelope now)
# ---------------------------------------------------------------------------

_KC_YAML = "apiVersion: v1\nclusters:\n- cluster: {}\ncurrent-context: ctx\n"


def test_extract_kubeconfig_from_json_envelope():
    """The kubeconfig endpoint returns {kubeConfig, status, ...} — the YAML is
    inside the kubeConfig field."""
    from greennode.vks_mcp_server.kubeconfig import extract_kubeconfig

    envelope = _json.dumps({"kubeConfig": _KC_YAML, "status": "ACTIVE", "expirationDays": 90})
    assert extract_kubeconfig(envelope) == _KC_YAML


def test_extract_kubeconfig_passthrough_raw_yaml():
    """Older responses were the bare YAML — still accepted."""
    from greennode.vks_mcp_server.kubeconfig import extract_kubeconfig

    assert extract_kubeconfig(_KC_YAML) == _KC_YAML


def test_extract_kubeconfig_cluster_not_ready():
    """A CREATING cluster's envelope has no kubeConfig — clear, actionable error."""
    from greennode.vks_mcp_server.kubeconfig import extract_kubeconfig

    envelope = _json.dumps({"renewalWarning": None, "status": "CREATING"})
    with pytest.raises(ValueError, match="ACTIVE"):
        extract_kubeconfig(envelope)


@respx.mock
@pytest.mark.asyncio
async def test_get_cluster_kubeconfig_returns_yaml_not_envelope(config, client, respx_mock):
    """The tool must hand back kubectl-ready YAML, not the JSON envelope."""
    _mock_iam(respx_mock)
    handler = ClusterHandler(FastMCP("t"), config, client, allow_sensitive_data_access=True)
    respx_mock.get(f"{VKS_BASE}/v1/clusters/k8s-abc/kubeconfig").mock(
        return_value=httpx.Response(200, json={"kubeConfig": _KC_YAML, "status": "ACTIVE"})
    )
    result = await handler.get_cluster_kubeconfig(cluster_id="k8s-abc", region=None)
    assert result.startswith("apiVersion: v1")
    assert "kubeConfig" not in result  # no envelope leakage


@respx.mock
@pytest.mark.asyncio
async def test_generate_kubeconfig_posts_expiration(config, client, respx_mock):
    """generate_kubeconfig requests async generation (POST {expirationDays})."""
    _mock_iam(respx_mock)
    handler = ClusterHandler(FastMCP("t"), config, client, allow_write=True)
    route = respx_mock.post(f"{VKS_BASE}/v1/clusters/k8s-abc/kubeconfig").mock(
        return_value=httpx.Response(202)
    )
    result = await handler.generate_kubeconfig(
        cluster_id="k8s-abc", expiration_days=30, region=None
    )
    assert route.called
    assert _json.loads(route.calls.last.request.content) == {"expirationDays": 30}
    assert "asynchronous" in result.lower() or "async" in result.lower()
    assert "get_cluster_kubeconfig" in result  # what to do next


@pytest.mark.asyncio
async def test_generate_kubeconfig_is_write_gated(config, client):
    """A read-only handler must not register the tool (it mints credentials)."""
    handler = ClusterHandler(FastMCP("t-ro"), config, client, allow_write=False)
    names = {t.name for t in await handler.mcp.list_tools()}
    assert "generate_kubeconfig" not in names


def test_extract_kubeconfig_not_generated_teaches_generate():
    """A cluster whose kubeconfig was never generated: point at generate_kubeconfig,
    with the ask-the-user step for the expiration (a credential lifetime is a user
    decision) — and no pointer at guides that don't exist."""
    from greennode.vks_mcp_server.kubeconfig import extract_kubeconfig

    envelope = _json.dumps({"renewalWarning": None, "status": "CREATING"})
    with pytest.raises(ValueError) as exc_info:
        extract_kubeconfig(envelope)
    message = str(exc_info.value)
    assert "generate_kubeconfig" in message
    assert "ask the user" in message
    assert "expiration_days" in message
    # get_creation_guide only serves cluster|nodegroup — a 'kubeconfig' pointer
    # would send the agent into a validation error (regression: reverted guide)
    assert "get_creation_guide" not in message


@pytest.mark.asyncio
async def test_generate_kubeconfig_expiration_is_required(config, client):
    """expiration_days has NO schema default: a credential lifetime is a user
    decision, and a default is an escape hatch agents will silently take."""
    handler = ClusterHandler(FastMCP("t-schema"), config, client, allow_write=True)
    tool = next(t for t in await handler.mcp.list_tools() if t.name == "generate_kubeconfig")
    schema = tool.inputSchema
    prop = schema["properties"]["expiration_days"]
    assert "expiration_days" in schema.get("required", [])
    assert "default" not in prop
    # API contract: 1-365 days (QC-confirmed; the old 1825 bound was wrong)
    assert prop["minimum"] == 1
    assert prop["maximum"] == 365


@respx.mock
@pytest.mark.asyncio
async def test_cluster_delete_dryrun_lists_all_node_groups(client):
    """The deletion preview must show EVERY node group — VKS enforces paging
    (default pageSize=10), so the dryrun has to page through like the list tools."""
    _mock_iam(respx.mock)
    respx.get(f"{VKS_BASE}/v1/clusters/cid-1").mock(
        return_value=httpx.Response(200, json={"uid": "cid-1", "name": "big", "status": "ACTIVE"})
    )

    def responder(request):
        page = int(request.url.params.get("page", 0))
        size = int(request.url.params.get("pageSize", 10))
        all_items = [{"uid": f"ng-{i}", "name": f"ng{i}", "nodeCount": 1} for i in range(12)]
        chunk = all_items[page * size : (page + 1) * size]
        return httpx.Response(
            200, json={"items": chunk, "total": 12, "page": page, "pageSize": size}
        )

    respx.get(f"{VKS_BASE}/v1/clusters/cid-1/node-groups").mock(side_effect=responder)
    result = await _cluster_delete_dryrun(client, {"cluster_id": "cid-1"})
    text = result[0].text
    assert "Node groups to be deleted (12)" in text
    assert "ng-11" in text  # the tail beyond one page is present


# ---------------------------------------------------------------------------
# get_cluster_kubeconfig sensitive-data gate (kubeconfig = cluster-admin creds)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cluster_kubeconfig_denied_without_sensitive_flag(config, client):
    """The kubeconfig carries a cluster-admin cert + private key — same gate as
    Secrets/logs: no --allow-sensitive-data-access, no kubeconfig."""
    handler = ClusterHandler(FastMCP("t"), config, client)  # default: no sensitive access
    with pytest.raises(RuntimeError, match="allow-sensitive-data-access"):
        await handler.get_cluster_kubeconfig(cluster_id="k8s-abc", region=None)


@respx.mock
@pytest.mark.asyncio
async def test_get_cluster_kubeconfig_allowed_with_sensitive_flag(config, client, respx_mock):
    _mock_iam(respx_mock)
    handler = ClusterHandler(FastMCP("t"), config, client, allow_sensitive_data_access=True)
    respx_mock.get(f"{VKS_BASE}/v1/clusters/k8s-abc/kubeconfig").mock(
        return_value=httpx.Response(200, json={"kubeConfig": _KC_YAML, "status": "ACTIVE"})
    )
    result = await handler.get_cluster_kubeconfig(cluster_id="k8s-abc", region=None)
    assert result.startswith("apiVersion: v1")


@respx.mock
@pytest.mark.asyncio
async def test_delete_auto_upgrade_handles_empty_202(config, client, respx_mock):
    """DELETE .../auto-upgrade-config also answers 202 with an empty body
    (bug report F-02) — the tool must report success, not a JSON parse error."""
    _mock_iam(respx_mock)
    handler = ClusterHandler(FastMCP("t"), config, client, allow_write=True)
    respx_mock.delete(f"{VKS_BASE}/v1/clusters/k8s-abc/auto-upgrade-config").mock(
        return_value=httpx.Response(202)
    )
    result = await handler.delete_auto_upgrade(cluster_id="k8s-abc", region=None)
    assert "deleted successfully" in result
    assert "None" not in result


def test_cluster_create_validate_rejects_accented_description():
    """F-05a: the API enforces ^[a-zA-Z0-9-_. @]{0,255}$ on description —
    validate must fail early instead of letting create die with a 400."""
    body = {**_VALID_BODY, "description": "Cụm thử nghiệm có dấu"}
    result = _cluster_create_validate({"body": body})
    text = result[0].text
    assert text != "valid"
    assert "description" in text


def test_cluster_create_validate_accepts_ascii_description():
    body = {**_VALID_BODY, "description": "Test cluster v1.0 - team_A @dev"}
    result = _cluster_create_validate({"body": body})
    assert result[0].text == "valid"


def test_cluster_create_validate_rejects_overlong_description():
    body = {**_VALID_BODY, "description": "x" * 256}
    result = _cluster_create_validate({"body": body})
    assert "description" in result[0].text
