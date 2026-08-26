"""Backup destinations: the Backup Location lifecycle and its detail views.

The payload shapes here mirror what the live gateway returns. Two of them are
easy to get wrong and are pinned by this file: ``maxQuota`` is an object
holding a GB number rather than a byte count, and a destination's storage sits
under ``config.vault`` OR ``config.vstorage`` depending on its type.
"""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import (
    API_BASE,
    RAW_BACKUP_DATABASE,
    RAW_BACKUP_REGIONS,
    RAW_DESTINATION,
    RAW_DESTINATION_HISTORY,
    RAW_DESTINATION_TAG,
    RAW_DESTINATION_VSTORAGE,
    RAW_PRODUCTS,
    RAW_SERVER,
    envelope,
    mock_iam,
)
from greennode.vbackup_mcp_server.destination_handler import DestinationHandler
from greennode.vbackup_mcp_server.models import (
    BackupDestinationItem,
    BackupDestinationListData,
    CreateBackupDestinationDto,
    MaxQuotaDto,
    SoftDeleteDto,
    UpdateBackupDestinationNameDto,
    UpdateMaxQuotaDto,
    VaultLockDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


DEST_ID = "bk-des-0001"


@pytest.fixture
def handler(config, client, no_cache):
    return DestinationHandler(MCPServer("test"), config, client, no_cache)


@pytest.fixture
def writer(config, client, no_cache):
    return DestinationHandler(MCPServer("test"), config, client, no_cache, allow_write=True)


@pytest.mark.asyncio
async def test_read_tools_registered_read_only(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    for name in (
        "list_backup_destinations",
        "get_backup_destination",
        "list_backup_destination_servers",
        "list_backup_destination_databases",
        "list_backup_destination_tags",
        "list_backup_destination_history",
        "list_backup_products",
        "list_backup_regions",
    ):
        assert name in tools
        assert tools[name].annotations.read_only_hint is True
    assert "create_backup_destination" not in tools
    assert "delete_backup_destination" not in tools


@pytest.mark.asyncio
async def test_delete_is_the_only_destructive_write(writer):
    tools = {t.name: t for t in await writer.mcp.list_tools()}
    assert tools["delete_backup_destination"].annotations.destructive_hint is True
    for name in (
        "create_backup_destination",
        "update_backup_destination_name",
        "update_backup_destination_max_quota",
        "update_backup_destination_soft_delete",
        "update_backup_destination_vault_lock",
    ):
        assert tools[name].annotations.destructive_hint is False


@respx.mock
@pytest.mark.asyncio
async def test_quota_is_an_object_holding_gb_not_a_byte_count(handler):
    """`maxQuota` is `{unlimited, maxQuota}` and the number is GB, not bytes."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DESTINATION]))
    )
    result = await handler.list_backup_destinations(
        region="HCM-3", name=None, type=None, backend_id=None, refresh=False
    )
    assert isinstance(result, BackupDestinationListData)
    dest = result.destinations[0]
    assert dest.quota_unlimited is False
    assert dest.max_quota_gb == 150
    assert dest.vault.used_gb == 30.0


@respx.mock
@pytest.mark.asyncio
async def test_vstorage_destination_reports_its_storage(handler):
    """A VSTORAGE destination keeps its numbers under `config.vstorage`."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DESTINATION_VSTORAGE]))
    )
    result = await handler.list_backup_destinations(
        region="HCM-3", name=None, type=None, backend_id=None, refresh=False
    )
    dest = result.destinations[0]
    assert dest.type == "VSTORAGE"
    assert dest.quota_unlimited is True
    assert dest.vault.used_gb == 10.0
    assert dest.vault.total_gb == 30.0
    assert dest.vault.container_name == "container-0002"


@respx.mock
@pytest.mark.asyncio
async def test_soft_delete_and_lock_are_structured_or_none(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DESTINATION]))
    )
    dest = (
        await handler.list_backup_destinations(
            region="HCM-3", name=None, type=None, backend_id=None, refresh=False
        )
    ).destinations[0]
    assert dest.soft_delete.enabled is True
    assert dest.soft_delete.retain_days == 8
    assert dest.vault_lock is None


@respx.mock
@pytest.mark.asyncio
async def test_config_accepted_as_json_string(handler):
    """Some endpoints send `config` as an escaped JSON string, not an object."""
    raw = {**RAW_DESTINATION, "config": json.dumps(RAW_DESTINATION["config"])}
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations").mock(
        return_value=httpx.Response(200, json=envelope([raw]))
    )
    result = await handler.list_backup_destinations(
        region="HCM-3", name=None, type=None, backend_id=None, refresh=False
    )
    assert result.destinations[0].vault.used_gb == 30.0


@respx.mock
@pytest.mark.asyncio
async def test_list_filters_forwarded(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/backup-destinations").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DESTINATION]))
    )
    await handler.list_backup_destinations(
        region="HCM-3", name="vault", type="VAULT", backend_id="be-0001", refresh=False
    )
    params = route.calls[0].request.url.params
    assert params["name"] == "vault"
    assert params["type"] == "VAULT"
    assert params["backendId"] == "be-0001"


