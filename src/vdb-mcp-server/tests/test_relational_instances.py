"""Tests for the relational instance handler.

Fixtures mirror the live shapes probed on 2026-09-08, including the create
response, whose `orderId` and `resourceId` really do come back null.
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
    ActionData,
    CreateRelationalInstanceDto,
    DeleteRelationalInstanceDto,
    DryRunData,
    RelationalInstance,
    RelationalInstanceListData,
    RelationalUserDto,
    ResizeRelationalStorageDto,
    UpdateRelationalSettingDto,
    UpdateSecurityRuleDto,
)
from greennode.vdb_mcp_server.relational_instance_handler import RelationalInstanceHandler
from mcp.server.mcpserver import MCPServer
from tests.helpers import RELATIONAL, content_list, envelope, mock_iam, nested_list, page_object


def _instance(instance_id="db-1111", name="app-db", status="ACTIVE", zone="HCM03-1A", **over):
    row = {
        "id": instance_id,
        "name": name,
        "status": status,
        "datastoreType": "MySQL",
        "datastoreVersion": "8.0",
        "vcpus": 2,
        "ram": 4,
        "volumeSize": 40,
        "volumeType": "Gen2-NVMe2-IOPS3000",
        "zoneId": zone,
        "subnetId": "sub-aaaa",
        "ip": ["172.24.0.9", "61.28.228.29"],
        "port": 3306,
        "publicAccess": True,
        "backupAuto": True,
        "backupTime": "00:00",
        "backupDuration": 2,
        "configuration": {"id": "cfg-1", "name": "tf-mysql8"},
        "replicaSourceId": None,
        "created": "2026-08-13 12:40:25.0",
        "updated": "2026-08-18 15:20:40.0",
        # fields present upstream that the projection deliberately drops
        "cost": 1234,
        "packageName": "db.s-general-2x4",
        "enableAutoRenew": False,
    }
    row.update(over)
    return row


def _cluster(cluster_id="pg-2222", name="pg-cluster"):
    return _instance(cluster_id, name, dbBackendId=None, deployType="cluster", numberOfNodes=3)


ORDER_RESPONSE = envelope(
    [
        {
            "orderUrl": "https://payment.console.vngcloud.vn/orders/65c00881",
            "orderId": None,
            "resourceId": None,
        }
    ]
)

ACTION_RESPONSE = envelope(
    [
        {
            "databaseInstances": "db-1111",
            "action": "start",
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
    return RelationalInstanceHandler(MCPServer("test"), config, client, allow_write=allow_write)


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
    assert "list_relational_instances" in tools
    assert "delete_relational_instance_dryrun" in tools, "previews stay available"
    for name in (
        "create_relational_instance",
        "delete_relational_instance",
        "start_relational_instance",
        "update_relational_instance_setting",
    ):
        assert name not in tools


@pytest.mark.asyncio
async def test_write_mode_registers_the_write_tools(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    for name in (
        "create_relational_instance",
        "start_relational_instance",
        "stop_relational_instance",
        "reboot_relational_instance",
        "resize_relational_instance",
        "resize_relational_instance_storage",
        "create_relational_instance_replicas",
        "detach_relational_instance_replica",
        "update_relational_instance_setting",
        "update_relational_instance_config_group",
        "update_relational_instance_secrules",
        "delete_relational_instance",
    ):
        assert name in tools


@pytest.mark.asyncio
async def test_dryrun_tools_are_annotated_read_only(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    for name in tools:
        if name.endswith("_dryrun"):
            assert tools[name].annotations.read_only_hint is True, name


@pytest.mark.asyncio
async def test_delete_is_annotated_destructive(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    assert tools["delete_relational_instance"].annotations.destructive_hint is True
    assert tools["stop_relational_instance"].annotations.destructive_hint is False


@pytest.mark.asyncio
async def test_write_methods_refuse_when_write_is_disabled(readonly_handler):
    """Second line of defence: a direct call must still be refused."""
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.start_relational_instance(instance_id="db-1111")


# --------------------------------------------------------------------------
# the mixed listing
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_listing_drops_postgresql_clusters_and_counts_them(handler):
    """The endpoint is shared with the PostgreSQL Cluster family."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(
            200,
            json=nested_list(
                [_cluster(), _instance("db-1111"), _instance("db-2222", "other")],
                page_object(total_elements=3),
            ),
        )
    )
    result = await handler.list_relational_instances(
        name=None, statuses=None, page=1, page_size=20
    )
    assert isinstance(result, RelationalInstanceListData)
    assert [i.id for i in result.items] == ["db-1111", "db-2222"]
    assert result.count == 2
    assert result.postgresql_clusters_excluded == 1
    assert result.page.total_items == 3, "the API's total still counts the pg- row"


