"""Tests for the MemoryStore (Redis) instance handler.

Fixtures mirror the shapes probed live on 2026-09-10 against `cli-redis-test`,
including the two details that most easily go unnoticed: a memory instance id
starts with `db-` exactly like a relational one, and the by-instance backup
listing returns null for the very fields a restore needs.
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
from greennode.vdb_mcp_server.memory_instance_handler import MemoryInstanceHandler
from greennode.vdb_mcp_server.models import (
    ActionData,
    CreateMemoryInstanceDto,
    CreateMemoryReplicaDto,
    DeleteMemoryInstanceDto,
    DryRunData,
    MemoryInstance,
    MemoryInstanceListData,
    ResizeMemoryInstanceDto,
    UpdateMemorySettingDto,
    UpdateSecurityRuleDto,
)
from mcp.server.mcpserver import MCPServer
from tests.helpers import MEMORY, content_list, envelope, mock_iam, nested_list, page_object


INSTANCE_ID = "db-5a4d26f1-1451-4a93-9d9a-72d117f98831"
PASSWORD = "Rediscachepass123"  # 17 chars: valid for memory, too long to be a relational habit


def _instance(instance_id=INSTANCE_ID, name="cli-redis-test", status="ACTIVE", **over):
    """A memory instance row as the live listing returns it."""
    row = {
        "id": instance_id,
        "name": name,
        "status": status,
        "datastoreType": "Redis",
        "datastoreVersion": "7.2",
        "vcpus": 4,
        "ram": 8,
        "volumeSize": 17,
        "volumeType": "Gen2-NVMe2-IOPS3000-HCM03-1B",
        "zoneId": "HCM03-1B",
        "subnetId": "sub-7cc39ad2-a00f-4edd-8e8b-5f5aaa3eeffe",
        "ip": ["172.24.0.10"],
        "port": 6379,
        "publicAccess": False,
        "redisPasswordEnabled": True,
        "backupAuto": True,
        "backupTime": "05:30",
        "backupDuration": 4,
        "configuration": {"id": None, "name": None},
        "configId": None,
        "replicaSourceId": None,
        "created": "2026-08-13 15:13:43.0",
        "updated": "2026-08-13 16:10:39.0",
        # present upstream, deliberately dropped by the projection
        "cost": 1234,
        "packageName": "db.s-general-4x8",
        "enableAutoRenew": False,
    }
    row.update(over)
    return row


ORDER_RESPONSE = envelope(
    [
        {
            "orderUrl": "https://vdb.console.vngcloud.vn/memorystore/database",
            "orderId": "65c00881",
            "resourceId": INSTANCE_ID,
        }
    ]
)

ACTION_RESPONSE = envelope(
    [
        {
            "databaseInstances": INSTANCE_ID,
            "action": "start",
            "status": "PROCESSING",
            "success": True,
            "errorMsg": None,
            "code": 200,
        }
    ]
)

# The live by-instance listing: an index entry, not a description.
THIN_BACKUP = {
    "id": "bk-5f34c2a4-06d0-4fe1-862b-93225748d309",
    "name": "mds_db_5a4d26f1_260907053000+0700",
    "description": "Auto backup daily",
    "dbInstanceId": None,
    "instanceName": None,
    "type": "AUTO_DAILY",
    "backupType": "FULL",
    "status": "COMPLETED",
    "datastoreType": "redis",
    "datastoreVersion": "7.2",
    "storageSize": None,
    "packageId": None,
    "netIds": None,
    "backupTier": "FREE",
    "size": 0.022,
    "created": "2026-09-07 05:30:07",
}


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return MemoryInstanceHandler(MCPServer("test"), config, client, allow_write=allow_write)


@pytest.fixture
def handler(sample_config):
    return _handler(sample_config, allow_write=True)


@pytest.fixture
def readonly_handler(sample_config):
    return _handler(sample_config, allow_write=False)


def _valid_create_spec(**over):
    payload = {
        "name": "mcpmem1a",
        "datastoreType": "Redis",
        "datastoreVersion": "7.2",
        "packageId": "139",
        "netIds": ["sub-aaaa"],
        "locateZoneId": "HCM03-1A",
        "redisPassword": PASSWORD,
    }
    payload.update(over)
    return CreateMemoryInstanceDto(**payload)


# --------------------------------------------------------------------------
# registration and write gating
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_are_absent_in_read_only_mode(readonly_handler):
    tools = {t.name for t in await readonly_handler.mcp.list_tools()}
    assert "list_memory_instances" in tools
    assert "delete_memory_instance_dryrun" in tools, "previews stay available"
    for name in (
        "create_memory_instance",
        "delete_memory_instance",
        "start_memory_instance",
        "update_memory_instance_setting",
        "update_memory_instance_secrules",
    ):
        assert name not in tools


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    expected = {
        "list_memory_instances",
        "get_memory_instance",
        "list_memory_instance_histories",
        "list_memory_instance_replicas",
        "list_memory_instance_secrules",
        "list_memory_instance_backups",
        "create_memory_instance_dryrun",
        "resize_memory_instance_dryrun",
        "create_memory_instance_replicas_dryrun",
        "delete_memory_instance_dryrun",
        "create_memory_instance",
        "start_memory_instance",
        "stop_memory_instance",
        "reboot_memory_instance",
        "resize_memory_instance",
        "create_memory_instance_replicas",
        "detach_memory_instance_replica",
        "update_memory_instance_setting",
        "update_memory_instance_config_group",
        "update_memory_instance_secrules",
        "delete_memory_instance",
    }
    assert expected <= tools
    assert len(expected) == 21


@pytest.mark.asyncio
async def test_write_helpers_refuse_without_allow_write(readonly_handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.stop_memory_instance(INSTANCE_ID)
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.create_memory_instance(_valid_create_spec(), "IAM_USER")


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_instances_projects_and_pages(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/database-instances").mock(
        return_value=httpx.Response(
            200, json=nested_list([_instance()], page_object(number=1, size=20, total_elements=1))
        )
    )

    result = await handler.list_memory_instances(None, None, 1, 20)

    assert isinstance(result, MemoryInstanceListData)
    assert result.count == 1
    assert result.page.page == 1 and result.page.total_items == 1
    item = result.items[0]
    assert item.id == INSTANCE_ID
    assert item.datastore_type == "Redis"
    assert item.ram_gb == 8
    assert item.port == 6379
    assert item.redis_password_enabled is True
    assert item.zone_id == "HCM03-1B"
    assert item.status_kind == "settled"
    assert item.status_guidance
    assert "cost" not in item.model_dump(), "billing fields stay out of the projection"
    assert route.calls[0].request.url.params["pageNumber"] == "1"


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_instances_has_no_approximate_filter_caveat(handler):
    """The relational listing mixes families; this one does not, so no caveat."""
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database-instances").mock(
        return_value=httpx.Response(200, json=nested_list([], page_object(total_elements=0)))
    )

    result = await handler.list_memory_instances("zzz-nope", None, 1, 20)

    assert result.count == 0
    assert not hasattr(result, "filters_are_approximate")
    assert not hasattr(result, "postgresql_clusters_excluded")


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_instances_passes_filters_flat_and_repeats_status(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/database-instances").mock(
        return_value=httpx.Response(200, json=nested_list([], page_object()))
    )

    await handler.list_memory_instances("cache", ["ACTIVE", "BUILDING"], 2, 50)

    params = route.calls[0].request.url.params
    assert params["name"] == "cache"
    assert params.get_list("status") == ["ACTIVE", "BUILDING"]
    assert params["pageNumber"] == "2"


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_instance_uses_the_memory_path(handler):
    """Memory reads /database-instances/{id}; relational reads /database-instances/id/{id}."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}").mock(
        return_value=httpx.Response(200, json=envelope(_instance()))
    )

    result = await handler.get_memory_instance(INSTANCE_ID)

    assert isinstance(result, MemoryInstance)
    assert result.id == INSTANCE_ID
    assert route.called
    assert "/id/" not in str(route.calls[0].request.url)


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_instance_raises_on_an_empty_payload(handler):
    """A 200 with nothing in it must not become an instance whose every field is ''."""
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )

    with pytest.raises(ValueError, match="get_relational_instance"):
        await handler.get_memory_instance(INSTANCE_ID)


