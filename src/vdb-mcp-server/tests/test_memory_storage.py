"""Tests for the MemoryStore (Redis) backup-storage handler.

The whole risk in this handler is routing, so that is what these pin down: the
two `GET` paths mean the **opposite** of what the same paths mean in the
relational family, the delete path is `/actions/delete` rather than
`/actions/deletions`, and resize/delete spell the resource-type key
differently.
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
from greennode.vdb_mcp_server.memory_storage_handler import MemoryStorageHandler
from greennode.vdb_mcp_server.models import (
    BackupStorageActionData,
    BackupStorageListData,
    BackupStoragePackageListData,
    CreateBackupStorageDto,
    DryRunData,
    OrderData,
    ResizeBackupStorageDto,
)
from mcp.server.mcpserver import MCPServer
from tests.helpers import MEMORY, envelope, mock_iam


STORAGE_ID = "db-bk-storage-32b911d6-7a0c-4b3c-8a89-2abfb1415d34"

HELD = [
    {
        "id": STORAGE_ID,
        "name": "memory_backup_storage",
        "quota": 100,
        "usage": 0.022,
        "status": "ACTIVE",
        "engineGroup": 2,
        "backupPackageId": "1",
        # Empty upstream even though the catalogue names the package.
        "backupPackageName": "",
    }
]


def _package(package_id, gb):
    """A package row in the shape the live API returns.

    Note `packageId` is a **number** upstream and `packageQuota` a **string** --
    the model normalises both, so a fixture that invented `id`/`quota` would
    pass through as empty and prove nothing.
    """
    return {
        "packageId": package_id,
        "sku": f"db.backup.quota.{gb}GB",
        "resourceTypeId": package_id,
        "resourceName": f"db.backup.quota.{gb}GB",
        "description": f"Backup Storage Size: {gb}GB",
        "packageName": f"db.backup.quota.{gb}GB",
        "packageQuota": str(gb),
        "packageConfig": None,
        "price": None,
    }


PACKAGES = [
    {"engineGroup": 1, "packages": [_package(1, 100), _package(2, 200)]},
    # Identical repetition -- the catalogue is per engine group but the same.
    {"engineGroup": 2, "packages": [_package(1, 100), _package(2, 200)]},
    {"engineGroup": 3, "packages": []},
]

ORDER_RESPONSE = envelope(
    [
        {
            "orderUrl": "https://vdb.console.vngcloud.vn/memorystore/backup-storage",
            "orderId": "77c11881",
            "resourceId": STORAGE_ID,
        }
    ]
)

# A real release: PROCESSING with success null, which is NOT a failure.
DELETE_RESPONSE = envelope(
    [
        {
            "databaseInstances": STORAGE_ID,
            "action": "delete",
            "status": "PROCESSING",
            "success": None,
            "errorMsg": None,
            "code": None,
        }
    ]
)


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return MemoryStorageHandler(
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
    assert "get_memory_backup_storage" in tools
    assert "create_memory_backup_storage_dryrun" in tools, "previews stay available"
    for name in (
        "create_memory_backup_storage",
        "resize_memory_backup_storage",
        "delete_memory_backup_storage",
    ):
        assert name not in tools


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    expected = {
        "list_memory_backup_storage_packages",
        "get_memory_backup_storage",
        "create_memory_backup_storage_dryrun",
        "resize_memory_backup_storage_dryrun",
        "create_memory_backup_storage",
        "resize_memory_backup_storage",
        "delete_memory_backup_storage",
    }
    assert expected <= tools
    assert len(expected) == 7


@pytest.mark.asyncio
async def test_write_helpers_refuse_without_allow_write(readonly_handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.create_memory_backup_storage(
            CreateBackupStorageDto(backupPackageId="1"), "IAM_USER"
        )
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.delete_memory_backup_storage([STORAGE_ID])


# --------------------------------------------------------------------------
# routing: the two GET paths are swapped relative to the relational family
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_held_quota_comes_from_the_BARE_path(handler):
    """`/backup-storages` = what is held here; in the relational family it is the price list."""
    mock_iam(respx.mock)
    bare = respx.get(f"{MEMORY}/v1/backup-storages").mock(
        return_value=httpx.Response(200, json=envelope(HELD))
    )

    result = await handler.get_memory_backup_storage()

    assert isinstance(result, BackupStorageListData)
    assert result.count == 1
    assert result.none_purchased is False
    item = result.items[0]
    assert item.id == STORAGE_ID
    assert item.quota_gb == 100
    assert item.usage_gb == pytest.approx(0.022)
    assert item.package_id == "1"
    assert item.package_name == "", "empty upstream; look the id up in the catalogue"
    url = str(bare.calls[0].request.url)
    assert url.endswith("/v1/backup-storages")
    assert "/information" not in url, "that is the relational path"
    assert "/packages" not in url


@respx.mock
@pytest.mark.asyncio
async def test_price_list_comes_from_the_PACKAGES_path(handler):
    """`/backup-storages/packages` = the price list here; the relational family has no such path."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/backup-storages/packages").mock(
        return_value=httpx.Response(200, json=envelope(PACKAGES))
    )

    result = await handler.list_memory_backup_storage_packages(False)

    assert isinstance(result, BackupStoragePackageListData)
    assert result.count == 2, "deduplicated across the identical engine groups"
    assert {p.package_id for p in result.packages} == {"1", "2"}, "numeric ids normalised"
    assert {p.quota_gb for p in result.packages} == {100, 200}, "string quota normalised"
    assert len(result.groups) == 3
    assert result.groups_are_identical is True, "an empty group is not a disagreement"
    assert str(route.calls[0].request.url).endswith("/backup-storages/packages")


