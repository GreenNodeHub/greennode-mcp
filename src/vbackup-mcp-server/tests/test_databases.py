"""Backup databases — the vDB half of vBackup.

The shapes here mirror live payloads: a nested policy the destination
projection nulls out, two sizes per restore point, and the vDB gateway's own
doubly-nested envelope.
"""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import (
    API_BASE,
    BACKUP_DB_ID,
    DATABASE_ID,
    GIB,
    RAW_BACKUP_DATABASE,
    RAW_BACKUP_DATABASE_POINT,
    RAW_VDB_POSTGRES_CLUSTER,
    RAW_VDB_POSTGRES_SINGLE,
    RAW_VDB_REDIS,
    VDB_BASE,
    VDB_RELATIONAL_BASE,
    envelope,
    mock_iam,
    vdb_envelope,
)
from greennode.vbackup_mcp_server.database_handler import DatabaseHandler
from greennode.vbackup_mcp_server.models import (
    CreateBackupDatabaseDto,
    UpdateBackupDatabasePolicyDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


@pytest.fixture
def handler(config, client, no_cache):
    return DatabaseHandler(MCPServer("test"), config, client, no_cache, allow_write=True)


@pytest.fixture
def read_only(config, client, no_cache):
    return DatabaseHandler(MCPServer("test"), config, client, no_cache, allow_write=False)


@respx.mock
@pytest.mark.asyncio
async def test_list_reads_engine_and_both_size_units(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-databases").mock(
        return_value=httpx.Response(200, json=envelope([RAW_BACKUP_DATABASE]))
    )
    result = await handler.list_backup_databases(region="HCM-3", name=None, refresh=False)
    item = result.databases[0]
    assert item.id == BACKUP_DB_ID
    assert item.database_id == DATABASE_ID
    assert item.engine == "Redis"
    assert item.engine_version == "v7.2.13"
    assert item.total_backup_size_bytes == 2 * GIB
    assert item.total_backup_size_gb == 2.0
    assert item.free_usage_gb == 50
    assert item.policy.id == "bk-pol-0001"
    assert item.destination.name == "vdb-location"


@respx.mock
@pytest.mark.asyncio
async def test_nulled_policy_and_destination_do_not_break_the_item(handler):
    """The destination sub-resource returns the same item with both refs null."""
    mock_iam(respx.mock)
    projected = {**RAW_BACKUP_DATABASE, "policy": None, "backupDestination": None}
    respx.get(f"{API_BASE}/v1/backup-databases").mock(
        return_value=httpx.Response(200, json=envelope([projected]))
    )
    result = await handler.list_backup_databases(region="HCM-3", name=None, refresh=False)
    item = result.databases[0]
    assert item.policy.id == ""
    assert item.destination.id == ""
    assert item.backup_policy_id == "bk-pol-0001"


@respx.mock
@pytest.mark.asyncio
async def test_points_report_stored_and_uncompressed_separately(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-databases/{BACKUP_DB_ID}/backup-database-points").mock(
        return_value=httpx.Response(200, json=envelope([RAW_BACKUP_DATABASE_POINT]))
    )
    result = await handler.list_backup_database_points(
        backup_database_id=BACKUP_DB_ID, region="HCM-3"
    )
    point = result.points[0]
    assert point.id == "bk-db-pt-0001"
    assert point.size_gb == 1.0
    assert point.uncompressed_size_gb == 3.0
    assert point.backup_type_at_run == "MANUAL_FULL"
    assert point.restoring is False


@respx.mock
@pytest.mark.asyncio
async def test_protected_databases_reads_the_ids_key(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/protected-resources/databases").mock(
        return_value=httpx.Response(200, json={"ids": [DATABASE_ID]})
    )
    result = await handler.list_protected_databases(
        database_type="RedisCluster", region="HCM-3", refresh=False
    )
    assert result.database_ids == [DATABASE_ID]
    assert route.calls[0].request.url.params["databaseType"] == "RedisCluster"


@respx.mock
@pytest.mark.asyncio
async def test_list_databases_marks_the_protected_instance_ineligible(handler):
    """The vDB estate joined with vBackup's protection list."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/protected-resources/databases").mock(
        return_value=httpx.Response(200, json={"ids": [DATABASE_ID]})
    )
    respx.get(f"{VDB_BASE}/v1/database-instances").mock(
        return_value=httpx.Response(200, json=vdb_envelope([RAW_VDB_REDIS]))
    )
    result = await handler.list_databases(
        database_type="RedisCluster", region="HCM-3", eligible_only=False, refresh=False
    )
    assert result.project_id == "pro-0001"
    assert result.eligible_total == 0
    item = result.databases[0]
    assert item.already_protected is True
    assert item.eligible is False
    assert "Already has a backup database" in item.ineligible_reason


@respx.mock
@pytest.mark.asyncio
async def test_single_node_postgres_is_ineligible_and_says_why(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/protected-resources/databases").mock(
        return_value=httpx.Response(200, json={"ids": []})
    )
    respx.get(f"{VDB_RELATIONAL_BASE}/v1/database-instances").mock(
        return_value=httpx.Response(
            200, json=vdb_envelope([RAW_VDB_POSTGRES_SINGLE, RAW_VDB_POSTGRES_CLUSTER])
        )
    )
    result = await handler.list_databases(
        database_type="PostgresCluster", region="HCM-3", eligible_only=False, refresh=False
    )
    assert result.eligible_total == 1
    assert result.databases[0].id == "pg-0001"
    single = next(d for d in result.databases if d.id == "pg-0002")
    assert single.eligible is False
    assert "not a cluster" in single.ineligible_reason


@respx.mock
@pytest.mark.asyncio
async def test_eligible_only_hides_the_rejected_instances(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/protected-resources/databases").mock(
        return_value=httpx.Response(200, json={"ids": []})
    )
    respx.get(f"{VDB_RELATIONAL_BASE}/v1/database-instances").mock(
        return_value=httpx.Response(
            200, json=vdb_envelope([RAW_VDB_POSTGRES_SINGLE, RAW_VDB_POSTGRES_CLUSTER])
        )
    )
    result = await handler.list_databases(
        database_type="PostgresCluster", region="HCM-3", eligible_only=True, refresh=False
    )
    assert [d.id for d in result.databases] == ["pg-0001"]
    assert result.total == 2


@respx.mock
@pytest.mark.asyncio
async def test_create_sends_a_flat_database_id(handler):
    """Not databaseIds, not a nested databaseConfig — the API rejects both."""
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/backup-databases").mock(
        return_value=httpx.Response(201, json={})
    )
    body = CreateBackupDatabaseDto(
        databaseId=DATABASE_ID,
        databaseType="RedisCluster",
        backupPolicyId="bk-pol-0001",
        backupDestinationId="bk-des-0001",
        description="nightly",
    )
    result = await handler.create_backup_database(body=body, region="HCM-3")
    sent = json.loads(route.calls[0].request.read())
    assert sent["databaseId"] == DATABASE_ID
    assert "databaseIds" not in sent and "databaseConfig" not in sent
    assert sent["databaseType"] == "RedisCluster"
    assert sent["backupEnabled"] is True
    assert result.action == "created"


def test_create_rejects_an_engine_name_as_database_type():
    """PostgreSQL/Redis are engine names; the API takes the cluster spellings."""
    with pytest.raises(ValidationError):
        CreateBackupDatabaseDto(
            databaseId=DATABASE_ID,
            databaseType="PostgreSQL",
            backupPolicyId="bk-pol-0001",
            backupDestinationId="bk-des-0001",
        )


def test_create_forbids_an_unknown_field():
    with pytest.raises(ValidationError):
        CreateBackupDatabaseDto(
            databaseId=DATABASE_ID,
            databaseType="RedisCluster",
            backupPolicyId="bk-pol-0001",
            backupDestinationId="bk-des-0001",
            backendId="be-0001",
        )


@respx.mock
@pytest.mark.asyncio
async def test_start_backup_posts_to_backup_now(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/backup-databases/{BACKUP_DB_ID}/backup-now").mock(
        return_value=httpx.Response(200, json={})
    )
    result = await handler.start_database_backup(backup_database_id=BACKUP_DB_ID, region="HCM-3")
    assert route.called
    assert result.action == "backup started"
    assert "not completed" in result.detail


@respx.mock
@pytest.mark.asyncio
async def test_policy_enable_and_disable_use_their_own_subpaths(handler):
    mock_iam(respx.mock)
    policy = respx.put(f"{API_BASE}/v1/backup-databases/{BACKUP_DB_ID}/policies").mock(
        return_value=httpx.Response(200, json={})
    )
    enabled = respx.put(f"{API_BASE}/v1/backup-databases/{BACKUP_DB_ID}/enabled").mock(
        return_value=httpx.Response(200, json={})
    )
    disabled = respx.put(f"{API_BASE}/v1/backup-databases/{BACKUP_DB_ID}/disabled").mock(
        return_value=httpx.Response(200, json={})
    )
    await handler.update_backup_database_policy(
        backup_database_id=BACKUP_DB_ID,
        body=UpdateBackupDatabasePolicyDto(id="bk-pol-0002"),
        region="HCM-3",
    )
    await handler.enable_backup_database(backup_database_id=BACKUP_DB_ID, region="HCM-3")
    result = await handler.disable_backup_database(backup_database_id=BACKUP_DB_ID, region="HCM-3")
    assert policy.called and enabled.called and disabled.called
    assert json.loads(policy.calls[0].request.read()) == {"id": "bk-pol-0002"}
    assert "still" in result.detail


@respx.mock
@pytest.mark.asyncio
async def test_point_delete_uses_the_point_collection_not_the_database(handler):
    """The route carries only the point id — the backup database is not in it."""
    mock_iam(respx.mock)
    route = respx.delete(f"{API_BASE}/v1/backup-database-points/bk-db-pt-0001").mock(
        return_value=httpx.Response(204)
    )
    result = await handler.delete_backup_database_point(point_id="bk-db-pt-0001", region="HCM-3")
    assert route.called
    assert result.action == "deleted"


@respx.mock
@pytest.mark.asyncio
async def test_delete_removes_the_backup_database(handler):
    mock_iam(respx.mock)
    route = respx.delete(f"{API_BASE}/v1/backup-databases/{BACKUP_DB_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = await handler.delete_backup_database(backup_database_id=BACKUP_DB_ID, region="HCM-3")
    assert route.called
    assert "vDB instance is unaffected" in result.detail


@pytest.mark.asyncio
async def test_writes_are_refused_in_read_only_mode(read_only):
    with pytest.raises(ValueError, match="--allow-write"):
        await read_only.delete_backup_database(backup_database_id=BACKUP_DB_ID, region="HCM-3")


@pytest.mark.asyncio
async def test_path_traversal_is_rejected(handler):
    with pytest.raises(ValueError):
        await handler.get_backup_database(backup_database_id="../../v1/backends", region="HCM-3")


@respx.mock
@pytest.mark.asyncio
async def test_an_occupied_destination_is_refused(handler):
    """A vDB destination holds at most one backup database."""
    mock_iam(respx.mock)
    respx.post(f"{API_BASE}/v1/backup-databases").mock(
        return_value=httpx.Response(
            400, json={"message": "The backup destination already contains resources."}
        )
    )
    body = CreateBackupDatabaseDto(
        databaseId=DATABASE_ID,
        databaseType="RedisCluster",
        backupPolicyId="bk-pol-0001",
        backupDestinationId="bk-des-0001",
    )
    with pytest.raises(RuntimeError, match="already contains resources"):
        await handler.create_backup_database(body=body, region="HCM-3")


@respx.mock
@pytest.mark.asyncio
async def test_vault_lock_conflict_surfaces_its_own_message(handler):
    """The vault-lock 409 must stay distinguishable from the retryable one."""
    mock_iam(respx.mock)
    respx.delete(f"{API_BASE}/v1/backup-database-points/bk-db-pt-0001").mock(
        return_value=httpx.Response(
            409, json={"message": "Your resource is being managed by Vault."}
        )
    )
    with pytest.raises(RuntimeError, match="managed by Vault"):
        await handler.delete_backup_database_point(point_id="bk-db-pt-0001", region="HCM-3")