@pytest.mark.asyncio
async def test_get_memory_instance_validates_the_id(handler):
    with pytest.raises(ValueError, match="Invalid instance_id"):
        await handler.get_memory_instance("../../etc/passwd")


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_instance_histories_reads_the_content_key(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/histories").mock(
        return_value=httpx.Response(
            200,
            json=content_list(
                [
                    {
                        "id": 24081,
                        "instanceId": INSTANCE_ID,
                        "action": "Update",
                        "status": "Finished",
                        "description": "Update database with following changes: "
                        "Change redis password",
                        "errorMessage": "",
                        "createdTime": "2026-08-13T09:10:40.000+00:00",
                        "updatedTime": "2026-08-13T09:10:40.000+00:00",
                    }
                ],
                page_object(),
            ),
        )
    )

    result = await handler.list_memory_instance_histories(INSTANCE_ID, 1, 20)

    assert result.instance_id == INSTANCE_ID
    assert result.count == 1
    assert result.items[0].action == "Update"
    assert result.items[0].status == "Finished"


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_instance_replicas_reads_a_bare_array(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/replicas").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )

    result = await handler.list_memory_instance_replicas(INSTANCE_ID)

    assert result.source_instance_id == INSTANCE_ID
    assert result.count == 0
    assert result.items == []


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_instance_secrules(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/secrules").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "id": "429f68e6",
                        "direction": "ingress",
                        "etherType": "IPv4",
                        "protocol": "tcp",
                        "portRangeMin": 6379,
                        "portRangeMax": 6379,
                        "remoteIpPrefix": "0.0.0.0/0",
                        "status": "ACTIVE",
                        "createdAt": "2026-08-13T15:13:43.000+00:00",
                    }
                ]
            ),
        )
    )

    result = await handler.list_memory_instance_secrules(INSTANCE_ID)

    assert result.count == 1
    assert result.items[0].port_range_min == 6379
    assert result.items[0].remote_ip_prefix == "0.0.0.0/0"


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_instance_backups_marks_rows_as_summaries(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/backups").mock(
        return_value=httpx.Response(200, json=envelope([THIN_BACKUP]))
    )

    result = await handler.list_memory_instance_backups(INSTANCE_ID)

    assert result.instance_id == INSTANCE_ID
    assert result.count == 1
    assert result.rows_are_summaries is True, (
        "the row leaves instance_id, sizing and network fields null -- callers must be told"
    )
    backup = result.items[0]
    assert backup.id == THIN_BACKUP["id"]
    assert backup.instance_id == "", "null upstream on this endpoint"
    assert backup.storage_size_gb is None
    assert backup.datastore_type == "redis", "lowercased here, capitalised in the detail"
    assert backup.origin == "AUTO_DAILY"
    assert backup.tier == "FREE"


