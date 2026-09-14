"""Tests for the relational backup handler.

Fixtures mirror the shapes the spec declares for the seven backup endpoints:
`/backups` pages through `data.content`, `/backups/insId/{id}` answers with a
plain array, and the restore body carries constants that differ from every
other action envelope in the API.
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
from greennode.vdb_mcp_server.models import (
    BackupCreateData,
    BackupDeleteData,
    CreateBackupDto,
    DatabaseBackup,
    DryRunData,
    FreeBackupUsageData,
    OrderData,
    RelationalBackupListData,
    RelationalInstanceBackupListData,
    RestoreRelationalBackupDto,
)
from greennode.vdb_mcp_server.relational_backup_handler import RelationalBackupHandler
from mcp.server.mcpserver import MCPServer
from tests.helpers import RELATIONAL, content_list, envelope, mock_iam, page_object


def _backup(backup_id="bk-1111", name="nightly", status="COMPLETED", **over):
    """A DETAIL row -- every field populated, as `/backups/detail/{id}` returns it."""
    row = {
        "id": backup_id,
        "name": name,
        "description": "before the migration",
        "dbInstanceId": "db-1111",
        "instanceName": "app-db",
        "backupType": "FULL",
        "type": "MANUAL",
        "backupTier": "FREE",
        "parent": None,
        "parentName": None,
        "status": status,
        "size": 1.25,
        "storageSize": 40,
        "storageType": "Gen2-NVMe2-IOPS3000",
        "datastoreType": "MySQL",
        "datastoreVersion": "8.0",
        "packageId": 224,
        "vcpu": 2,
        "ram": 4,
        "netIds": ["net-ed67af02"],
        "configId": "cfg-1",
        "configName": "tf-mysql8",
        "username": "admin",
        "backupDuration": 7,
        "isRestoring": False,
        "created": "2026-09-01 02:00:11.0",
        "updated": "2026-09-01 02:04:52.0",
        # fields present upstream that the projection deliberately drops
        "backendId": 918273,
        "projectId": "pro-2b233cab",
        "priceKey": "backup_gb",
        "engineGroup": 1,
        "sharedBy": None,
        "sharedActions": [],
        "netName": "demo-vpc",
    }
    row.update(over)
    return row


def _summary(backup_id="bk-1111", name="nightly", status="COMPLETED", **over):
    """A LIST row, as `/backups` really returns it: most fields null.

    Verified live by reading one backup through both endpoints -- the listing
    nulls everything a restore needs and lowercases the engine name.
    """
    row = _backup(backup_id, name, status)
    row.update(
        {
            "datastoreType": "mysql",
            "storageType": None,
            "storageSize": None,
            "ram": None,
            "vcpu": None,
            "packageId": None,
            "username": None,
            "configId": None,
            "configName": None,
            "netIds": None,
            "backupDuration": None,
            "projectId": None,
        }
    )
    row.update(over)
    return row


def _restore_spec(**over):
    fields = {
        "name": "restored-db",
        "packageId": "224",
        "volumeType": "Gen2-NVMe2-IOPS3000",
        "volumeSize": 40,
        "datastoreType": "MySQL",
        "datastoreVersion": "8.0",
        "netIds": ["sub-aaaa"],
        "locateZoneId": "HCM03-1A",
    }
    fields.update(over)
    return RestoreRelationalBackupDto(**fields)


ORDER_RESPONSE = envelope(
    [
        {
            "orderUrl": "https://vdb.console.vngcloud.vn/relational/database",
            "orderId": "ord-771",
            "resourceId": "db-9999",
        }
    ]
)

DELETE_RESPONSE = envelope(
    [
        {
            "backupId": "bk-1111",
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
    return RelationalBackupHandler(MCPServer("test"), config, client, allow_write=allow_write)


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
    assert "list_relational_backups" in tools
    assert "restore_relational_backup_dryrun" in tools, "previews stay available"
    for name in (
        "create_relational_backup",
        "restore_relational_backup",
        "delete_relational_backup",
    ):
        assert name not in tools


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert tools == {
        "list_relational_backups",
        "get_relational_backup",
        "list_relational_instance_backups",
        "get_relational_free_backup_usage",
        "restore_relational_backup_dryrun",
        "create_relational_backup",
        "restore_relational_backup",
        "delete_relational_backup",
    }


@pytest.mark.asyncio
async def test_annotations_match_the_effect_of_each_tool(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    assert tools["restore_relational_backup_dryrun"].annotations.read_only_hint is True
    assert tools["delete_relational_backup"].annotations.destructive_hint is True
    assert tools["restore_relational_backup"].annotations.destructive_hint is False, (
        "a restore builds a new instance; it does not overwrite anything"
    )
    assert tools["create_relational_backup"].annotations.destructive_hint is False


@pytest.mark.asyncio
async def test_write_methods_refuse_when_write_is_disabled(readonly_handler):
    """Second line of defence: a direct call must still be refused."""
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.delete_relational_backup(backup_id="bk-1111")


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_listing_reads_items_from_data_content(handler):
    """Backups paginate through `data.content`, not `data.data`."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/backups").mock(
        return_value=httpx.Response(
            200,
            json=content_list(
                [_summary(), _summary("bk-2222", "weekly")],
                page_object(number=2, size=10, total_pages=3, total_elements=25),
            ),
        )
    )
    result = await handler.list_relational_backups(page=2, page_size=10)
    assert isinstance(result, RelationalBackupListData)
    assert [b.id for b in result.items] == ["bk-1111", "bk-2222"]
    assert result.count == 2
    assert result.page.page == 2, "pageNumber is 1-based in both directions"
    assert result.page.total_items == 25
    assert dict(route.calls.last.request.url.params) == {"pageNumber": "2", "pageSize": "10"}