@respx.mock
@pytest.mark.asyncio
async def test_no_paid_storage_is_an_answer_not_a_failure(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/backup-storages").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )

    result = await handler.get_memory_backup_storage()

    assert result.count == 0
    assert result.none_purchased is True


@respx.mock
@pytest.mark.asyncio
async def test_the_price_list_is_cached_and_refreshable(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/backup-storages/packages").mock(
        return_value=httpx.Response(200, json=envelope(PACKAGES))
    )

    await handler.list_memory_backup_storage_packages(False)
    await handler.list_memory_backup_storage_packages(False)
    assert route.call_count == 1

    await handler.list_memory_backup_storage_packages(True)
    assert route.call_count == 2, "refresh=True bypasses the cache"


@respx.mock
@pytest.mark.asyncio
async def test_a_divergent_catalogue_is_reported_rather_than_flattened(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/backup-storages/packages").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {"engineGroup": 1, "packages": [_package(1, 100)]},
                    {"engineGroup": 2, "packages": [_package(9, 100)]},
                ]
            ),
        )
    )

    result = await handler.list_memory_backup_storage_packages(False)

    assert result.groups_are_identical is False
    assert result.count == 2


# --------------------------------------------------------------------------
# dry runs
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_dryrun_names_the_recurring_charge_and_sends_nothing(handler):
    mock_iam(respx.mock)

    result = await handler.create_memory_backup_storage_dryrun(
        CreateBackupStorageDto(backupPackageId="1")
    )

    assert isinstance(result, DryRunData)
    assert result.tool == "create_memory_backup_storage"
    assert result.path == "/v1/payment/backup-storages"
    assert result.service == "vdb-memory"
    assert result.user_type == "IAM_USER"
    assert result.body == {"backupPackageId": "1"}
    assert any("RECURRING" in w for w in result.warnings)
    assert not respx.mock.calls, "a dry run must not touch the API"


@pytest.mark.asyncio
async def test_resize_dryrun_uses_resourceType(handler):
    result = await handler.resize_memory_backup_storage_dryrun(
        STORAGE_ID, ResizeBackupStorageDto(backupPackageId="2")
    )

    assert result.path == "/v1/backup-storages/actions/resize"
    assert result.body["action"] == "resize"
    assert result.body["resourceType"] == "dbaas-backup-storage"
    assert "resType" not in result.body, "resize spells it resourceType"
    detail = result.body["databaseInstances"][0]
    assert detail["instancesId"] == STORAGE_ID, "a storage id under a key called instancesId"
    assert detail["config"]["backupPackageId"] == "2"


@pytest.mark.asyncio
async def test_dryruns_validate_the_storage_id(handler):
    with pytest.raises(ValueError, match="Invalid storage_id"):
        await handler.resize_memory_backup_storage_dryrun(
            "../../etc/passwd", ResizeBackupStorageDto(backupPackageId="2")
        )


