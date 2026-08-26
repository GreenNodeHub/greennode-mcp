"""Tests for the vMonitor synthetic uptime + location tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorUptimeClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    CreateLocationDto,
    CreateUptimeDto,
    SyntheticListData,
    SyntheticResource,
    ValidateUptimeDto,
)
from greennode.vmonitor_mcp_server.synthetic_location_handler import SyntheticLocationHandler
from greennode.vmonitor_mcp_server.synthetic_uptime_handler import SyntheticUptimeHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/vmonitor-uptime-manager/v1"
UID = "0ad0da63-2c9a-489c-8e0a-1cb21de13d95"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def _clients(sample_config):
    config = load_config(sample_config)
    return config, VmonitorUptimeClient(config, TokenManager(config))


@pytest.fixture
def uptime(sample_config):
    config, client = _clients(sample_config)
    return SyntheticUptimeHandler(MCPServer("test"), config, client)


@pytest.fixture
def uptime_rw(sample_config):
    config, client = _clients(sample_config)
    return SyntheticUptimeHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.fixture
def location_rw(sample_config):
    config, client = _clients(sample_config)
    return SyntheticLocationHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.mark.asyncio
async def test_reads_registered_writes_gated(uptime, uptime_rw):
    read_only = {t.name for t in await uptime.mcp.list_tools()}
    assert {"list_uptimes", "get_uptime", "get_uptime_config", "validate_uptime"} <= read_only
    assert "create_uptime" not in read_only
    assert "delete_uptime" not in read_only

    with_write = {t.name for t in await uptime_rw.mcp.list_tools()}
    assert {
        "create_uptime",
        "update_uptime",
        "update_uptime_status",
        "delete_uptime",
    } <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_uptimes_parses_bare_list(uptime):
    _mock_iam(respx.mock)
    respx.get(f"{API}/uptimes").mock(
        return_value=httpx.Response(200, json=[{"id": UID, "name": "hc", "status": "ENABLED"}])
    )

    result = await uptime.list_uptimes()

    assert isinstance(result, SyntheticListData)
    assert result.total_item == 1
    assert result.items[0]["name"] == "hc"


@respx.mock
@pytest.mark.asyncio
async def test_get_uptime_surfaces_scalars(uptime):
    _mock_iam(respx.mock)
    respx.get(f"{API}/uptimes/{UID}").mock(
        return_value=httpx.Response(
            200, json={"id": UID, "name": "hc", "status": "ENABLED", "type": "API"}
        )
    )

    result = await uptime.get_uptime(uptime_id=UID)

    assert isinstance(result, SyntheticResource)
    assert result.id == UID
    assert result.status == "ENABLED"


@pytest.mark.asyncio
async def test_get_uptime_rejects_traversal(uptime):
    with pytest.raises(ValueError):
        await uptime.get_uptime(uptime_id="../secret")


@respx.mock
@pytest.mark.asyncio
async def test_create_uptime_posts_body(uptime_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/uptimes").mock(return_value=httpx.Response(200, json={"id": UID}))

    body = CreateUptimeDto(
        name="hc",
        subtype="HTTP",
        config={"assertions": [], "request": {"url": "https://x", "method": "GET"}},
        locations=["loc-1"],
    )
    await uptime_rw.create_uptime(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["name"] == "hc"
    assert sent["type"] == "API"
    assert sent["locations"] == ["loc-1"]
    assert "options" not in sent
    assert sent["notifications"] == {"Undetermined": [], "Up": [], "In-alarm": []}


@respx.mock
@pytest.mark.asyncio
async def test_create_uptime_keeps_caller_notifications(uptime_rw):
    """An explicit notifications map is passed through unchanged (not defaulted)."""
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/uptimes").mock(return_value=httpx.Response(200, json={"id": UID}))

    body = CreateUptimeDto(
        name="hc",
        subtype="HTTP",
        config={"assertions": [], "request": {"url": "https://x", "method": "GET"}},
        locations=["loc-1"],
        notifications={"Undetermined": [], "Up": ["chan-1"], "In-alarm": ["chan-1"]},
    )
    await uptime_rw.create_uptime(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent["notifications"]["Up"] == ["chan-1"]


@respx.mock
@pytest.mark.asyncio
async def test_update_uptime_status_toggles(uptime_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{API}/uptimes/status/{UID}").mock(return_value=httpx.Response(200))

    result = await uptime_rw.update_uptime_status(uptime_id=UID)

    assert route.called
    assert UID in result


@respx.mock
@pytest.mark.asyncio
async def test_validate_uptime_is_read(uptime):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/uptimes/test").mock(
        return_value=httpx.Response(200, json={"result": "ok"})
    )

    body = ValidateUptimeDto(subtype="HTTP", config={"request": {"url": "https://x"}})
    await uptime.validate_uptime(body=body)

    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_create_location_posts_body(location_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/locations").mock(
        return_value=httpx.Response(200, json={"id": "loc-9", "name": "my-loc"})
    )

    body = CreateLocationDto(name="my-loc")
    result = await location_rw.create_location(body=body)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"name": "my-loc", "type": "PRIVATE"}
    assert result.data["id"] == "loc-9"


def test_dtos_forbid_extra():
    with pytest.raises(ValidationError):
        CreateUptimeDto(name="x", subtype="HTTP", config={}, locations=[], bogus=1)
    with pytest.raises(ValidationError):
        CreateLocationDto(name="x", bogus=1)