@respx.mock
@pytest.mark.asyncio
async def test_page_size_is_clamped_to_the_api_ceiling(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/backups").mock(
        return_value=httpx.Response(200, json=content_list([], page_object()))
    )
    await handler.list_relational_backups(page=1, page_size=100)
    assert route.calls.last.request.url.params["pageSize"] == "100"


@respx.mock
@pytest.mark.asyncio
async def test_get_uses_detail_before_the_id_not_after(handler):
    """`/backups/detail/{id}` here; the memory family is `/backups/{id}/detail`."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/backups/detail/bk-1111").mock(
        return_value=httpx.Response(200, json=envelope(_backup()))
    )
    result = await handler.get_relational_backup(backup_id="bk-1111")
    assert isinstance(result, DatabaseBackup)
    assert result.id == "bk-1111"
    assert route.calls.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_projection_keeps_what_a_restore_needs_and_drops_billing(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/detail/bk-1111").mock(
        return_value=httpx.Response(200, json=envelope(_backup()))
    )
    result = await handler.get_relational_backup(backup_id="bk-1111")
    # everything a restore has to re-specify survives the projection
    assert result.storage_size_gb == 40
    assert result.storage_type == "Gen2-NVMe2-IOPS3000"
    assert result.datastore_type == "MySQL"
    assert result.datastore_version == "8.0"
    assert result.package_id == "224", "numeric upstream, string here like every other flavour id"
    assert result.network_ids == ["net-ed67af02"]
    assert result.origin == "MANUAL"
    assert result.tier == "FREE"
    assert result.instance_id == "db-1111"
    assert result.size_gb == 1.25
    assert result.backup_duration_days == 7
    # and the billing/sharing fields do not
    dumped = result.model_dump()
    for dropped in ("priceKey", "engineGroup", "sharedBy", "sharedActions", "backendId"):
        assert dropped not in dumped


@respx.mock
@pytest.mark.asyncio
async def test_the_listing_returns_summaries_not_descriptions(handler):
    """Live, the same backup reads differently through the two endpoints.

    `/backups` nulls everything a restore needs. A caller that plans a restore
    from a list row would send `volumeSize=None` and get back a bare
    `in_valid`, so the response says outright that the rows are summaries.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups").mock(
        return_value=httpx.Response(200, json=content_list([_summary()], page_object()))
    )
    listed = (await handler.list_relational_backups(page=1, page_size=20)).items[0]
    assert listed.storage_size_gb is None
    assert listed.storage_type == ""
    assert listed.package_id == ""
    assert listed.network_ids == []
    assert listed.datastore_type == "mysql", "the listing lowercases what the detail capitalises"

    respx.get(f"{RELATIONAL}/v1/backups/detail/bk-1111").mock(
        return_value=httpx.Response(200, json=envelope(_backup()))
    )
    detailed = await handler.get_relational_backup(backup_id="bk-1111")
    assert detailed.id == listed.id
    assert detailed.storage_size_gb == 40
    assert detailed.datastore_type == "MySQL"


