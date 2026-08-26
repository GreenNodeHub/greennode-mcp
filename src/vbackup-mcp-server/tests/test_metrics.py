"""vMonitor dashboards: fixed payloads, string values, and the empty-200 traps.

The statistics API answers an unknown metric name and an unknown location id
the same way it answers a genuinely quiet one — `200 []` — so most of what
these tests pin is how that ambiguity is surfaced rather than hidden.
"""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import API_BASE, RAW_DESTINATION, envelope, mock_iam
from greennode.vbackup_mcp_server.config import REGIONS, VMONITOR_SERVICE
from greennode.vbackup_mcp_server.metrics_handler import (
    LOCATION_METRICS,
    OVERALL_METRICS,
    MetricsHandler,
)
from mcp.server.mcpserver import MCPServer


STATS_URL = f"{REGIONS['HCM-3'][VMONITOR_SERVICE]}/api/v1/statistics/default"
DEST_ID = "bk-des-0001"


def series(region: str, values: list[str], storage_type: str | None = None) -> dict:
    """One vMonitor series: epoch SECONDS and STRING values, as the API sends them."""
    dims = {"region": region}
    if storage_type:
        dims["type"] = storage_type
    return {
        "id": "0",
        "name": None,
        "dimensions": dims,
        "columns": [],
        "statistics": [[1786943100.0 + i * 3600, v] for i, v in enumerate(values)],
    }


@pytest.fixture
def handler(config, client):
    return MetricsHandler(MCPServer("test"), config, client)