@respx.mock
@pytest.mark.asyncio
async def test_get_destination_reads_the_object_directly(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations/{DEST_ID}").mock(
        return_value=httpx.Response(200, json=RAW_DESTINATION)
    )
    dest = await handler.get_backup_destination(destination_id=DEST_ID, region="HCM-3")
    assert isinstance(dest, BackupDestinationItem)
    assert dest.id == DEST_ID
    assert dest.max_quota_gb == 150


@respx.mock
@pytest.mark.asyncio
async def test_destination_servers_reuse_the_backup_server_shape(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backup-destinations/{DEST_ID}/backup-instances").mock(
        return_value=httpx.Response(200, json=envelope([RAW_SERVER]))
    )
    result = await handler.list_backup_destination_servers(
        destination_id=DEST_ID, region="HCM-3", name=None
    )
    assert result.total == 1
    assert result.backup_servers[0].id == RAW_SERVER["id"]


@respx.mock
@pytest.mark.asyncio
async def test_destination_databases_read_the_projected_item(handler):
    """The sub-resource returns a full backup database with both refs nulled."""
    mock_iam(respx.mock)
    projected = {**RAW_BACKUP_DATABASE, "policy": None, "backupDestination": None}
    respx.get(f"{API_BASE}/v1/backup-destinations/{DEST_ID}/backup-databases").mock(
        return_value=httpx.Response(200, json=envelope([projected]))
    )
    result = await handler.list_backup_destination_databases(
        destination_id=DEST_ID, region="HCM-3", name=None
    )
    assert result.destination_id == DEST_ID
    assert result.databases[0].engine == "Redis"
    assert result.databases[0].destination.id == ""


@respx.mock
@pytest.mark.asyncio
async def test_tags_come_from_the_account_tag_service(handler):
    """Tags are addressed by destination id on /v1/tags, not under the destination."""
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/tags/{DEST_ID}").mock(
        return_value=httpx.Response(200, json=envelope([RAW_DESTINATION_TAG]))
    )
    result = await handler.list_backup_destination_tags(
        destination_id=DEST_ID, region="HCM-3", refresh=False
    )
    assert route.called
    assert result.tags[0].resource_type == "BACKUP_LOCATION"
    assert result.tags[0].system_tag is True


@respx.mock
@pytest.mark.asyncio
async def test_history_keeps_failed_attempts(handler):
    """A refused delete is recorded with its reason — that is the point of the trail."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/histories/backup-destinations/{DEST_ID}").mock(
        return_value=httpx.Response(200, json=envelope(RAW_DESTINATION_HISTORY))
    )
    result = await handler.list_backup_destination_history(
        region="HCM-3", destination_id=DEST_ID, limit=50
    )
    failed = [c for c in result.changes if c.status == "ERROR"]
    assert failed[0].error_message == "backup_location_is_being_used"
    assert "150GB" in result.changes[0].description


@respx.mock
@pytest.mark.asyncio
async def test_history_respects_the_limit(handler):
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/histories/backup-destinations/{DEST_ID}").mock(
        return_value=httpx.Response(200, json=envelope(RAW_DESTINATION_HISTORY))
    )
    result = await handler.list_backup_destination_history(
        region="HCM-3", destination_id=DEST_ID, limit=1
    )
    assert result.total == 1


@respx.mock
@pytest.mark.asyncio
async def test_products_and_regions_are_bare_arrays(handler):
    """Both lookups answer with a bare array, not the list envelope."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/products").mock(return_value=httpx.Response(200, json=RAW_PRODUCTS))
    route = respx.get(f"{API_BASE}/v1/regions").mock(
        return_value=httpx.Response(200, json=RAW_BACKUP_REGIONS)
    )
    products = await handler.list_backup_products(region="HCM-3", refresh=False)
    assert [p.product for p in products.products] == ["vServer", "vDB"]

    regions = await handler.list_backup_regions(product="vServer", region="HCM-3", refresh=False)
    assert route.calls[0].request.url.params["product"] == "vServer"
    assert regions.regions[0].region_id == "rgn-0001"
    assert regions.regions[0].id != regions.regions[0].region_id


@respx.mock
@pytest.mark.asyncio
async def test_create_sends_the_nested_quota(writer):
    mock_iam(respx.mock)
    route = respx.post(f"{API_BASE}/v1/backup-destinations").mock(
        return_value=httpx.Response(200, json=RAW_DESTINATION)
    )
    body = CreateBackupDestinationDto(
        name="nightly-store",
        regionId="rgn-0001",
        product="vServer",
        maxQuota=MaxQuotaDto(unlimited=False, maxQuota=150),
    )
    dest = await writer.create_backup_destination(body=body, region="HCM-3")
    sent = json.loads(route.calls[0].request.content)
    assert sent["maxQuota"] == {"unlimited": False, "maxQuota": 150}
    assert sent["product"] == "vServer"
    assert sent["regionId"] == "rgn-0001"
    assert dest.id == DEST_ID