@respx.mock
@pytest.mark.asyncio
async def test_the_list_result_flags_itself_as_summaries(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups").mock(
        return_value=httpx.Response(200, json=content_list([_summary()], page_object()))
    )
    result = await handler.list_relational_backups(page=1, page_size=20)
    assert result.rows_are_summaries is True


@respx.mock
@pytest.mark.asyncio
async def test_net_ids_are_networks_and_are_not_named_subnets(handler):
    """`netIds` on a backup holds `net-` ids; `netIds` on a restore wants `sub-` ids.

    Same upstream name, incompatible values. Calling the field `subnet_ids`
    would invite feeding it straight back into a restore.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/detail/bk-1111").mock(
        return_value=httpx.Response(200, json=envelope(_backup()))
    )
    result = await handler.get_relational_backup(backup_id="bk-1111")
    assert result.network_ids == ["net-ed67af02"]
    assert "subnet_ids" not in result.model_dump()


@respx.mock
@pytest.mark.asyncio
async def test_a_retention_below_the_create_minimum_is_reported_not_rejected(handler):
    """Rows predating the 2-14 rule report 1; a response model must not police input rules."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/detail/bk-1111").mock(
        return_value=httpx.Response(200, json=envelope(_backup(backupDuration=1)))
    )
    result = await handler.get_relational_backup(backup_id="bk-1111")
    assert result.backup_duration_days == 1


