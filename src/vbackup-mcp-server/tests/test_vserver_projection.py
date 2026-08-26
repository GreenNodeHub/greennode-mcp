"""The /v1/vserver/** projection and volume usage."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import API_BASE, GIB, mock_iam
from greennode.vbackup_mcp_server.models import (
    CreateVserverBackupServersDto,
    VolumeUsageQueryDto,
)
from greennode.vbackup_mcp_server.vserver_handler import VserverHandler
from mcp.server.mcpserver import MCPServer


RAW_VSERVER_SERVER = {
    "backupInstanceId": "bk-ins-0001",
    "backupInstanceName": "web-01-backup",
    "serverId": "ins-0001",
    "createdAt": "2026-07-24T07:50:28.000+00:00",
    "latestRecord": "2026-07-24T07:50:29.000+00:00",
    "protectedVolumes": 2,
    "products": ["vServer"],
    "backupDestination": {
        "id": "bk-des-0001",
        "name": "default-vault",
        "status": "ACTIVE",
        "isDefault": True,
        "type": "VAULT",
        "config": {"vault": {"regionName": "HCM04", "used": 30 * GIB}},
    },
}

RAW_VSERVER_POINT = {
    "backupInstancePointId": "bk-ins-pt-0001",
    "backupInstanceId": "bk-ins-0001",
    "snapshotTime": "2026-05-28T05:00:06.000+00:00",
    "size": 20 * GIB,
    "usage": 11 * GIB,
    "serverInfo": {
        "name": "web-01",
        "imageId": "img-0001",
        "imageType": "Ubuntu_GPU",
        "imageVersion": "1-Ubuntu-22.04",
        "encryptionVolume": False,
    },
    "backupDestination": {"vault": {"regionName": "HCM04", "used": 30 * GIB}},
}

VOLUME_POINTS = [
    {
        "backupVolumePointId": "bk-vol-pt-0001",
        "name": "web-01 boot_volume",
        "size": 20 * GIB,
        "bootIndex": 0,
        "volumeTypeId": "vtype-0001",
        "bootable": True,
        "backupInstancePointId": "bk-ins-pt-0001",
    },
    {
        "backupVolumePointId": "bk-vol-pt-0002",
        "name": "web-01 data",
        "size": 50 * GIB,
        "bootIndex": 1,
        "volumeTypeId": "vtype-0001",
        "bootable": False,
        "backupInstancePointId": "bk-ins-pt-0001",
    },
]


@pytest.fixture
def handler(config, client, no_cache):
    return VserverHandler(MCPServer("test"), config, client, no_cache)


@pytest.fixture
def handler_rw(config, client, no_cache):
    return VserverHandler(MCPServer("test"), config, client, no_cache, allow_write=True)


@pytest.mark.asyncio
async def test_read_tools_registered(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    for name in (
        "list_vserver_backup_servers",
        "get_vserver_backup_server",
        "list_vserver_backup_server_points",
        "get_vserver_backup_server_point",
        "list_vserver_backup_volume_points",
        "get_vserver_backup_volume_point",
        "list_volume_usage",
    ):
        assert tools[name].annotations.read_only_hint is True
    assert "create_vserver_backup_servers" not in tools


@respx.mock
@pytest.mark.asyncio
async def test_projection_list_requires_and_sends_project_id(handler):
    """Without projectId the API answers 200 with an empty array, not an error."""
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/vserver/backup-instances").mock(
        return_value=httpx.Response(200, json=[RAW_VSERVER_SERVER])
    )
    result = await handler.list_vserver_backup_servers(
        project_id="pro-0001", region="HCM-3", backend_id=None
    )
    assert route.calls[0].request.url.params["projectId"] == "pro-0001"
    assert result.backup_servers[0].id == "bk-ins-0001"


@respx.mock
@pytest.mark.asyncio
async def test_projection_list_reads_a_bare_array(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/vserver/backup-instances").mock(
        return_value=httpx.Response(200, json=[RAW_VSERVER_SERVER, RAW_VSERVER_SERVER])
    )
    result = await handler.list_vserver_backup_servers(
        project_id="pro-0001", region="HCM-3", backend_id=None
    )
    assert result.total == 2


@respx.mock
@pytest.mark.asyncio
async def test_projection_reads_its_own_field_names(handler):
    """The projection renames every field; the generic model returns empty ids here."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/vserver/backup-instances").mock(
        return_value=httpx.Response(200, json=[RAW_VSERVER_SERVER])
    )
    result = await handler.list_vserver_backup_servers(
        project_id="pro-0001", region="HCM-3", backend_id=None
    )
    server = result.backup_servers[0]
    assert server.id == "bk-ins-0001"
    assert server.name == "web-01-backup"
    assert server.protected_volume_count == 2
    assert server.destination_name == "default-vault"
    assert server.vault.used_gb == 30.0
    assert result.project_id == "pro-0001"


