"""Tests for the PostgreSQL Cluster backup handler.

Fixtures mirror the shapes probed live on 2026-09-14. The vocabulary is the
thing these tests defend: a "backup" here is a cluster's standing backup
*configuration* (`bk-db-...`), a restore point (`bk-db-pt-...`) is the actual
copy, and every per-cluster endpoint -- including the one spelled `/detail` --
is keyed by the cluster id, never by a backup id.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.mcp_core.auth import TokenManager
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import load_config
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.models import (
    BackupLocationListData,
    BackupPolicyListData,
    PostgresqlBackup,
    PostgresqlBackupListData,
    PostgresqlBackupNowData,
    PostgresqlRestorePointListData,
)
from greennode.vdb_mcp_server.postgresql_backup_handler import PostgresqlBackupHandler
from mcp.server.mcpserver import MCPServer
from tests.helpers import POSTGRESQL, envelope, mock_iam


CLUSTER_ID = "pg-82777ff7-a1be-4f61-bd07-6d1f6fc3da80"
BACKUP_ID = "bk-db-9d345209-8042-4d50-a418-eac15906bbe3"
POINT_ID = "bk-db-pt-91abebf5-f607-4c62-bade-15591281a5e0"
LOCATION_ID = "bk-des-2204bc65-5789-4e05-8da0-1149ee0c7a98"
POLICY_ID = "bk-pol-aec0a843-ee9d-4bc2-ae4c-7e48435cd23f"


def _backup(cluster_id=CLUSTER_ID, deleted=False):
    return {
        "id": BACKUP_ID,
        "userId": 54549,
        "name": "database-or7221hk-90-d1077470",
        "databaseId": cluster_id,
        "description": "Created by vDB.",
        "status": "ACTIVE",
        "backupEnabled": True,
        "latestRecord": "2026-09-13T18:00:09.000+00:00",
        "createdAt": "2026-09-08T05:54:20.000+00:00",
        "updatedAt": "2026-09-14T02:50:02.000+00:00",
        "backupPolicyId": POLICY_ID,
        "backupDestinationId": LOCATION_ID,
        # Both arrive twice: nested with a name, flat as null.
        "backupDestination": {"id": LOCATION_ID, "name": "bk-location-db8b438380e6"},
        "policy": {"id": POLICY_ID, "name": "vDB-test"},
        "backupPolicyName": None,
        "backupDestinationName": None,
        "databaseDeleted": deleted,
        "totalBackupSize": 5861990,
    }


def _restore_point():
    return {
        "id": POINT_ID,
        "backupDatabaseId": BACKUP_ID,
        "databaseId": CLUSTER_ID,
        "backupName": "base_000000010000000100000012",
        "status": "ACTIVE",
        # Both of these are JSON *encoded as a string* inside the JSON body.
        "destinationSnapshot": '{"id":"bk-des-2204","name":"bk-location"}',
        "policySnapshot": '{"id":"bk-pol-aec0","modes":["daily"]}',
        "compressedSize": 5861990,
        "uncompressedSize": 31677313,
        "createdAt": "2026-09-13T18:00:09.000+00:00",
        "updatedAt": "2026-09-13T18:00:25.000+00:00",
        "time": "2026-09-13T18:00:09.000+00:00",
        "engineVersion": "17",
    }


def _location(is_default=False):
    return {
        "id": LOCATION_ID,
        "name": "bk-location-db8b438380e6",
        "product": "vDB",
        "status": "ACTIVE",
        "isDefault": is_default,
        "type": "VAULT",
        "maxQuota": {"unlimited": True, "maxQuota": 0},
        "config": {
            "vault": {"used": 5861990, "total": 0, "regionName": "HCM04"},
            "vstorage": None,
        },
        "numberOfBackupInstances": 1,
    }


def _policy():
    return {
        "id": POLICY_ID,
        "name": "vDB-test",
        "isDefault": True,
        "config": {
            "hour": 1,
            "minute": 0,
            "timeZone": "Asia/Ho_Chi_Minh",
            "hourlyEnabled": False,
            # A disabled frequency still carries an (empty) config object.
            "hourlyConfig": {},
            "dailyEnabled": True,
            "dailyConfig": {"retention": 1, "backupType": "FULL", "incrementalQuantity": 0},
            "weeklyEnabled": False,
            "weeklyConfig": {},
            "monthlyEnabled": False,
            "monthlyConfig": {},
        },
        "backupInstanceCount": 4,
    }


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return PostgresqlBackupHandler(
        MCPServer("test"), config, client, DiscoveryCache(), allow_write=allow_write
    )


@pytest.fixture
def handler(sample_config):
    return _handler(sample_config, allow_write=True)


@pytest.fixture
def readonly_handler(sample_config):
    return _handler(sample_config, allow_write=False)


# --------------------------------------------------------------------------
# backup configurations
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_listing_returns_backup_configurations_keyed_by_cluster(handler):
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/backup-vdb").mock(
        return_value=httpx.Response(200, json=envelope([_backup(), _backup("pg-other")]))
    )
    result = await handler.list_postgresql_backups()
    assert isinstance(result, PostgresqlBackupListData)
    assert result.count == 2
    assert {b.cluster_id for b in result.items} == {CLUSTER_ID, "pg-other"}


@pytest.mark.asyncio
@respx.mock
async def test_a_backup_row_reads_its_names_from_the_nested_objects(handler):
    """The flat `backupPolicyName` is null while the nested `policy.name` is set."""
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/backup-vdb").mock(
        return_value=httpx.Response(200, json=envelope([_backup()]))
    )
    row = (await handler.list_postgresql_backups()).items[0]
    assert row.policy_name == "vDB-test"
    assert row.location_name == "bk-location-db8b438380e6"


@pytest.mark.asyncio
@respx.mock
async def test_a_backup_of_a_deleted_cluster_is_flagged(handler):
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/backup-vdb").mock(
        return_value=httpx.Response(200, json=envelope([_backup(deleted=True)]))
    )
    assert (await handler.list_postgresql_backups()).items[0].cluster_deleted is True


@pytest.mark.asyncio
@respx.mock
async def test_the_detail_endpoint_is_keyed_by_cluster_id_not_backup_id(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{POSTGRESQL}/v1/backup/backup-vdb/{CLUSTER_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_backup()))
    )
    result = await handler.get_postgresql_cluster_backup(CLUSTER_ID)
    assert route.called
    assert isinstance(result, PostgresqlBackup)
    # What comes back is a backup id, from a cluster id -- the point of the test.
    assert result.id == BACKUP_ID
    assert result.cluster_id == CLUSTER_ID


@pytest.mark.asyncio
async def test_the_detail_endpoint_rejects_a_traversing_id(handler):
    with pytest.raises(ValueError):
        await handler.get_postgresql_cluster_backup("../../etc/passwd")


# --------------------------------------------------------------------------
# restore points
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_restore_points_carry_the_engine_version_a_restore_must_match(handler):
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/backup-vdb/{CLUSTER_ID}/restore-point").mock(
        return_value=httpx.Response(200, json=envelope([_restore_point()]))
    )
    result = await handler.list_postgresql_restore_points(CLUSTER_ID)
    assert isinstance(result, PostgresqlRestorePointListData)
    assert result.items[0].id == POINT_ID
    assert result.items[0].engine_version == "17"
    assert result.cluster_id == CLUSTER_ID


@pytest.mark.asyncio
@respx.mock
async def test_restore_points_drop_the_double_encoded_snapshots(handler):
    """Both snapshot fields are JSON inside a string; surfacing them helps nobody."""
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/backup-vdb/{CLUSTER_ID}/restore-point").mock(
        return_value=httpx.Response(200, json=envelope([_restore_point()]))
    )
    point = (await handler.list_postgresql_restore_points(CLUSTER_ID)).items[0]
    dumped = point.model_dump()
    assert "destinationSnapshot" not in dumped
    assert "policySnapshot" not in dumped


@pytest.mark.asyncio
@respx.mock
async def test_a_cluster_with_no_restore_points_is_an_empty_list(handler):
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/backup-vdb/{CLUSTER_ID}/restore-point").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_postgresql_restore_points(CLUSTER_ID)
    assert result.count == 0


# --------------------------------------------------------------------------
# locations and policies
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_locations_report_their_vault_region_and_quota(handler):
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/location").mock(
        return_value=httpx.Response(200, json=envelope([_location(is_default=True)]))
    )
    result = await handler.list_postgresql_backup_locations(False)
    assert isinstance(result, BackupLocationListData)
    row = result.items[0]
    assert row.id == LOCATION_ID
    assert row.region_name == "HCM04"
    assert row.unlimited_quota is True
    assert row.is_default is True


@pytest.mark.asyncio
@respx.mock
async def test_locations_are_cached_until_refresh(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{POSTGRESQL}/v1/backup/location").mock(
        return_value=httpx.Response(200, json=envelope([_location()]))
    )
    await handler.list_postgresql_backup_locations(False)
    await handler.list_postgresql_backup_locations(False)
    assert route.call_count == 1
    await handler.list_postgresql_backup_locations(True)
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_policies_read_the_schedule_off_the_enabled_flags(handler):
    """A disabled frequency still has a config object, so the flag decides."""
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/backup/policy").mock(
        return_value=httpx.Response(200, json=envelope([_policy()]))
    )
    result = await handler.list_postgresql_backup_policies(False)
    assert isinstance(result, BackupPolicyListData)
    row = result.items[0]
    assert row.daily_enabled is True
    assert row.hourly_enabled is False
    assert row.weekly_enabled is False
    assert row.daily_retention == 1
    assert row.daily_backup_type == "FULL"
    assert (row.hour, row.minute) == (1, 0)


@pytest.mark.asyncio
@respx.mock
async def test_policies_are_cached_until_refresh(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{POSTGRESQL}/v1/backup/policy").mock(
        return_value=httpx.Response(200, json=envelope([_policy()]))
    )
    await handler.list_postgresql_backup_policies(False)
    await handler.list_postgresql_backup_policies(False)
    assert route.call_count == 1


# --------------------------------------------------------------------------
# backup now
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_now_is_refused_without_allow_write(readonly_handler):
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.create_postgresql_cluster_backup(CLUSTER_ID)


@pytest.mark.asyncio
@respx.mock
async def test_backup_now_returns_a_bare_string_and_says_how_to_confirm(handler):
    """No id comes back, so the result must point at the only evidence there is."""
    mock_iam(respx.mock)
    route = respx.post(f"{POSTGRESQL}/v1/backup/backup-vdb/{CLUSTER_ID}/backup-now").mock(
        return_value=httpx.Response(200, json=envelope("Backup is being processed"))
    )
    result = await handler.create_postgresql_cluster_backup(CLUSTER_ID)
    assert route.called
    assert isinstance(result, PostgresqlBackupNowData)
    assert result.message == "Backup is being processed"
    assert result.accepted is True
    assert "list_postgresql_restore_points" in result.next_step


@pytest.mark.asyncio
async def test_backup_now_rejects_a_traversing_id(handler):
    with pytest.raises(ValueError):
        await handler.create_postgresql_cluster_backup("../../etc/passwd")