# --------------------------------------------------------------------------
# DTO validation -- the local defence against a bare `in_valid`
# --------------------------------------------------------------------------


def test_create_dto_enforces_the_memory_password_length():
    """8-32 is the relational rule; memory needs 16-128."""
    with pytest.raises(pydantic.ValidationError, match="16-128"):
        _valid_create_spec(redisPassword="Short1_ok")


def test_create_dto_rejects_a_password_with_a_disallowed_character():
    with pytest.raises(pydantic.ValidationError, match="16-128"):
        _valid_create_spec(redisPassword="Redis!cachepass123")


def test_create_dto_requires_a_password_when_the_password_is_enabled():
    with pytest.raises(pydantic.ValidationError, match="redisPassword is required"):
        _valid_create_spec(redisPassword=None)


def test_create_dto_refuses_public_access_without_a_password():
    with pytest.raises(pydantic.ValidationError, match="publicAccess requires"):
        _valid_create_spec(publicAccess=True, redisPasswordEnabled=False, redisPassword=None)


def test_create_dto_forbids_relational_only_fields():
    """extra=forbid turns a copied relational payload into a named error."""
    for field, value in (("volumeType", "Gen2-NVMe2-IOPS3000"), ("volumeSize", 20)):
        with pytest.raises(pydantic.ValidationError, match=field):
            _valid_create_spec(**{field: value})


