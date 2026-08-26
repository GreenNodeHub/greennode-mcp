"""Tests for the vMonitor dashboard-view tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import CreateViewDto, UpdateViewDto, ViewSummary
from greennode.vmonitor_mcp_server.view_handler import ViewHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"
DASH = "dsh-1"

VIEW = {
    "id": "view-1",
    "name": "Last 24h",
    "dashboardId": DASH,
    "filters": "f",
    "query": "q",
    "timeRange": "t",
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return ViewHandler(MCPServer("test"), config, client)


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return ViewHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.mark.asyncio
async def test_read_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert {"list_dashboard_views", "get_dashboard_view"} <= read_only
    assert "create_dashboard_view" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {
        "create_dashboard_view",
        "update_dashboard_view",
        "delete_dashboard_view",
    } <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_dashboard_views_parses_array(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}/views").mock(return_value=httpx.Response(200, json=[VIEW]))

    result = await handler.list_dashboard_views(dashboard_id=DASH)

    assert result.count == 1
    assert result.items[0].name == "Last 24h"


@respx.mock
@pytest.mark.asyncio
async def test_create_dashboard_view_sends_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/dashboards/{DASH}/views").mock(
        return_value=httpx.Response(200, json=VIEW)
    )

    body = CreateViewDto(name="Last 24h", variables={"host": "srv-1"}, filters="f")
    result = await handler_rw.create_dashboard_view(dashboard_id=DASH, body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "name": "Last 24h",
        "variables": {"host": "srv-1"},
        "filters": "f",
        "query": "{}",
        "timeRange": "{}",
    }
    assert isinstance(result, ViewSummary)
    assert result.id == "view-1"


@respx.mock
@pytest.mark.asyncio
async def test_create_dashboard_view_defaults_state_when_name_only(handler_rw):
    """A name-only body 500s upstream; the tool defaults all four state fields."""
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/dashboards/{DASH}/views").mock(
        return_value=httpx.Response(200, json=VIEW)
    )

    await handler_rw.create_dashboard_view(dashboard_id=DASH, body=CreateViewDto(name="v"))

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "name": "v",
        "variables": {},
        "filters": "[]",
        "query": "{}",
        "timeRange": "{}",
    }


@respx.mock
@pytest.mark.asyncio
async def test_update_dashboard_view_sends_only_provided(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/dashboards/{DASH}/views/view-1").mock(
        return_value=httpx.Response(200, json=VIEW)
    )

    body = UpdateViewDto(query="q2")
    await handler_rw.update_dashboard_view(dashboard_id=DASH, view_id="view-1", body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"query": "q2"}


@respx.mock
@pytest.mark.asyncio
async def test_delete_dashboard_view_confirms(handler_rw):
    _mock_iam(respx.mock)
    respx.delete(f"{API}/dashboards/{DASH}/views/view-1").mock(return_value=httpx.Response(200))

    msg = await handler_rw.delete_dashboard_view(dashboard_id=DASH, view_id="view-1")

    assert "view-1" in msg


@pytest.mark.asyncio
async def test_delete_rejects_bad_id(handler_rw):
    with pytest.raises(ValueError):
        await handler_rw.delete_dashboard_view(dashboard_id=DASH, view_id="../../secret")


def test_view_dtos_forbid_extra():
    with pytest.raises(ValidationError):
        CreateViewDto(name="x", bogus=1)
    with pytest.raises(ValidationError):
        UpdateViewDto(bogus=1)
