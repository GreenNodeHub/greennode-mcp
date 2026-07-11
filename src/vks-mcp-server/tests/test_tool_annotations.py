"""Convention: every MCP tool declares ToolAnnotations (readOnlyHint/destructiveHint).

Clients use these hints to auto-approve reads and warn before destructive calls,
so a missing or wrong hint silently downgrades the UX for every tool call.
Naming is the contract: list_/get_/validate_/generate_/*_dryrun tools are reads;
delete_* (non-dryrun) tools are destructive writes; everything else registered
behind allow_write is a non-destructive write.
"""

from __future__ import annotations

import pytest
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.auth_handler import AuthHandler
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.cluster_handler import ClusterHandler
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.discovery_handler import DiscoveryHandler
from greennode.vks_mcp_server.k8s_handler import K8sHandler
from greennode.vks_mcp_server.nodegroup_handler import NodeGroupHandler
from greennode.vks_mcp_server.version_handler import VersionHandler
from mcp.server.fastmcp import FastMCP


READ_ONLY_PREFIXES = ("list_", "get_", "validate_", "generate_")

# Write tools that are destructive beyond plain delete_* naming:
# manage_k8s_resource supports the delete operation; upgrade_nodegroup_version
# cannot be rolled back (Kubernetes does not downgrade).
EXTRA_DESTRUCTIVE = {"manage_k8s_resource", "upgrade_nodegroup_version"}


def _is_read_only(name: str) -> bool:
    return name.startswith(READ_ONLY_PREFIXES) or name.endswith("_dryrun")


def _is_destructive(name: str) -> bool:
    return (name.startswith("delete_") and not name.endswith("_dryrun")) or (
        name in EXTRA_DESTRUCTIVE
    )


@pytest.fixture
def all_tools_mcp(sample_config):
    """One FastMCP with every handler registered, write tools included."""
    config = load_config(sample_config)
    client = VksClient(config, TokenManager(config))
    cache = DiscoveryCache()
    mcp = FastMCP("test-annotations")
    AuthHandler(mcp, config, client._token_manager)
    ClusterHandler(mcp, config, client, allow_write=True)
    NodeGroupHandler(mcp, config, client, allow_write=True)
    DiscoveryHandler(mcp, config, client, cache)
    VersionHandler(mcp, config, client, cache)
    K8sHandler(mcp, config, client, allow_write=True, allow_sensitive_data_access=True)
    return mcp


@pytest.mark.asyncio
async def test_every_tool_declares_annotations(all_tools_mcp):
    missing = [
        t.name
        for t in await all_tools_mcp.list_tools()
        if t.annotations is None or t.annotations.readOnlyHint is None
    ]
    assert not missing, f"tools without annotations/readOnlyHint: {missing}"


@pytest.mark.asyncio
async def test_read_tools_marked_read_only(all_tools_mcp):
    wrong = [
        t.name
        for t in await all_tools_mcp.list_tools()
        if _is_read_only(t.name)
        and (t.annotations is None or t.annotations.readOnlyHint is not True)
    ]
    assert not wrong, f"read tools not marked readOnlyHint=True: {wrong}"


@pytest.mark.asyncio
async def test_write_tools_marked_writable_with_destructive_hint(all_tools_mcp):
    wrong = []
    for t in await all_tools_mcp.list_tools():
        if _is_read_only(t.name):
            continue
        a = t.annotations
        if a is None or a.readOnlyHint is not False or a.destructiveHint is None:
            wrong.append(t.name)
            continue
        if a.destructiveHint is not _is_destructive(t.name):
            wrong.append(t.name)
    assert not wrong, f"write tools with wrong readOnly/destructive hints: {wrong}"


