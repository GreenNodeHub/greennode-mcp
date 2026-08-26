"""Tests for the vMonitor notification-gateway tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorNotificationClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    CreateNotificationDto,
    CreateNotificationOtpDto,
    NotificationListData,
    NotificationTypeListData,
    UpdateNotificationDto,
    ValidateNotificationOtpDto,
)
from greennode.vmonitor_mcp_server.notification_handler import NotificationHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/notification-gateway/api/v1"

TYPE_ENVELOPE = {
    "lstData": [
        {"id": "t1", "name": "Email", "description": ""},
        {"id": "t2", "name": "SMS", "description": ""},
    ],
    "page": None,
    "pageSize": None,
    "totalPage": None,
    "totalItem": 2,
}

LIST_ENVELOPE = {
    "lstData": [
        {
            "id": "n1",
            "name": "ops-email",
            "address": "alerts@example.com",
            "header": "",
            "typeNotification": {"id": "t1", "name": "Email", "description": ""},
            "createdDate": "2026-06-11T17:22:11",
            "metricMappingId": "m1",
        }
    ],
    "page": 1,
    "pageSize": 10,
    "totalPage": 1,
    "totalItem": 1,
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    return NotificationHandler(
        MCPServer("test"), config, VmonitorNotificationClient(config, TokenManager(config))
    )


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    return NotificationHandler(
        MCPServer("test"),
        config,
        VmonitorNotificationClient(config, TokenManager(config)),
        allow_write=True,
    )


@pytest.mark.asyncio
async def test_read_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert "list_notifications" in read_only
    assert "create_notification" not in read_only
    assert "delete_notification" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {
        "create_notification_otp",
        "validate_notification_otp",
        "create_notification",
        "update_notification",
        "delete_notification",
    } <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_notification_types_parses(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/type/list").mock(return_value=httpx.Response(200, json=TYPE_ENVELOPE))

    result = await handler.list_notification_types(page=None, size=None)

    assert isinstance(result, NotificationTypeListData)
    assert result.total_item == 2
    assert [t.name for t in result.items] == ["Email", "SMS"]


@respx.mock
@pytest.mark.asyncio
async def test_list_notifications_parses(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/notification/list/typeSearch").mock(
        return_value=httpx.Response(200, json=LIST_ENVELOPE)
    )

    result = await handler.list_notifications(searchtext="", field="", type="", page=1, size=10)

    assert isinstance(result, NotificationListData)
    assert result.total_item == 1
    assert result.items[0].type_name == "Email"
    assert result.items[0].address == "alerts@example.com"


@respx.mock
@pytest.mark.asyncio
async def test_list_notifications_defaults_field_when_searchtext_set(handler):
    """A non-empty searchtext with an empty field 500s upstream, so the handler
    fills field='name' — searching by text without a field must still work."""
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/notification/list/typeSearch").mock(
        return_value=httpx.Response(200, json=LIST_ENVELOPE)
    )

    await handler.list_notifications(searchtext="mcp", field="", type="", page=1, size=10)

    assert route.calls.last.request.url.params["field"] == "name"
    # an explicit field is preserved
    await handler.list_notifications(searchtext="mcp", field="address", type="", page=1, size=10)
    assert route.calls.last.request.url.params["field"] == "address"


@respx.mock
@pytest.mark.asyncio
async def test_get_otp_info_encodes_address(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/notification/otps/a%40b.com").mock(
        return_value=httpx.Response(200, json={"ref": "r1", "expiredAt": "123"})
    )

    result = await handler.get_notification_otp_info(address="a@b.com")

    assert route.called
    assert result.ref == "r1"


@respx.mock
@pytest.mark.asyncio
async def test_create_otp_posts_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/notification/otps").mock(
        return_value=httpx.Response(200, json={"ref": "r1", "expiredAt": "999"})
    )

    body = CreateNotificationOtpDto(type="Email", address="a@b.com")
    result = await handler_rw.create_notification_otp(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"type": "Email", "address": "a@b.com", "header": ""}
    assert result.ref == "r1"


@respx.mock
@pytest.mark.asyncio
async def test_create_notification_posts_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/notification").mock(
        return_value=httpx.Response(200, json={"id": "n9", "name": "chan"})
    )

    body = CreateNotificationDto(name="chan", address="a@b.com", type="Email", otpCode="code123")
    result = await handler_rw.create_notification(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["otpCode"] == "code123"
    assert sent["type"] == "Email"
    assert result.data["id"] == "n9"
    assert result.id == "n9"


@respx.mock
@pytest.mark.asyncio
async def test_update_notification_puts_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/notification").mock(return_value=httpx.Response(200, json={}))

    body = UpdateNotificationDto(id="n1", name="chan2", address="a@b.com", type="Email")
    await handler_rw.update_notification(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["id"] == "n1"
    assert sent["name"] == "chan2"


@respx.mock
@pytest.mark.asyncio
async def test_delete_notification_confirms(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{API}/notification/n1").mock(return_value=httpx.Response(200))

    result = await handler_rw.delete_notification(notification_id="n1")

    assert route.called
    assert "n1" in result


@pytest.mark.asyncio
async def test_delete_notification_rejects_traversal(handler_rw):
    with pytest.raises(ValueError):
        await handler_rw.delete_notification(notification_id="../secret")


def test_dtos_forbid_extra_and_validate_channel():
    with pytest.raises(ValidationError):
        CreateNotificationDto(name="x", address="a", type="Email", bogus=1)
    with pytest.raises(ValidationError):
        CreateNotificationOtpDto(type="Carrier-Pigeon", address="a")
    ValidateNotificationOtpDto(otp="1", address="a", ref="r")
