"""Tests for the vMonitor metric-catalogue (Query) tools."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.metric_catalogue_handler import MetricCatalogueHandler
from greennode.vmonitor_mcp_server.models import MetricDimensionData
from mcp.server.mcpserver import MCPServer


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"

DIMENSIONS = [
    {"key": "server_id", "value": ["ins-1", "ins-2"]},
    {"key": "instance", "value": ["10.0.0.1:9100"]},
]


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return MetricCatalogueHandler(MCPServer("test"), config, client)


@pytest.mark.asyncio
async def test_catalogue_tools_registered(handler):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert {
        "get_metric_names",
        "list_metric_dimension_names",
        "list_metric_dimension_values",
        "get_metric_dimensions",
    } <= read_only


@respx.mock
@pytest.mark.asyncio
async def test_get_metric_dimensions_parses_array(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/metrics/dimensions").mock(
        return_value=httpx.Response(200, json=DIMENSIONS)
    )

    result = await handler.get_metric_dimensions(
        name="vServerCPUUsage", dimensions=None, start_time=None, end_time=None
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"name": "vServerCPUUsage"}
    assert isinstance(result, MetricDimensionData)
    assert result.metric_name == "vServerCPUUsage"
    assert result.items[0].key == "server_id"
    assert result.items[0].values == ["ins-1", "ins-2"]
    assert result.items[1].key == "instance"


@respx.mock
@pytest.mark.asyncio
async def test_get_metric_names_parses_array(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/metrics/metric-name").mock(
        return_value=httpx.Response(
            200, json=[{"name": "vServerCPUUsage"}, {"name": "vServerMemUsage"}]
        )
    )

    result = await handler.get_metric_names(start_time="1", end_time="2")

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"start_time": "1", "end_time": "2"}
    assert result.count == 2
    assert result.items == ["vServerCPUUsage", "vServerMemUsage"]


@respx.mock
@pytest.mark.asyncio
async def test_list_metric_dimension_names_parses_array(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/metrics/dimensions-names").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"dimension_name": "host"},
                {"dimension_name": "cpu"},
                {"dimension_name": "instance"},
            ],
        )
    )

    result = await handler.list_metric_dimension_names()

    assert result.count == 3
    assert result.items == ["host", "cpu", "instance"]


@respx.mock
@pytest.mark.asyncio
async def test_list_metric_dimension_values_sends_required_param(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/metrics/dimensions-values").mock(
        return_value=httpx.Response(
            200, json=[{"dimension_value": "srv-1"}, {"dimension_value": "srv-2"}]
        )
    )

    result = await handler.list_metric_dimension_values(
        dimension_name="host", dimensions=None, start_time=None, end_time=None
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"dimension_name": "host"}
    assert result.dimension_name == "host"
    assert result.items == ["srv-1", "srv-2"]
