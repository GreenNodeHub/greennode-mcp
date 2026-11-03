"""Tests for the MemoryStore (Redis) backup handler.

The three things this family does differently from the relational one, and the
three things this file therefore pins down: the get path has its segments
reversed, delete is a `POST` whose array body can remove several backups at
once, and the restore config carries the Redis password pair instead of volume
fields.
"""

from __future__ import annotations

import httpx
import json
import pydantic
import pytest
import respx
from greennode.mcp_core.auth import TokenManager
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import load_config
from greennode.vdb_mcp_server.memory_backup_handler import MemoryBackupHandler
from greennode.vdb_mcp_server.models import (
    BackupCreateData,
    BackupDeleteData,
    CreateBackupDto,
    DatabaseBackup,
    DryRunData,
    FreeBackupUsageData,
    MemoryBackupListData,
    OrderData,
    RestoreMemoryBackupDto,
)
from mcp.server.mcpserver import MCPServer
from tests.helpers import MEMORY, content_list, envelope, mock_iam, page_object


BACKUP_ID = "bk-5f34c2a4-06d0-4fe1-862b-93225748d309"
INSTANCE_ID = "db-5a4d26f1-1451-4a93-9d9a-72d117f98831"
PASSWORD = "Rediscachepass123"  # 17 chars: legal here, too short for nothing


def _backup(backup_id=BACKUP_ID, thin=False, **over):
    """A backup row. `thin=True` mirrors what the listing actually returns."""
    row = {
        "id": backup_id,
        "name": "mds_db_5a4d26f1_260907053000+0700",
        "description": "Auto backup daily",
        "dbInstanceId": INSTANCE_ID,
        "instanceName": "cli-redis-test",
        "type": "AUTO_DAILY",
        "backupType": "FULL",
        "status": "COMPLETED",
        "datastoreType": "Redis",
        "datastoreVersion": "7.2",
        "storageSize": 8,
        "storageType": "Gen2-NVMe2-IOPS3000-HCM03-1B",
        "ram": 8,
        "vcpu": 4,
        "packageId": "240",
        "netIds": ["net-372ddaf7-6933-4f86-b0f9-564db84a67d4"],
        "backupTier": "FREE",
        "size": 0.022,
        "created": "2026-09-07 05:30:07",
    }
    if thin:
        # Exactly the fields the project-wide listing was measured to null,
        # 2026-09-10. Note it DOES carry dbInstanceId and instanceName -- it is
        # the per-instance listing that nulls those, which is the opposite of
        # what "the listing is thinner" would lead you to assume.
        row.update(
            {
                "datastoreType": "redis",
                "storageSize": None,
                "storageType": None,
                "ram": None,
                "vcpu": None,
                "packageId": None,
                "netIds": None,
                "backupDuration": None,
                "isRestoring": False,
            }
        )
    row.update(over)
    return row


ORDER_RESPONSE = envelope(
    [
        {
            "orderUrl": "https://vdb.console.vngcloud.vn/memorystore/database",
            "orderId": "65c00881",
            "resourceId": "db-99999999-0000-0000-0000-000000000000",
        }
    ]
)


def _delete_response(*rows) -> dict:
    return envelope(list(rows))


def _ok_row(backup_id):
    return {
        "backupId": backup_id,
        "action": "delete",
        "status": "PROCESSING",
        "success": True,
        "errorMsg": None,
        "code": 200,
    }


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return MemoryBackupHandler(MCPServer("test"), config, client, allow_write=allow_write)


@pytest.fixture
def handler(sample_config):
    return _handler(sample_config, allow_write=True)


@pytest.fixture
def readonly_handler(sample_config):
    return _handler(sample_config, allow_write=False)


def _restore_spec(**over):
    payload = {
        "name": "mcpmemrst",
        "packageId": "139",
        "datastoreType": "Redis",
        "datastoreVersion": "7.2",
        "netIds": ["sub-aaaa"],
        "locateZoneId": "HCM03-1A",
        "redisPassword": PASSWORD,
    }
    payload.update(over)
    return RestoreMemoryBackupDto(**payload)


