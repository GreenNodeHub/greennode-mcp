"""Tests for the vMonitor alarm tools (metric + log families)."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.alarm_handler import AlarmHandler
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    AlarmDetail,
    AlarmHistoryData,
    AlarmListData,
    CreateLogAlarmDto,
    CreateMetricAlarmDto,
    LogAlarmStatus,
    UpdateMetricAlarmDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"
NOTIF = "https://vmonitorapis.vngcloud.vn/notification-gateway/api/v1"

ALARM_ENVELOPE = {
    "lstData": [
        {
            "id": "al-1",
            "name": "cpu-high",
            "type": "Metric",
            "severity": "High",
            "progressStatus": "OK",
        },
    ],
    "page": 1,
    "pageSize": 10,
    "totalItem": 1,
    "totalPage": 1,
}
ALARM_DETAIL = {
    "data": {
        "id": "al-1",
        "name": "cpu-high",
        "type": "Metric",
        "severity": "High",
        "progressStatus": "ALARM",
        "alarmMetric": {"metricName": "cpu", "thresholdValue": 80},
    }
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    return AlarmHandler(MCPServer("test"), config, VmonitorClient(config, TokenManager(config)))


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    return AlarmHandler(
        MCPServer("test"), config, VmonitorClient(config, TokenManager(config)), allow_write=True
    )


@pytest.mark.asyncio
async def test_reads_registered_writes_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    expected_reads = {
        "list_alarms",
        "get_alarm",
        "get_metric_alarm_definition",
        "list_metric_alarm_histories",
        "get_synthetic_alarm_definition",
        "list_synthetic_alarm_histories",
        "list_log_alarm_histories",
        "get_log_alarm_status",
    }
    assert expected_reads <= read_only
    assert "create_metric_alarm" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    expected_writes = {
        "create_metric_alarm",
        "update_metric_alarm",
        "delete_metric_alarm",
        "delete_metric_sub_alarm",
        "create_log_alarm",
        "update_log_alarm",
        "delete_log_alarm",
    }
    assert expected_writes <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_alarms_maps_type_param(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/alarms/list").mock(
        return_value=httpx.Response(200, json=ALARM_ENVELOPE)
    )

    result = await handler.list_alarms(
        name="cpu", severity="High", status="ALARM", type_alarm="Metric", page=None, size=None
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"name": "cpu", "severity": "High", "status": "ALARM", "type-alarm": "Metric"}
    assert isinstance(result, AlarmListData)
    assert result.items[0].id == "al-1"


@respx.mock
@pytest.mark.asyncio
async def test_list_alarms_no_type_merges_and_sends_required_defaults(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/alarms/list").mock(
        return_value=httpx.Response(200, json=ALARM_ENVELOPE)
    )

    result = await handler.list_alarms(
        name=None, severity=None, status=None, type_alarm=None, page=None, size=None
    )

    assert len(route.calls) == 3
    types_queried = sorted(
        dict(c.request.url.params.multi_items())["type-alarm"] for c in route.calls
    )
    assert types_queried == ["Change", "Log", "Metric"]
    for c in route.calls:
        sent = dict(c.request.url.params.multi_items())
        assert sent["name"] == "" and sent["status"] == "any" and sent["severity"] == "any"
    assert result.total_item == 3


@respx.mock
@pytest.mark.asyncio
async def test_get_alarm_unwraps_and_keeps_config(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/alarms/al-1").mock(return_value=httpx.Response(200, json=ALARM_DETAIL))

    result = await handler.get_alarm(alarm_id="al-1")

    assert isinstance(result, AlarmDetail)
    assert result.progress_status == "ALARM"
    assert result.alarm_metric == {"metricName": "cpu", "thresholdValue": 80}


@respx.mock
@pytest.mark.asyncio
async def test_list_metric_alarm_histories_parses(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/alarms/metrics/mona/al-1/histories").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "h1"}]})
    )

    result = await handler.list_metric_alarm_histories(
        alarm_id="al-1", start_time=None, end_time=None, interval=None
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert "start_time" in sent and "end_time" in sent
    assert sent["interval"] == "0"
    assert isinstance(result, AlarmHistoryData)
    assert result.count == 1


@respx.mock
@pytest.mark.asyncio
async def test_get_log_alarm_status_parses(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/alarms/logs/butler/al-1/status").mock(
        return_value=httpx.Response(200, json={"data": {"status": "OK", "updated_on": 123}})
    )

    result = await handler.get_log_alarm_status(alarm_id="al-1")

    assert isinstance(result, LogAlarmStatus)
    assert result.status == "OK"
    assert result.updated_on == 123


@respx.mock
@pytest.mark.asyncio
async def test_list_log_alarm_histories_maps_len_param(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/alarms/logs/butler/al-1/histories").mock(
        return_value=httpx.Response(200, json={"data": {"alarms": [{"id": "a"}], "total": 1}})
    )

    result = await handler.list_log_alarm_histories(
        alarm_id="al-1", start=None, end=None, order="desc", page=1, length=20
    )

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent["order"] == "desc"
    assert sent["page"] == "1"
    assert sent["len"] == "20"
    assert "start" in sent and "end" in sent
    assert result.total == 1
    assert result.items == [{"id": "a"}]


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_alarm_posts(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/alarms/metrics").mock(
        return_value=httpx.Response(200, json={"id": "al-9", "name": "cpu", "type": "Metric"})
    )

    body = CreateMetricAlarmDto(
        name="cpu",
        metricName="cpu.usage",
        metricStatistic="avg",
        condition="gt",
        severity="MEDIUM",
        thresholdValue=80.0,
    )
    result = await handler_rw.create_metric_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "name": "cpu",
        "metricName": "cpu.usage",
        "metricStatistic": "avg",
        "condition": "gt",
        "severity": "MEDIUM",
        "metricPeriod": 60,
        "interval": 60,
        "checkTime": 1,
        "formula": "a",
        "thresholdValue": 80.0,
        "thresholdMethod": "static",
        "metricProduct": "",
        "metricGroupBy": "none",
        "metricFilter": {},
        "timeshift": "-60",
    }
    assert result.id == "al-9"


def test_create_metric_alarm_dto_requires_semantic_core():
    """The fields the API errors/500s without must fail schema validation up front."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateMetricAlarmDto(name="cpu")  # missing metricName/statistic/condition/severity


