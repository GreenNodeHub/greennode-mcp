"""Tests for the vMonitor change-detection alarm tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.change_alarm_handler import ChangeAlarmHandler
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    AlarmDefinitionData,
    AlarmHistoryData,
    CreateChangeAlarmDto,
    UpdateChangeAlarmDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    return ChangeAlarmHandler(
        MCPServer("test"), config, VmonitorClient(config, TokenManager(config))
    )


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    return ChangeAlarmHandler(
        MCPServer("test"), config, VmonitorClient(config, TokenManager(config)), allow_write=True
    )


@pytest.mark.asyncio
async def test_read_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert {"get_change_alarm", "list_change_alarm_histories"} <= read_only
    assert "create_change_alarm" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {
        "create_change_alarm",
        "update_change_alarm",
        "delete_change_alarm",
        "delete_change_alarm_history",
    } <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_get_change_alarm_requires_window(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/alarms/change-method/al-1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "al-1", "name": "cpu-change"}})
    )

    result = await handler.get_change_alarm(alarm_id="al-1", start_time="1", end_time="2")

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"start_time": "1", "end_time": "2"}
    assert isinstance(result, AlarmDefinitionData)
    assert result.id == "al-1"


@respx.mock
@pytest.mark.asyncio
async def test_list_change_alarm_histories_parses(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/alarms/change-method/al-1/histories").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "h1"}, {"id": "h2"}]})
    )

    result = await handler.list_change_alarm_histories(
        alarm_id="al-1", start_time="1", end_time="2", interval=None
    )

    assert isinstance(result, AlarmHistoryData)
    assert result.count == 2


@respx.mock
@pytest.mark.asyncio
async def test_create_change_alarm_posts(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/alarms/change-method").mock(
        return_value=httpx.Response(200, json={"id": "al-9", "name": "c", "type": "Change"})
    )

    body = CreateChangeAlarmDto(name="c", metricName="cpu", timeshift=3600)
    result = await handler_rw.create_change_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"name": "c", "metricName": "cpu", "timeshift": 3600}
    assert result.id == "al-9"


@respx.mock
@pytest.mark.asyncio
async def test_update_change_alarm_injects_id(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/alarms/change-method/al-1").mock(return_value=httpx.Response(200))

    body = UpdateChangeAlarmDto(thresholdValue=5.0)
    msg = await handler_rw.update_change_alarm(alarm_id="al-1", body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"thresholdValue": 5.0, "id": "al-1"}
    assert "al-1" in msg


@respx.mock
@pytest.mark.asyncio
async def test_delete_change_alarm_history_hits_histories_path(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{API}/alarms/change-method/al-1/histories").mock(
        return_value=httpx.Response(200)
    )

    msg = await handler_rw.delete_change_alarm_history(alarm_id="al-1")

    assert route.called
    assert "al-1" in msg


@pytest.mark.asyncio
async def test_delete_rejects_bad_id(handler_rw):
    with pytest.raises(ValueError):
        await handler_rw.delete_change_alarm(alarm_id="../../secret")


def test_dtos_forbid_extra():
    with pytest.raises(ValidationError):
        CreateChangeAlarmDto(name="x", bogus=1)
    with pytest.raises(ValidationError):
        UpdateChangeAlarmDto(bogus=1)