def test_create_dto_normalises_the_engine_spelling():
    assert _valid_create_spec(datastoreType="redis").datastoreType == "Redis"


def test_create_dto_bounds_backup_retention_in_days():
    with pytest.raises(pydantic.ValidationError):
        _valid_create_spec(backupAuto=True, backupDuration=1)
    assert _valid_create_spec(backupAuto=True, backupDuration=2).backupDuration == 2


def test_update_setting_dto_requires_the_edit_flag_to_change_a_password():
    with pytest.raises(pydantic.ValidationError, match="editRedisPassword must be true"):
        UpdateMemorySettingDto(redisPassword=PASSWORD)
    with pytest.raises(pydantic.ValidationError, match="editRedisPassword must be true"):
        UpdateMemorySettingDto(redisPasswordEnabled=False)

    ok = UpdateMemorySettingDto(
        editRedisPassword=True, redisPasswordEnabled=True, redisPassword=PASSWORD
    )
    assert ok.editRedisPassword is True


def test_update_setting_dto_allows_non_password_changes_without_the_flag():
    spec = UpdateMemorySettingDto(backupAuto=True, backupDuration=7, backupTime="05:30")
    assert spec.editRedisPassword is False


def test_replica_dto_has_no_password_or_volume_fields():
    for field, value in (
        ("redisPassword", PASSWORD),
        ("volumeSize", 20),
        ("volumeType", "Gen2-NVMe2-IOPS3000"),
    ):
        with pytest.raises(pydantic.ValidationError, match=field):
            CreateMemoryReplicaDto(name="r1", packageId="139", **{field: value})


# --------------------------------------------------------------------------
# dry runs
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_dryrun_sends_nothing_and_names_the_costs(handler):
    mock_iam(respx.mock)
    result = await handler.create_memory_instance_dryrun(_valid_create_spec())

    assert isinstance(result, DryRunData)
    assert result.tool == "create_memory_instance"
    assert result.path == "/v1/payment/database-instances"
    assert result.service == "vdb-memory"
    assert result.user_type == "IAM_USER"
    assert result.body["redisPassword"] == PASSWORD
    assert any("BILLABLE" in w for w in result.warnings)
    assert any("0.0.0.0/0" in w for w in result.warnings)
    assert not respx.mock.calls, "a dry run must not touch the API"


@pytest.mark.asyncio
async def test_resize_dryrun_uses_the_resourceType_spelling(handler):
    result = await handler.resize_memory_instance_dryrun(
        INSTANCE_ID, ResizeMemoryInstanceDto(packageId="240")
    )

    assert result.path == f"/v1/database-instances/{INSTANCE_ID}/resize-instance"
    assert result.body["action"] == "resize"
    assert result.body["resourceType"] == "dbaas", "resize spells the key resourceType"
    assert "resType" not in result.body
    assert result.body["databaseInstances"][0]["config"]["packageId"] == "240"


@pytest.mark.asyncio
async def test_replica_dryrun_injects_the_source_id(handler):
    result = await handler.create_memory_instance_replicas_dryrun(
        INSTANCE_ID, CreateMemoryReplicaDto(name="replica-1", packageId="139")
    )

    assert result.body["replicaSourceId"] == INSTANCE_ID
    assert result.path.endswith("/create-replicas")


