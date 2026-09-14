"""Tests for the relational backup-storage handler.

Fixtures mirror the shapes probed live on 2026-09-09: `/backup-storages`
returns the price list grouped by engine group, `/backup-storages/information`
returns what is held (empty on the test account), and the two action bodies
disagree about whether the resource-type key is `resourceType` or `resType`.
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
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.models import (
    BackupStorageActionData,
    BackupStorageListData,
    BackupStoragePackageListData,
    CreateBackupStorageDto,
    DryRunData,
    OrderData,
    ResizeBackupStorageDto,
)
from greennode.vdb_mcp_server.relational_storage_handler import RelationalStorageHandler
from mcp.server.mcpserver import MCPServer
from tests.helpers import RELATIONAL, envelope, mock_iam


STORAGE_ID = "db-bk-storage-7a0c736d"


def _package(package_id=3, quota="500"):
    return {
        "packageId": package_id,
        "sku": f"db.backup.quota.{quota}GB",
        "resourceTypeId": package_id,
        "resourceName": f"db.backup.quota.{quota}GB",
        "description": f"Backup Storage Size: {quota}GB",
        "packageName": f"db.backup.quota.{quota}GB",
        "packageQuota": quota,
        "packageConfig": None,
        "price": None,
    }


PACKAGES = [
    # Live shape: the catalogue is repeated verbatim per engine group, and
    # group 3 is empty.
    {"engineGroup": 1, "packages": [_package(1, "100"), _package(3, "500")]},
    {"engineGroup": 2, "packages": [_package(1, "100"), _package(3, "500")]},
    {"engineGroup": 3, "packages": []},
]


def _storage(storage_id=STORAGE_ID, status="ACTIVE", usage=12.5, quota=500):
    return {
        "id": storage_id,
        "name": "backup-storage-1",
        "usage": usage,
        "quota": quota,
        "status": status,
        "engineGroup": 1,
        "backupPackageId": "3",
        "backupPackageName": "db.backup.quota.500GB",
        "skuDbaasBackupStorage": "db.backup.quota.500GB",
    }


ORDER_RESPONSE = envelope(
    [{"orderUrl": "https://vdb.console.vngcloud.vn/", "orderId": "ord-1", "resourceId": None}]
)

ACTION_RESPONSE = envelope(
    [
        {
            "databaseInstances": STORAGE_ID,
            "action": "delete",
            "status": "PROCESSING",
            "success": True,
            "errorMsg": None,
            "code": 200,
        }
    ]
)


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return RelationalStorageHandler(
        MCPServer("test"), config, client, DiscoveryCache(), allow_write=allow_write
    )


@pytest.fixture
def handler(sample_config):
    return _handler(sample_config, allow_write=True)


@pytest.fixture
def readonly_handler(sample_config):
    return _handler(sample_config, allow_write=False)


# --------------------------------------------------------------------------
# registration and write gating
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_are_absent_in_read_only_mode(readonly_handler):
    tools = {t.name for t in await readonly_handler.mcp.list_tools()}
    assert tools == {
        "list_relational_backup_storage_packages",
        "get_relational_backup_storage",
        "create_relational_backup_storage_dryrun",
        "resize_relational_backup_storage_dryrun",
    }


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert tools == {
        "list_relational_backup_storage_packages",
        "get_relational_backup_storage",
        "create_relational_backup_storage_dryrun",
        "resize_relational_backup_storage_dryrun",
        "create_relational_backup_storage",
        "resize_relational_backup_storage",
        "delete_relational_backup_storage",
    }


@pytest.mark.asyncio
async def test_annotations_match_the_effect(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    for name in tools:
        if name.endswith("_dryrun"):
            assert tools[name].annotations.read_only_hint is True, name
    assert tools["delete_relational_backup_storage"].annotations.destructive_hint is True
    assert tools["create_relational_backup_storage"].annotations.destructive_hint is False


@pytest.mark.asyncio
async def test_write_methods_refuse_when_write_is_disabled(readonly_handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.delete_relational_backup_storage(storage_ids=[STORAGE_ID])


# --------------------------------------------------------------------------
# the price list
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_the_repeated_catalogue_is_deduplicated_for_the_caller(handler):
    """Measured live: engine groups 1 and 2 return byte-identical package lists.

    So a package id is unambiguous, and the caller wants one list of choices
    rather than the same packages twice.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backup-storages").mock(
        return_value=httpx.Response(200, json=envelope(PACKAGES))
    )
    result = await handler.list_relational_backup_storage_packages(refresh=False)
    assert isinstance(result, BackupStoragePackageListData)
    assert result.count == 2, "two distinct packages, not four rows"
    assert sorted(p.package_id for p in result.packages) == ["1", "3"]
    assert result.groups_are_identical is True
    assert [g.engine_group for g in result.groups] == [1, 2, 3], "raw answer kept too"


