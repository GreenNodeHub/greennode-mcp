"""Tests for the vMonitor dashboard-widget tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    CreateWidgetDto,
    GraphRequestDto,
    UpdateWidgetLayoutDto,
    UpdateWidgetV2Dto,
    WidgetDetail,
)
from greennode.vmonitor_mcp_server.widget_handler import WidgetHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"
DASH = "dsh-1"

WIDGET_ENVELOPE = {
    "data": {
        "id": "wid-1",
        "name": "CPU",
        "type": {"name": "Metric"},
        "typeChart": "line",
        "period": 60,
        "metricGraphs": [{"id": "g1", "statistic": "avg"}],
        "logGraphs": [],
    }
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return WidgetHandler(MCPServer("test"), config, client)


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return WidgetHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.mark.asyncio
async def test_read_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert "get_widget" in read_only
    assert "create_widget" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {
        "create_widget",
        "update_widget",
        "update_widget_v2",
        "update_widget_layout",
        "delete_widget",
    } <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_get_widget_unwraps_envelope(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}/widgets/wid-1").mock(
        return_value=httpx.Response(200, json=WIDGET_ENVELOPE)
    )

    result = await handler.get_widget(dashboard_id=DASH, widget_id="wid-1")

    assert isinstance(result, WidgetDetail)
    assert result.id == "wid-1"
    assert result.type == "Metric"
    assert result.type_chart == "line"
    assert result.metric_graphs == [{"id": "g1", "statistic": "avg"}]


@respx.mock
@pytest.mark.asyncio
async def test_create_widget_posts_v2_and_parses(handler_rw):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}").mock(
        return_value=httpx.Response(200, json={"widgets": []})
    )
    route = respx.post(f"{API}/dashboards/{DASH}/widgets/v2").mock(
        return_value=httpx.Response(200, json=WIDGET_ENVELOPE)
    )

    body = CreateWidgetDto(
        name="CPU",
        typeChart="line",
        graphs={"a": GraphRequestDto(type="METRIC_GRAPH", data={"statistic": "avg"})},
    )
    result = await handler_rw.create_widget(dashboard_id=DASH, body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["name"] == "CPU"
    assert sent["type"] == "Metric"
    assert sent["graphs"] == {"a": {"type": "METRIC_GRAPH", "data": {"statistic": "avg"}}}
    assert sent["layout"] == "cols:5, rows:2, x:0, y:0"
    assert sent["position"] == "BOTTOM"
    assert sent["fixedTimeRange"] == "global"
    assert result.id == "wid-1"


@respx.mock
@pytest.mark.asyncio
async def test_create_widget_auto_places_without_overlap(handler_rw):
    """A new widget is slotted into the first free cell of the 10-col grid."""
    _mock_iam(respx.mock)
    existing = {
        "widgets": [
            {"layout": "cols:5, rows:2, x:0, y:0"},
            {"layout": "cols:5, rows:2, x:5, y:0"},
        ]
    }
    respx.get(f"{API}/dashboards/{DASH}").mock(return_value=httpx.Response(200, json=existing))
    route = respx.post(f"{API}/dashboards/{DASH}/widgets/v2").mock(
        return_value=httpx.Response(200, json=WIDGET_ENVELOPE)
    )

    body = CreateWidgetDto(
        name="Next",
        typeChart="line",
        graphs={"a": GraphRequestDto(type="METRIC_GRAPH", data={"statistic": "avg"})},
    )
    await handler_rw.create_widget(dashboard_id=DASH, body=body)
    sent = json.loads(route.calls.last.request.content)
    assert sent["layout"] == "cols:5, rows:2, x:0, y:2"


@respx.mock
@pytest.mark.asyncio
async def test_create_widget_respects_explicit_layout(handler_rw):
    """An explicit layout is used as-is (no auto-placement lookup)."""
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/dashboards/{DASH}/widgets/v2").mock(
        return_value=httpx.Response(200, json=WIDGET_ENVELOPE)
    )
    body = CreateWidgetDto(
        name="Pinned",
        typeChart="line",
        layout="cols:10, rows:3, x:0, y:6",
        graphs={"a": GraphRequestDto(type="METRIC_GRAPH", data={"statistic": "avg"})},
    )
    await handler_rw.create_widget(dashboard_id=DASH, body=body)
    sent = json.loads(route.calls.last.request.content)
    assert sent["layout"] == "cols:10, rows:3, x:0, y:6"


def test_next_grid_slot_packs_two_per_row():
    from greennode.vmonitor_mcp_server.widget_handler import _next_grid_slot

    assert _next_grid_slot([], 5, 2) == "cols:5, rows:2, x:0, y:0"
    assert _next_grid_slot(["cols:5, rows:2, x:0, y:0"], 5, 2) == "cols:5, rows:2, x:5, y:0"
    assert (
        _next_grid_slot(["cols:5, rows:2, x:0, y:0", "cols:5, rows:2, x:5, y:0"], 5, 2)
        == "cols:5, rows:2, x:0, y:2"
    )
    assert _next_grid_slot(["cols:5, rows:2, x:0, y:0"], 3, 2) == "cols:3, rows:2, x:5, y:0"


@respx.mock
@pytest.mark.asyncio
async def test_update_widget_v2_sends_partial_and_confirms(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/dashboards/{DASH}/widgets/v2/wid-1").mock(
        return_value=httpx.Response(200)
    )

    body = UpdateWidgetV2Dto(name="CPU (avg)")
    msg = await handler_rw.update_widget_v2(dashboard_id=DASH, widget_id="wid-1", body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"name": "CPU (avg)"}
    assert "wid-1" in msg


@respx.mock
@pytest.mark.asyncio
async def test_update_widget_layout_hits_layout_path(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/dashboards/{DASH}/widgets/layout/wid-1").mock(
        return_value=httpx.Response(200)
    )

    body = UpdateWidgetLayoutDto(layout="{x:0,y:0}")
    await handler_rw.update_widget_layout(dashboard_id=DASH, widget_id="wid-1", body=body)

    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"layout": "{x:0,y:0}"}


@respx.mock
@pytest.mark.asyncio
async def test_delete_widget_confirms(handler_rw):
    _mock_iam(respx.mock)
    respx.delete(f"{API}/dashboards/{DASH}/widgets/wid-1").mock(return_value=httpx.Response(200))

    msg = await handler_rw.delete_widget(dashboard_id=DASH, widget_id="wid-1")

    assert "wid-1" in msg


@pytest.mark.asyncio
async def test_get_widget_rejects_bad_id(handler):
    with pytest.raises(ValueError):
        await handler.get_widget(dashboard_id=DASH, widget_id="../../secret")


def test_widget_dtos_forbid_extra():
    with pytest.raises(ValidationError):
        GraphRequestDto(type="x", data={}, bogus=1)
    with pytest.raises(ValidationError):
        CreateWidgetDto(name="x", typeChart="line", graphs={}, bogus=1)


DASHBOARD_WITH_WIDGETS = {
    "id": DASH,
    "name": "vServer-web-01-a81b",
    "system": True,
    "widgets": [
        {
            "id": "wid-cpu",
            "name": "CPU Utilization",
            "typeChart": "LINE",
            "period": 300,
            "layout": "cols:7, rows:2, x:3, y:0",
            "type": {"name": "Metric"},
            "metricGraphs": [
                {
                    "name": "vserver.cpu.utilization_norm_perc",
                    "statistic": "avg",
                    "alias": "cpu_utillization",
                    "groupBy": "none",
                    "filter": "resource_id:ins-f73b9c98,product:vserver",
                    "enabled": True,
                    "limit": "",
                    "product": "",
                }
            ],
            "logGraphs": [],
        },
        {
            "id": "wid-net",
            "name": "Network Packets/s by device",
            "typeChart": "LINE",
            "period": 300,
            "type": {"name": "Metric"},
            "metricGraphs": [
                {
                    "name": "vserver.net.in_packets_sec",
                    "statistic": "avg",
                    "groupBy": "device",
                    "filter": "resource_id:ins-f73b9c98,product:vserver",
                    "enabled": True,
                },
                {
                    "name": "vserver.net.out_packets_sec",
                    "statistic": "avg",
                    "groupBy": "device",
                    "filter": "resource_id:ins-f73b9c98,product:vserver",
                    "enabled": True,
                },
            ],
            "logGraphs": [{"id": "lg-1"}],
        },
    ],
}


@respx.mock
@pytest.mark.asyncio
async def test_list_widgets_exposes_replayable_metric_queries(handler):
    """A default dashboard's widgets carry the exact query get_statistics_v2 needs."""
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}").mock(
        return_value=httpx.Response(200, json=DASHBOARD_WITH_WIDGETS)
    )

    result = await handler.list_widgets(dashboard_id=DASH)

    assert result.dashboard_id == DASH
    assert result.dashboard_name == "vServer-web-01-a81b"
    assert result.system is True
    assert result.total_item == 2

    cpu = result.items[0]
    assert cpu.id == "wid-cpu"
    assert cpu.type_chart == "LINE"
    assert cpu.period == 300
    assert cpu.layout == "cols:7, rows:2, x:3, y:0"
    assert len(cpu.metric_queries) == 1

    query = cpu.metric_queries[0]
    assert query.metric_name == "vserver.cpu.utilization_norm_perc"
    assert query.statistic == "avg"
    # `filter` on the wire IS the `dimensions` string a statistics query takes.
    assert query.dimensions == "resource_id:ins-f73b9c98,product:vserver"
    assert query.group_by == "none"
    assert query.alias == "cpu_utillization"
    assert query.enabled is True


