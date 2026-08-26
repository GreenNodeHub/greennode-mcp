"""Backup servers: reads, volumes, restore points and the write cycle."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import API_BASE, GIB, RAW_POINT, RAW_SERVER, envelope, mock_iam
from greennode.vbackup_mcp_server.backup_server_handler import BackupServerHandler
from greennode.vbackup_mcp_server.models import (
    BackupServerItem,
    BackupServerListData,
    CreateBackupServerDto,
    ServerSelectionDto,
    UpdateBackupServerPolicyDto,
    UpdateBackupServerVolumesDto,
    VolumeSelectionDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


@pytest.fixture
def handler(config, client, no_cache):
    return BackupServerHandler(MCPServer("test"), config, client, no_cache)


@pytest.fixture
def handler_rw(config, client, no_cache):
    return BackupServerHandler(MCPServer("test"), config, client, no_cache, allow_write=True)


@pytest.mark.asyncio
async def test_read_tools_registered_read_only(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    for name in (
        "list_backup_servers",
        "get_backup_server",
        "list_backup_server_volumes",
        "list_backup_server_points",
    ):
        assert tools[name].annotations.read_only_hint is True
    assert "delete_backup_server" not in tools


@pytest.mark.asyncio
async def test_delete_is_annotated_destructive(handler_rw):
    tools = {t.name: t for t in await handler_rw.mcp.list_tools()}
    assert tools["delete_backup_server"].annotations.destructive_hint is True
    assert tools["disable_backup_server"].annotations.destructive_hint is False


@respx.mock
@pytest.mark.asyncio
async def test_list_returns_structured_output(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([RAW_SERVER]))
    )
    result = await handler.list_backup_servers(
        region="HCM-3", server_id=None, name=None, backend_id=None, refresh=False
    )
    assert isinstance(result, BackupServerListData)
    server = result.backup_servers[0]
    assert server.id == "bk-ins-0001"
    assert server.server_id == "ins-0001"
    assert server.policy.schedule.startswith("hourly every 4h")


@respx.mock
@pytest.mark.asyncio
async def test_volume_sizes_converted_from_bytes(handler):
    """The API reports bytes; a caller reading them as GiB is off by 2^30."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([RAW_SERVER]))
    )
    result = await handler.list_backup_servers(
        region="HCM-3", server_id=None, name=None, backend_id=None, refresh=False
    )
    boot = result.backup_servers[0].volumes[0]
    assert boot.size_gb == 20.0
    assert boot.used_gb == 5.0
    assert boot.size_bytes == 20 * GIB


def test_excluded_volume_is_reported():
    """A disk with backup_enabled=false is silently skipped by every run."""
    item = BackupServerItem.from_api(RAW_SERVER)
    assert {v.volume_id: v.backup_enabled for v in item.volumes} == {
        "vol-0001": True,
        "vol-0002": False,
    }


def test_deleted_source_server_is_surfaced():
    """serverDeleted=true still bills the customer, so it must reach the caller."""
    assert BackupServerItem.from_api({**RAW_SERVER, "serverDeleted": True}).server_deleted is True


def test_missing_nested_objects_do_not_raise():
    item = BackupServerItem.from_api({"id": "bk-ins-0002", "serverId": "ins-0002"})
    assert item.policy.id == ""
    assert item.destination.id == ""
    assert item.volumes == []


@respx.mock
@pytest.mark.asyncio
async def test_server_id_filter_is_forwarded(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([RAW_SERVER]))
    )
    await handler.list_backup_servers(
        region="HCM-3", server_id="ins-0001", name=None, backend_id=None, refresh=False
    )
    assert route.calls[0].request.url.params["serverId"] == "ins-0001"


@respx.mock
@pytest.mark.asyncio
async def test_empty_result_is_not_an_error(handler):
    """No backup server for a serverId means 'unprotected', not a failure."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_backup_servers(
        region="HCM-3", server_id="ins-9999", name=None, backend_id=None, refresh=False
    )
    assert result.total == 0


@pytest.mark.asyncio
async def test_malformed_ids_are_rejected_before_any_request(handler):
    with pytest.raises(ValueError, match="server_id"):
        await handler.list_backup_servers(
            region="HCM-3",
            server_id="../../etc/passwd",
            name=None,
            backend_id=None,
            refresh=False,
        )


@respx.mock
@pytest.mark.asyncio
async def test_volumes_endpoint_reads_a_bare_array(handler):
    """This sub-resource answers with a bare array, not the items envelope."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instances/bk-ins-0001/volumes").mock(
        return_value=httpx.Response(200, json=RAW_SERVER["volumes"])
    )
    result = await handler.list_backup_server_volumes(
        backup_server_id="bk-ins-0001", region="HCM-3"
    )
    assert result.total == 2
    assert result.volumes[1].backup_enabled is False


