"""Tests for registered tool input schemas via FastMCP introspection.

The existing handler tests call internal functions directly and bypass FastMCP
validation, so Literal / Field(ge,le) constraints are only observable on the
registered tool's inputSchema. These tests assert those constraints.
"""

from __future__ import annotations

import pytest
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.cluster_handler import ClusterHandler
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.discovery_handler import DiscoveryHandler
from greennode.vks_mcp_server.k8s_handler import K8sHandler
from greennode.vks_mcp_server.nodegroup_handler import NodeGroupHandler
from greennode.vks_mcp_server.version_handler import VersionHandler
from mcp.server.fastmcp import FastMCP


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VksClient(config, TokenManager(config))


async def _schema_for(register, tool_name):
    """Register handler(s) on a fresh FastMCP and return a tool's inputSchema."""
    mcp = FastMCP("test")
    register(mcp)
    tools = await mcp.list_tools()
    return next(t for t in tools if t.name == tool_name).inputSchema


def _minimum(prop):
    """Extract the numeric 'minimum' whether top-level or inside anyOf (Optional)."""
    if "minimum" in prop:
        return prop["minimum"]
    for sub in prop.get("anyOf", []):
        if "minimum" in sub:
            return sub["minimum"]
    return None


def _maximum(prop):
    """Extract the numeric 'maximum' whether top-level or inside anyOf (Optional)."""
    if "maximum" in prop:
        return prop["maximum"]
    for sub in prop.get("anyOf", []):
        if "maximum" in sub:
            return sub["maximum"]
    return None


@pytest.mark.asyncio
async def test_manage_k8s_resource_operation_is_enum(config, client):
    schema = await _schema_for(
        lambda mcp: K8sHandler(
            mcp, config, client, allow_write=True, allow_sensitive_data_access=True
        ),
        "manage_k8s_resource",
    )
    enum = schema["properties"]["operation"]["enum"]
    assert set(enum) == {"create", "replace", "patch", "delete", "read"}


@pytest.mark.asyncio
async def test_get_pod_logs_numeric_constraints(config, client):
    schema = await _schema_for(
        lambda mcp: K8sHandler(
            mcp, config, client, allow_write=True, allow_sensitive_data_access=True
        ),
        "get_pod_logs",
    )
    props = schema["properties"]
    assert props["tail_lines"]["minimum"] == 1
    assert props["limit_bytes"]["minimum"] == 1
    assert _minimum(props["since_seconds"]) == 0


@pytest.mark.asyncio
async def test_cluster_list_has_no_paging_params(config, client):
    """list_clusters fetches every page itself — agents never paginate."""
    schema = await _schema_for(
        lambda mcp: ClusterHandler(mcp, config, client, allow_write=True),
        "list_clusters",
    )
    props = schema["properties"]
    assert "page" not in props
    assert "pageSize" not in props


@pytest.mark.asyncio
async def test_cluster_create_body_lists_valid_values(config, client):
    schema = await _schema_for(
        lambda mcp: ClusterHandler(mcp, config, client, allow_write=True),
        "create_cluster",
    )
    # body is now a typed DTO — validate via $defs enum constraints
    defs = schema.get("$defs", {})
    dto_def = defs.get("CreateClusterComboDto", {})
    release_channel_enum = dto_def["properties"]["releaseChannel"]["enum"]
    assert "RAPID" in release_channel_enum and "STABLE" in release_channel_enum
    network_type_enum = dto_def["properties"]["networkType"]["enum"]
    assert "CILIUM_NATIVE_ROUTING" in network_type_enum
    assert "secondarySubnets" in dto_def["properties"]


@pytest.mark.asyncio
async def test_nodegroup_list_nodes_has_no_paging_params(config, client):
    """list_nodes fetches every page itself — agents never paginate."""
    schema = await _schema_for(
        lambda mcp: NodeGroupHandler(mcp, config, client, allow_write=True),
        "list_nodes",
    )
    props = schema["properties"]
    assert "page" not in props
    assert "pageSize" not in props