@respx.mock
@pytest.mark.asyncio
async def test_groups_that_really_differ_are_flagged(handler):
    """The dedup is safe only while the repetitions agree; say so if they stop."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backup-storages").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {"engineGroup": 1, "packages": [_package(1, "100")]},
                    {"engineGroup": 2, "packages": [_package(11, "100")]},
                ]
            ),
        )
    )
    result = await handler.list_relational_backup_storage_packages(refresh=False)
    assert result.groups_are_identical is False
    assert result.count == 2


@respx.mock
@pytest.mark.asyncio
async def test_an_engine_group_with_no_packages_is_kept(handler):
    """Group 3 really is empty on the live account; dropping it hides that."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backup-storages").mock(
        return_value=httpx.Response(200, json=envelope(PACKAGES))
    )
    result = await handler.list_relational_backup_storage_packages(refresh=False)
    empty = [g for g in result.groups if g.engine_group == 3][0]
    assert empty.count == 0
    assert empty.packages == []


@respx.mock
@pytest.mark.asyncio
async def test_quota_is_parsed_from_the_string_the_api_sends(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backup-storages").mock(
        return_value=httpx.Response(200, json=envelope(PACKAGES))
    )
    result = await handler.list_relational_backup_storage_packages(refresh=False)
    quotas = sorted(p.quota_gb for p in result.packages)
    assert quotas == [100, 500], "packageQuota arrives as a string of GB"


@respx.mock
@pytest.mark.asyncio
async def test_the_price_list_is_cached_and_refreshable(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/backup-storages").mock(
        return_value=httpx.Response(200, json=envelope(PACKAGES))
    )
    await handler.list_relational_backup_storage_packages(refresh=False)
    await handler.list_relational_backup_storage_packages(refresh=False)
    assert route.calls.call_count == 1
    await handler.list_relational_backup_storage_packages(refresh=True)
    assert route.calls.call_count == 2


# --------------------------------------------------------------------------
# what is held
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_holdings_come_from_the_information_path(handler):
    """The operationIds read as though swapped; the paths are authoritative."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/backup-storages/information").mock(
        return_value=httpx.Response(200, json=envelope([_storage()]))
    )
    result = await handler.get_relational_backup_storage()
    assert isinstance(result, BackupStorageListData)
    assert result.count == 1
    assert result.items[0].id == STORAGE_ID
    assert result.items[0].quota_gb == 500
    assert result.items[0].usage_gb == 12.5
    assert result.items[0].status_kind == "settled"
    assert result.none_purchased is False
    assert route.calls.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_no_paid_storage_is_flagged_rather_than_looking_like_a_failure(handler):
    """The live test account holds none -- the normal state, not an error."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backup-storages/information").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.get_relational_backup_storage()
    assert result.count == 0
    assert result.none_purchased is True


# --------------------------------------------------------------------------
# the two envelopes -- the point of this handler
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_resize_spells_the_key_resourceType(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backup-storages/actions/resize").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.resize_relational_backup_storage(
        storage_id=STORAGE_ID,
        spec=ResizeBackupStorageDto(backupPackageId="4"),
        user_type="IAM_USER",
    )
    assert isinstance(result, OrderData)
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "databaseInstances": [{"instancesId": STORAGE_ID, "config": {"backupPackageId": "4"}}],
        "action": "resize",
        "resourceType": "dbaas-backup-storage",
    }
    assert "resType" not in body
    assert route.calls.last.request.headers["user-type"] == "IAM_USER"