@respx.mock
@pytest.mark.asyncio
async def test_listing_flags_filters_as_approximate_only_when_filtering(handler):
    """The API does not apply name/status filters to the pg- rows it mixes in."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(200, json=nested_list([_instance()], page_object()))
    )
    unfiltered = await handler.list_relational_instances(
        name=None, statuses=None, page=1, page_size=20
    )
    assert unfiltered.filters_are_approximate is False

    filtered = await handler.list_relational_instances(
        name="app", statuses=["ACTIVE", "BUILDING"], page=1, page_size=20
    )
    assert filtered.filters_are_approximate is True
    params = route.calls.last.request.url.params
    assert params["name"] == "app"
    assert params.get_list("status") == ["ACTIVE", "BUILDING"], "status repeats the key"


@respx.mock
@pytest.mark.asyncio
async def test_listing_sends_one_based_paging(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(200, json=nested_list([], page_object()))
    )
    await handler.list_relational_instances(name=None, statuses=None, page=1, page_size=20)
    params = route.calls.last.request.url.params
    assert params["pageNumber"] == "1" and params["pageSize"] == "20"


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_get_instance_uses_the_id_path_segment(handler):
    """Relational is `/database-instances/id/{id}`; memory is `/database-instances/{id}`."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/id/db-1111").mock(
        return_value=httpx.Response(200, json=envelope(_instance()))
    )
    result = await handler.get_relational_instance(instance_id="db-1111")
    assert route.called
    assert isinstance(result, RelationalInstance)
    assert result.ip_addresses == ["172.24.0.9", "61.28.228.29"]
    assert result.config_group_id == "cfg-1"
    assert result.status_kind == "settled", "ACTIVE needs no follow-up"


@respx.mock
@pytest.mark.asyncio
async def test_projection_drops_billing_fields(handler):
    """A 68-field row must not reach the caller whole."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/id/db-1111").mock(
        return_value=httpx.Response(200, json=envelope(_instance()))
    )
    result = await handler.get_relational_instance(instance_id="db-1111")
    dumped = result.model_dump()
    for dropped in ("cost", "packageName", "enableAutoRenew"):
        assert dropped not in dumped


@pytest.mark.asyncio
async def test_reads_validate_the_instance_id(handler):
    """Every id reaching a URL goes through validate_id -- no path traversal."""
    with pytest.raises(ValueError):
        await handler.get_relational_instance(instance_id="../../secrets")


@respx.mock
@pytest.mark.asyncio
async def test_histories_read_the_content_key(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/db-1111/histories").mock(
        return_value=httpx.Response(
            200,
            json=content_list(
                [
                    {
                        "id": 1,
                        "instanceId": "db-1111",
                        "action": "RESIZE",
                        "status": "COMPLETED",
                        "description": "resize done",
                        "errorMessage": None,
                        "createdTime": "2026-09-08T00:00:00Z",
                        "updatedTime": "2026-09-08T00:05:00Z",
                    }
                ],
                page_object(total_elements=1),
            ),
        )
    )
    result = await handler.list_relational_instance_histories(
        instance_id="db-1111", page=1, page_size=20
    )
    assert result.count == 1
    assert result.items[0].action == "RESIZE"
    assert result.page.total_items == 1


@respx.mock
@pytest.mark.asyncio
async def test_replicas_read_an_array(handler):
    """The spec does not describe this response; live it is an array."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/db-1111/replicas").mock(
        return_value=httpx.Response(200, json=envelope([_instance("db-3333", "replica")]))
    )
    result = await handler.list_relational_instance_replicas(source_instance_id="db-1111")
    assert result.count == 1
    assert result.items[0].id == "db-3333"