@pytest.mark.asyncio
async def test_delete_dryrun_escalates_the_warning_for_deleteAllBackup(handler):
    plain = await handler.delete_memory_instance_dryrun(INSTANCE_ID, None)
    assert plain.body["action"] == "delete"
    assert plain.body["resType"] == "dbaas", "delete spells the key resType"
    assert plain.user_type is None
    assert any("final backup" in w for w in plain.warnings)

    destructive = await handler.delete_memory_instance_dryrun(
        INSTANCE_ID, DeleteMemoryInstanceDto(deleteAllBackup=True)
    )
    assert "every backup" in destructive.warnings[0]


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_memory_instance_sends_the_user_type_header(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/payment/database-instances").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    result = await handler.create_memory_instance(_valid_create_spec(), "IAM_USER")

    assert route.calls[0].request.headers["user-type"] == "IAM_USER"
    body = json.loads(route.calls[0].request.content)
    assert body["datastoreType"] == "Redis"
    assert "volumeSize" not in body, "the memory create body has no volume fields"
    assert result.orders[0].resource_id == INSTANCE_ID
    assert result.instance_name == "mcpmem1a"
    assert "asynchronously" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_stop_sends_action_stop_not_shutdown(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/shutdown").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )

    result = await handler.stop_memory_instance(INSTANCE_ID)

    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "stop", "the path says shutdown; the payload must say stop"
    assert body["resType"] == "dbaas"
    assert body["databaseInstances"] == [{"instancesId": INSTANCE_ID}]
    assert isinstance(result, ActionData)
    assert result.accepted is True
    assert "user-type" not in route.calls[0].request.headers


@respx.mock
@pytest.mark.asyncio
async def test_start_and_reboot_send_their_own_action(handler):
    mock_iam(respx.mock)
    start = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/start").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    reboot = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/reboot").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )

    await handler.start_memory_instance(INSTANCE_ID)
    await handler.reboot_memory_instance(INSTANCE_ID)

    assert json.loads(start.calls[0].request.content)["action"] == "start"
    assert json.loads(reboot.calls[0].request.content)["action"] == "reboot"


@respx.mock
@pytest.mark.asyncio
async def test_detach_replica_sends_an_underscored_action(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/detach-replica").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )

    await handler.detach_memory_instance_replica(INSTANCE_ID)

    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "detach_replica", "underscore, not the hyphen from the path"


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], None], ids=["empty-array", "null"])
async def test_an_empty_result_is_reported_as_not_applied(handler, payload):
    """A wrong action/resType answers 200 and changes nothing.

    Measured live: the relational endpoints answer `data: []` and the memory
    ones `data: null` for the same mistake, so both have to read as "not
    applied" rather than one of them slipping through as success.
    """
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/start").mock(
        return_value=httpx.Response(200, json=envelope(payload))
    )

    result = await handler.start_memory_instance(INSTANCE_ID)

    assert result.accepted is False
    assert result.warning and "NOT applied" in result.warning
    assert "list_memory_instance_histories" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_resize_memory_instance_orders_with_the_resize_envelope(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/resize-instance").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    await handler.resize_memory_instance(
        INSTANCE_ID, ResizeMemoryInstanceDto(packageId="240"), "IAM_USER"
    )

    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "resize"
    assert body["resourceType"] == "dbaas"
    assert route.calls[0].request.headers["user-type"] == "IAM_USER"


@respx.mock
@pytest.mark.asyncio
async def test_create_replicas_orders_with_the_source_id_in_the_body(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/create-replicas").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    result = await handler.create_memory_instance_replicas(
        INSTANCE_ID, CreateMemoryReplicaDto(name="replica-1", packageId="139"), "IAM_USER"
    )

    body = json.loads(route.calls[0].request.content)
    assert body["replicaSourceId"] == INSTANCE_ID
    assert result.instance_name == "replica-1"


@respx.mock
@pytest.mark.asyncio
async def test_update_setting_uses_the_hyphenated_memory_path(handler):
    """Memory: /update-setting. Relational: /update/setting."""
    mock_iam(respx.mock)
    route = respx.put(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/update-setting").mock(
        return_value=httpx.Response(200, json=envelope({"status": 202}))
    )

    result = await handler.update_memory_instance_setting(
        INSTANCE_ID,
        UpdateMemorySettingDto(
            editRedisPassword=True, redisPasswordEnabled=True, redisPassword=PASSWORD
        ),
    )

    body = json.loads(route.calls[0].request.content)
    assert body["dbInstanceId"] == INSTANCE_ID
    assert body["editRedisPassword"] is True
    assert body["redisPassword"] == PASSWORD
    assert result.applied is True
    assert result.instance_id == INSTANCE_ID


