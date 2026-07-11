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