@respx.mock
@pytest.mark.asyncio
async def test_no_replicas_is_an_empty_list_not_an_error(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/db-1111/replicas").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_relational_instance_replicas(source_instance_id="db-1111")
    assert result.count == 0


@respx.mock
@pytest.mark.asyncio
async def test_secrules_are_projected(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/db-1111/secrules").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "id": "sgr-1",
                        "direction": "ingress",
                        "etherType": "IPv4",
                        "protocol": "tcp",
                        "portRangeMin": 3306,
                        "portRangeMax": 3306,
                        "remoteIpPrefix": "10.0.0.0/8",
                        "status": "ACTIVE",
                        "createdAt": "2026-09-08",
                    }
                ]
            ),
        )
    )
    result = await handler.list_relational_instance_secrules(instance_id="db-1111")
    assert result.items[0].port_range_min == 3306
    assert result.items[0].remote_ip_prefix == "10.0.0.0/8"


# --------------------------------------------------------------------------
# the password rule the spec does not mention
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "Mcp!Test#2026",  # ! and # are not in the allowed set
        "McpTest 2026a",  # space
        "1McpTest2026",  # must start with a letter
        "McpTest2026_",  # must end alphanumeric
        "Mcp2026",  # 7 characters, one short
        "M" + "a" * 32,  # 33 characters, one over
        "",
    ],
)
def test_password_rule_rejects_what_the_api_rejects(password):
    """The API answers a bad password with a bare `in_valid` -- catch it locally."""
    with pytest.raises(pydantic.ValidationError, match="password must be"):
        RelationalUserDto(name="admin", password=password)


@pytest.mark.parametrize("password", ["McpTest2026aA", "Abcdefg1", "A$^_<>abc9"])
def test_password_rule_accepts_what_the_api_accepts(password):
    assert RelationalUserDto(name="admin", password=password).password == password


def test_update_setting_dto_applies_the_same_password_rule():
    with pytest.raises(pydantic.ValidationError, match="password must be"):
        UpdateRelationalSettingDto(password="bad!")
    assert UpdateRelationalSettingDto(password=None).password is None


def test_dtos_reject_unknown_fields():
    """`extra="forbid"` turns a typo into a named error instead of `in_valid`."""
    with pytest.raises(pydantic.ValidationError):
        ResizeRelationalStorageDto(volumeSize=40, volume_type="oops")


def test_create_dto_enforces_the_minimum_volume_size():
    with pytest.raises(pydantic.ValidationError):
        CreateRelationalInstanceDto(
            name="x",
            datastoreType="mysql",
            datastoreVersion="8.0",
            packageId="103",
            volumeType="Gen2-NVMe2-IOPS3000",
            volumeSize=10,
            netIds=["sub-a"],
            locateZoneId="HCM03-1A",
        )


def test_create_dto_requires_at_least_one_subnet():
    with pytest.raises(pydantic.ValidationError):
        CreateRelationalInstanceDto(
            name="x",
            datastoreType="mysql",
            datastoreVersion="8.0",
            packageId="103",
            volumeType="Gen2-NVMe2-IOPS3000",
            volumeSize=20,
            netIds=[],
            locateZoneId="HCM03-1A",
        )


# --------------------------------------------------------------------------
# dry runs
# --------------------------------------------------------------------------


def _create_spec(zone="HCM03-1A", volume_type="Gen2-NVMe2-IOPS3000"):
    return CreateRelationalInstanceDto(
        name="mcptest",
        datastoreType="mysql",
        datastoreVersion="8.0",
        packageId="103",
        volumeType=volume_type,
        volumeSize=20,
        netIds=["sub-aaaa"],
        locateZoneId=zone,
        user=RelationalUserDto(name="mcpadmin", password="McpTest2026aA"),
    )