@respx.mock
@pytest.mark.asyncio
async def test_projection_points_report_the_captured_image(handler):
    """server_info is the only place the image behind a restore point is reported."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/vserver/backup-instances/bk-ins-0001/backup-instance-points").mock(
        return_value=httpx.Response(200, json=[RAW_VSERVER_POINT])
    )
    result = await handler.list_vserver_backup_server_points(
        backup_server_id="bk-ins-0001", region="HCM-3"
    )
    point = result.points[0]
    assert point.id == "bk-ins-pt-0001"
    assert point.server_info.image_type == "Ubuntu_GPU"
    assert point.server_info.image_version == "1-Ubuntu-22.04"
    assert point.size_gb == 20.0
    assert point.used_gb == 11.0


@respx.mock
@pytest.mark.asyncio
async def test_volume_points_identify_the_boot_disk(handler):
    """This family is the only one reporting bootable/boot_index/volume_type."""
    mock_iam(respx.mock)
    respx.get(
        f"{API_BASE}/v1/vserver/backup-instance-points/bk-ins-pt-0001/backup-volume-points"
    ).mock(return_value=httpx.Response(200, json=VOLUME_POINTS))
    result = await handler.list_vserver_backup_volume_points(
        point_id="bk-ins-pt-0001", region="HCM-3"
    )
    assert result.total == 2
    boot = next(v for v in result.volume_points if v.bootable)
    assert boot.id == "bk-vol-pt-0001"
    assert boot.boot_index == 0
    assert boot.size_gb == 20.0


@respx.mock
@pytest.mark.asyncio
async def test_volume_point_id_read_from_its_own_spelling(handler):
    """This family spells the id backupVolumePointId, not id."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/vserver/backup-volume-points/bk-vol-pt-0001").mock(
        return_value=httpx.Response(200, json=VOLUME_POINTS[0])
    )
    result = await handler.get_vserver_backup_volume_point(
        volume_point_id="bk-vol-pt-0001", region="HCM-3"
    )
    assert result.id == "bk-vol-pt-0001"


@respx.mock
@pytest.mark.asyncio
async def test_generic_volume_point_shape_also_understood(handler):
    """The generic family spells it `id` with volumeSize/volumeUsage instead."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/vserver/backup-volume-points/bk-vol-pt-0003").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "bk-vol-pt-0003",
                "volumeId": "vol-0003",
                "parentId": "bk-ins-pt-0001",
                "volumeSize": 10 * GIB,
                "volumeUsage": 2 * GIB,
                "status": "ACTIVE",
            },
        )
    )
    result = await handler.get_vserver_backup_volume_point(
        volume_point_id="bk-vol-pt-0003", region="HCM-3"
    )
    assert result.id == "bk-vol-pt-0003"
    assert result.volume_id == "vol-0003"
    assert result.size_gb == 10.0
    assert result.backup_server_point_id == "bk-ins-pt-0001"


@respx.mock
@pytest.mark.asyncio
async def test_iam_denied_surfaces_as_an_error(handler):
    """A 403 here means 'not allowed', and must not read as 'nothing found'."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/vserver/backup-instances/bk-ins-0001").mock(
        return_value=httpx.Response(
            403, json=[{"code": "IAM_PERMISSION_DENIED", "message": "IAM denied action"}]
        )
    )
    with pytest.raises(RuntimeError, match="403"):
        await handler.get_vserver_backup_server(backup_server_id="bk-ins-0001", region="HCM-3")


@respx.mock
@pytest.mark.asyncio
async def test_projection_points_read_a_bare_array(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/vserver/backup-instances/bk-ins-0001/backup-instance-points").mock(
        return_value=httpx.Response(200, json=[RAW_VSERVER_POINT])
    )
    result = await handler.list_vserver_backup_server_points(
        backup_server_id="bk-ins-0001", region="HCM-3"
    )
    assert result.points[0].id == "bk-ins-pt-0001"


@respx.mock
@pytest.mark.asyncio
async def test_volume_usage_posts_the_query_and_converts_bytes(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/volume-usage").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "volumeId": "vol-0001",
                    "volumeSize": 20 * GIB,
                    "volumeUsage": 5 * GIB,
                    "backendId": "be-0001",
                    "projectId": "pro-0001",
                }
            ],
        )
    )
    body = VolumeUsageQueryDto(backendId="be-0001", projectId="pro-0001", volumeIds=["vol-0001"])
    result = await handler.list_volume_usage(body=body, region="HCM-3")
    assert route.called
    assert result.volumes[0].used_gb == 5.0
    assert result.missing_volume_ids == []


@respx.mock
@pytest.mark.asyncio
async def test_volume_usage_reports_volumes_it_could_not_measure(handler):
    """A volume whose server was deleted no longer exists and cannot be measured."""
    mock_iam(respx.mock)
    respx.post(f"{API_BASE}/v1/volume-usage").mock(
        return_value=httpx.Response(
            200, json=[{"volumeId": "vol-0001", "volumeSize": GIB, "volumeUsage": 0}]
        )
    )
    body = VolumeUsageQueryDto(
        backendId="be-0001", projectId="pro-0001", volumeIds=["vol-0001", "vol-gone"]
    )
    result = await handler.list_volume_usage(body=body, region="HCM-3")
    assert result.missing_volume_ids == ["vol-gone"]


@respx.mock
@pytest.mark.asyncio
async def test_projection_create_sends_server_ids(handler_rw):
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/vserver/backup-instances").mock(
        return_value=httpx.Response(200)
    )
    body = CreateVserverBackupServersDto(projectId="pro-0001", serverIds=["ins-0001"])
    result = await handler_rw.create_vserver_backup_servers(body=body, region="HCM-3")
    assert '"serverIds"' in route.calls[0].request.content.decode()
    assert result.action == "created"
    assert "default policy" in result.detail


@pytest.mark.asyncio
async def test_projection_create_blocked_without_allow_write(handler):
    body = CreateVserverBackupServersDto(projectId="pro-0001", serverIds=["ins-0001"])
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.create_vserver_backup_servers(body=body, region="HCM-3")


@pytest.mark.asyncio
async def test_malformed_project_id_rejected(handler):
    with pytest.raises(ValueError, match="project_id"):
        await handler.list_vserver_backup_servers(
            project_id="../etc", region="HCM-3", backend_id=None
        )