@respx.mock
@pytest.mark.asyncio
async def test_delete_spells_the_same_key_resType(handler):
    """One character apart from resize, and not interchangeable."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backup-storages/actions/deletions").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    result = await handler.delete_relational_backup_storage(storage_ids=[STORAGE_ID])
    assert isinstance(result, BackupStorageActionData)
    assert result.accepted is True
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "databaseInstances": [{"instancesId": STORAGE_ID}],
        "action": "delete",
        "resType": "dbaas-backup-storage",
    }
    assert "resourceType" not in body
    assert "user-type" not in route.calls.last.request.headers, "delete is not an order flow"


def test_the_resource_type_is_neither_dbaas_nor_dbaas_backup():
    """Three values now exist across this API and none follows from the path."""
    from greennode.vdb_mcp_server.relational_backup_handler import BACKUP_RESOURCE_TYPE
    from greennode.vdb_mcp_server.relational_instance_handler import RESOURCE_TYPE
    from greennode.vdb_mcp_server.relational_storage_handler import STORAGE_RESOURCE_TYPE

    assert STORAGE_RESOURCE_TYPE == "dbaas-backup-storage"
    assert len({RESOURCE_TYPE, BACKUP_RESOURCE_TYPE, STORAGE_RESOURCE_TYPE}) == 3


@respx.mock
@pytest.mark.asyncio
async def test_delete_treats_an_empty_result_as_not_applied(handler):
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/backup-storages/actions/deletions").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.delete_relational_backup_storage(storage_ids=[STORAGE_ID])
    assert result.accepted is False
    assert "NOT applied" in result.warning
    assert "resType" in result.warning, "names the likeliest cause"


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_a_failure_carried_inside_a_200(handler):
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/backup-storages/actions/deletions").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "databaseInstances": STORAGE_ID,
                        "action": "delete",
                        "status": None,
                        "success": False,
                        "errorMsg": "Backup storage still in use",
                        "code": 400,
                    }
                ]
            ),
        )
    )
    result = await handler.delete_relational_backup_storage(storage_ids=[STORAGE_ID])
    assert result.accepted is False
    assert "still in use" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_delete_can_release_several_at_once(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backup-storages/actions/deletions").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    await handler.delete_relational_backup_storage(storage_ids=[STORAGE_ID, "db-bk-storage-2"])
    body = json.loads(route.calls.last.request.content)
    assert body["databaseInstances"] == [
        {"instancesId": STORAGE_ID},
        {"instancesId": "db-bk-storage-2"},
    ]


# --------------------------------------------------------------------------
# create and dry runs
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_sends_the_package_id_and_the_billing_header(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/payment/backup-storages").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.create_relational_backup_storage(
        spec=CreateBackupStorageDto(backupPackageId="3"), user_type="IAM_USER"
    )
    assert result.orders[0].order_id == "ord-1"
    assert json.loads(route.calls.last.request.content) == {"backupPackageId": "3"}
    assert route.calls.last.request.headers["user-type"] == "IAM_USER"


@pytest.mark.asyncio
async def test_dryruns_preview_without_calling_the_api(handler):
    """No respx routes registered: any HTTP call would fail the test."""
    create = await handler.create_relational_backup_storage_dryrun(
        spec=CreateBackupStorageDto(backupPackageId="3")
    )
    assert isinstance(create, DryRunData)
    assert create.body == {"backupPackageId": "3"}
    assert any("RECURRING" in w for w in create.warnings)

    resize = await handler.resize_relational_backup_storage_dryrun(
        storage_id=STORAGE_ID, spec=ResizeBackupStorageDto(backupPackageId="4")
    )
    assert resize.body["resourceType"] == "dbaas-backup-storage"
    assert resize.user_type == "IAM_USER"
    assert any("usage_gb" in w for w in resize.warnings)


def test_the_dtos_forbid_unknown_fields_and_empty_ids():
    with pytest.raises(pydantic.ValidationError):
        CreateBackupStorageDto(backupPackageId="3", quota=500)
    with pytest.raises(pydantic.ValidationError):
        ResizeBackupStorageDto(backupPackageId="")


@pytest.mark.asyncio
async def test_storage_ids_are_validated(handler):
    with pytest.raises(ValueError):
        await handler.delete_relational_backup_storage(storage_ids=["../../etc/passwd"])
    with pytest.raises(ValueError):
        await handler.resize_relational_backup_storage_dryrun(
            storage_id="a/b", spec=ResizeBackupStorageDto(backupPackageId="4")
        )
