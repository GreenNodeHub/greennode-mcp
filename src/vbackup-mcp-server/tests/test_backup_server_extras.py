"""The backup-server tools added on top of the original create/update/delete cycle.

Covers the account statistics, the vServer instance lookup that reaches a
different gateway, immediate backups, moving a destination, and the two restore
point operations.
"""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import (
    API_BASE,
    RAW_INSTANCE,
    RAW_PRESIGNED,
    RAW_STATISTIC,
    mock_iam,
)
from greennode.vbackup_mcp_server.backup_server_handler import BackupServerHandler
from greennode.vbackup_mcp_server.config import REGIONS, VSERVER_SERVICE
from greennode.vbackup_mcp_server.models import (
    BackupNowDto,
    BackupStatisticData,
    UpdateBackupServerDestinationDto,
)
from mcp.server.mcpserver import MCPServer


VSERVER_BASE = REGIONS["HCM-3"][VSERVER_SERVICE]
PROJECT_ID = "pro-0001"
SERVER_ID = "ins-0001"
POINT_ID = "bk-ins-pt-0001"
BACKUP_SERVER_ID = "bk-ins-0001"


@pytest.fixture
def handler(config, client, no_cache):
    return BackupServerHandler(MCPServer("test"), config, client, no_cache)


@pytest.fixture
def writer(config, client, no_cache):
    return BackupServerHandler(MCPServer("test"), config, client, no_cache, allow_write=True)


@respx.mock
@pytest.mark.asyncio
async def test_statistics_expose_coverage_and_waste(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/backup-statistic").mock(
        return_value=httpx.Response(200, json=RAW_STATISTIC)
    )
    result = await handler.get_backup_statistics(project_id=PROJECT_ID, region="HCM-3")
    assert route.calls[0].request.url.params["projectId"] == PROJECT_ID
    assert result.total_servers == 30
    assert result.unprotected_servers == 26
    assert result.orphaned_backup_servers == 35
    assert result.total_backup_failed == 2


def test_statistic_ratios_never_go_negative():
    """Without projectId the API reports total_servers as 0, below the protected count."""
    from greennode.vbackup_mcp_server.models import BackupStatisticData

    data = BackupStatisticData.from_api("HCM-3", "", {**RAW_STATISTIC, "totalServers": 0})
    assert data.total_servers == 0
    assert data.unprotected_servers == 0


@respx.mock
@pytest.mark.asyncio
async def test_instance_lookup_uses_the_vserver_gateway(handler):
    """A project-scoped /v2 path on a different host from every other call here."""
    mock_iam(respx.mock)
    route = respx.get(f"{VSERVER_BASE}/v2/{PROJECT_ID}/servers/{SERVER_ID}").mock(
        return_value=httpx.Response(200, json=RAW_INSTANCE)
    )
    result = await handler.get_vserver_instance(
        server_id=SERVER_ID, project_id=PROJECT_ID, region="HCM-3"
    )
    assert route.called
    assert result.name == "web-01"
    assert result.flavor.cpu == 1
    assert result.flavor.memory_gb == 2
    assert result.image.type == "Ubuntu"
    assert result.boot_volume_id == "vol-0001"
    assert result.addresses[0].fixed_ip == "192.0.2.10"


@respx.mock
@pytest.mark.asyncio
async def test_download_urls_keep_every_part(handler):
    """A split disk returns several links and all of them are needed."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-instance-points/{POINT_ID}/pre-signed-url").mock(
        return_value=httpx.Response(200, json=RAW_PRESIGNED)
    )
    result = await handler.get_backup_server_point_download_urls(point_id=POINT_ID, region="HCM-3")
    assert result.total_volumes == 1
    assert len(result.volumes[0].urls) == 2
    assert result.volumes[0].volume_id == "vol-0001"
    assert result.backup_server_id == BACKUP_SERVER_ID
    assert "shared chats" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_start_backup_posts_to_the_instance_id(writer):
    """The path takes the INSTANCE id while the body carries backend and project."""
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/backup-instances/backup-now/{SERVER_ID}").mock(
        return_value=httpx.Response(202)
    )
    result = await writer.start_backup(
        server_id=SERVER_ID,
        body=BackupNowDto(backendId="be-0001", projectId=PROJECT_ID),
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"backendId": "be-0001", "projectId": PROJECT_ID}
    assert result.resource_id == SERVER_ID
    assert "background" in result.detail


@respx.mock
@pytest.mark.asyncio
async def test_destination_change_warns_that_old_points_stay(writer):
    mock_iam(respx.mock)
    route = respx.put(f"{API_BASE}/v1/backup-instances/{BACKUP_SERVER_ID}/destination").mock(
        return_value=httpx.Response(204)
    )
    result = await writer.update_backup_server_destination(
        backup_server_id=BACKUP_SERVER_ID,
        body=UpdateBackupServerDestinationDto(backupDestinationId="bk-des-0002"),
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {"backupDestinationId": "bk-des-0002"}
    assert "still billed" in result.detail


@respx.mock
@pytest.mark.asyncio
async def test_deleting_one_point_is_destructive(writer):
    mock_iam(respx.mock)
    route = respx.delete(f"{API_BASE}/v1/backup-instance-points/{POINT_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = await writer.delete_backup_server_point(point_id=POINT_ID, region="HCM-3")
    assert route.called
    assert result.action == "restore point deleted"
    assert "soft delete" in result.detail

    tools = {t.name: t for t in await writer.mcp.list_tools()}
    assert tools["delete_backup_server_point"].annotations.destructive_hint is True
    assert tools["start_backup"].annotations.destructive_hint is False
    assert tools["get_backup_statistics"].annotations.read_only_hint is True


@pytest.mark.asyncio
async def test_new_writes_refused_without_allow_write(handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.delete_backup_server_point(point_id=POINT_ID, region="HCM-3")
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.start_backup(
            server_id=SERVER_ID,
            body=BackupNowDto(backendId="be-0001", projectId=PROJECT_ID),
            region="HCM-3",
        )


@pytest.mark.asyncio
async def test_ids_used_in_paths_are_validated(handler):
    with pytest.raises(ValueError, match="point_id"):
        await handler.get_backup_server_point_download_urls(point_id="../../etc", region="HCM-3")
    with pytest.raises(ValueError, match="project_id"):
        await handler.get_vserver_instance(
            server_id=SERVER_ID, project_id="../etc", region="HCM-3"
        )


def test_derived_counters_survive_serialisation():
    """The derived counters must reach the client, not just Python callers.

    A plain `@property` is not serialised by Pydantic, so a value defined that
    way would be absent from every MCP response. This pins both counters into
    the payload and the serialization schema.
    """
    stats = BackupStatisticData.from_api(
        "HCM-3",
        "pro-0001",
        {"totalServers": 30, "totalProtectedServers": 3, "totalBackupServers": 35},
    )
    dumped = stats.model_dump()
    assert dumped["unprotected_servers"] == 27
    assert dumped["orphaned_backup_servers"] == 32
    schema = BackupStatisticData.model_json_schema(mode="serialization")
    assert "unprotected_servers" in schema["properties"]
    assert "orphaned_backup_servers" in schema["properties"]