@respx.mock
@pytest.mark.asyncio
async def test_list_widgets_keeps_every_graph_of_a_multi_series_widget(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}").mock(
        return_value=httpx.Response(200, json=DASHBOARD_WITH_WIDGETS)
    )

    result = await handler.list_widgets(dashboard_id=DASH)

    net = result.items[1]
    assert [q.metric_name for q in net.metric_queries] == [
        "vserver.net.in_packets_sec",
        "vserver.net.out_packets_sec",
    ]
    assert {q.group_by for q in net.metric_queries} == {"device"}
    assert net.log_graph_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_list_widgets_handles_an_empty_dashboard(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}").mock(
        return_value=httpx.Response(200, json={"id": DASH, "name": "empty"})
    )

    result = await handler.list_widgets(dashboard_id=DASH)

    assert result.total_item == 0
    assert result.items == []


@respx.mock
@pytest.mark.asyncio
async def test_list_widgets_unwraps_a_data_envelope(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/dashboards/{DASH}").mock(
        return_value=httpx.Response(200, json={"data": DASHBOARD_WITH_WIDGETS})
    )

    result = await handler.list_widgets(dashboard_id=DASH)

    assert result.total_item == 2


@pytest.mark.asyncio
async def test_list_widgets_rejects_bad_id(handler):
    with pytest.raises(ValueError):
        await handler.list_widgets(dashboard_id="../../secret")


def test_list_widgets_is_registered_without_allow_write(sample_config):
    """Reading a dashboard's widgets is a read tool — available in read-only mode."""
    config = load_config(sample_config)
    mcp = MCPServer("test")
    WidgetHandler(mcp, config, VmonitorClient(config, TokenManager(config)))

    assert "list_widgets" in {t.name for t in mcp._tool_manager.list_tools()}