@pytest.mark.asyncio
async def test_nodegroup_create_body_documents_ranges(config, client):
    schema = await _schema_for(
        lambda mcp: NodeGroupHandler(mcp, config, client, allow_write=True),
        "create_nodegroup",
    )
    desc = schema["properties"]["body"]["description"]
    assert "20-5000" in desc
    assert "0-10" in desc


@pytest.mark.asyncio
async def test_cluster_auto_healing_config_constraints(config, client):
    schema = await _schema_for(
        lambda mcp: ClusterHandler(mcp, config, client, allow_write=True),
        "configure_auto_healing",
    )
    props = schema["properties"]
    assert _minimum(props["timeout_unhealthy"]) == 5
    assert _maximum(props["timeout_unhealthy"]) == 180
    assert "enable_auto_healing" in schema["required"]
    assert props["enable_auto_healing"]["type"] == "boolean"


async def _description_for(register, tool_name):
    """Register handler(s) on a fresh FastMCP and return a tool's description (docstring)."""
    mcp = FastMCP("test")
    register(mcp)
    tools = await mcp.list_tools()
    return next(t for t in tools if t.name == tool_name).description


@pytest.mark.asyncio
async def test_version_tools_have_workflow_hints(config, client):
    from greennode.vks_mcp_server.discovery_cache import DiscoveryCache

    cv = await _description_for(
        lambda mcp: VersionHandler(mcp, config, client, DiscoveryCache()), "list_cluster_versions"
    )
    assert "create_cluster" in cv


@pytest.mark.asyncio
async def test_generate_app_manifest_schema(config, client):
    schema = await _schema_for(
        lambda mcp: K8sHandler(
            mcp, config, client, allow_write=True, allow_sensitive_data_access=True
        ),
        "generate_app_manifest",
    )
    props = schema["properties"]
    assert set(props["load_balancer_scheme"]["enum"]) == {"internet-facing", "internal"}
    assert props["port"]["minimum"] == 1
    assert props["port"]["maximum"] == 65535
    assert props["replicas"]["minimum"] == 1


@pytest.mark.asyncio
async def test_discovery_tools_registered(config, client):
    mcp = FastMCP("test")
    DiscoveryHandler(mcp, config, client, DiscoveryCache())
    names = {t.name for t in await mcp.list_tools()}
    assert {
        "list_vpcs",
        "list_subnets",
        "list_flavors",
        "list_ssh_keys",
        "list_security_groups",
    } <= names


@pytest.mark.asyncio
async def test_structured_tools_have_output_schema(config, client):
    mcp = FastMCP("test")
    ClusterHandler(mcp, config, client, allow_write=True)
    NodeGroupHandler(mcp, config, client, allow_write=True)
    DiscoveryHandler(mcp, config, client, DiscoveryCache())
    VersionHandler(mcp, config, client, DiscoveryCache())
    K8sHandler(mcp, config, client, allow_write=True, allow_sensitive_data_access=True)
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}
    for name in [
        "list_vpcs",
        "list_clusters",
        "get_cluster",
        "list_nodegroups",
        "list_cluster_versions",
        "list_k8s_resources",
    ]:
        assert by_name[name].outputSchema is not None, f"{name} missing outputSchema"


@pytest.mark.asyncio
async def test_create_dto_field_descriptions_state_the_rules(config, client):
    """F-05: schema field descriptions must carry the constraints agents can
    break — not bare labels like 'Cluster description' / 'Node group name'."""
    from greennode.vks_mcp_server.nodegroup_handler import NodeGroupHandler

    cluster_schema = await _schema_for(
        lambda mcp: ClusterHandler(mcp, config, client, allow_write=True), "create_cluster"
    )
    dto = cluster_schema["$defs"]["CreateClusterComboDto"]["properties"]
    desc_doc = dto["description"]["description"]
    assert "255" in desc_doc
    assert "ASCII" in desc_doc or "accented" in desc_doc.lower()

    ng_schema = await _schema_for(
        lambda mcp: NodeGroupHandler(mcp, config, client, allow_write=True), "create_nodegroup"
    )
    name_doc = ng_schema["$defs"]["CreateNodeGroupDto"]["properties"]["name"]["description"]
    assert "5-15" in name_doc
