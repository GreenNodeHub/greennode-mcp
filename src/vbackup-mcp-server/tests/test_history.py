"""Backup and restore history."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import API_BASE, RAW_DB_HISTORY, RAW_DB_RESTORE, RAW_HISTORY, envelope, mock_iam
from greennode.vbackup_mcp_server.config import REGIONS, VSERVER_SERVICE
from greennode.vbackup_mcp_server.history_handler import HistoryHandler, to_epoch_millis
from greennode.vbackup_mcp_server.models import BackupHistoryItem, count_failures
from mcp.server.mcpserver import MCPServer


@pytest.fixture
def handler(config, client):
    return HistoryHandler(MCPServer("test"), config, client)


@pytest.mark.asyncio
async def test_tools_registered_read_only(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    assert tools["list_backup_history"].annotations.read_only_hint is True
    assert tools["list_restore_history"].annotations.read_only_hint is True


@respx.mock
@pytest.mark.asyncio
async def test_history_parses_json_string_snapshots(handler):
    """policySnapshot and destinationSnapshot arrive as escaped JSON STRINGS."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/histories/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([RAW_HISTORY]))
    )
    result = await handler.list_backup_history(
        region="HCM-3", backup_server_id=None, server_id=None, from_date=None, limit=50
    )
    run = result.runs[0]
    assert run.policy_name_at_run == "nightly-as-it-was"
    assert run.destination_name_at_run == "vault-as-it-was"
    assert run.size_gb == 20.0


@respx.mock
@pytest.mark.asyncio
async def test_history_filters_forwarded(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/histories/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([RAW_HISTORY]))
    )
    await handler.list_backup_history(
        region="HCM-3",
        backup_server_id="bk-ins-0001",
        server_id="ins-0001",
        from_date=None,
        limit=50,
    )
    params = route.calls[0].request.url.params
    assert params["backupInstanceId"] == "bk-ins-0001"
    assert params["serverId"] == "ins-0001"


@respx.mock
@pytest.mark.asyncio
async def test_history_respects_the_limit(handler):
    """The full history runs to thousands of records; the tool must not dump them."""
    mock_iam(respx.mock)
    many = [{**RAW_HISTORY, "id": f"bk-ins-pt-{i:04d}"} for i in range(120)]
    respx.get(f"{API_BASE}/v1/histories/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope(many))
    )
    result = await handler.list_backup_history(
        region="HCM-3", backup_server_id=None, server_id=None, from_date=None, limit=10
    )
    assert result.total == 10
    assert len(result.runs) == 10


@respx.mock
@pytest.mark.asyncio
async def test_failed_run_carries_its_error(handler):
    mock_iam(respx.mock)
    failed = {**RAW_HISTORY, "status": "FAILED", "errorMessage": "vault quota exceeded"}
    respx.get(f"{API_BASE}/v1/histories/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([failed]))
    )
    result = await handler.list_backup_history(
        region="HCM-3", backup_server_id=None, server_id=None, from_date=None, limit=50
    )
    assert result.runs[0].error_message == "vault quota exceeded"
    assert count_failures(result.runs) == 1


def test_successful_run_has_no_error_message():
    assert BackupHistoryItem.from_api(RAW_HISTORY).error_message == ""