@respx.mock
@pytest.mark.asyncio
async def test_points_expose_the_policy_as_it_was(handler):
    """policySnapshot is an escaped JSON STRING, not an object."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instances/bk-ins-0001/backup-instance-points").mock(
        return_value=httpx.Response(200, json=envelope([RAW_POINT]))
    )
    result = await handler.list_backup_server_points(
        backup_server_id="bk-ins-0001", region="HCM-3"
    )
    point = result.points[0]
    assert point.id == "bk-ins-pt-0001"
    assert point.policy_name_at_run == "nightly-as-it-was"
    assert point.size_gb == 20.0


@respx.mock
@pytest.mark.asyncio
async def test_point_volume_slices_identify_the_boot_disk(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instances/bk-ins-0001/backup-instance-points").mock(
        return_value=httpx.Response(200, json=envelope([RAW_POINT]))
    )
    result = await handler.list_backup_server_points(
        backup_server_id="bk-ins-0001", region="HCM-3"
    )
    slice_ = result.points[0].volume_points[0]
    assert slice_.id == "bk-vol-pt-0001"
    assert slice_.bootable is True
    assert slice_.boot_index == 0


@respx.mock
@pytest.mark.asyncio
async def test_create_sends_the_nested_server_config(handler_rw):
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/backup-instances").mock(return_value=httpx.Response(201))
    body = CreateBackupServerDto(
        backendId="be-0001",
        projectId="pro-0001",
        backupPolicyId="bk-pol-0001",
        backupDestinationId="bk-des-0001",
        serverConfig=[
            ServerSelectionDto(
                serverId="ins-0001",
                volumes=[VolumeSelectionDto(volumeId="vol-0001", backupEnabled=True)],
            )
        ],
    )
    result = await handler_rw.create_backup_server(body=body, region="HCM-3")
    sent = route.calls[0].request.content.decode()
    assert '"serverConfig"' in sent
    assert '"volumeId"' in sent
    assert result.action == "created"


@respx.mock
@pytest.mark.asyncio
async def test_enable_and_disable_hit_their_paths(handler_rw):
    mock_iam(respx.mock)
    enabled = respx.put(f"{API_BASE}/v1/backup-instances/bk-ins-0001/enabled").mock(
        return_value=httpx.Response(200)
    )
    disabled = respx.put(f"{API_BASE}/v1/backup-instances/bk-ins-0001/disabled").mock(
        return_value=httpx.Response(200)
    )
    on = await handler_rw.enable_backup_server(backup_server_id="bk-ins-0001", region="HCM-3")
    off = await handler_rw.disable_backup_server(backup_server_id="bk-ins-0001", region="HCM-3")
    assert enabled.called and disabled.called
    assert on.action == "enabled"
    assert off.action == "disabled"


@respx.mock
@pytest.mark.asyncio
async def test_disable_does_not_claim_to_free_storage(handler_rw):
    """Pausing keeps every restore point; saying otherwise misleads on cost."""
    mock_iam(respx.mock)
    respx.put(f"{API_BASE}/v1/backup-instances/bk-ins-0001/disabled").mock(
        return_value=httpx.Response(200)
    )
    result = await handler_rw.disable_backup_server(backup_server_id="bk-ins-0001", region="HCM-3")
    assert "still billed" in result.detail


@respx.mock
@pytest.mark.asyncio
async def test_update_volume_reports_which_way_it_went(handler_rw):
    mock_iam(respx.mock)
    respx.put(f"{API_BASE}/v1/backup-instances/bk-ins-0001/volumes").mock(
        return_value=httpx.Response(200)
    )
    result = await handler_rw.update_backup_server_volumes(
        backup_server_id="bk-ins-0001",
        body=UpdateBackupServerVolumesDto(volumeId="vol-0002", backupEnabled=False),
        region="HCM-3",
    )
    assert result.action == "volume excluded"
    assert "vol-0002" in result.detail


@respx.mock
@pytest.mark.asyncio
async def test_update_policy_attaches_the_new_schedule(handler_rw):
    mock_iam(respx.mock)
    respx.put(f"{API_BASE}/v1/backup-instances/bk-ins-0001/policies").mock(
        return_value=httpx.Response(200)
    )
    result = await handler_rw.update_backup_server_policy(
        backup_server_id="bk-ins-0001",
        body=UpdateBackupServerPolicyDto(id="bk-pol-0002"),
        region="HCM-3",
    )
    assert result.action == "policy attached"


@respx.mock
@pytest.mark.asyncio
async def test_delete_states_the_data_loss(handler_rw):
    mock_iam(respx.mock)
    respx.delete(f"{API_BASE}/v1/backup-instances/bk-ins-0001").mock(
        return_value=httpx.Response(204)
    )
    result = await handler_rw.delete_backup_server(backup_server_id="bk-ins-0001", region="HCM-3")
    assert result.action == "deleted"
    assert "restore points are gone" in result.detail


@pytest.mark.asyncio
async def test_writes_blocked_without_allow_write(handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.delete_backup_server(backup_server_id="bk-ins-0001", region="HCM-3")


def test_create_dto_requires_at_least_one_server():
    with pytest.raises(ValidationError):
        CreateBackupServerDto(
            backendId="be-0001",
            projectId="pro-0001",
            backupPolicyId="bk-pol-0001",
            backupDestinationId="bk-des-0001",
            serverConfig=[],
        )


def test_create_dto_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CreateBackupServerDto(
            backendId="be-0001",
            projectId="pro-0001",
            backupPolicyId="bk-pol-0001",
            backupDestinationId="bk-des-0001",
            serverConfig=[ServerSelectionDto(serverId="ins-0001")],
            retention=7,
        )


@respx.mock
@pytest.mark.asyncio
async def test_a_second_backup_server_on_one_instance_is_refused(handler_rw):
    """One instance holds one backup server; the API enforces it with a 409."""
    mock_iam(respx.mock)
    respx.post(f"{API_BASE}/v1/backup-instances").mock(
        return_value=httpx.Response(
            409,
            json={"message": "The backup server for server ins-0001 already exists"},
        )
    )
    body = CreateBackupServerDto(
        backendId="be-0001",
        projectId="pro-0001",
        backupPolicyId="bk-pol-0001",
        backupDestinationId="bk-des-0001",
        serverConfig=[
            ServerSelectionDto(
                serverId="ins-0001",
                volumes=[VolumeSelectionDto(volumeId="vol-0001", backupEnabled=True)],
            )
        ],
    )
    with pytest.raises(RuntimeError, match="already exists"):
        await handler_rw.create_backup_server(body=body, region="HCM-3")