@respx.mock
@pytest.mark.asyncio
async def test_auto_daily_backups_are_distinguishable_from_manual_ones(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups").mock(
        return_value=httpx.Response(
            200,
            json=content_list(
                [_summary(), _summary("bk-2222", "daily", type="AUTO_DAILY")], page_object()
            ),
        )
    )
    result = await handler.list_relational_backups(page=1, page_size=20)
    assert [b.origin for b in result.items] == ["MANUAL", "AUTO_DAILY"]


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("COMPLETED", "settled"),
        ("NEW", "transitional"),
        ("BUILDING", "transitional"),
        ("SAVING", "transitional"),
        ("ERROR", "failed"),
        ("FAILED", "failed"),
        ("A_STATUS_ADDED_LATER", "unknown"),
    ],
)
async def test_backup_status_carries_actionable_classification(handler, status, kind):
    """All six published backup statuses classify, and an unknown one is not an error."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/detail/bk-1111").mock(
        return_value=httpx.Response(200, json=envelope(_backup(status=status)))
    )
    result = await handler.get_relational_backup(backup_id="bk-1111")
    assert result.status == status, "the raw value is preserved verbatim"
    assert result.status_kind == kind
    assert result.status_guidance


@respx.mock
@pytest.mark.asyncio
async def test_instance_backups_read_a_plain_array_and_do_not_paginate(handler):
    """This endpoint answers with the whole set inside `data`, with no pageObject."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/backups/insId/db-1111").mock(
        return_value=httpx.Response(200, json=envelope([_summary(), _summary("bk-2222")]))
    )
    result = await handler.list_relational_instance_backups(instance_id="db-1111")
    assert isinstance(result, RelationalInstanceBackupListData)
    assert result.instance_id == "db-1111"
    assert result.count == 2
    assert not hasattr(result, "page")
    assert route.calls.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_an_instance_with_no_backups_is_an_empty_list_not_an_error(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/insId/db-1111").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_relational_instance_backups(instance_id="db-1111")
    assert result.count == 0
    assert result.items == []


@respx.mock
@pytest.mark.asyncio
async def test_free_backup_usage_maps_both_numbers(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/free-backup").mock(
        return_value=httpx.Response(
            200, json=envelope({"freeBackupStorage": 50, "backupUsage": 12.5})
        )
    )
    result = await handler.get_relational_free_backup_usage()
    assert isinstance(result, FreeBackupUsageData)
    assert result.free_backup_storage_gb == 50
    assert result.backup_usage_gb == 12.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("get_relational_backup", {"backup_id": "../../etc/passwd"}),
        ("list_relational_instance_backups", {"instance_id": "db-1/../admin"}),
        ("restore_relational_backup_dryrun", {"backup_id": "a/b"}),
    ],
)
async def test_ids_used_in_urls_are_validated(handler, method, kwargs):
    if method == "restore_relational_backup_dryrun":
        kwargs["spec"] = _restore_spec()
    with pytest.raises(ValueError):
        await getattr(handler, method)(**kwargs)


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_sends_the_flat_body_and_returns_the_backup_id(handler):
    """Create backup is not an order flow: the id comes back straight away."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backups/create").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                {
                    "errorMsg": None,
                    "code": 200,
                    "success": True,
                    "total": 1,
                    "backupId": "bk-3333",
                    "projectId": "pro-2b233cab",
                    "dbInstanceId": "db-1111",
                }
            ),
        )
    )
    result = await handler.create_relational_backup(
        spec=CreateBackupDto(dbInstanceId="db-1111", name="pre-migration", backupType="FULL")
    )
    assert isinstance(result, BackupCreateData)
    assert result.backup_id == "bk-3333"
    assert result.instance_id == "db-1111"
    assert result.success is True
    assert "COMPLETED" in result.next_step, "the id exists before the data does"

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "dbInstanceId": "db-1111",
        "name": "pre-migration",
        "backupType": "FULL",
        "description": "Manual created",
    }, "no action/resType envelope on this endpoint, no null parentId, always a description"
    assert "user-type" not in route.calls.last.request.headers, "not a billable endpoint"


@respx.mock
@pytest.mark.asyncio
async def test_a_description_is_always_sent(handler):
    """Optional in the spec, required in practice.

    Verified live: three backups requested without a description were accepted
    with HTTP 200 and a `backupId`, then failed asynchronously with "An error
    occurred when communicating with system" and vanished from every listing.
    The one sent with a description completed. The Portal never sends an empty
    one -- it generates "Manual created" -- so neither does this DTO.
    """
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backups/create").mock(
        return_value=httpx.Response(200, json=envelope({"backupId": "bk-3333", "success": True}))
    )
    await handler.create_relational_backup(
        spec=CreateBackupDto(dbInstanceId="db-1111", name="no-description-given")
    )
    body = json.loads(route.calls.last.request.content)
    assert body["description"] == "Manual created"


def test_an_empty_description_is_rejected_locally():
    """Sending "" is the failure the default exists to prevent."""
    with pytest.raises(pydantic.ValidationError):
        CreateBackupDto(dbInstanceId="db-1111", name="x", description="")


def test_create_documents_the_one_backup_at_a_time_rule():
    """Verified live: a concurrent request is accepted with 200, then fails.

    `Cannot perform action CREATE_BACKUP, current database action is
    CREATE_BACKUP` -- reported only in the instance history, never at the call
    site, so the docstring has to warn before the fact.
    """
    from greennode.vdb_mcp_server.relational_backup_handler import RelationalBackupHandler

    doc = " ".join(RelationalBackupHandler.create_relational_backup.__doc__.split())
    assert "Only one backup action is allowed at a time" in doc
    assert "current database action is CREATE_BACKUP" in doc
    assert "list_relational_instance_histories" in doc


def test_a_caller_supplied_description_is_kept():
    spec = CreateBackupDto(
        dbInstanceId="db-1111", name="x", description="before the schema migration"
    )
    assert spec.description == "before the schema migration"


def test_incremental_backup_without_a_parent_is_rejected_locally():
    """The API would answer a bare `in_valid`, naming no field."""
    with pytest.raises(pydantic.ValidationError, match="parentId is required"):
        CreateBackupDto(dbInstanceId="db-1111", name="delta", backupType="INCREMENTAL")


def test_full_backup_with_a_parent_is_rejected_locally():
    with pytest.raises(pydantic.ValidationError, match="only applies to an INCREMENTAL"):
        CreateBackupDto(dbInstanceId="db-1111", name="full", backupType="FULL", parentId="bk-1111")


def test_incremental_backup_with_a_parent_is_accepted():
    spec = CreateBackupDto(
        dbInstanceId="db-1111", name="delta", backupType="INCREMENTAL", parentId="bk-1111"
    )
    assert spec.parentId == "bk-1111"


def test_an_unknown_backup_type_is_rejected():
    """One of the few closed sets in the API -- the spec names both values."""
    with pytest.raises(pydantic.ValidationError):
        CreateBackupDto(dbInstanceId="db-1111", name="x", backupType="DIFFERENTIAL")


def test_create_dto_forbids_unknown_fields():
    with pytest.raises(pydantic.ValidationError):
        CreateBackupDto(dbInstanceId="db-1111", name="x", retention=7)


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_restore_sends_the_backup_specific_envelope(handler):
    """`restore_backup` + `resourceType: dbaas-backup` -- neither matches the
    lifecycle actions' `resType: dbaas`, and a wrong value returns 200 with
    nothing applied."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backups/bk-1111/restore").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.restore_relational_backup(
        backup_id="bk-1111", spec=_restore_spec(), user_type="IAM_USER"
    )
    assert isinstance(result, OrderData)
    assert result.orders[0].resource_id == "db-9999"
    assert result.instance_name == "restored-db"

    body = json.loads(route.calls.last.request.content)
    assert body["action"] == "restore_backup"
    assert body["resourceType"] == "dbaas-backup"
    assert "resType" not in body
    assert body["databaseInstances"][0]["config"]["name"] == "restored-db"


