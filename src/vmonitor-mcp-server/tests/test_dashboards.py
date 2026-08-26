"""Tests for the vMonitor list_dashboards tool."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.dashboard_handler import DashboardHandler
from greennode.vmonitor_mcp_server.models import DashboardDetail, DashboardListData
from mcp.server.mcpserver import MCPServer


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
DASHBOARDS_URL = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1/dashboards"

SAMPLE_DASHBOARD = {
    "id": "dash-1",
    "name": "My dashboard",
    "favorite": False,
    "system": False,
    "darkMode": False,
    "timeRange": '{"timeRange":1}',
    "timeRangeType": "DEFAULT",
    "refreshActive": False,
    "refreshInterval": 10,
    "viewSelectedId": "view-9",
    "createdUser": 107710,
    "createdAt": "2026-01-01T00:00:00Z",
    "updatedAt": "2026-01-02T00:00:00Z",
    "widgets": [{"id": "w1"}, {"id": "w2"}],
}

SAMPLE_ENVELOPE = {
    "lstData": [
        {
            "id": "dash-1",
            "name": "vServer overview",
            "favorite": True,
            "system": True,
            "darkMode": False,
            "timeRange": "1h",
            "timeRangeType": "RELATIVE",
            "refreshActive": True,
            "refreshInterval": 30,
            "createdUser": 42,
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-02-01T00:00:00Z",
        }
    ],
    "page": 1,
    "pageSize": 5,
    "totalItem": 1,
    "totalPage": 1,
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def handler(config):
    client = VmonitorClient(config, TokenManager(config))
    return DashboardHandler(MCPServer("test"), config, client)


@pytest.fixture
def handler_rw(config):
    client = VmonitorClient(config, TokenManager(config))
    return DashboardHandler(MCPServer("test"), config, client, allow_write=True)


@respx.mock
@pytest.mark.asyncio
async def test_list_dashboards_returns_structured(handler):
    _mock_iam(respx.mock)
    respx.get(DASHBOARDS_URL).mock(return_value=httpx.Response(200, json=SAMPLE_ENVELOPE))

    result = await handler.list_dashboards(
        searching_text=None, searching_field="name", page=None, size=None
    )

    assert isinstance(result, DashboardListData)
    assert result.total_item == 1
    assert len(result.items) == 1
    item = result.items[0]
    assert item.id == "dash-1"
    assert item.name == "vServer overview"
    assert item.favorite is True
    assert item.refresh_interval == 30


@respx.mock
@pytest.mark.asyncio
async def test_list_dashboards_no_params_omits_query(handler):
    _mock_iam(respx.mock)
    route = respx.get(DASHBOARDS_URL).mock(return_value=httpx.Response(200, json=SAMPLE_ENVELOPE))

    await handler.list_dashboards(
        searching_text=None, searching_field="name", page=None, size=None
    )

    assert route.called
    assert route.calls.last.request.url.params.multi_items() == []


@respx.mock
@pytest.mark.asyncio
async def test_list_dashboards_forwards_search_and_paging(handler):
    _mock_iam(respx.mock)
    route = respx.get(DASHBOARDS_URL).mock(return_value=httpx.Response(200, json=SAMPLE_ENVELOPE))

    await handler.list_dashboards(
        searching_text="vServer", searching_field="name", page=2, size=10
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {
        "searching-field": "name",
        "searching-text": "vServer",
        "page": "2",
        "size": "10",
    }


@pytest.mark.asyncio
async def test_write_tools_gated_by_allow_write(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert "get_dashboard" in read_only
    assert "create_dashboard" not in read_only
    assert "delete_dashboard" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {
        "create_dashboard",
        "create_dashboard_clone",
        "update_dashboard_name",
        "update_dashboard_favorite",
        "delete_dashboard",
    } <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_get_dashboard_returns_detail(handler):
    _mock_iam(respx.mock)
    respx.get(f"{DASHBOARDS_URL}/dash-1").mock(
        return_value=httpx.Response(200, json=SAMPLE_DASHBOARD)
    )

    result = await handler.get_dashboard(dashboard_id="dash-1")

    assert isinstance(result, DashboardDetail)
    assert result.id == "dash-1"
    assert result.view_selected_id == "view-9"
    assert result.widget_count == 2


@pytest.mark.asyncio
async def test_get_dashboard_rejects_bad_id(handler):
    with pytest.raises(ValueError):
        await handler.get_dashboard(dashboard_id="../etc/passwd")


@respx.mock
@pytest.mark.asyncio
async def test_create_dashboard_posts_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(DASHBOARDS_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE_DASHBOARD)
    )
    from greennode.vmonitor_mcp_server.models import CreateDashboardDto

    result = await handler_rw.create_dashboard(body=CreateDashboardDto(name="My dashboard"))

    assert isinstance(result, DashboardDetail)
    assert result.name == "My dashboard"
    import json

    assert json.loads(route.calls.last.request.content) == {"name": "My dashboard"}


@respx.mock
@pytest.mark.asyncio
async def test_clone_dashboard_unwraps_envelope(handler_rw):
    _mock_iam(respx.mock)
    cloned = {**SAMPLE_DASHBOARD, "id": "dash-2", "name": "Clone"}
    route = respx.post(f"{DASHBOARDS_URL}/clone").mock(
        return_value=httpx.Response(200, json={"data": cloned})
    )

    result = await handler_rw.create_dashboard_clone(dashboard_id="dash-1", name="Clone")

    assert result.id == "dash-2"
    assert result.name == "Clone"
    import json

    assert json.loads(route.calls.last.request.content) == {"id": "dash-1", "name": "Clone"}


@respx.mock
@pytest.mark.asyncio
async def test_rename_dashboard_puts_body(handler_rw):
    _mock_iam(respx.mock)
    renamed = {**SAMPLE_DASHBOARD, "name": "Renamed"}
    route = respx.put(f"{DASHBOARDS_URL}/rename").mock(
        return_value=httpx.Response(200, json=renamed)
    )

    result = await handler_rw.update_dashboard_name(dashboard_id="dash-1", name="Renamed")

    assert result.name == "Renamed"
    import json

    assert json.loads(route.calls.last.request.content) == {"id": "dash-1", "name": "Renamed"}


@respx.mock
@pytest.mark.asyncio
async def test_favorite_dashboard_puts_body(handler_rw):
    _mock_iam(respx.mock)
    fav = {**SAMPLE_DASHBOARD, "favorite": True}
    route = respx.put(f"{DASHBOARDS_URL}/favorite").mock(
        return_value=httpx.Response(200, json=fav)
    )

    result = await handler_rw.update_dashboard_favorite(dashboard_id="dash-1", favorite=True)

    assert result.favorite is True
    import json

    assert json.loads(route.calls.last.request.content) == {"id": "dash-1", "favorite": True}


@respx.mock
@pytest.mark.asyncio
async def test_delete_dashboard_returns_confirmation(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{DASHBOARDS_URL}/dash-1").mock(return_value=httpx.Response(200))

    result = await handler_rw.delete_dashboard(dashboard_id="dash-1")

    assert route.called
    assert "dash-1" in result


@respx.mock
@pytest.mark.asyncio
async def test_get_dashboard_by_name_encodes_segment(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{DASHBOARDS_URL}/name/My%20dashboard").mock(
        return_value=httpx.Response(200, json=SAMPLE_DASHBOARD)
    )

    result = await handler.get_dashboard_by_name(name="My dashboard")

    assert route.called
    assert isinstance(result, DashboardDetail)
    assert result.id == "dash-1"


@pytest.mark.asyncio
async def test_get_dashboard_by_name_rejects_path_separator(handler):
    with pytest.raises(ValueError):
        await handler.get_dashboard_by_name(name="../secret")


@pytest.mark.asyncio
async def test_new_dashboard_write_tools_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert "get_dashboard_by_name" in read_only
    assert "update_dashboard" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {"update_dashboard", "create_dashboard", "delete_dashboard"} <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_update_dashboard_puts_settings(handler_rw):
    import json
    from greennode.vmonitor_mcp_server.models import UpdateDashboardDto

    _mock_iam(respx.mock)
    route = respx.put(DASHBOARDS_URL).mock(return_value=httpx.Response(200, json=SAMPLE_DASHBOARD))

    body = UpdateDashboardDto(id="dash-1", darkMode=True, refreshInterval=30)
    result = await handler_rw.update_dashboard(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"id": "dash-1", "darkMode": True, "refreshInterval": 30}
    assert isinstance(result, DashboardDetail)


def test_new_dashboard_dtos_forbid_extra():
    from greennode.vmonitor_mcp_server.models import UpdateDashboardDto
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UpdateDashboardDto(id="d", bogus=1)