@respx.mock
@pytest.mark.asyncio
async def test_each_edit_hits_its_own_endpoint(writer):
    mock_iam(respx.mock)
    routes = {
        name: respx.put(f"{API_BASE}/v1/backup-destinations/{DEST_ID}/{name}").mock(
            return_value=httpx.Response(204)
        )
        for name in ("name", "max-quota", "soft-delete", "vault-lock")
    }

    await writer.update_backup_destination_name(
        destination_id=DEST_ID,
        body=UpdateBackupDestinationNameDto(name="renamed"),
        region="HCM-3",
    )
    await writer.update_backup_destination_max_quota(
        destination_id=DEST_ID,
        body=UpdateMaxQuotaDto(maxQuota=MaxQuotaDto(unlimited=False, maxQuota=200)),
        region="HCM-3",
    )
    await writer.update_backup_destination_soft_delete(
        destination_id=DEST_ID, body=SoftDeleteDto(enable=True, retainDays=4), region="HCM-3"
    )
    result = await writer.update_backup_destination_vault_lock(
        destination_id=DEST_ID,
        body=VaultLockDto(enable=True, changeDuration=3, minRetention=1, maxRetention=2),
        region="HCM-3",
    )

    assert all(r.called for r in routes.values())
    assert json.loads(routes["name"].calls[0].request.content) == {"name": "renamed"}
    assert json.loads(routes["max-quota"].calls[0].request.content) == {
        "maxQuota": {"unlimited": False, "maxQuota": 200}
    }
    assert json.loads(routes["soft-delete"].calls[0].request.content)["retainDays"] == 4
    assert result.succeeded is True
    assert "permanent" in result.detail


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_what_is_gone(writer):
    mock_iam(respx.mock)
    route = respx.delete(f"{API_BASE}/v1/backup-destinations/{DEST_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = await writer.delete_backup_destination(destination_id=DEST_ID, region="HCM-3")
    assert route.called
    assert result.action == "deleted"
    assert "cannot be recovered" in result.detail


@pytest.mark.asyncio
async def test_writes_refused_without_allow_write(handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.delete_backup_destination(destination_id=DEST_ID, region="HCM-3")
    with pytest.raises(ValueError, match="--allow-write"):
        await handler.create_backup_destination(
            body=CreateBackupDestinationDto(name="x", regionId="rgn-0001", product="vServer"),
            region="HCM-3",
        )


@pytest.mark.asyncio
async def test_malformed_destination_id_rejected(handler):
    for call in (
        handler.get_backup_destination(destination_id="../etc", region="HCM-3"),
        handler.list_backup_destination_tags(
            destination_id="../etc", region="HCM-3", refresh=False
        ),
    ):
        with pytest.raises(ValueError, match="destination_id"):
            await call


def test_create_dto_rejects_unknown_and_bad_product():
    with pytest.raises(ValidationError):
        CreateBackupDestinationDto(name="x", regionId="rgn-0001", product="vServer", typo=True)
    with pytest.raises(ValidationError):
        CreateBackupDestinationDto(name="x", regionId="rgn-0001", product="vStorage")


def test_quota_dto_refuses_a_negative_ceiling():
    with pytest.raises(ValidationError):
        MaxQuotaDto(unlimited=False, maxQuota=-1)


def test_vault_lock_rejects_an_inverted_retention_window():
    """The API calls this `vault_locked_invalid` without naming a field."""
    with pytest.raises(ValidationError, match="minRetention"):
        VaultLockDto(enable=True, changeDuration=3, minRetention=2, maxRetention=1)
    VaultLockDto(enable=True, changeDuration=3, minRetention=2, maxRetention=2)


def test_vault_lock_change_duration_is_capped_at_a_week():
    with pytest.raises(ValidationError):
        VaultLockDto(enable=True, changeDuration=30, minRetention=1, maxRetention=2)


def test_create_defaults_send_every_required_config_object():
    """maxQuota, softDeleteConfig and vaultLock are all required by the API."""
    body = CreateBackupDestinationDto(name="x", regionId="rgn-0001", product="vServer")
    payload = body.model_dump(exclude_none=True)
    assert set(payload) == {
        "name",
        "regionId",
        "product",
        "maxQuota",
        "softDeleteConfig",
        "vaultLock",
        "isDefault",
    }
    assert payload["isDefault"] is False
    assert payload["vaultLock"]["enable"] is False
    assert payload["vaultLock"]["changeDuration"] != 0


@respx.mock
@pytest.mark.asyncio
async def test_history_without_an_id_reads_the_account_wide_log(handler):
    """Omitting destination_id switches to the collection endpoint, no id in the path."""
    mock_iam(respx.mock)
    scoped = respx.get(f"{API_BASE}/v1/histories/backup-destinations/{DEST_ID}").mock(
        return_value=httpx.Response(200, json=envelope(RAW_DESTINATION_HISTORY))
    )
    account = respx.get(f"{API_BASE}/v1/histories/backup-destinations").mock(
        return_value=httpx.Response(200, json=envelope(RAW_DESTINATION_HISTORY))
    )
    result = await handler.list_backup_destination_history(
        region="HCM-3", destination_id=None, limit=50
    )
    assert account.called
    assert not scoped.called
    assert result.destination_id == ""
    assert result.total == 2