# --------------------------------------------------------------------------
# registration and write gating
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_are_absent_in_read_only_mode(readonly_handler):
    tools = {t.name for t in await readonly_handler.mcp.list_tools()}
    assert "list_memory_backups" in tools
    assert "restore_memory_backup_dryrun" in tools, "previews stay available"
    for name in ("create_memory_backup", "restore_memory_backup", "delete_memory_backups"):
        assert name not in tools


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    expected = {
        "list_memory_backups",
        "get_memory_backup",
        "get_memory_free_backup_usage",
        "restore_memory_backup_dryrun",
        "create_memory_backup",
        "restore_memory_backup",
        "delete_memory_backups",
    }
    assert expected <= tools
    assert len(expected) == 7


@pytest.mark.asyncio
async def test_write_helpers_refuse_without_allow_write(readonly_handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.delete_memory_backups([BACKUP_ID])
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.create_memory_backup(
            CreateBackupDto(dbInstanceId=INSTANCE_ID, name="b1")
        )


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_backups_marks_rows_as_summaries(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/backups").mock(
        return_value=httpx.Response(
            200, json=content_list([_backup(thin=True)], page_object(total_elements=4))
        )
    )

    result = await handler.list_memory_backups(1, 20)

    assert isinstance(result, MemoryBackupListData)
    assert result.count == 1
    assert result.rows_are_summaries is True
    row = result.items[0]
    assert row.id == BACKUP_ID
    assert row.instance_id == INSTANCE_ID, (
        "the project-wide listing DOES name the instance -- it is the per-instance listing "
        "that nulls it"
    )
    assert row.storage_size_gb is None
    assert row.ram_gb is None
    assert row.package_id == ""
    assert row.network_ids == []
    assert row.backup_duration_days is None
    assert row.datastore_type == "redis", "lowercased here, capitalised in the detail"
    assert row.is_restoring is False, "populated here, null in the detail"
    assert row.origin == "AUTO_DAILY"
    assert row.tier == "FREE"
    assert result.page.total_items == 4
    assert route.calls[0].request.url.params["pageNumber"] == "1"


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_backup_reverses_the_path_segments(handler):
    """Memory: /backups/{id}/detail. Relational: /backups/detail/{id}."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/backups/{BACKUP_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_backup()))
    )

    result = await handler.get_memory_backup(BACKUP_ID)

    assert isinstance(result, DatabaseBackup)
    assert result.id == BACKUP_ID
    assert result.storage_size_gb == 8
    assert result.ram_gb == 8
    assert result.datastore_type == "Redis"
    assert result.network_ids == ["net-372ddaf7-6933-4f86-b0f9-564db84a67d4"], (
        "net- ids, not the sub- ids a restore needs"
    )
    url = str(route.calls[0].request.url)
    assert url.endswith(f"/backups/{BACKUP_ID}/detail")
    assert "/backups/detail/" not in url


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_backup_raises_on_an_empty_payload(handler):
    """200 with nothing in it must not become a backup whose every field is ''."""
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/backups/{BACKUP_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )

    with pytest.raises(ValueError, match="does not exist at all"):
        await handler.get_memory_backup(BACKUP_ID)


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_backup_is_not_family_scoped(handler):
    """Measured live: this endpoint returns a MySQL backup without complaint.

    So the tool must not claim a result proves the backup is Redis. The
    docstring says to read `datastore_type`; this pins the behaviour the
    docstring describes.
    """
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/backups/{BACKUP_ID}/detail").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                _backup(datastoreType="MySQL", datastoreVersion="8.0", instanceName="app-db")
            ),
        )
    )

    result = await handler.get_memory_backup(BACKUP_ID)

    assert result.datastore_type == "MySQL", (
        "no error, no filtering -- the caller has to check the engine itself"
    )


@pytest.mark.asyncio
async def test_get_memory_backup_validates_the_id(handler):
    with pytest.raises(ValueError, match="Invalid backup_id"):
        await handler.get_memory_backup("../../etc/passwd")


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_free_backup_usage(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/backups/free-backup").mock(
        return_value=httpx.Response(
            200, json=envelope({"freeBackupStorage": 100, "backupUsage": 0.022000002})
        )
    )

    result = await handler.get_memory_free_backup_usage()

    assert isinstance(result, FreeBackupUsageData)
    assert result.free_backup_storage_gb == 100, "100 here, 150 for relational"
    assert result.backup_usage_gb == pytest.approx(0.022, abs=1e-3)


# --------------------------------------------------------------------------
# DTO validation
# --------------------------------------------------------------------------


def test_restore_dto_has_no_volume_fields():
    """A Redis flavour brings its disk; copying the relational spec must fail loudly."""
    for field, value in (("volumeType", "Gen2-NVMe2-IOPS3000"), ("volumeSize", 20)):
        with pytest.raises(pydantic.ValidationError, match=field):
            _restore_spec(**{field: value})


def test_restore_dto_has_no_backup_id():
    """The handler fills it from the path argument so the two cannot disagree."""
    with pytest.raises(pydantic.ValidationError, match="backupId"):
        _restore_spec(backupId=BACKUP_ID)


def test_restore_dto_enforces_the_memory_password_rule():
    with pytest.raises(pydantic.ValidationError, match="16-128"):
        _restore_spec(redisPassword="Short1_ok")
    with pytest.raises(pydantic.ValidationError, match="redisPassword is required"):
        _restore_spec(redisPassword=None)


def test_restore_dto_refuses_public_access_without_a_password():
    with pytest.raises(pydantic.ValidationError, match="publicAccess requires"):
        _restore_spec(publicAccess=True, redisPasswordEnabled=False, redisPassword=None)


def test_restore_dto_normalises_the_engine_spelling():
    assert _restore_spec(datastoreType="redis").datastoreType == "Redis"


def test_create_dto_is_shared_with_the_relational_family():
    """One upstream CreateBackupRequest, one DTO — the description default included."""
    spec = CreateBackupDto(dbInstanceId=INSTANCE_ID, name="pre-upgrade")
    assert spec.backupType == "FULL"
    assert spec.description, "defaults to the Portal's own wording; empty fails async"
    with pytest.raises(pydantic.ValidationError):
        CreateBackupDto(dbInstanceId=INSTANCE_ID, name="x", backupType="INCREMENTAL")


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_restore_dryrun_sends_nothing_and_uses_the_restore_constants(handler):
    mock_iam(respx.mock)

    result = await handler.restore_memory_backup_dryrun(BACKUP_ID, _restore_spec())

    assert isinstance(result, DryRunData)
    assert result.tool == "restore_memory_backup"
    assert result.path == f"/v1/backups/{BACKUP_ID}/restore"
    assert result.service == "vdb-memory"
    assert result.user_type == "IAM_USER"
    assert result.body["action"] == "restore_backup", "not 'restore', not the path segment"
    assert result.body["resourceType"] == "dbaas-backup", "not resType, not plain dbaas"
    assert "resType" not in result.body
    config = result.body["databaseInstances"][0]["config"]
    assert config["backupId"] == BACKUP_ID, "the id goes in the body as well as the path"
    assert config["redisPassword"] == PASSWORD
    assert "volumeSize" not in config
    assert any("BILLABLE" in w for w in result.warnings)
    assert any("not roll anything back" in w for w in result.warnings)
    assert not respx.mock.calls, "a dry run must not touch the API"


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_memory_backup_is_not_an_order_flow(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/backups/create").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                {
                    "errorMsg": None,
                    "code": 200,
                    "success": True,
                    "total": 1,
                    "backupId": "bk-new-0001",
                    "projectId": "pro-2b233cab",
                    "dbInstanceId": INSTANCE_ID,
                }
            ),
        )
    )

    result = await handler.create_memory_backup(
        CreateBackupDto(dbInstanceId=INSTANCE_ID, name="pre-upgrade")
    )

    assert isinstance(result, BackupCreateData)
    assert result.backup_id == "bk-new-0001"
    assert result.success is True
    assert "COMPLETED" in result.next_step, "the id exists before the data does"
    assert "user-type" not in route.calls[0].request.headers, "no order, no billing header"
    body = json.loads(route.calls[0].request.content)
    assert body["description"], "must not be empty -- an empty one fails asynchronously"


@respx.mock
@pytest.mark.asyncio
async def test_restore_memory_backup_orders_with_the_restore_envelope(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/backups/{BACKUP_ID}/restore").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    result = await handler.restore_memory_backup(BACKUP_ID, _restore_spec(), "IAM_USER")

    assert isinstance(result, OrderData)
    assert result.instance_name == "mcpmemrst"
    assert "untouched" in result.next_step, "a restore creates a second instance"
    assert route.calls[0].request.headers["user-type"] == "IAM_USER"
    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "restore_backup"
    assert body["resourceType"] == "dbaas-backup"
    assert body["databaseInstances"][0]["config"]["backupId"] == BACKUP_ID


@respx.mock
@pytest.mark.asyncio
async def test_delete_sends_a_json_array_and_no_path_id(handler):
    """The array body is the whole instruction here — every entry is deleted."""
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/backups/delete").mock(
        return_value=httpx.Response(200, json=_delete_response(_ok_row("bk-1"), _ok_row("bk-2")))
    )

    result = await handler.delete_memory_backups(["bk-1", "bk-2"])

    assert isinstance(result, BackupDeleteData)
    body = json.loads(route.calls[0].request.content)
    assert body == [{"backupId": "bk-1"}, {"backupId": "bk-2"}]
    assert str(route.calls[0].request.url).endswith("/backups/delete"), "no id in the path"
    assert result.accepted is True
    assert result.backup_ids == ["bk-1", "bk-2"], "all of them, not just the first"
    assert len(result.results) == 2


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_a_success_false_row_as_not_deleted(handler):
    """HTTP 200 can carry a 404 in the body."""
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/backups/delete").mock(
        return_value=httpx.Response(
            200,
            json=_delete_response(
                _ok_row("bk-1"),
                {
                    "backupId": "bk-missing",
                    "action": "delete",
                    "status": None,
                    "success": False,
                    "errorMsg": "Resource not found",
                    "code": 404,
                },
            ),
        )
    )

    result = await handler.delete_memory_backups(["bk-1", "bk-missing"])

    assert result.accepted is False
    assert result.warning and "did NOT happen" in result.warning
    assert "bk-missing" in result.warning, "say which id failed, not just that one did"
    assert "Resource not found" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_an_empty_response_as_not_deleted(handler):
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/backups/delete").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )

    result = await handler.delete_memory_backups(["bk-1"])

    assert result.accepted is False
    assert result.warning and "NOT deleted" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_a_short_result_list_as_not_deleted(handler):
    """Two ids in, one result out: the response does not say which was skipped."""
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/backups/delete").mock(
        return_value=httpx.Response(200, json=_delete_response(_ok_row("bk-1")))
    )

    result = await handler.delete_memory_backups(["bk-1", "bk-2"])

    assert result.accepted is False
    assert result.warning and "fewer results" in result.warning
    assert "Asked for 2, got 1" in result.warning


@pytest.mark.asyncio
async def test_delete_validates_every_id_not_just_the_first(handler):
    with pytest.raises(ValueError, match="Invalid backup_ids"):
        await handler.delete_memory_backups(["bk-1", "../../etc/passwd"])


@pytest.mark.asyncio
async def test_delete_rejects_an_empty_list(handler):
    """An empty array would be a destructive call that identifies nothing.

    `min_length=1` on the Field only constrains the MCP input schema, so the
    handler checks again -- a direct call must not reach the wire either.
    """
    with pytest.raises(ValueError, match="at least one backup"):
        await handler.delete_memory_backups([])


# --------------------------------------------------------------------------
# family separation
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_every_call_goes_to_the_memory_service(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/backups/{BACKUP_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_backup()))
    )

    await handler.get_memory_backup(BACKUP_ID)

    urls = [str(c.request.url) for c in respx.mock.calls if "vdb-" in str(c.request.url)]
    assert urls and all("/vdb-memory/" in u for u in urls)
