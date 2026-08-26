"""Tests for the vMonitor metric API key tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.apikey_handler import ApiKeyHandler
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    ApiKeyListData,
    ApiKeySummary,
    CreateMetricApiKeyDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"

ENVELOPE = {
    "lstData": [{"name": "agent-1", "key": "abc123", "description": "prod"}],
    "page": 1,
    "pageSize": 10,
    "totalItem": 1,
    "totalPage": 1,
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    return ApiKeyHandler(MCPServer("test"), config, VmonitorClient(config, TokenManager(config)))


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    return ApiKeyHandler(
        MCPServer("test"), config, VmonitorClient(config, TokenManager(config)), allow_write=True
    )


@pytest.mark.asyncio
async def test_read_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert "list_metric_api_keys" in read_only
    assert "create_metric_api_key" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {"create_metric_api_key", "delete_metric_api_key"} <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_metric_api_keys_parses(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/apikeys/metric/list").mock(
        return_value=httpx.Response(200, json=ENVELOPE)
    )

    result = await handler.list_metric_api_keys(name=None, page=1, size=100)

    assert isinstance(result, ApiKeyListData)
    assert result.total_item == 1
    assert result.items[0].key == "abc123"
    sent = dict(route.calls.last.request.url.params)
    assert sent["page"] == "1" and sent["size"] == "100"


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_api_key_posts_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/apikeys/metric").mock(
        return_value=httpx.Response(200, json={"name": "agent-1", "key": "xyz", "description": ""})
    )

    body = CreateMetricApiKeyDto(name="agent-1")
    result = await handler_rw.create_metric_api_key(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"name": "agent-1"}
    assert isinstance(result, ApiKeySummary)
    assert result.key == "xyz"


@respx.mock
@pytest.mark.asyncio
async def test_delete_metric_api_key_encodes_and_confirms(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{API}/apikeys/metric/a%2Bb%3Dc").mock(return_value=httpx.Response(200))

    result = await handler_rw.delete_metric_api_key(key="a+b=c")

    assert route.called
    assert "a+b=c" in result


@pytest.mark.asyncio
async def test_delete_metric_api_key_rejects_separator(handler_rw):
    with pytest.raises(ValueError):
        await handler_rw.delete_metric_api_key(key="a/b")


def test_create_dto_forbids_extra():
    with pytest.raises(ValidationError):
        CreateMetricApiKeyDto(name="x", bogus=1)
