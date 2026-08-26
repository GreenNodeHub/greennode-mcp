"""Backup policies: reads, the schedule model, and the write cycle."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import API_BASE, POLICY_CONFIG, RAW_POLICY, envelope, mock_iam
from greennode.vbackup_mcp_server.models import (
    BackupPolicyConfigDto,
    BackupPolicyItem,
    CreateBackupPolicyDto,
    DailyConfigDto,
    HourlyConfigDto,
    UpdateBackupPolicyDto,
)
from greennode.vbackup_mcp_server.policy_handler import PolicyHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


@pytest.fixture
def handler(config, client, no_cache):
    return PolicyHandler(MCPServer("test"), config, client, no_cache)


@pytest.fixture
def handler_rw(config, client, no_cache):
    return PolicyHandler(MCPServer("test"), config, client, no_cache, allow_write=True)


@pytest.mark.asyncio
async def test_write_tools_hidden_in_read_only_mode(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert "list_backup_policies" in tools
    assert "create_backup_policy" not in tools
    assert "delete_backup_policy" not in tools


@pytest.mark.asyncio
async def test_write_tools_registered_with_allow_write(handler_rw):
    tools = {t.name: t for t in await handler_rw.mcp.list_tools()}
    assert tools["create_backup_policy"].annotations.read_only_hint is False
    assert tools["delete_backup_policy"].annotations.destructive_hint is True
    assert tools["update_backup_policy"].annotations.destructive_hint is False


@respx.mock
@pytest.mark.asyncio
async def test_list_policies_structured(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-policies").mock(
        return_value=httpx.Response(200, json=envelope([RAW_POLICY]))
    )
    result = await handler.list_backup_policies(
        region="HCM-3", name=None, backend_id=None, refresh=False
    )
    policy = result.policies[0]
    assert policy.id == "bk-pol-0001"
    assert policy.backup_server_count == 2


def test_schedule_summary_lists_only_enabled_cadences():
    policy = BackupPolicyItem.from_api(RAW_POLICY)
    summary = policy.schedule.summary
    assert "hourly every 4h keep 1 (INCREMENTAL)" in summary
    assert "daily at 12:00 keep 3 (FULL)" in summary
    assert "weekly" not in summary
    assert "monthly" not in summary


def test_disabled_cadence_reported_as_disabled():
    """A disabled cadence carries an EMPTY config, so only the flag is reliable."""
    policy = BackupPolicyItem.from_api(RAW_POLICY)
    assert policy.schedule.weekly.enabled is False
    assert policy.schedule.hourly.enabled is True
    assert policy.schedule.hourly.interval_hours == 4


def test_float_numbers_coerced():
    """The API sends these as floats, so an int-only model would reject them."""
    policy = BackupPolicyItem.from_api(RAW_POLICY)
    assert policy.schedule.run_at == "12:00"
    assert policy.schedule.daily.retention == 3
    assert policy.schedule.hourly.incremental_quantity == 3


def test_policy_with_no_cadence_has_empty_summary():
    """An empty summary means the policy never runs — a real, easily-missed state."""
    raw = {**RAW_POLICY, "config": {"hour": 0.0, "minute": 0.0}}
    assert BackupPolicyItem.from_api(raw).schedule.summary == ""


def test_weekly_summary_names_the_day():
    raw = {
        **RAW_POLICY,
        "config": {
            **POLICY_CONFIG,
            "hourlyEnabled": False,
            "dailyEnabled": False,
            "weeklyEnabled": True,
            "weeklyConfig": {"dayOfWeek": 7.0, "retention": 4.0, "backupType": "FULL"},
        },
    }
    assert (
        "weekly at 12:00 on Sun keep 4 (FULL)" in BackupPolicyItem.from_api(raw).schedule.summary
    )


@respx.mock
@pytest.mark.asyncio
async def test_get_policy_reads_the_bare_object(handler):
    """Detail endpoints return the resource directly, with no data envelope."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-policies/bk-pol-0001").mock(
        return_value=httpx.Response(200, json=RAW_POLICY)
    )
    policy = await handler.get_backup_policy(policy_id="bk-pol-0001", region="HCM-3")
    assert policy.name == "nightly"


