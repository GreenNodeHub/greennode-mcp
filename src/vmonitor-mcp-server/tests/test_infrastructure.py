"""Tests for the vMonitor infrastructure host-listing tools."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.infrastructure_handler import InfrastructureHandler
from greennode.vmonitor_mcp_server.models import (
    HostDetail,
    HostListData,
    HostMetricInfo,
    HostMetricSnapshot,
    HostSummary,
)
from mcp.server.mcpserver import MCPServer


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
INFRA = "https://vmonitorapis.vngcloud.vn/vmonitor-api/api/v1/infrastructure"

VBACKUP_ENVELOPE = {
    "lstData": [
        {
            "id": "host-1",
            "user_id": 42,
            "vbackup_id": "bk-9",
            "vbackup_name": "nightly-backup",
            "monitor_enabled": True,
            "blocked": False,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "deleted_at": None,
        }
    ],
    "page": 1,
    "pageSize": 20,
    "totalItem": 5,
    "totalPage": 1,
}

BASE_HOST_ENVELOPE = {
    "lstData": [
        {
            "id": "host-2",
            "name": "web-01",
            "os": "Ubuntu 22.04",
            "enabled": True,
            "plugins": [{"name": "cpu"}, {"name": "mem"}],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }
    ],
    "page": 1,
    "pageSize": 20,
    "totalItem": 1,
    "totalPage": 1,
}


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


HOST_DETAIL = {
    "id": "host-2",
    "name": "web-01",
    "os": "Ubuntu 22.04",
    "enabled": True,
    "plugins": [{"name": "cpu"}, {"name": "mem"}],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
}

HOST_METRIC = {
    "status": {
        "name": "status",
        "status": "UP",
        "value": "1",
        "createdAt": "2026-01-02T00:00:00Z",
    },
    "cpuUsage": {"name": "cpu_usage", "value": "12.5", "createdAt": "2026-01-02T00:00:00Z"},
    "memAvail": {"name": "mem_avail", "value": "2048", "createdAt": "2026-01-02T00:00:00Z"},
}


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return InfrastructureHandler(MCPServer("test"), config, client)


@pytest.fixture
def handler_rw(sample_config):
    config = load_config(sample_config)
    client = VmonitorClient(config, TokenManager(config))
    return InfrastructureHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.mark.asyncio
async def test_all_host_tools_registered(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert {
        "list_hosts",
        "list_vserver_hosts",
        "list_vstorage_hosts",
        "list_vdb_hosts",
        "list_vdb_kafka_hosts",
        "list_vlb_hosts",
        "list_vbackup_hosts",
        "list_vbandwidth_hosts",
        "list_vas_hosts",
    } <= tools


@respx.mock
@pytest.mark.asyncio
async def test_vbackup_hosts_normalised(handler):
    _mock_iam(respx.mock)
    respx.get(f"{INFRA}/vbackup/hosts").mock(
        return_value=httpx.Response(200, json=VBACKUP_ENVELOPE)
    )

    result = await handler.list_vbackup_hosts(name=None, page=1, size=20)

    assert isinstance(result, HostListData)
    assert result.kind == "vbackup"
    assert result.total_item == 5
    host = result.items[0]
    assert host.id == "host-1"
    assert host.name == "nightly-backup"
    assert host.resource_id == "bk-9"
    assert host.monitor_enabled is True
    assert host.user_id == 42


@respx.mock
@pytest.mark.asyncio
async def test_base_hosts_use_searching_text_and_normalise(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{INFRA}/hosts").mock(
        return_value=httpx.Response(200, json=BASE_HOST_ENVELOPE)
    )

    result = await handler.list_hosts(name="web", page=2, size=10)

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"page": "2", "size": "10", "searching_text": "web"}
    host = result.items[0]
    assert host.name == "web-01"
    assert host.os == "Ubuntu 22.04"
    assert host.enabled is True
    assert host.plugin_count == 2
    assert host.resource_id == ""


@respx.mock
@pytest.mark.asyncio
async def test_page_and_size_always_sent_without_filter(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{INFRA}/vserver/hosts").mock(
        return_value=httpx.Response(200, json={"lstData": [], "totalItem": 0})
    )

    await handler.list_vserver_hosts(name=None, page=1, size=20)

    sent = dict(route.calls.last.request.url.params.multi_items())
    assert sent == {"page": "1", "size": "20"}


@pytest.mark.asyncio
async def test_host_write_tools_gated_by_allow_write(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    assert "get_host" in read_only
    assert "get_host_metrics" in read_only
    assert "delete_host" not in read_only
    assert "update_host_enabled" not in read_only

    with_write = {t.name for t in await handler_rw.mcp.list_tools()}
    assert {"update_host_enabled", "update_host_disabled", "delete_host"} <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_get_host_returns_detail(handler):
    _mock_iam(respx.mock)
    respx.get(f"{INFRA}/hosts/host-2").mock(return_value=httpx.Response(200, json=HOST_DETAIL))

    result = await handler.get_host(host_id="host-2")

    assert isinstance(result, HostDetail)
    assert result.name == "web-01"
    assert result.os == "Ubuntu 22.04"
    assert result.enabled is True
    assert result.plugin_count == 2


@pytest.mark.asyncio
async def test_get_host_rejects_bad_id(handler):
    with pytest.raises(ValueError):
        await handler.get_host(host_id="../../secret")


@respx.mock
@pytest.mark.asyncio
async def test_get_host_metrics_returns_snapshot(handler):
    _mock_iam(respx.mock)
    respx.get(f"{INFRA}/hosts/host-2/metric").mock(
        return_value=httpx.Response(200, json=HOST_METRIC)
    )

    result = await handler.get_host_metrics(host_id="host-2")

    assert isinstance(result, HostMetricInfo)
    assert result.status is not None and result.status.status == "UP"
    assert result.cpu_usage is not None and result.cpu_usage.value == "12.5"
    assert result.mem_avail is not None and result.mem_avail.value == "2048"
    assert result.iowait is None


@respx.mock
@pytest.mark.asyncio
async def test_enable_disable_host_put_correct_paths(handler_rw):
    _mock_iam(respx.mock)
    en = respx.put(f"{INFRA}/hosts/host-2/enabled").mock(
        return_value=httpx.Response(200, json={**HOST_DETAIL, "enabled": True})
    )
    dis = respx.put(f"{INFRA}/hosts/host-2/disabled").mock(
        return_value=httpx.Response(200, json={**HOST_DETAIL, "enabled": False})
    )

    enabled = await handler_rw.update_host_enabled(host_id="host-2")
    disabled = await handler_rw.update_host_disabled(host_id="host-2")

    assert en.called and enabled.enabled is True
    assert dis.called and disabled.enabled is False


@respx.mock
@pytest.mark.asyncio
async def test_delete_host_returns_confirmation(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{INFRA}/hosts/host-2").mock(return_value=httpx.Response(200))

    result = await handler_rw.delete_host(host_id="host-2")

    assert route.called
    assert "host-2" in result


@pytest.mark.asyncio
async def test_delete_host_rejects_bad_id(handler_rw):
    with pytest.raises(ValueError):
        await handler_rw.delete_host(host_id="bad/id")


TYPED_METRIC = {
    "vServerCPUUsage": {"name": "cpu", "value": "12.5", "createdAt": "2026-01-02T00:00:00Z"},
    "vServerLoad": None,
    "status": {
        "name": "status",
        "status": "UP",
        "value": "1",
        "createdAt": "2026-01-02T00:00:00Z",
    },
}

VSERVER_HOST = {
    "id": "host-9",
    "user_id": 7,
    "server_id": "ins-abc",
    "server_name": "web-01",
    "monitor_enabled": False,
    "blocked": False,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
}


@pytest.mark.asyncio
async def test_typed_by_id_tools_gated(handler, handler_rw):
    read_only = {t.name for t in await handler.mcp.list_tools()}
    with_write = {t.name for t in await handler_rw.mcp.list_tools()}

    metric_tools = {
        f"get_{t}_host_metrics"
        for t in ["vserver", "vstorage", "vdb", "vlb", "vbackup", "vbandwidth", "vas"]
    }
    assert metric_tools <= read_only

    write_tools = {
        f"update_{t}_host"
        for t in ["vserver", "vstorage", "vdb", "vlb", "vbackup", "vbandwidth", "vas"]
    }
    write_tools |= {
        f"delete_{t}_host"
        for t in ["vserver", "vstorage", "vdb", "vlb", "vbackup", "vbandwidth", "vas"]
    }
    assert not (write_tools & read_only)
    assert write_tools <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_typed_host_metrics_generic_snapshot(handler):
    _mock_iam(respx.mock)
    respx.get(f"{INFRA}/vserver/hosts/host-9/metric").mock(
        return_value=httpx.Response(200, json=TYPED_METRIC)
    )

    result = await handler.get_vserver_host_metrics(host_id="host-9")

    assert isinstance(result, HostMetricSnapshot)
    assert result.kind == "vserver"
    assert result.status is not None and result.status.status == "UP"
    assert result.metrics["vServerCPUUsage"].value == "12.5"
    assert result.metrics["vServerLoad"] is None
    assert "status" not in result.metrics


@respx.mock
@pytest.mark.asyncio
async def test_typed_host_metrics_null_created_at(handler):
    """A host with no recent data point returns createdAt=null; must not crash.

    The gateway sends ``"createdAt": null`` on both the status block and metric
    samples when a host has reported nothing recently (e.g. monitoring disabled).
    created_at must coerce to "" rather than fail model validation.
    """
    _mock_iam(respx.mock)
    payload = {
        "vServerCPUUsage": {"name": "cpu", "value": "0", "createdAt": None},
        "status": {"name": "status", "status": "UP", "value": "1", "createdAt": None},
    }
    respx.get(f"{INFRA}/vserver/hosts/host-null/metric").mock(
        return_value=httpx.Response(200, json=payload)
    )

    result = await handler.get_vserver_host_metrics(host_id="host-null")

    assert isinstance(result, HostMetricSnapshot)
    assert result.status is not None and result.status.status == "UP"
    assert result.status.created_at == ""
    assert result.metrics["vServerCPUUsage"].created_at == ""


@respx.mock
@pytest.mark.asyncio
async def test_update_typed_host_sends_enabled_body(handler_rw):
    _mock_iam(respx.mock)
    route = respx.put(f"{INFRA}/vserver/hosts/host-9").mock(
        return_value=httpx.Response(200, json={**VSERVER_HOST, "monitor_enabled": True})
    )

    result = await handler_rw.update_vserver_host(host_id="host-9", enabled=True)

    import json

    assert json.loads(route.calls.last.request.content) == {"enabled": True}
    assert isinstance(result, HostSummary)
    assert result.kind == "vserver"
    assert result.name == "web-01"
    assert result.resource_id == "ins-abc"
    assert result.monitor_enabled is True


@respx.mock
@pytest.mark.asyncio
async def test_delete_typed_host_confirmation_and_bad_id(handler_rw):
    _mock_iam(respx.mock)
    route = respx.delete(f"{INFRA}/vbackup/hosts/host-9").mock(return_value=httpx.Response(204))

    result = await handler_rw.delete_vbackup_host(host_id="host-9")
    assert route.called
    assert "host-9" in result

    with pytest.raises(ValueError):
        await handler_rw.delete_vlb_host(host_id="../oops")