@respx.mock
@pytest.mark.asyncio
async def test_restore_history_empty_is_normal(handler):
    """No restore ever run is the normal state for an account without an incident."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/histories/restoration").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_restore_history(
        region="HCM-3", backup_server_id=None, server_id=None, limit=50
    )
    assert result.total == 0
    assert result.restores == []


@respx.mock
@pytest.mark.asyncio
async def test_restore_history_structured(handler):
    mock_iam(respx.mock)
    raw = {
        "id": "rst-0001",
        "type": "SERVER",
        "status": "SUCCESS",
        "backupInstanceId": "bk-ins-0001",
        "backupInstancePointId": "bk-ins-pt-0001",
        "destinationServerId": "ins-0002",
        "createdAt": "2026-06-01T00:00:00.000+00:00",
        "finishAt": "2026-06-01T00:20:00.000+00:00",
    }
    respx.get(f"{API_BASE}/v1/histories/restoration").mock(
        return_value=httpx.Response(200, json=envelope([raw]))
    )
    result = await handler.list_restore_history(
        region="HCM-3", backup_server_id=None, server_id=None, limit=50
    )
    restore = result.restores[0]
    assert restore.backup_server_point_id == "bk-ins-pt-0001"
    assert restore.destination_server_id == "ins-0002"


@pytest.mark.asyncio
async def test_malformed_filter_rejected(handler):
    with pytest.raises(ValueError, match="backup_server_id"):
        await handler.list_backup_history(
            region="HCM-3", backup_server_id="../etc", server_id=None, from_date=None, limit=50
        )


@respx.mock
@pytest.mark.asyncio
async def test_from_date_is_sent_as_epoch_millis_under_its_snake_case_name(handler):
    """`fromDate` is silently ignored by the gateway; only `from_date` filters."""
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/histories/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([RAW_HISTORY]))
    )
    await handler.list_backup_history(
        region="HCM-3",
        backup_server_id=None,
        server_id=None,
        from_date="2026-03-01",
        limit=50,
    )
    params = route.calls[0].request.url.params
    assert "fromDate" not in params
    assert params["from_date"] == str(to_epoch_millis("2026-03-01", "x"))


def test_from_date_accepts_iso_forms_and_rejects_prose():
    assert to_epoch_millis("1970-01-01", "x") == 0
    naive = to_epoch_millis("2026-03-01", "x")
    explicit_utc = to_epoch_millis("2026-03-01T00:00:00Z", "x")
    assert naive == explicit_utc
    with pytest.raises(ValueError, match="ISO-8601"):
        to_epoch_millis("last week", "from_date")


@respx.mock
@pytest.mark.asyncio
async def test_database_backup_history_reports_both_sizes(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/histories/backup-databases").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DB_HISTORY]))
    )
    result = await handler.list_database_backup_history(
        region="HCM-3",
        database_id=None,
        backup_database_id=None,
        from_date=None,
        limit=50,
    )
    run = result.runs[0]
    assert run.compressed_gb == 2.0
    assert run.uncompressed_gb == 10.0
    assert run.database_id == "pg-0001"
    assert run.policy_name_at_run == "db-nightly-as-it-was"


@respx.mock
@pytest.mark.asyncio
async def test_database_history_uses_its_own_endpoints(handler):
    """A vDB run never appears in the vServer trail, and vice versa."""
    mock_iam(respx.mock)
    backup = respx.get(f"{API_BASE}/v1/histories/backup-databases").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DB_HISTORY]))
    )
    restore = respx.get(f"{API_BASE}/v1/histories/restoration/databases").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DB_RESTORE]))
    )
    await handler.list_database_backup_history(
        region="HCM-3", database_id="pg-0001", backup_database_id=None, from_date=None, limit=50
    )
    result = await handler.list_database_restore_history(
        region="HCM-3", backup_database_id=None, limit=50
    )
    assert backup.calls[0].request.url.params["databaseId"] == "pg-0001"
    assert restore.called
    assert result.restores[0].destination_database_id == "pg-0002"
    assert result.restores[0].backup_database_point_id == "bk-db-pt-0001"


@respx.mock
@pytest.mark.asyncio
async def test_limit_keeps_the_newest_runs_not_an_arbitrary_slice(handler):
    """The API returns history unordered, so the cap must sort before slicing.

    Slicing an unordered response keeps an arbitrary subset while presenting it
    as the newest runs.
    """
    mock_iam(respx.mock)
    scrambled = [
        {**RAW_HISTORY, "id": "h-old", "snapshotTime": "2026-02-20T01:00:14.000+00:00"},
        {**RAW_HISTORY, "id": "h-new", "snapshotTime": "2026-08-18T09:00:05.000+00:00"},
        {**RAW_HISTORY, "id": "h-mid", "snapshotTime": "2026-06-24T01:00:29.000+00:00"},
    ]
    respx.get(f"{API_BASE}/v1/histories/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope(scrambled))
    )
    result = await handler.list_backup_history(
        region="HCM-3", backup_server_id=None, server_id=None, from_date=None, limit=2
    )
    assert [r.id for r in result.runs] == ["h-new", "h-mid"]


@respx.mock
@pytest.mark.asyncio
async def test_a_record_with_no_timestamp_sinks_instead_of_raising(handler):
    mock_iam(respx.mock)
    rows = [
        {
            **RAW_HISTORY,
            "id": "h-blank",
            "snapshotTime": None,
            "finishTime": None,
            "createdAt": None,
        },
        {**RAW_HISTORY, "id": "h-real", "snapshotTime": "2026-08-18T09:00:05.000+00:00"},
    ]
    respx.get(f"{API_BASE}/v1/histories/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope(rows))
    )
    result = await handler.list_backup_history(
        region="HCM-3", backup_server_id=None, server_id=None, from_date=None, limit=50
    )
    assert [r.id for r in result.runs] == ["h-real", "h-blank"]


VSERVER_BASE = REGIONS["HCM-3"][VSERVER_SERVICE]
MIGRATION_PROJECT = "pro-0001"
MIGRATION_URL = f"{VSERVER_BASE}/v1/{MIGRATION_PROJECT}/histories/server-migration"


def migration_row(**overrides) -> dict:
    """One migration record in the shape the vServer gateway returns."""
    return {
        "id": "server-migration-his-0001",
        "projectId": MIGRATION_PROJECT,
        "serverId": "ins-0001",
        "serverName": "web-01",
        "action": "START-MIGRATING",
        "status": "START-MIGRATING-SUCCESS",
        "createdAt": "2026-03-10T10:55:58.000+07:00",
        "updatedAt": "2026-03-10T10:56:31.000+07:00",
        **overrides,
    }


def migration_envelope(rows: list[dict], total: int | None = None, total_page: int = 1) -> dict:
    """The vServer migration envelope: `listData` plus SINGULAR counters."""
    return {
        "listData": rows,
        "page": 1,
        "pageSize": 100,
        "totalPage": total_page,
        "totalItem": total if total is not None else len(rows),
    }


@respx.mock
@pytest.mark.asyncio
async def test_migration_reads_the_listdata_envelope(handler):
    """Neither `items` nor `data` — this trail answers under `listData`."""
    mock_iam(respx.mock)
    respx.get(MIGRATION_URL).mock(
        return_value=httpx.Response(200, json=migration_envelope([migration_row()]))
    )
    result = await handler.list_server_migration_history(
        project_id=MIGRATION_PROJECT,
        region="HCM-3",
        server_id=None,
        status=None,
        action=None,
        limit=50,
    )
    assert result.total == 1
    step = result.migrations[0]
    assert step.server_id == "ins-0001"
    assert step.server_name == "web-01"
    assert step.action == "START-MIGRATING"


@respx.mock
@pytest.mark.asyncio
async def test_migration_always_sends_page_and_size(handler):
    """Omitting either answers 500, so there is no unpaged fast path here."""
    mock_iam(respx.mock)
    route = respx.get(MIGRATION_URL).mock(
        return_value=httpx.Response(200, json=migration_envelope([migration_row()]))
    )
    await handler.list_server_migration_history(
        project_id=MIGRATION_PROJECT,
        region="HCM-3",
        server_id=None,
        status=None,
        action=None,
        limit=50,
    )
    params = route.calls[0].request.url.params
    assert params["page"] == "1"
    assert params["size"]


@respx.mock
@pytest.mark.asyncio
async def test_migration_walks_every_page(handler):
    """`totalPage` drives the walk; the rows arrive one page at a time."""
    mock_iam(respx.mock)
    page_one = migration_envelope(
        [migration_row(id=f"server-migration-his-{i:04d}") for i in range(100)],
        total=138,
        total_page=2,
    )
    page_two = migration_envelope(
        [migration_row(id=f"server-migration-his-{i:04d}") for i in range(100, 138)],
        total=138,
        total_page=2,
    )
    respx.get(MIGRATION_URL).mock(
        side_effect=[httpx.Response(200, json=page_one), httpx.Response(200, json=page_two)]
    )
    result = await handler.list_server_migration_history(
        project_id=MIGRATION_PROJECT,
        region="HCM-3",
        server_id=None,
        status=None,
        action=None,
        limit=500,
    )
    assert result.total == 138
    assert result.total_available == 138


@respx.mock
@pytest.mark.asyncio
async def test_rollback_is_distinguishable_only_by_action(handler):
    """A rollback reports the same status as a confirmed migration."""
    mock_iam(respx.mock)
    rows = [
        migration_row(
            id="server-migration-his-0002",
            action="ROLLBACK",
            status="COMPLETE-MIGRATING-SUCCESS",
            createdAt="2026-03-11T10:00:00.000+07:00",
        ),
        migration_row(
            id="server-migration-his-0003",
            action="COMPLETE-MIGRATING",
            status="COMPLETE-MIGRATING-SUCCESS",
            createdAt="2026-03-09T10:00:00.000+07:00",
        ),
    ]
    respx.get(MIGRATION_URL).mock(return_value=httpx.Response(200, json=migration_envelope(rows)))
    result = await handler.list_server_migration_history(
        project_id=MIGRATION_PROJECT,
        region="HCM-3",
        server_id=None,
        status=None,
        action=None,
        limit=50,
    )
    assert {s.status for s in result.migrations} == {"COMPLETE-MIGRATING-SUCCESS"}
    assert [s.action for s in result.migrations] == ["ROLLBACK", "COMPLETE-MIGRATING"]


@respx.mock
@pytest.mark.asyncio
async def test_action_filter_is_applied_here_not_by_the_api(handler):
    """The API accepts `action` and ignores it, so this server filters the rows."""
    mock_iam(respx.mock)
    rows = [
        migration_row(id="server-migration-his-0004", action="START-MIGRATING"),
        migration_row(
            id="server-migration-his-0005",
            action="ROLLBACK",
            status="COMPLETE-MIGRATING-SUCCESS",
        ),
    ]
    route = respx.get(MIGRATION_URL).mock(
        return_value=httpx.Response(200, json=migration_envelope(rows))
    )
    result = await handler.list_server_migration_history(
        project_id=MIGRATION_PROJECT,
        region="HCM-3",
        server_id=None,
        status=None,
        action="ROLLBACK",
        limit=50,
    )
    assert "action" not in route.calls[0].request.url.params
    assert [s.action for s in result.migrations] == ["ROLLBACK"]
    assert result.total_available == 1


@respx.mock
@pytest.mark.asyncio
async def test_migration_server_and_status_filters_go_to_the_api(handler):
    mock_iam(respx.mock)
    route = respx.get(MIGRATION_URL).mock(
        return_value=httpx.Response(200, json=migration_envelope([migration_row()]))
    )
    await handler.list_server_migration_history(
        project_id=MIGRATION_PROJECT,
        region="HCM-3",
        server_id="ins-0001",
        status="START-MIGRATING-SUCCESS",
        action=None,
        limit=50,
    )
    params = route.calls[0].request.url.params
    assert params["serverId"] == "ins-0001"
    assert params["status"] == "START-MIGRATING-SUCCESS"


@respx.mock
@pytest.mark.asyncio
async def test_migration_limit_keeps_the_newest_and_reports_the_total(handler):
    mock_iam(respx.mock)
    rows = [
        migration_row(id="server-migration-his-old", createdAt="2025-01-01T00:00:00.000+07:00"),
        migration_row(id="server-migration-his-new", createdAt="2026-08-01T00:00:00.000+07:00"),
        migration_row(id="server-migration-his-mid", createdAt="2026-01-01T00:00:00.000+07:00"),
    ]
    respx.get(MIGRATION_URL).mock(return_value=httpx.Response(200, json=migration_envelope(rows)))
    result = await handler.list_server_migration_history(
        project_id=MIGRATION_PROJECT,
        region="HCM-3",
        server_id=None,
        status=None,
        action=None,
        limit=1,
    )
    assert [s.id for s in result.migrations] == ["server-migration-his-new"]
    assert result.total == 1
    assert result.total_available == 3


@pytest.mark.asyncio
async def test_migration_rejects_path_traversal_in_project_id(handler):
    with pytest.raises(ValueError):
        await handler.list_server_migration_history(
            project_id="../../v1/backends",
            region="HCM-3",
            server_id=None,
            status=None,
            action=None,
            limit=50,
        )