def test_the_storage_dtos_forbid_extra_fields():
    for dto in (CreateBackupStorageDto, ResizeBackupStorageDto):
        with pytest.raises(pydantic.ValidationError):
            dto(backupPackageId="1", quota=100)


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_sends_the_billing_header(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/payment/backup-storages").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    result = await handler.create_memory_backup_storage(
        CreateBackupStorageDto(backupPackageId="1"), "IAM_USER"
    )

    assert isinstance(result, OrderData)
    assert result.orders[0].resource_id == STORAGE_ID
    assert route.calls[0].request.headers["user-type"] == "IAM_USER"
    assert "recurring" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_resize_orders_with_the_resourceType_envelope(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/backup-storages/actions/resize").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    await handler.resize_memory_backup_storage(
        STORAGE_ID, ResizeBackupStorageDto(backupPackageId="2"), "IAM_USER"
    )

    body = json.loads(route.calls[0].request.content)
    assert body["resourceType"] == "dbaas-backup-storage"
    assert "resType" not in body
    assert route.calls[0].request.headers["user-type"] == "IAM_USER"


@respx.mock
@pytest.mark.asyncio
async def test_delete_uses_the_delete_path_and_resType(handler):
    """`/actions/delete` here; the relational family says `/actions/deletions`."""
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/backup-storages/actions/delete").mock(
        return_value=httpx.Response(200, json=DELETE_RESPONSE)
    )

    result = await handler.delete_memory_backup_storage([STORAGE_ID])

    assert isinstance(result, BackupStorageActionData)
    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "delete"
    assert body["resType"] == "dbaas-backup-storage", "delete spells it resType"
    assert "resourceType" not in body
    assert body["databaseInstances"] == [{"instancesId": STORAGE_ID}]
    assert str(route.calls[0].request.url).endswith("/actions/delete")
    assert "user-type" not in route.calls[0].request.headers, "not an order flow"


@respx.mock
@pytest.mark.asyncio
async def test_a_null_success_on_delete_is_NOT_treated_as_failure(handler):
    """A real release answers PROCESSING with success null; only false is a failure."""
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/backup-storages/actions/delete").mock(
        return_value=httpx.Response(200, json=DELETE_RESPONSE)
    )

    result = await handler.delete_memory_backup_storage([STORAGE_ID])

    assert result.accepted is True, "catching `not success` would fail every real release"
    assert result.warning is None
    assert result.results[0].success is None
    assert result.storage_ids == [STORAGE_ID]


@respx.mock
@pytest.mark.asyncio
async def test_a_false_success_on_delete_IS_a_failure(handler):
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/backup-storages/actions/delete").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "databaseInstances": STORAGE_ID,
                        "action": "delete",
                        "status": None,
                        "success": False,
                        "errorMsg": "Resource not found",
                        "code": 404,
                    }
                ]
            ),
        )
    )

    result = await handler.delete_memory_backup_storage([STORAGE_ID])

    assert result.accepted is False
    assert result.warning and "did NOT succeed" in result.warning
    assert "Resource not found" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_an_empty_result_array_is_reported_as_not_applied(handler):
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/backup-storages/actions/delete").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )

    result = await handler.delete_memory_backup_storage([STORAGE_ID])

    assert result.accepted is False
    assert result.warning and "NOT applied" in result.warning
    assert "resourceType" in result.warning, "the warning names the likely cause"


@pytest.mark.asyncio
async def test_delete_validates_every_id_and_rejects_an_empty_list(handler):
    with pytest.raises(ValueError, match="Invalid storage_ids"):
        await handler.delete_memory_backup_storage([STORAGE_ID, "../../secrets"])
    with pytest.raises(ValueError, match="at least one backup storage"):
        await handler.delete_memory_backup_storage([])


# --------------------------------------------------------------------------
# family separation
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_every_call_goes_to_the_memory_service(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/backup-storages").mock(
        return_value=httpx.Response(200, json=envelope(HELD))
    )

    await handler.get_memory_backup_storage()

    urls = [str(c.request.url) for c in respx.mock.calls if "vdb-" in str(c.request.url)]
    assert urls and all("/vdb-memory/" in u for u in urls)