@pytest.mark.asyncio
async def test_both_dashboards_are_read_only(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    assert tools["get_backup_metrics"].annotations.read_only_hint is True
    assert tools["get_backup_destination_metrics"].annotations.read_only_hint is True


@respx.mock
@pytest.mark.asyncio
async def test_overall_dashboard_sends_the_console_payload(handler):
    """The query is fixed except name/dimensions/window — those defaults are the contract."""
    mock_iam(respx.mock)
    route = respx.post(STATS_URL).mock(
        return_value=httpx.Response(200, json=[series("HCM", ["3", "4"])])
    )
    result = await handler.get_backup_metrics(
        from_date="2026-08-01", to_date="2026-08-02", period_seconds=60, region="HCM-3"
    )
    assert len(route.calls) == len(OVERALL_METRICS)

    sent = [json.loads(c.request.content) for c in route.calls]
    assert {s["data"]["graph"]["name"] for s in sent} == set(OVERALL_METRICS)
    graph = sent[0]["data"]["graph"]
    assert graph["dimensions"] == "product:vbackup"
    assert graph["statistics"] == "max"
    assert graph["group_by"] == "region,type"
    assert sent[0]["type"] == "SIMPLE"
    assert sent[0]["data"]["period"] == 60
    assert sent[0]["data"]["alarm"] is False
    assert sent[0]["data"]["end_time"] - sent[0]["data"]["start_time"] == 86_400_000
    assert result.window.period_seconds == 60


@respx.mock
@pytest.mark.asyncio
async def test_string_values_become_numbers_and_seconds_become_iso(handler):
    mock_iam(respx.mock)
    respx.post(STATS_URL).mock(
        return_value=httpx.Response(200, json=[series("HCM", ["27844", "32317"])])
    )
    result = await handler.get_backup_metrics(
        from_date=None, to_date=None, period_seconds=60, region="HCM-3"
    )
    usage = [s for s in result.series if s.metric == "vbk.total_usage"][0]
    assert usage.latest == 32317.0
    assert usage.minimum == 27844.0
    assert usage.maximum == 32317.0
    assert usage.unit == "GB"
    assert usage.samples[0].timestamp.startswith("2026-")
    assert isinstance(usage.samples[0].value, float)


@respx.mock
@pytest.mark.asyncio
async def test_region_label_is_normalised_to_this_servers_spelling(handler):
    """vMonitor says HCM where every other tool here says HCM-3."""
    mock_iam(respx.mock)
    respx.post(STATS_URL).mock(
        return_value=httpx.Response(200, json=[series("HCM", ["4"]), series("HAN", ["1"])])
    )
    result = await handler.get_backup_metrics(
        from_date=None, to_date=None, period_seconds=60, region="HCM-3"
    )
    assert {s.region for s in result.series} == {"HCM-3", "HAN"}


@respx.mock
@pytest.mark.asyncio
async def test_empty_metrics_are_reported_not_swallowed(handler):
    """An unknown or quiet metric answers 200 [] — that must be visible."""
    mock_iam(respx.mock)
    respx.post(STATS_URL).mock(return_value=httpx.Response(200, json=[]))
    result = await handler.get_backup_metrics(
        from_date=None, to_date=None, period_seconds=60, region="HCM-3"
    )
    assert result.series == []
    assert set(result.empty_metrics) == set(OVERALL_METRICS)


@respx.mock
@pytest.mark.asyncio
async def test_location_dashboard_puts_the_id_in_the_dimensions(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations/{DEST_ID}").mock(
        return_value=httpx.Response(200, json=RAW_DESTINATION)
    )
    route = respx.post(STATS_URL).mock(
        return_value=httpx.Response(200, json=[series("HCM", ["253"], "VAULT")])
    )
    result = await handler.get_backup_destination_metrics(
        destination_id=DEST_ID,
        from_date=None,
        to_date=None,
        period_seconds=60,
        region="HCM-3",
    )
    sent = [json.loads(c.request.content) for c in route.calls]
    assert {s["data"]["graph"]["name"] for s in sent} == set(LOCATION_METRICS)
    for s in sent:
        assert s["data"]["graph"]["dimensions"] == (
            f"product:vbackup,backup_location_id:{DEST_ID}"
        )
    assert result.destination_name == "default-vault"
    assert result.series[0].storage_type == "VAULT"


@respx.mock
@pytest.mark.asyncio
async def test_rate_metrics_are_counts_not_percentages(handler):
    """A success_rate of 253 is 253 runs; the model must not clamp or rescale it."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations/{DEST_ID}").mock(
        return_value=httpx.Response(200, json=RAW_DESTINATION)
    )
    respx.post(STATS_URL).mock(
        return_value=httpx.Response(200, json=[series("HCM", ["239", "253"], "VAULT")])
    )
    result = await handler.get_backup_destination_metrics(
        destination_id=DEST_ID, from_date=None, to_date=None, period_seconds=60, region="HCM-3"
    )
    success = [s for s in result.series if s.metric == "vbk.location.success_rate"][0]
    assert success.latest == 253.0
    assert success.unit == ""


@respx.mock
@pytest.mark.asyncio
async def test_no_destination_id_charts_every_location(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations").mock(
        return_value=httpx.Response(
            200, json=envelope([RAW_DESTINATION, {**RAW_DESTINATION, "id": "bk-des-0002"}])
        )
    )
    route = respx.post(STATS_URL).mock(
        return_value=httpx.Response(200, json=[series("HCM", ["1"], "VAULT")])
    )
    result = await handler.get_backup_destination_metrics(
        destination_id=None, from_date=None, to_date=None, period_seconds=60, region="HCM-3"
    )
    assert result.total == 2
    assert len(route.calls) == 2 * len(LOCATION_METRICS)
    assert {d.destination_id for d in result.destinations} == {"bk-des-0001", "bk-des-0002"}


@respx.mock
@pytest.mark.asyncio
async def test_bucket_seconds_reports_what_the_api_really_used(handler):
    """Asking for 60s returns hourly data; the answer must say so."""
    mock_iam(respx.mock)
    respx.post(STATS_URL).mock(
        return_value=httpx.Response(200, json=[series("HCM", ["1", "2", "3"])])
    )
    result = await handler.get_backup_metrics(
        from_date=None, to_date=None, period_seconds=60, region="HCM-3"
    )
    assert result.window.period_seconds == 60
    assert result.window.bucket_seconds == 3600


def test_period_bounds_match_what_vmonitor_enforces():
    from greennode.vbackup_mcp_server.metrics_handler import validate_period

    assert validate_period(60) == 60
    assert validate_period(86_400) == 86_400
    for bad in (30, 59, 90, 86_401):
        with pytest.raises(ValueError, match="period_seconds"):
            validate_period(bad)


@pytest.mark.asyncio
async def test_bad_window_and_bad_id_are_rejected(handler):
    with pytest.raises(ValueError, match="before"):
        await handler.get_backup_metrics(
            from_date="2026-08-02", to_date="2026-08-01", period_seconds=60, region="HCM-3"
        )
    with pytest.raises(ValueError, match="destination_id"):
        await handler.get_backup_destination_metrics(
            destination_id="../etc",
            from_date=None,
            to_date=None,
            period_seconds=60,
            region="HCM-3",
        )