@respx.mock
@pytest.mark.asyncio
async def test_update_setting_does_not_surface_the_response_body(handler):
    """The spec describes no response here; the relational twin swaps two ids."""
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/update-setting").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                {"status": 202, "dbInstanceId": "pro-2b233cab", "projectId": INSTANCE_ID}
            ),
        )
    )

    result = await handler.update_memory_instance_setting(
        INSTANCE_ID, UpdateMemorySettingDto(backupAuto=False)
    )

    dumped = result.model_dump()
    assert "pro-2b233cab" not in json.dumps(dumped)
    assert dumped["instance_id"] == INSTANCE_ID


@respx.mock
@pytest.mark.asyncio
async def test_update_config_group_attaches_and_detaches(handler):
    mock_iam(respx.mock)
    route = respx.put(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/update-config-group").mock(
        return_value=httpx.Response(200, json=envelope({"status": 202}))
    )

    await handler.update_memory_instance_config_group(INSTANCE_ID, "cfg-b634172f")
    assert json.loads(route.calls[0].request.content)["configId"] == "cfg-b634172f"

    await handler.update_memory_instance_config_group(INSTANCE_ID, "")
    assert json.loads(route.calls[1].request.content)["configId"] is None, (
        "the caller says '' but the wire needs null -- the spec's empty string is rejected "
        "as in_valid, verified live on an idle instance with a group attached"
    )


@pytest.mark.asyncio
async def test_update_config_group_still_rejects_a_malformed_id(handler):
    with pytest.raises(ValueError, match="Invalid config_id"):
        await handler.update_memory_instance_config_group(INSTANCE_ID, "../../secrets")


@respx.mock
@pytest.mark.asyncio
async def test_update_secrules_sends_a_json_array(handler):
    mock_iam(respx.mock)
    route = respx.put(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/secrules").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "id": "429f68e6",
                        "direction": "ingress",
                        "portRangeMin": 6379,
                        "portRangeMax": 6379,
                        "remoteIpPrefix": "10.0.0.0/8",
                        "status": "ACTIVE",
                    }
                ]
            ),
        )
    )

    result = await handler.update_memory_instance_secrules(
        INSTANCE_ID,
        [
            UpdateSecurityRuleDto(
                id="429f68e6", portRangeMin=6379, portRangeMax=6379, remoteIpPrefix="10.0.0.0/8"
            )
        ],
    )

    body = json.loads(route.calls[0].request.content)
    assert isinstance(body, list), "this endpoint takes an array, not an object"
    assert body[0]["remoteIpPrefix"] == "10.0.0.0/8"
    assert result.items[0].remote_ip_prefix == "10.0.0.0/8"


@respx.mock
@pytest.mark.asyncio
async def test_delete_memory_instance_carries_the_backup_options(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/delete").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )

    await handler.delete_memory_instance(
        INSTANCE_ID, DeleteMemoryInstanceDto(createFinalBackup=True)
    )

    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "delete"
    assert body["resType"] == "dbaas"
    assert body["databaseInstances"][0]["config"] == {
        "createFinalBackup": True,
        "deleteAllBackup": False,
    }


@respx.mock
@pytest.mark.asyncio
async def test_delete_defaults_keep_every_backup(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}/delete").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )

    await handler.delete_memory_instance(INSTANCE_ID, None)

    config = json.loads(route.calls[0].request.content)["databaseInstances"][0]["config"]
    assert config == {"createFinalBackup": False, "deleteAllBackup": False}


# --------------------------------------------------------------------------
# family separation
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_every_call_goes_to_the_memory_service(handler):
    """A relational path would still resolve some of these ids -- it must not be used."""
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database-instances/{INSTANCE_ID}").mock(
        return_value=httpx.Response(200, json=envelope(_instance()))
    )

    await handler.get_memory_instance(INSTANCE_ID)

    hosts = [str(call.request.url) for call in respx.mock.calls if "vdb-" in str(call.request.url)]
    assert hosts and all("/vdb-memory/" in url for url in hosts)


def test_a_memory_id_looks_exactly_like_a_relational_id():
    """Documented in the model, asserted here so it is not 'fixed' by prefix filtering."""
    assert INSTANCE_ID.startswith("db-")
    assert MemoryInstance.from_api(_instance()).id.startswith("db-")
