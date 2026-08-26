"""Tests for the vMonitor statistics tools."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import StatisticData, StatisticQueryDto
from greennode.vmonitor_mcp_server.statistic_handler import StatisticHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"

SERIES = [
    {"name": "vServerCPUUsage", "dimensions": {"host": "srv-1"}, "statistics": [[1, 10], [2, 12]]},
    {"name": "vServerCPUUsage", "dimensions": {"host": "srv-2"}, "statistics": [[1, 20]]},
]


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return StatisticHandler(MCPServer("test"), config, client)


@pytest.mark.asyncio
async def test_statistics_tools_registered_read_only(handler):
    names = {t.name for t in await handler.mcp.list_tools()}
    assert {"get_statistics", "get_statistics_synthetic", "get_statistics_v2"} <= names


@respx.mock
@pytest.mark.asyncio
async def test_get_statistics_maps_params_and_parses(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/statistics").mock(return_value=httpx.Response(200, json=SERIES))

    result = await handler.get_statistics(
        name="vServerCPUUsage",
        statistics="avg",
        dimensions="host=srv-1",
        start_time="1",
        end_time="2",
        group_by="host",
        period="60",
        alarm=None,
        limit="10",
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {
        "name": "vServerCPUUsage",
        "statistics": "avg",
        "dimensions": "host=srv-1",
        "start_time": "1",
        "end_time": "2",
        "group_by": "host",
        "period": "60",
        "limit": "10",
    }
    assert isinstance(result, StatisticData)
    assert result.count == 2
    assert result.series[0]["name"] == "vServerCPUUsage"


def test_default_window_fills_last_hour_when_start_omitted(handler):
    """A missing start_time makes the backend answer 500; the tool defaults a window."""
    start, end = handler._default_window(None, None)
    assert start.isdigit() and end.isdigit()
    assert int(start) < int(end)
    assert 0 < int(end) - int(start) <= 3600_000

    start, end = handler._default_window(None, "222")
    assert start.isdigit() and end == "222"
    assert handler._default_window("111", None) == ("111", None)


@respx.mock
@pytest.mark.asyncio
async def test_get_statistics_preserves_explicit_window(handler):
    """An explicit start_time is never overridden by the default."""
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/statistics").mock(return_value=httpx.Response(200, json=SERIES))

    await handler.get_statistics(name="m", statistics="avg", start_time="111", end_time="222")

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent["start_time"] == "111" and sent["end_time"] == "222"


@respx.mock
@pytest.mark.asyncio
async def test_get_statistics_synthetic_hits_synthetics_path(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/statistics/synthetics").mock(
        return_value=httpx.Response(200, json=SERIES)
    )

    result = await handler.get_statistics_synthetic(name="vServerCPUUsage")

    assert route.called
    assert result.count == 2


@respx.mock
@pytest.mark.asyncio
async def test_get_statistics_v2_posts_typed_simple_body(handler):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/statistics").mock(return_value=httpx.Response(200, json=SERIES))

    body = StatisticQueryDto(
        type="SIMPLE",
        data={
            "graph": {"name": "vServerCPUUsage", "dimensions": "host:srv-1", "statistics": "max"},
            "start_time": 1785222888626,
            "end_time": 1785228288626,
            "period": 60,
            "alarm": False,
        },
    )
    result = await handler.get_statistics_v2(body=body)

    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["type"] == "SIMPLE"
    assert sent["data"]["graph"]["statistics"] == "max"
    assert sent["data"]["start_time"] == 1785222888626
    assert "reduction" not in sent["data"]
    assert result.count == 2


def test_statistic_query_dto_forbids_extra():
    with pytest.raises(ValidationError):
        StatisticQueryDto(
            type="SIMPLE", data={"graph": {"name": "m", "statistics": "max"}}, bogus=1
        )


def test_statistic_query_dto_rejects_backend_crashing_shapes():
    """The shapes that made the backend answer 500 must fail schema validation."""
    with pytest.raises(ValidationError):
        StatisticQueryDto(type="metric", data={"graph": {"name": "m", "statistics": "max"}})
    with pytest.raises(ValidationError):
        StatisticQueryDto(type="SIMPLE", data={"graph": {"name": "m", "statistics": ["max"]}})
    with pytest.raises(ValidationError):
        StatisticQueryDto(
            type="SIMPLE",
            data={"graph": {"name": "m", "statistics": "max", "dimensions": {"host": "x"}}},
        )
    with pytest.raises(ValidationError):
        StatisticQueryDto(
            type="SIMPLE",
            data={
                "graph": {"name": "m", "statistics": "max"},
                "start_time": "2026-07-28T08:00:00Z",
            },
        )