@respx.mock
@pytest.mark.asyncio
async def test_create_dryrun_sends_no_request(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/payment/database-instances").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.create_relational_instance_dryrun(spec=_create_spec())
    assert isinstance(result, DryRunData)
    assert not route.called, "a dry run must not reach the API"
    assert result.body["name"] == "mcptest"
    assert result.body["locateZoneId"] == "HCM03-1A"
    assert result.user_type == "IAM_USER", "the preview must show the flow that would be used"
    assert any("BILLABLE" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_delete_dryrun_escalates_the_warning_for_delete_all_backup(handler):
    keep = await handler.delete_relational_instance_dryrun(
        instance_id="db-1111", options=DeleteRelationalInstanceDto()
    )
    assert not any("every backup" in w for w in keep.warnings)

    nuke = await handler.delete_relational_instance_dryrun(
        instance_id="db-1111", options=DeleteRelationalInstanceDto(deleteAllBackup=True)
    )
    assert "every backup" in nuke.warnings[0]


@pytest.mark.asyncio
async def test_delete_dryrun_shows_the_nested_action_envelope(handler):
    result = await handler.delete_relational_instance_dryrun(instance_id="db-1111", options=None)
    assert result.body == {
        "databaseInstances": [
            {
                "instancesId": "db-1111",
                "config": {"createFinalBackup": False, "deleteAllBackup": False},
            }
        ],
        "action": "delete",
        "resType": "dbaas",
    }


@pytest.mark.asyncio
async def test_resize_dryrun_uses_resourceType_not_resType(handler):
    """The two spellings are not interchangeable: resize uses `resourceType`."""
    result = await handler.resize_relational_instance_storage_dryrun(
        instance_id="db-1111", spec=ResizeRelationalStorageDto(volumeSize=60)
    )
    assert "resourceType" in result.body
    assert "resType" not in result.body


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_reports_an_order_not_an_instance(handler):
    """Live, `orderId` and `resourceId` come back null and no instance exists yet."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/payment/database-instances").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.create_relational_instance(spec=_create_spec(), user_type="IAM_USER")
    assert route.calls.last.request.headers["user-type"] == "IAM_USER"
    assert result.orders[0].order_url.startswith("https://payment.console.vngcloud.vn/")
    assert result.orders[0].resource_id == ""
    assert result.instance_name == "mcptest"
    assert "order" in result.next_step.lower()


@respx.mock
@pytest.mark.asyncio
async def test_billable_tools_default_to_auto_payment(handler):
    """ROOT_USER leaves the order unpaid and provisions nothing, so it is not the default."""
    from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE

    assert DEFAULT_USER_TYPE == "IAM_USER"
    for tool in await handler.mcp.list_tools():
        prop = (tool.input_schema.get("properties") or {}).get("user_type")
        if prop is not None:
            assert prop.get("default") == "IAM_USER", tool.name

    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/payment/database-instances").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    await handler.create_relational_instance(spec=_create_spec(), user_type="ROOT_USER")
    assert route.calls.last.request.headers["user-type"] == "ROOT_USER", (
        "the manual flow stays reachable, it is just not the default"
    )


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "path_suffix", "action"),
    [
        ("start_relational_instance", "start", "start"),
        ("stop_relational_instance", "shutdown", "stop"),
        ("reboot_relational_instance", "reboot", "reboot"),
        ("detach_relational_instance_replica", "detach-replica", "detach_replica"),
    ],
)
async def test_lifecycle_actions_send_the_nested_envelope(
    handler, method_name, path_suffix, action
):
    """The id travels in the path AND in the body, in a list, with the action repeated."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/database-instances/db-1111/{path_suffix}").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    result = await getattr(handler, method_name)(instance_id="db-1111")
    assert isinstance(result, ActionData)
    assert json.loads(route.calls.last.request.content) == {
        "databaseInstances": [{"instancesId": "db-1111"}],
        "action": action,
        "resType": "dbaas",
    }
    assert result.results[0].success is True
    assert "asynchronous" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_action_result_reports_acceptance_not_completion(handler):
    """A successful call means accepted, not applied -- the model must say so."""
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/database-instances/db-1111/start").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    result = await handler.start_relational_instance(instance_id="db-1111")
    assert result.results[0].status == "PROCESSING"
    assert "get_relational_instance" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_delete_sends_the_backup_options(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/database-instances/db-1111/delete").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    await handler.delete_relational_instance(
        instance_id="db-1111",
        options=DeleteRelationalInstanceDto(createFinalBackup=True, deleteAllBackup=False),
    )
    body = json.loads(route.calls.last.request.content)
    assert body["databaseInstances"][0]["config"] == {
        "createFinalBackup": True,
        "deleteAllBackup": False,
    }


@respx.mock
@pytest.mark.asyncio
async def test_delete_defaults_keep_backups(handler):
    """Omitting options must not silently destroy backups."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/database-instances/db-1111/delete").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    await handler.delete_relational_instance(instance_id="db-1111", options=None)
    body = json.loads(route.calls.last.request.content)
    assert body["databaseInstances"][0]["config"]["deleteAllBackup"] is False


@respx.mock
@pytest.mark.asyncio
async def test_resize_storage_sends_resource_type_spelling(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/database-instances/db-1111/resize-storage").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    await handler.resize_relational_instance_storage(
        instance_id="db-1111",
        spec=ResizeRelationalStorageDto(volumeSize=60, volumeType="Gen2-NVMe2-IOPS3000"),
        user_type="IAM_USER",
    )
    body = json.loads(route.calls.last.request.content)
    assert body["resourceType"] == "dbaas"
    assert body["databaseInstances"][0]["config"]["volumeSize"] == 60
    assert route.calls.last.request.headers["user-type"] == "IAM_USER"


@respx.mock
@pytest.mark.asyncio
async def test_update_setting_reports_success_from_the_http_status(handler):
    """The body echoes two id fields holding each other's values, so it is not surfaced."""
    mock_iam(respx.mock)
    route = respx.put(f"{RELATIONAL}/v1/database-instances/db-1111/update/setting").mock(
        return_value=httpx.Response(200, json=envelope({"ok": True}))
    )
    result = await handler.update_relational_instance_setting(
        instance_id="db-1111",
        spec=UpdateRelationalSettingDto(backupAuto=True, backupTime="02:00", backupDuration=2),
    )
    body = json.loads(route.calls.last.request.content)
    assert body["dbInstanceId"] == "db-1111"
    assert body["backupTime"] == "02:00"
    assert "password" not in body, "unset optional fields must not be sent as null"
    assert result.instance_id == "db-1111"
    assert result.applied is True


@respx.mock
@pytest.mark.asyncio
async def test_update_config_group_sends_both_ids(handler):
    mock_iam(respx.mock)
    route = respx.put(f"{RELATIONAL}/v1/database-instances/db-1111/update/config-group").mock(
        return_value=httpx.Response(200, json=envelope({"ok": True}))
    )
    await handler.update_relational_instance_config_group(instance_id="db-1111", config_id="cfg-1")
    assert json.loads(route.calls.last.request.content) == {
        "dbInstanceId": "db-1111",
        "configId": "cfg-1",
    }


@respx.mock
@pytest.mark.asyncio
async def test_update_config_group_detaches_with_a_null_not_an_empty_string(handler):
    """The spec says `""` detaches; the wire rejects it as in_valid and takes null.

    Measured on this endpoint: `configId: ""` comes back `400 in_valid`. The
    memory endpoint shares the same `UpdateDbConfigGroupRequest` schema and the
    same response, and there the null was confirmed to detach by the instance
    history naming the action. So the tool keeps `""` as its own vocabulary and
    translates.
    """
    mock_iam(respx.mock)
    route = respx.put(f"{RELATIONAL}/v1/database-instances/db-1111/update/config-group").mock(
        return_value=httpx.Response(200, json=envelope({"status": 202}))
    )

    await handler.update_relational_instance_config_group(instance_id="db-1111", config_id="")

    assert json.loads(route.calls.last.request.content) == {
        "dbInstanceId": "db-1111",
        "configId": None,
    }


@pytest.mark.asyncio
async def test_update_config_group_still_rejects_a_malformed_id(handler):
    """Accepting `""` must not widen what else gets through into the payload."""
    with pytest.raises(ValueError, match="Invalid config_id"):
        await handler.update_relational_instance_config_group(
            instance_id="db-1111", config_id="../../secrets"
        )


@respx.mock
@pytest.mark.asyncio
async def test_update_secrules_sends_a_bare_json_array(handler):
    """This endpoint takes an array body, not an object, and replaces the whole set."""
    mock_iam(respx.mock)
    route = respx.put(f"{RELATIONAL}/v1/database-instances/db-1111/secrules").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    await handler.update_relational_instance_secrules(
        instance_id="db-1111",
        rules=[
            UpdateSecurityRuleDto(
                id="sgr-1", portRangeMin=3306, portRangeMax=3306, remoteIpPrefix="10.0.0.0/8"
            ),
            UpdateSecurityRuleDto(
                portRangeMin=3307, portRangeMax=3307, remoteIpPrefix="10.1.0.0/16"
            ),
        ],
    )
    body = json.loads(route.calls.last.request.content)
    assert isinstance(body, list)
    assert body[0]["id"] == "sgr-1"
    assert "id" not in body[1], "a new rule is sent without an id"


def test_secrule_dto_bounds_ports():
    with pytest.raises(pydantic.ValidationError):
        UpdateSecurityRuleDto(portRangeMin=0, portRangeMax=70000, remoteIpPrefix="0.0.0.0/0")


# --------------------------------------------------------------------------
# behaviours found only by calling the real API
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_empty_action_response_is_not_reported_as_success(handler):
    """`/shutdown` answers 200 with `data: []` and does nothing.

    Live, on three instances across three zones and two flavour families. An
    action the API has really queued always reports a row, so an empty array
    has to surface as "not applied" -- otherwise the caller tells the user the
    database was stopped when it is still serving traffic.
    """
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/database-instances/db-1111/shutdown").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.stop_relational_instance(instance_id="db-1111")
    assert result.accepted is False
    assert result.results == []
    assert result.warning is not None
    assert "NOT applied" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_non_empty_action_response_is_accepted(handler):
    """`/reboot` does report a row, and must not carry the warning."""
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/database-instances/db-1111/reboot").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "databaseInstances": "db-1111",
                        "action": "reboot",
                        "status": "PROCESSING",
                        "success": True,
                        "code": 202,
                    }
                ]
            ),
        )
    )
    result = await handler.reboot_relational_instance(instance_id="db-1111")
    assert result.accepted is True
    assert result.warning is None
    assert result.results[0].code == 202


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("mysql", "MySQL"),
        ("MYSQL", "MySQL"),
        ("MySQL", "MySQL"),
        ("postgresql", "PostgreSQL"),
        ("mariadb", "MariaDB"),
        ("  redis  ", "Redis"),
    ],
)
def test_engine_names_are_normalised_to_the_canonical_spelling(given, expected):
    """A listing echoes whatever spelling created the instance, so requests normalise.

    Without this the same engine appears as both `mysql` and `MySQL` in one
    listing, depending on who created each row.
    """
    spec = CreateRelationalInstanceDto(
        name="x",
        datastoreType=given,
        datastoreVersion="8.0",
        packageId="103",
        volumeType="Gen2-NVMe2-IOPS3000",
        volumeSize=20,
        netIds=["sub-a"],
        locateZoneId="HCM03-1A",
    )
    assert spec.datastoreType == expected