@respx.mock
@pytest.mark.asyncio
async def test_restore_injects_the_path_backup_id_into_the_config(handler):
    """The API wants the id twice; the caller supplies it once so they agree."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backups/bk-1111/restore").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    await handler.restore_relational_backup(
        backup_id="bk-1111", spec=_restore_spec(), user_type="IAM_USER"
    )
    body = json.loads(route.calls.last.request.content)
    assert body["databaseInstances"][0]["config"]["backupId"] == "bk-1111"


def test_restore_spec_has_no_backup_id_of_its_own():
    """Two sources for one value is two chances to disagree."""
    assert "backupId" not in RestoreRelationalBackupDto.model_fields


@respx.mock
@pytest.mark.asyncio
async def test_restore_defaults_to_the_auto_payment_flow(handler):
    """ROOT_USER also answers 200 but provisions nothing."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/backups/bk-1111/restore").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    await handler.restore_relational_backup(
        backup_id="bk-1111", spec=_restore_spec(), user_type="IAM_USER"
    )
    assert route.calls.last.request.headers["user-type"] == "IAM_USER"


@pytest.mark.asyncio
async def test_restore_dryrun_previews_without_calling_the_api(handler):
    """No respx routes are registered: any HTTP call would fail the test."""
    result = await handler.restore_relational_backup_dryrun(
        backup_id="bk-1111", spec=_restore_spec()
    )
    assert isinstance(result, DryRunData)
    assert result.tool == "restore_relational_backup"
    assert result.path == "/v1/backups/bk-1111/restore"
    assert result.user_type == "IAM_USER"
    assert result.body["resourceType"] == "dbaas-backup"
    assert result.body["databaseInstances"][0]["config"]["backupId"] == "bk-1111"
    assert any("BILLABLE" in w for w in result.warnings)
    assert any("untouched" in w for w in result.warnings), (
        "the caller must not read 'restore' as 'roll back'"
    )


