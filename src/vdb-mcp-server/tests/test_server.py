"""Tests for the vDB MCP server wiring."""

from __future__ import annotations

import pytest
from greennode.mcp_core.auth import TokenManager
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import load_config
from greennode.vdb_mcp_server.server import (
    SERVER_INSTRUCTIONS,
    create_server,
    register_handlers,
)


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VdbClient(config, TokenManager(config))


@pytest.fixture
def server(config, client):
    mcp = create_server()
    register_handlers(mcp, config, client, allow_write=False)
    return mcp


def test_create_server():
    server = create_server()
    assert server.name == "vdb-mcp-server"


@pytest.mark.asyncio
async def test_catalogue_tools_are_wired_in(server):
    tools = {t.name for t in await server.list_tools()}
    assert "list_relational_datastores" in tools
    assert "list_memory_flavors" in tools


@pytest.mark.asyncio
async def test_read_only_server_registers_no_write_tools(server):
    """Nothing mutating may appear without --allow-write."""
    write_prefixes = ("create_", "update_", "delete_", "restore_", "resize_", "start_", "stop_")
    tools = {t.name for t in await server.list_tools()}
    mutating = [t for t in tools if t.startswith(write_prefixes) and not t.endswith("_dryrun")]
    assert not mutating, mutating


@pytest.mark.asyncio
async def test_no_tool_takes_a_region_argument(server):
    """vDB has one gateway for the whole account -- a region argument would be a lie."""
    for tool in await server.list_tools():
        assert "region" not in (tool.input_schema.get("properties") or {}), tool.name


def test_instructions_state_the_mixed_listing_and_the_order_flow():
    """The two things an agent cannot infer from a tool signature."""
    assert "MIXED listing" in SERVER_INSTRUCTIONS
    assert "read-only" in SERVER_INSTRUCTIONS
    assert "_dryrun" in SERVER_INSTRUCTIONS
