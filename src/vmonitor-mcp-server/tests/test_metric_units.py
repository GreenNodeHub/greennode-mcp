"""Tests for the vMonitor metric-information (unit) tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.metric_unit_handler import MetricUnitHandler
from greennode.vmonitor_mcp_server.models import (
    CreateMetricUnitMappingDto,
    MetricUnitListData,
    MetricUnitMappingListData,
    MetricUnitMappingUserDetail,
)
from mcp.server.mcpserver import MCPServer


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"

UNIT_ENVELOPE = {
    "lstData": [
        {"id": 1, "name": "Bytes", "newUnit": "KB", "threshold": 1024},
        {"id": 2, "name": "Percent", "newUnit": "Percent", "threshold": 100},
    ],
    "page": 1,
    "pageSize": 20,
    "totalItem": 2,
    "totalPage": 1,
}

MAPPING_ENVELOPE = {
    "lstData": [
        {
            "id": "map-1",
            "metricName": "vServerCPUUsage",
            "unit": "Percent",
            "description": "cpu usage",
            "metricUnitMappingUserId": "user-map-9",
        },
        {
            "id": "map-2",
            "metricName": "vServerMemUsage",
            "unit": "Bytes",
            "description": "",
            "metricUnitMappingUserId": "",
        },
    ],
    "page": 1,
    "pageSize": 20,
    "totalItem": 2,
    "totalPage": 1,
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return MetricUnitHandler(MCPServer("test"), config, client)


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return MetricUnitHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.mark.asyncio
async def test_read_tools_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert {"list_metric_units", "list_metric_unit_mappings"} <= read_only
    assert "create_metric_unit_mapping" not in read_only
    assert "delete_metric_unit_mapping" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {"create_metric_unit_mapping", "delete_metric_unit_mapping"} <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_metric_units_normalised(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/metricUnits/list").mock(return_value=httpx.Response(200, json=UNIT_ENVELOPE))

    result = await handler.list_metric_units(page=1, size=20)

    assert isinstance(result, MetricUnitListData)
    assert result.total_item == 2
    assert result.items[0].name == "Bytes"
    assert result.items[0].new_unit == "KB"
    assert result.items[0].threshold == 1024


@respx.mock
@pytest.mark.asyncio
async def test_list_metric_unit_mappings_flags_user_override(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/metric-unit-mappings/list").mock(
        return_value=httpx.Response(200, json=MAPPING_ENVELOPE)
    )

    result = await handler.list_metric_unit_mappings(
        name="vServer", is_default=False, page=None, size=None
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"name": "vServer", "isDefault": "false"}
    assert isinstance(result, MetricUnitMappingListData)
    assert result.items[0].metric_unit_mapping_user_id == "user-map-9"
    assert result.items[1].metric_unit_mapping_user_id == ""


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_unit_mapping_sends_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/metric-unit-mapping-users").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "user-map-1",
                "metricName": "vServerCPUUsage",
                "unit": "Percent",
                "description": "custom",
            },
        )
    )

    result = await handler_rw.create_metric_unit_mapping(
        body=CreateMetricUnitMappingDto(
            metricName="vServerCPUUsage", unit="Percent", description="custom"
        )
    )

    assert json.loads(route.calls.last.request.content) == {
        "metricName": "vServerCPUUsage",
        "unit": "Percent",
        "description": "custom",
    }
    assert isinstance(result, MetricUnitMappingUserDetail)
    assert result.id == "user-map-1"
    assert result.unit == "Percent"


def test_create_metric_unit_mapping_dto_forbids_extra():
    with pytest.raises(ValueError):
        CreateMetricUnitMappingDto(metricName="m", unit="Percent", bogus="x")


@respx.mock
@pytest.mark.asyncio
async def test_delete_metric_unit_mapping_confirmation(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{API}/metric-unit-mapping-users/user-map-9").mock(
        return_value=httpx.Response(200)
    )

    result = await handler_rw.delete_metric_unit_mapping(mapping_user_id="user-map-9")

    assert route.called
    assert "user-map-9" in result


@pytest.mark.asyncio
async def test_delete_metric_unit_mapping_rejects_bad_id(handler_rw):
    with pytest.raises(ValueError):
        await handler_rw.delete_metric_unit_mapping(mapping_user_id="../../secret")
