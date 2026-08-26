"""Tests for the vMonitor integration tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.integration_handler import IntegrationHandler
from greennode.vmonitor_mcp_server.models import (
    InstallIntegrationDto,
    IntegrationDetail,
    IntegrationListData,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1"

ENVELOPE = {
    "lstData": [{"id": "app-1", "name": "MySQL", "description": "db agent", "installed": True}],
    "page": 1,
    "pageSize": 10,
    "totalItem": 1,
    "totalPage": 1,
}
DETAIL = {"data": {"id": "app-1", "name": "MySQL", "installed": False, "configuration": "cfg"}}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    return IntegrationHandler(
        MCPServer("test"), config, VmonitorClient(config, TokenManager(config))
    )


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    return IntegrationHandler(
        MCPServer("test"), config, VmonitorClient(config, TokenManager(config)), allow_write=True
    )


@pytest.mark.asyncio
async def test_read_registered_write_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert {"list_integrations", "get_integration"} <= read_only
    assert "delete_integration" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {
        "update_integration_installed",
        "update_integration_uninstalled",
        "delete_integration",
    } <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_integrations_parses(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/integrations/list").mock(return_value=httpx.Response(200, json=ENVELOPE))

    result = await handler.list_integrations(page=None, size=None)

    assert isinstance(result, IntegrationListData)
    assert result.items[0].installed is True


@respx.mock
@pytest.mark.asyncio
async def test_get_integration_unwraps(handler):
    _mock_iam(respx.mock)
    respx.get(f"{API}/integrations/app-1").mock(return_value=httpx.Response(200, json=DETAIL))

    result = await handler.get_integration(integration_id="app-1")

    assert isinstance(result, IntegrationDetail)
    assert result.id == "app-1"
    assert result.configuration == "cfg"


@respx.mock
@pytest.mark.asyncio
async def test_update_integration_installed_puts_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/integrations/install/app-1").mock(
        return_value=httpx.Response(200, json=DETAIL)
    )

    body = InstallIntegrationDto(logProjectId="log-9")
    await handler_rw.update_integration_installed(integration_id="app-1", body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"logProjectId": "log-9"}


@respx.mock
@pytest.mark.asyncio
async def test_uninstall_hits_uninstall_path(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/integrations/uninstall/app-1").mock(return_value=httpx.Response(200))

    msg = await handler_rw.update_integration_uninstalled(integration_id="app-1")

    assert route.called
    assert "app-1" in msg


@respx.mock
@pytest.mark.asyncio
async def test_delete_integration_confirms(handler_rw):
    _mock_iam(respx.mock)
    respx.delete(f"{API}/integrations/app-1").mock(return_value=httpx.Response(200))

    msg = await handler_rw.delete_integration(integration_id="app-1")

    assert "app-1" in msg


@pytest.mark.asyncio
async def test_get_rejects_bad_id(handler):
    with pytest.raises(ValueError):
        await handler.get_integration(integration_id="../../secret")


def test_install_dto_forbids_extra():
    with pytest.raises(ValidationError):
        InstallIntegrationDto(bogus=1)