def _valid_config() -> BackupPolicyConfigDto:
    return BackupPolicyConfigDto(
        hour=1,
        minute=0,
        dailyEnabled=True,
        dailyConfig=DailyConfigDto(retention=7),
    )


@respx.mock
@pytest.mark.asyncio
async def test_create_policy_posts_camelcase_body(handler_rw):
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/backup-policies").mock(
        return_value=httpx.Response(201, json=RAW_POLICY)
    )
    body = CreateBackupPolicyDto(
        backendId="be-0001", projectId="pro-0001", name="nightly", config=_valid_config()
    )
    result = await handler_rw.create_backup_policy(body=body, region="HCM-3")
    sent = route.calls[0].request.content.decode()
    assert '"backendId"' in sent
    assert '"dailyConfig"' in sent
    assert result.id == "bk-pol-0001"


@respx.mock
@pytest.mark.asyncio
async def test_update_policy_puts_full_body(handler_rw):
    mock_iam(respx.mock)
    respx.put(f"{API_BASE}/v1/backup-policies/bk-pol-0001").mock(
        return_value=httpx.Response(200, json=RAW_POLICY)
    )
    body = UpdateBackupPolicyDto(
        backendId="be-0001", projectId="pro-0001", name="nightly", config=_valid_config()
    )
    result = await handler_rw.update_backup_policy(
        policy_id="bk-pol-0001", body=body, region="HCM-3"
    )
    assert result.id == "bk-pol-0001"


@respx.mock
@pytest.mark.asyncio
async def test_delete_policy_reports_the_outcome(handler_rw):
    """The API answers 204 with no body, so the tool reports what it did."""
    mock_iam(respx.mock)
    respx.delete(f"{API_BASE}/v1/backup-policies/bk-pol-0001").mock(
        return_value=httpx.Response(204)
    )
    result = await handler_rw.delete_backup_policy(policy_id="bk-pol-0001", region="HCM-3")
    assert result.succeeded is True
    assert result.action == "deleted"
    assert "restore points" in result.detail


@pytest.mark.asyncio
async def test_write_blocked_without_allow_write(handler):
    body = CreateBackupPolicyDto(
        backendId="be-0001", projectId="pro-0001", name="x", config=_valid_config()
    )
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.create_backup_policy(body=body, region="HCM-3")


def test_dto_rejects_unknown_fields():
    """extra=forbid catches a misspelt field here instead of dropping it silently."""
    with pytest.raises(ValidationError):
        CreateBackupPolicyDto(
            backendId="be-0001",
            projectId="pro-0001",
            name="x",
            config=_valid_config(),
            retention=7,
        )


def test_dto_rejects_an_hourly_interval_the_platform_refuses():
    with pytest.raises(ValidationError):
        HourlyConfigDto(interval=1, retention=1)


def test_dto_rejects_out_of_range_retention():
    with pytest.raises(ValidationError):
        DailyConfigDto(retention=0)
    with pytest.raises(ValidationError):
        DailyConfigDto(retention=30001)


def test_dto_rejects_an_impossible_hour():
    with pytest.raises(ValidationError):
        BackupPolicyConfigDto(hour=24, dailyEnabled=True, dailyConfig=DailyConfigDto(retention=1))


@respx.mock
@pytest.mark.asyncio
async def test_switch_default_promotes_and_invalidates_the_list(handler_rw):
    """One PUT, no body, and the cached policy list must not survive it."""
    mock_iam(respx.mock)
    route = respx.put(f"{API_BASE}/v1/backup-policies/bk-pol-0001/switch-default").mock(
        return_value=httpx.Response(204)
    )
    result = await handler_rw.update_default_backup_policy(policy_id="bk-pol-0001", region="HCM-3")
    assert route.called
    assert not route.calls[0].request.content
    assert result.resource_id == "bk-pol-0001"
    assert "default" in result.detail.lower()


@pytest.mark.asyncio
async def test_switch_default_needs_write_and_a_clean_id(handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.update_default_backup_policy(policy_id="bk-pol-0001", region="HCM-3")