def test_create_metric_alarm_dto_normalizes_enum_case_and_symbols():
    """The casings/symbols a naive agent types (the recurring 400s) are normalised
    to the wire form instead of rejected."""
    kw = {"name": "cpu", "metricName": "cpu.usage", "metricStatistic": "avg"}

    dto = CreateMetricAlarmDto(condition="GT", severity="High", **kw)
    assert dto.condition == "gt" and dto.severity == "HIGH"

    dto = CreateMetricAlarmDto(condition=">=", severity="medium", **kw)
    assert dto.condition == "gte" and dto.severity == "MEDIUM"

    dto = CreateMetricAlarmDto(condition="<", severity="low", **kw)
    assert dto.condition == "lt" and dto.severity == "LOW"


def test_metric_alarm_dto_still_rejects_unknown_enum():
    """A genuinely invalid value still fails validation after normalisation.

    This product has no CRITICAL tier, so CRITICAL is rejected like any other
    unknown severity.
    """
    from pydantic import ValidationError

    kw = {"name": "cpu", "metricName": "cpu.usage", "metricStatistic": "avg"}
    with pytest.raises(ValidationError):
        CreateMetricAlarmDto(condition="between", severity="HIGH", **kw)
    with pytest.raises(ValidationError):
        CreateMetricAlarmDto(condition="gt", severity="URGENT", **kw)
    with pytest.raises(ValidationError):
        CreateMetricAlarmDto(condition="gt", severity="CRITICAL", **kw)


def test_log_alarm_dto_normalizes_enum_case():
    """Log-alarm severity/condition (plain str fields) are normalised too."""
    dto = CreateLogAlarmDto(name="logfire", severity="high", condition="GTE")
    assert dto.severity == "HIGH" and dto.condition == "gte"