def test_restore_spec_rejects_a_volume_below_the_api_minimum():
    with pytest.raises(pydantic.ValidationError):
        _restore_spec(volumeSize=10)


def test_restore_spec_requires_at_least_one_subnet():
    with pytest.raises(pydantic.ValidationError):
        _restore_spec(netIds=[])


def test_restore_spec_normalises_the_engine_spelling():
    assert _restore_spec(datastoreType="mysql").datastoreType == "MySQL"


def test_restore_spec_forbids_unknown_fields():
    with pytest.raises(pydantic.ValidationError):
        _restore_spec(user="admin")


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_delete_sends_a_json_array_body_on_a_delete_request(handler):
    """One of six endpoints whose body is an array rather than an object."""
    mock_iam(respx.mock)
    route = respx.delete(f"{RELATIONAL}/v1/backups/bk-1111/delete").mock(
        return_value=httpx.Response(200, json=DELETE_RESPONSE)
    )
    result = await handler.delete_relational_backup(backup_id="bk-1111")
    assert isinstance(result, BackupDeleteData)
    assert result.accepted is True
    assert result.results[0].backup_id == "bk-1111"
    assert result.results[0].success is True

    body = json.loads(route.calls.last.request.content)
    assert body == [{"backupId": "bk-1111"}], "an array, and the path id repeated inside it"


@respx.mock
@pytest.mark.asyncio
async def test_delete_treats_an_empty_result_as_not_deleted(handler):
    """200 with no per-backup result means silently ignored, not queued."""
    mock_iam(respx.mock)
    respx.delete(f"{RELATIONAL}/v1/backups/bk-1111/delete").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.delete_relational_backup(backup_id="bk-1111")
    assert result.accepted is False
    assert result.warning and "NOT deleted" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_a_404_carried_inside_a_200(handler):
    """Observed live: deleting an id that does not exist returns HTTP 200 whose
    body says `success: false, code: 404`. The HTTP status describes the call,
    not the deletion."""
    mock_iam(respx.mock)
    respx.delete(f"{RELATIONAL}/v1/backups/bk-gone/delete").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "backupId": "bk-gone",
                        "action": None,
                        "status": None,
                        "success": False,
                        "errorMsg": "Resource not found",
                        "code": 404,
                    }
                ]
            ),
        )
    )
    result = await handler.delete_relational_backup(backup_id="bk-gone")
    assert result.accepted is False, "a 404 in the body is not a successful deletion"
    assert "Resource not found" in result.warning
    assert "404" in result.warning
    assert result.results[0].success is False


@respx.mock
@pytest.mark.asyncio
async def test_a_backup_that_does_not_exist_raises_rather_than_returning_a_blank(handler):
    """The API answers 200 with an empty payload instead of 404.

    Building a model from that hands the caller a backup whose every field is
    empty -- which an agent cannot tell apart from a real one.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/detail/bk-gone").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )
    with pytest.raises(ValueError, match="No backup with id"):
        await handler.get_relational_backup(backup_id="bk-gone")


@respx.mock
@pytest.mark.asyncio
async def test_a_missing_backup_points_at_the_history_tool(handler):
    """A backup the platform failed to build vanishes; only the history records why."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/backups/detail/bk-gone").mock(
        return_value=httpx.Response(200, json=envelope(None))
    )
    with pytest.raises(ValueError, match="list_relational_instance_histories"):
        await handler.get_relational_backup(backup_id="bk-gone")


def test_create_warns_that_acceptance_is_not_completion():
    """`success: true` describes the request, not the backup."""
    from greennode.vdb_mcp_server.relational_backup_handler import CREATE_NEXT_STEP

    assert "NOT the backup being made" in CREATE_NEXT_STEP
    assert "list_relational_instance_histories" in CREATE_NEXT_STEP


@pytest.mark.asyncio
async def test_delete_validates_the_backup_id(handler):
    with pytest.raises(ValueError):
        await handler.delete_relational_backup(backup_id="bk-1111/../bk-2222")
