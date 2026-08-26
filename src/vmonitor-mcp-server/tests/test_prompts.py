"""Tests for the vMonitor guidance prompts and the get_feature_guide tool."""

from __future__ import annotations

import pytest
from greennode.vmonitor_mcp_server.prompts_handler import _FEATURE_GUIDES, PromptsHandler
from mcp.server.mcpserver import MCPServer


@pytest.fixture
def handler():
    return PromptsHandler(MCPServer("test"))


ALL_FEATURES = [
    "build_dashboard",
    "query_metrics",
    "create_metric_alarm",
    "monitor_infrastructure",
    "edit_metric_unit",
    "manage_log_projects",
    "manage_integrations",
    "create_notification_channel",
    "view_quota_usage",
    "create_uptime_monitor",
]


@pytest.mark.asyncio
async def test_guide_tool_and_prompts_registered(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert "get_feature_guide" in tools

    prompts = {p.name for p in await handler.mcp.list_prompts()}
    expected = {"vmonitor_getting_started"} | {f"vmonitor_{f}" for f in ALL_FEATURES}
    assert expected <= prompts


def test_every_feature_group_has_a_guide():
    assert set(_FEATURE_GUIDES) == set(ALL_FEATURES)


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", ALL_FEATURES)
async def test_each_guide_returns_nonempty_flow(handler, feature):
    guide = await handler.get_feature_guide(feature=feature)
    assert guide.strip()
    assert "## " in guide


@pytest.mark.asyncio
async def test_get_feature_guide_returns_flow(handler):
    guide = await handler.get_feature_guide(feature="edit_metric_unit")
    assert "create_metric_unit_mapping" in guide
    assert "delete_metric_unit_mapping" in guide
    assert "metric_unit_mapping_user_id" in guide
    assert "quota" in guide.lower()


@pytest.mark.asyncio
async def test_uptime_guide_registered_and_returns_flow(handler):
    prompts = {p.name for p in await handler.mcp.list_prompts()}
    assert "vmonitor_create_uptime_monitor" in prompts

    guide = await handler.get_feature_guide(feature="create_uptime_monitor")
    assert "create_uptime" in guide
    assert "validate_uptime" in guide
    assert "list_locations" in guide


@pytest.mark.asyncio
async def test_new_guides_reference_real_tools(handler):
    dashboard = await handler.get_feature_guide(feature="build_dashboard")
    assert "create_widget" in dashboard
    assert "update_dashboard_variables" in dashboard

    alarm = await handler.get_feature_guide(feature="create_metric_alarm")
    assert "create_metric_alarm" in alarm
    assert "list_notifications" in alarm

    notif = await handler.get_feature_guide(feature="create_notification_channel")
    assert "create_notification_otp" in notif
    assert "validate_notification_otp" in notif

    quota = await handler.get_feature_guide(feature="view_quota_usage")
    assert "get_creation_price" in quota
    for order_tool in (
        "resize_metric_quota",
        "create_log_project",
        "resize_log_project",
        "delete_log_project",
        "resize_sms_quota",
        "resize_email_quota",
    ):
        assert order_tool in quota
    assert "--allow-write" in quota


@pytest.mark.asyncio
async def test_prompt_and_tool_share_one_source(handler):
    assert await handler.vmonitor_edit_metric_unit() == await handler.get_feature_guide(
        feature="edit_metric_unit"
    )
    assert await handler.vmonitor_create_uptime_monitor() == await handler.get_feature_guide(
        feature="create_uptime_monitor"
    )
    assert await handler.vmonitor_build_dashboard() == await handler.get_feature_guide(
        feature="build_dashboard"
    )
    assert await handler.vmonitor_view_quota_usage() == await handler.get_feature_guide(
        feature="view_quota_usage"
    )


@pytest.mark.asyncio
async def test_getting_started_lists_feature_guides(handler):
    text = await handler.vmonitor_getting_started()
    assert "Dashboard" in text
    assert "host" in text.lower()
    assert "Metric information" in text
    for feature in ALL_FEATURES:
        assert feature in text