def test_unknown_engine_passes_through_rather_than_being_rejected():
    """The platform declares no enum, so a new engine must not be blocked."""
    spec = CreateRelationalInstanceDto(
        name="x",
        datastoreType="brand-new-engine",
        datastoreVersion="1.0",
        packageId="103",
        volumeType="v",
        volumeSize=20,
        netIds=["sub-a"],
        locateZoneId="HCM03-1A",
    )
    assert spec.datastoreType == "brand-new-engine"


def test_memory_passwords_need_sixteen_characters():
    """MemoryStore requires 16-128 where relational requires 8-32."""
    from greennode.vdb_mcp_server.models.requests import (
        validate_memory_password,
        validate_relational_password,
    )

    assert validate_relational_password("McpTest2026aA") == "McpTest2026aA"
    with pytest.raises(ValueError, match="16-128"):
        validate_memory_password("McpTest2026aA")
    assert validate_memory_password("McpTestPassword1") == "McpTestPassword1"


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("ACTIVE", "settled"),
        ("SHUTDOWN", "settled"),
        ("BUILDING", "transitional"),
        ("stopping", "transitional"),
        ("REBOOT", "transitional"),
        ("ERROR", "failed"),
        ("INTERNAL_ERROR", "failed"),
        ("RESTART_REQUIRED", "attention"),
        ("A_STATUS_ADDED_LATER", "unknown"),
    ],
)
async def test_instance_status_carries_actionable_classification(handler, status, kind):
    """A raw status string does not tell a caller whether to poll, report or act.

    ERROR and INTERNAL_ERROR in particular are platform faults to be reported,
    not requests to retry -- the response says so rather than leaving it to be
    inferred from a bare string.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/id/db-1111").mock(
        return_value=httpx.Response(200, json=envelope(_instance(status=status)))
    )
    result = await handler.get_relational_instance(instance_id="db-1111")
    assert result.status == status, "the raw value is preserved verbatim"
    assert result.status_kind == kind
    assert result.status_guidance
