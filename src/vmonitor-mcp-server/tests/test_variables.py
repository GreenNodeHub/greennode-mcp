"""Tests for the vMonitor dashboard-variable tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    UpdateVariableListDto,
    VariableListData,
    VariableSummary,
)
from greennode.vmonitor_mcp_server.variable_handler import VariableHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"
DASH = "dsh-1"

VARIABLES = [
    {
        "id": "var-1",
        "key": "host",
        "name": "Host",
        "currentValue": "srv-1",
        "defaultValue": "srv-1",
        "values": ["srv-1", "srv-2"],
        "isDynamic": True,
        "dashboardId": DASH,
    }
]


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return VariableHandler(MCPServer("test"), config, client)


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return VariableHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.mark.asyncio
async def test_read_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert {"list_dashboard_variables", "get_dashboard_variable"} <= read_only
    assert "update_dashboard_variables" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert "update_dashboard_variables" in with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_dashboard_variables_parses_array(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}/variables").mock(
        return_value=httpx.Response(200, json=VARIABLES)
    )

    result = await handler.list_dashboard_variables(dashboard_id=DASH)

    assert isinstance(result, VariableListData)
    assert result.count == 1
    assert result.items[0].key == "host"
    assert result.items[0].values == ["srv-1", "srv-2"]
    assert result.items[0].is_dynamic is True


@respx.mock
@pytest.mark.asyncio
async def test_get_dashboard_variable_parses(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}/variables/var-1").mock(
        return_value=httpx.Response(200, json=VARIABLES[0])
    )

    result = await handler.get_dashboard_variable(dashboard_id=DASH, variable_id="var-1")

    assert isinstance(result, VariableSummary)
    assert result.id == "var-1"
    assert result.current_value == "srv-1"


@respx.mock
@pytest.mark.asyncio
async def test_update_dashboard_variables_sends_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/dashboards/{DASH}/variables").mock(
        return_value=httpx.Response(200, json=VARIABLES)
    )

    body = UpdateVariableListDto(variables=[{"key": "host", "values": ["srv-1"]}])
    result = await handler_rw.update_dashboard_variables(dashboard_id=DASH, body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"variables": [{"key": "host", "values": ["srv-1"]}]}
    assert result.count == 1


@pytest.mark.asyncio
async def test_list_rejects_bad_dashboard_id(handler):
    with pytest.raises(ValueError):
        await handler.list_dashboard_variables(dashboard_id="../../secret")


def test_update_dto_forbids_extra():
    with pytest.raises(ValidationError):
        UpdateVariableListDto(variables=[], bogus=1)