def test_create_metric_alarm_dto_defaults_timing():
    """metricPeriod/interval/checkTime default so their absence can't 500 the API."""
    dto = CreateMetricAlarmDto(
        name="cpu",
        metricName="cpu.usage",
        metricStatistic="avg",
        condition="gt",
        severity="LOW",
    )
    assert (dto.metricPeriod, dto.interval, dto.checkTime) == (60, 60, 1)


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_alarm_keeps_explicit_group_and_filter(handler_rw):
    """Explicit metricGroupBy / metricFilter are passed through unchanged."""
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/alarms/metrics").mock(
        return_value=httpx.Response(200, json={"id": "al-9"})
    )

    body = CreateMetricAlarmDto(
        name="cpu",
        metricName="cpu.usage",
        metricStatistic="avg",
        condition="gt",
        severity="MEDIUM",
        metricGroupBy="host",
        metricFilter={"host": "srv-1"},
    )
    await handler_rw.create_metric_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["metricGroupBy"] == "host"
    assert sent["metricFilter"] == {"host": "srv-1"}


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_alarm_fills_evaluator_fields(handler_rw):
    """formula/thresholdMethod/timeshift the evaluator needs are auto-filled, and
    timeshift tracks metricPeriod so the first reasonable payload creates a
    working alarm."""
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/alarms/metrics").mock(
        return_value=httpx.Response(200, json={"id": "al-9"})
    )

    body = CreateMetricAlarmDto(
        name="cpu",
        metricName="cpu.usage",
        metricStatistic="avg",
        condition="gt",
        severity="MEDIUM",
        metricPeriod=300,
    )
    await handler_rw.create_metric_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["formula"] == "a"
    assert sent["thresholdMethod"] == "static"
    assert sent["metricProduct"] == ""
    assert sent["timeshift"] == "-300"


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_alarm_resolves_channel_id_to_mapping_id(handler_rw):
    """A notification channel `id` in inAlarm/ok is rewritten to its metricMappingId
    (the value the alarm API needs; a raw id 500s). A trailing comma is preserved."""
    _mock_iam(respx.mock)
    respx.get(f"{NOTIF}/notification/list/typeSearch").mock(
        return_value=httpx.Response(
            200,
            json={
                "lstData": [
                    {
                        "id": "9431ba72rawid",
                        "name": "sample-receiver",
                        "metricMappingId": "acc81c75-uuid",
                    }
                ],
                "page": 1,
                "pageSize": 200,
                "totalItem": 1,
                "totalPage": 1,
            },
        )
    )
    route = respx.post(f"{API}/alarms/metrics").mock(
        return_value=httpx.Response(200, json={"id": "al-9"})
    )

    body = CreateMetricAlarmDto(
        name="cpu-alarm",
        metricName="cpu.usage",
        metricStatistic="avg",
        condition="gt",
        severity="MEDIUM",
        inAlarm="9431ba72rawid,",
        ok="9431ba72rawid",
        undetermined="acc81c75-uuid",
    )
    await handler_rw.create_metric_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["inAlarm"] == "acc81c75-uuid,"
    assert sent["ok"] == "acc81c75-uuid"
    assert sent["undetermined"] == "acc81c75-uuid"


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_alarm_without_actions_skips_notification_lookup(handler_rw):
    """No inAlarm/ok/undetermined => no notification fetch (the lookup is best-effort)."""
    _mock_iam(respx.mock)
    notif = respx.get(f"{NOTIF}/notification/list/typeSearch").mock(
        return_value=httpx.Response(200, json={"lstData": []})
    )
    respx.post(f"{API}/alarms/metrics").mock(return_value=httpx.Response(200, json={"id": "al-9"}))

    body = CreateMetricAlarmDto(
        name="cpu-alarm",
        metricName="cpu.usage",
        metricStatistic="avg",
        condition="gt",
        severity="LOW",
    )
    await handler_rw.create_metric_alarm(body=body)
    assert not notif.called


@respx.mock
@pytest.mark.asyncio
async def test_create_metric_alarm_respects_explicit_evaluator_fields(handler_rw):
    """An explicit thresholdMethod / timeshift / formula overrides the default."""
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/alarms/metrics").mock(
        return_value=httpx.Response(200, json={"id": "al-9"})
    )

    body = CreateMetricAlarmDto(
        name="cpu",
        metricName="cpu.usage",
        metricStatistic="avg",
        condition="gt",
        severity="MEDIUM",
        thresholdMethod="flatline",
        timeshift="-600",
        formula="a+b",
    )
    await handler_rw.create_metric_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["thresholdMethod"] == "flatline"
    assert sent["timeshift"] == "-600"
    assert sent["formula"] == "a+b"


@respx.mock
@pytest.mark.asyncio
async def test_update_metric_alarm_injects_id(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/alarms/metrics/al-1").mock(return_value=httpx.Response(200))

    body = UpdateMetricAlarmDto(thresholdValue=90.0)
    msg = await handler_rw.update_metric_alarm(alarm_id="al-1", body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"thresholdValue": 90.0, "id": "al-1"}
    assert "al-1" in msg


@respx.mock
@pytest.mark.asyncio
async def test_delete_metric_sub_alarm_hits_sub_path(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{API}/alarms/metrics/sub-alarms/al-1").mock(
        return_value=httpx.Response(200)
    )

    msg = await handler_rw.delete_metric_sub_alarm(alarm_id="al-1")

    assert route.called
    assert "al-1" in msg


@respx.mock
@pytest.mark.asyncio
async def test_create_log_alarm_posts(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/alarms/logs").mock(
        return_value=httpx.Response(200, json={"id": "lg-1", "name": "errors", "type": "Log"})
    )

    body = CreateLogAlarmDto(name="errors", logProjectId="log-1", thresholdValue=5)
    result = await handler_rw.create_log_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "name": "errors",
        "logProjectId": "log-1",
        "thresholdValue": 5,
        "filter": {"type": "match_all", "value": {}},
    }
    assert result.id == "lg-1"


@respx.mock
@pytest.mark.asyncio
async def test_create_log_alarm_keeps_explicit_filter(handler_rw):
    """An explicit filter is passed through unchanged (not overwritten)."""
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/alarms/logs").mock(
        return_value=httpx.Response(200, json={"id": "lg-2"})
    )

    custom = {"type": "match_phrase", "value": {"field": "level", "query": "ERROR"}}
    body = CreateLogAlarmDto(name="errors", logProjectId="log-1", filter=custom)
    await handler_rw.create_log_alarm(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["filter"] == custom


@pytest.mark.asyncio
async def test_get_alarm_rejects_bad_id(handler):
    with pytest.raises(ValueError):
        await handler.get_alarm(alarm_id="../../secret")


def test_alarm_dtos_forbid_extra():
    with pytest.raises(ValidationError):
        CreateMetricAlarmDto(name="x", bogus=1)
    with pytest.raises(ValidationError):
        CreateLogAlarmDto(name="x", bogus=1)
