"""Tests for the {{PRODUCT}} MCP server scaffold."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.mcp_core.auth import TokenManager
from greennode.{{product_snake}}_mcp_server.client import {{Product}}Client
from greennode.{{product_snake}}_mcp_server.config import load_config
from greennode.{{product_snake}}_mcp_server.example_handler import ExampleHandler, ExampleListData
from greennode.{{product_snake}}_mcp_server.server import create_server
from mcp.server.fastmcp import FastMCP


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API_BASE = "https://{{product}}.api.vngcloud.vn"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return {{Product}}Client(config, TokenManager(config))


@pytest.fixture
def handler(config, client):
    return ExampleHandler(FastMCP("test"), config, client)


def test_create_server():
    server = create_server()
    assert server.name == "{{product}}-mcp-server"


@pytest.mark.asyncio
async def test_list_examples_registered(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert "list_examples" in tools


@respx.mock
@pytest.mark.asyncio
async def test_list_examples_returns_structured(config, client, handler):
    _mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/examples").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "ex-1", "name": "demo", "status": "ACTIVE"}]}
        )
    )
    result = await handler.list_examples(region=None)
    assert isinstance(result, ExampleListData)
    assert result.items[0].id == "ex-1"