@pytest.mark.asyncio
async def test_cluster_read_tools_teach_the_creation_chains(all_tools_mcp):
    """get_cluster/list_clusters descriptions wire agents into the create flows."""
    tools = {t.name: t for t in await all_tools_mcp.list_tools()}

    get_desc = tools["get_cluster"].description
    # get_cluster is step 1 of the create_nodegroup chain: it yields vpcId
    assert "vpcId" in get_desc or "vpc_id" in get_desc
    assert "create_nodegroup" in get_desc
    assert "list_subnets" in get_desc

    list_desc = tools["list_clusters"].description
    # list_clusters resolves a cluster name to its id; no paging params exposed
    assert "get_cluster" in list_desc
    props = tools["list_clusters"].inputSchema["properties"]
    assert "page" not in props and "pageSize" not in props


@pytest.mark.asyncio
async def test_create_cluster_description_has_discovery_workflow(all_tools_mcp):
    """create_cluster's ## Workflow sources every required id from discovery."""
    tools = {t.name: t for t in await all_tools_mcp.list_tools()}
    desc = tools["create_cluster"].description
    assert "## Workflow" in desc
    for name in (
        "get_quota",
        "list_vpcs",  # source of the required vpcId
        "list_cluster_versions",
        "validate_cluster_create",
        "vks_create_cluster",  # cross-ref to the full guided prompt
    ):
        assert name in desc, f"{name} missing from create_cluster description"
    # validate runs before create, quota before picking anything
    assert desc.index("get_quota") < desc.index("list_vpcs")
    assert desc.index("validate_cluster_create") < desc.rindex("create_cluster")


@pytest.mark.asyncio
async def test_upgrade_nodegroup_description_warns_irreversible(all_tools_mcp):
    """upgrade_nodegroup_version teaches the version constraint and no-rollback."""
    tools = {t.name: t for t in await all_tools_mcp.list_tools()}
    desc = tools["upgrade_nodegroup_version"].description
    assert "## Workflow" in desc
    assert "get_cluster" in desc  # control-plane version bounds the worker version
    assert "update_cluster" in desc  # how to raise that bound first
    assert "get_nodegroup" in desc  # current version + post-upgrade polling
    assert "IMPORTANT" in desc


@pytest.mark.asyncio
async def test_server_instructions_cover_every_tool(all_tools_mcp):
    """SERVER_INSTRUCTIONS is the always-in-context routing layer — a registered
    tool missing from it is invisible at the session level."""
    from greennode.vks_mcp_server.server import SERVER_INSTRUCTIONS

    missing = [
        t.name for t in await all_tools_mcp.list_tools() if t.name not in SERVER_INSTRUCTIONS
    ]
    assert not missing, f"tools missing from SERVER_INSTRUCTIONS: {missing}"


def test_server_instructions_teach_flows_regions_and_prompts():
    """Session-level guidance: creation chains, region model, guided prompts."""
    from greennode.vks_mcp_server.server import SERVER_INSTRUCTIONS

    text = SERVER_INSTRUCTIONS
    # the zone-scoped nodegroup chain, in order
    for a, b in [
        ("get_cluster", "list_subnets"),
        ("list_subnets", "list_flavors"),
        ("list_subnets", "list_volume_types"),
    ]:
        assert text.index(a) < text.rindex(b), f"{a} must come before {b} in the chain"
    # region model
    assert "HCM-3" in text and "HAN" in text
    # guided prompts are discoverable
    for p in ("vks_getting_started", "vks_create_cluster", "vks_create_nodegroup"):
        assert p in text, f"prompt {p} missing"


@pytest.mark.asyncio
async def test_zone_scoped_tools_take_cluster_and_subnet(all_tools_mcp):
    """list_flavors / list_volume_types derive region+zone server-side from ids
    the agent already holds — no zone/region juggling across calls."""
    tools = {t.name: t for t in await all_tools_mcp.list_tools()}
    for name in ("list_flavors", "list_volume_types"):
        schema = tools[name].inputSchema
        props = schema["properties"]
        required = schema.get("required", [])
        assert "cluster_id" in props and "subnet_id" in props, name
        assert "cluster_id" in required and "subnet_id" in required, name
        assert "zone" not in props, f"{name} must not expose zone"
        assert "region" not in props, f"{name} must not expose region (derived)"
