"""Tests for the vMonitor MCP server wiring."""

from __future__ import annotations

import pytest
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.dashboard_handler import DashboardHandler
from greennode.vmonitor_mcp_server.server import create_server
from mcp.server.mcpserver import MCPServer


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VmonitorClient(config, TokenManager(config))


@pytest.fixture
def handler(config, client):
    return DashboardHandler(MCPServer("test"), config, client)


def test_create_server():
    server = create_server()
    assert server.name == "vmonitor-mcp-server"


def test_config_base_url_ignores_region(config):
    assert config.get_base_url(None, "vmonitor") == "https://vmonitorapis.vngcloud.vn/vmonitor-api"
    assert config.get_base_url("anything", "vmonitor") == config.base_url


@pytest.mark.asyncio
async def test_list_dashboards_registered(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert "list_dashboards" in tools
