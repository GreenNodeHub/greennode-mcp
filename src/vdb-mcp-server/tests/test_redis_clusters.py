"""Tests for the Redis Cluster handler.

Redis Cluster is the fifth vDB family: it shares the memory service
(`/vdb-memory`) and its gateway controller mounts at `/v1/redis-cluster`, so
the family-separation tests at the bottom assert **paths**, not hosts -- a
memory-family test would pass against this handler's host by construction.

Wire shapes come from the gateway's `DBRedisClusterController` and the Redis
Cluster OpenAPI document (2026-11-04), not from the memory family's: the
delete envelope carries no engine group, and the catalogues answer bare
arrays where the memory volume types nest them.
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
    CreateRedisClusterDto,
    DeleteRedisClusterDto,
    DryRunData,
    OrderData,
    RedisCluster,
    RedisClusterListData,
    ResizeRedisClusterDto,
    SecurityRuleListData,
    SettingUpdateData,
    UpdateRedisClusterSecRuleDto,
    UpdateRedisClusterSettingDto,
)
from greennode.vdb_mcp_server.redis_cluster_handler import RedisClusterHandler
from mcp.server.mcpserver import MCPServer
from tests.helpers import MEMORY, envelope, mock_iam, nested_list, page_object


PACKAGE_ID = "rdp-51e30a6d-bc9e-4ffb-843f-11e950de12e8"
VOLUME_TYPE_ID = "rdst-63e28e83-165c-4a6d-8cd4-36ed37f1b65d"
CLUSTER_ID = "rd-3f2a8c94-6b1e-4d8a-9c72-e5d14b0a9f38"
CONFIG_GROUP_ID = "cfg-98a148e4-8eb8-4aa5-a5a2-bcdbfd3b821b"

PASSWORD = "Rediscachepass123"  # 17 chars: the memory-family rule, which this family shares


def _valid_create_spec(**over):
    payload = {
        "name": "mcp-redis-cluster-1",
        "locateZoneId": "HCM03-1A",
        "packageId": PACKAGE_ID,
        "volumeTypeId": VOLUME_TYPE_ID,
        "volumeSize": 50,
        "datastoreVersion": "v7.2.13",
        "deploymentMode": "non-sharding",
        "numberOfNodes": 3,
        "netIds": ["sub-7cc39ad2-a00f-4edd-8e8b-5f5aaa3eeffe"],
        "redisPassword": PASSWORD,
    }
    payload.update(over)
    return CreateRedisClusterDto(**payload)


def _cluster_row(cluster_id=CLUSTER_ID, name="mcp-redis-cluster-1", status="ACTIVE"):
    """A `RedisCluster` row as the listing and the detail return it.

    The row carries `configGroupId` directly -- no nested `configuration`
    object to fall back into, unlike the relational listing.
    """
    return {
        "id": cluster_id,
        "name": name,
        "status": status,
        "deleted": False,
        "deploymentMode": "non-sharding",
        "deployType": "cluster",
        "datastoreType": "Redis",
        "datastoreVersion": "v7.2.13",
        "numberOfNodes": 3,
        "numberOfReplicas": 0,
        "vcpus": 2,
        "ram": 8,
        "packageId": PACKAGE_ID,
        "packageName": "redis.s-general-2x8",
        "volumeSize": 50,
        "volumeType": "Gen2-NVMe2-IOPS3000",
        "volumeTypeId": VOLUME_TYPE_ID,
        "encryptVolume": False,
        "zoneId": "HCM03-1A",
        "publicAccess": False,
        "port": 6379,
        "configGroupId": CONFIG_GROUP_ID,
        "redisPasswordEnabled": True,
        "address": {"master": [{"addr": "10.5.1.20", "type": "master", "subnet": "sub-..."}]},
        "fixedIp": "10.5.1.20",
        "floatingIp": None,
        "lbInfo": [],
        "created": "2026-11-04 02:14:03.0",
        "updated": "2026-11-04 02:31:44.0",
    }


def _secrule(rule_id="28e4b02a-8bfd-406e-9573-b1802ddee282", port=6379, cidr="0.0.0.0/0"):
    """One `SecurityGroupRuleEntity` row, as the memory endpoints return them."""
    return {
        "id": rule_id,
        "direction": "ingress",
        "etherType": "IPv4",
        "protocol": "tcp",
        "portRangeMin": port,
        "portRangeMax": port,
        "remoteIpPrefix": cidr,
        "remoteGroupId": None,
        "remoteGroupName": None,
        "status": None,
        "description": None,
        "createdAt": "2026-11-04T06:05:18.000+00:00",
        "displayCreatedAt": None,
    }


def _full_secrule(id=None, port=6379, cidr="10.0.0.0/8"):
    """A rule DTO carrying the full entity the PUT endpoint binds.

    direction/etherType/protocol look redundant next to the slim schema the
    OpenAPI document publishes, but the wire rejects a row without them.
    """
    return UpdateRedisClusterSecRuleDto(
        **({"id": id} if id else {}),
        direction="ingress",
        etherType="IPv4",
        protocol="tcp",
        portRangeMin=port,
        portRangeMax=port,
        remoteIpPrefix=cidr,
    )


ORDER_RESPONSE = envelope(
    [
        {
            "orderUrl": "https://vdb.console.vngcloud.vn/",
            "orderId": "ord-1",
            "resourceId": CLUSTER_ID,
        }
    ]
)

ACTION_RESPONSE = envelope(
    [
        {
            "databaseInstances": CLUSTER_ID,
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
    return RedisClusterHandler(MCPServer("test"), config, client, allow_write=allow_write)


@pytest.fixture
def handler(sample_config):
    return _handler(sample_config, allow_write=True)


@pytest.fixture
def readonly_handler(sample_config):
    return _handler(sample_config, allow_write=False)


# --------------------------------------------------------------------------
# DTO validation -- the local defence against a bare `in_valid`
# --------------------------------------------------------------------------


def test_create_dto_enforces_the_redis_password_length():
    """The documented rule for this family is 16-128, the MemoryStore rule."""
    with pytest.raises(pydantic.ValidationError, match="16-128"):
        _valid_create_spec(redisPassword="Short1_ok")


def test_create_dto_rejects_a_password_with_a_disallowed_character():
    with pytest.raises(pydantic.ValidationError, match="16-128"):
        _valid_create_spec(redisPassword="Redis!clusterpass123")


def test_create_dto_non_sharding_requires_nodes():
    with pytest.raises(pydantic.ValidationError, match="requires numberOfNodes"):
        _valid_create_spec(numberOfNodes=None)


def test_create_dto_sharding_requires_shards():
    with pytest.raises(pydantic.ValidationError, match="requires numberOfShards"):
        _valid_create_spec(deploymentMode="sharding", numberOfNodes=None, numberOfShards=None)


def test_create_dto_refuses_mixing_the_two_modes():
    """A body sized on both axes is never what the caller meant."""
    with pytest.raises(pydantic.ValidationError, match="numberOfShards belongs to"):
        _valid_create_spec(numberOfShards=3)
    with pytest.raises(pydantic.ValidationError, match="numberOfNodes belongs to"):
        _valid_create_spec(deploymentMode="sharding", numberOfShards=3, numberOfNodes=3)


def test_create_dto_allows_replicas_in_both_modes():
    """`numberOfReplicas` is per shard or per master node, so both modes take it."""
    non_sharding = _valid_create_spec(numberOfReplicas=1)
    assert non_sharding.numberOfReplicas == 1
    sharding = _valid_create_spec(
        deploymentMode="sharding", numberOfNodes=None, numberOfShards=3, numberOfReplicas=2
    )
    assert sharding.numberOfShards == 3
    assert sharding.numberOfReplicas == 2


def test_create_dto_forbids_undocumented_fields():
    """The published schema has no user or databases: a Redis Cluster
    authenticates with redisPassword alone, so extra="forbid" turns a copied
    relational payload into a named error rather than a silent drop."""
    for field, value in (
        ("volumeType", "Gen2-NVMe2-IOPS3000"),  # relational spelling; this family takes the id
        ("user", {"name": "admin", "password": PASSWORD}),
        ("databases", [{"name": "db0"}]),
    ):
        with pytest.raises(pydantic.ValidationError, match=field):
            _valid_create_spec(**{field: value})


def test_create_dto_defaults():
    spec = _valid_create_spec()
    assert spec.encryptionVolume is False
    assert spec.publicAccess is False
    assert spec.isPoc is False
    assert spec.backupAuto is False
    assert spec.enableProxies is False
    assert spec.enableSlowlog is False


def test_create_dto_bounds_backup_retention_in_days():
    with pytest.raises(pydantic.ValidationError):
        _valid_create_spec(backupAuto=True, backupDuration=1)
    assert _valid_create_spec(backupAuto=True, backupDuration=2).backupDuration == 2


def test_resize_dto_requires_the_field_for_its_axis():
    with pytest.raises(pydantic.ValidationError, match="requires volumeSize"):
        ResizeRedisClusterDto(type="VOLUME-SIZE")
    with pytest.raises(pydantic.ValidationError, match="requires packageId"):
        ResizeRedisClusterDto(type="FLAVOR")


def test_resize_dto_rejects_a_second_axis():
    with pytest.raises(pydantic.ValidationError, match="one axis"):
        ResizeRedisClusterDto(type="VOLUME-SIZE", volumeSize=100, packageId=PACKAGE_ID)
    with pytest.raises(pydantic.ValidationError, match="one axis"):
        ResizeRedisClusterDto(
            type="FLAVOR",
            packageId=PACKAGE_ID,
            volumeTypeId=VOLUME_TYPE_ID,
        )


def test_resize_dto_replicas_ride_only_with_the_nodes_axis():
    """The field's own description: 'Use with type NUMBER-OF-NODES'."""
    with pytest.raises(pydantic.ValidationError, match="one axis"):
        ResizeRedisClusterDto(type="FLAVOR", packageId=PACKAGE_ID, numberOfReplicas=1)
    ok = ResizeRedisClusterDto(
        type="NUMBER-OF-NODES",
        numberOfNodes=5,
        numberOfReplicas=1,
        deploymentMode="non-sharding",
    )
    assert ok.numberOfReplicas == 1


def test_resize_dto_nodes_axis_requires_deployment_mode():
    """Measured on staging (2026-11-05): the nodes axis without deploymentMode
    answers 400 bad_request, and the published schema does not list the field
    at all -- the Portal sends it on every resize."""
    with pytest.raises(pydantic.ValidationError, match="requires deploymentMode"):
        ResizeRedisClusterDto(type="NUMBER-OF-NODES", numberOfNodes=5, numberOfReplicas=4)
    ok = ResizeRedisClusterDto(
        type="NUMBER-OF-NODES",
        numberOfNodes=5,
        numberOfReplicas=4,
        deploymentMode="non-sharding",
    )
    assert ok.deploymentMode == "non-sharding"
    # The other axes never needed it: the FLAVOR axis was accepted without it.
    assert ResizeRedisClusterDto(type="FLAVOR", packageId=PACKAGE_ID).deploymentMode is None


def test_resize_dto_takes_the_flavor_axis_the_pg_resize_lacks():
    ok = ResizeRedisClusterDto(type="FLAVOR", packageId=PACKAGE_ID)
    assert ok.type == "FLAVOR"
    with pytest.raises(pydantic.ValidationError, match="type"):
        ResizeRedisClusterDto(type="INSTANCE-TYPE", packageId=PACKAGE_ID)


def test_update_setting_dto_rejects_an_empty_body():
    with pytest.raises(pydantic.ValidationError, match="empty update"):
        UpdateRedisClusterSettingDto()


def test_update_setting_dto_is_a_patch():
    """Omit means unchanged here, unlike the relational family's whole-state update."""
    spec = UpdateRedisClusterSettingDto(publicAccess=True)
    assert spec.password is None
    assert spec.publicAccess is True


def test_update_setting_dto_enforces_the_password_rule():
    with pytest.raises(pydantic.ValidationError, match="16-128"):
        UpdateRedisClusterSettingDto(password="Short1_ok")


def test_secrule_dto_takes_an_optional_id():
    """An id keeps an existing rule; its absence inserts a new one."""
    new_rule = _full_secrule()
    assert new_rule.id is None
    kept = _full_secrule(id="98a148e4-8eb8-4aa5-a5a2-bcdbfd3b821b")
    assert kept.id is not None


def test_secrule_dto_requires_the_full_entity():
    """The published schema omits direction/etherType/protocol; the wire does not.

    The controller binds SecurityGroupRuleEntity and the backend rejects a row
    without the three with 400 bad_request (measured on staging, 2026-11-04) --
    so the DTO must refuse to build such a row locally.
    """
    with pytest.raises(pydantic.ValidationError, match="direction"):
        UpdateRedisClusterSecRuleDto(
            etherType="IPv4",
            protocol="tcp",
            portRangeMin=6379,
            portRangeMax=6379,
            remoteIpPrefix="10.0.0.0/8",
        )


def test_delete_dto_defaults_to_a_plain_delete():
    spec = DeleteRedisClusterDto()
    assert spec.createFinalBackup is False
    assert spec.deleteAllBackup is False


# --------------------------------------------------------------------------
# registration and the write gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_mode_registers_no_write_tools(readonly_handler):
    tools = {t.name for t in await readonly_handler.mcp.list_tools()}
    for name in (
        "create_redis_cluster",
        "resize_redis_cluster",
        "update_redis_cluster_setting",
        "update_redis_cluster_config_group",
        "update_redis_cluster_secrules",
        "delete_redis_cluster",
    ):
        assert name not in tools


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    expected = {
        "list_redis_clusters",
        "get_redis_cluster",
        "list_redis_cluster_secrules",
        "create_redis_cluster_dryrun",
        "resize_redis_cluster_dryrun",
        "delete_redis_cluster_dryrun",
        "create_redis_cluster",
        "resize_redis_cluster",
        "update_redis_cluster_setting",
        "update_redis_cluster_config_group",
        "update_redis_cluster_secrules",
        "delete_redis_cluster",
    }
    assert expected <= tools
    assert len(expected) == 12


@pytest.mark.asyncio
async def test_write_tools_refuse_without_allow_write(readonly_handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.create_redis_cluster(_valid_create_spec(), "IAM_USER")
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.resize_redis_cluster(
            CLUSTER_ID, ResizeRedisClusterDto(type="VOLUME-SIZE", volumeSize=100), "IAM_USER"
        )
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.update_redis_cluster_setting(
            CLUSTER_ID, UpdateRedisClusterSettingDto(publicAccess=True)
        )
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.update_redis_cluster_config_group(CLUSTER_ID, CONFIG_GROUP_ID)
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.update_redis_cluster_secrules(
            CLUSTER_ID,
            [_full_secrule()],
        )
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.delete_redis_cluster(CLUSTER_ID, None)


@pytest.mark.asyncio
async def test_read_only_still_serves_the_dry_runs(readonly_handler):
    """Planning an order must not require the right to place one."""
    for result in (
        await readonly_handler.create_redis_cluster_dryrun(_valid_create_spec()),
        await readonly_handler.resize_redis_cluster_dryrun(
            CLUSTER_ID, ResizeRedisClusterDto(type="VOLUME-SIZE", volumeSize=100)
        ),
        await readonly_handler.delete_redis_cluster_dryrun(CLUSTER_ID, None),
    ):
        assert isinstance(result, DryRunData)


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_projects_the_rows_and_pages(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/redis-cluster").mock(
        return_value=httpx.Response(
            200,
            json=nested_list([_cluster_row()], page_object(number=1, size=20, total_elements=1)),
        )
    )

    result = await handler.list_redis_clusters(1, 20)

    assert isinstance(result, RedisClusterListData)
    assert result.count == 1
    assert result.page.page == 1 and result.page.total_items == 1
    item = result.items[0]
    assert item.id == CLUSTER_ID
    assert item.datastore_type == "Redis"
    assert item.deployment_mode == "non-sharding"
    assert item.number_of_nodes == 3
    assert item.status_kind == "settled"
    # The row carries the config group id directly -- no nested fallback.
    assert item.config_group_id == CONFIG_GROUP_ID


@respx.mock
@pytest.mark.asyncio
async def test_list_sends_both_mandatory_page_params_and_no_filter(handler):
    """`pageNumber` and `pageSize` are required upstream, and the listing has
    no filter of any kind -- so the query carries exactly the two page keys."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/redis-cluster").mock(
        return_value=httpx.Response(200, json=nested_list([], page_object()))
    )

    await handler.list_redis_clusters(2, 50)

    params = route.calls[0].request.url.params
    assert params["pageNumber"] == "2"
    assert params["pageSize"] == "50"
    assert set(params.keys()) == {"pageNumber", "pageSize"}


@respx.mock
@pytest.mark.asyncio
async def test_get_reads_the_family_detail_path(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_cluster_row()))
    )

    cluster = await handler.get_redis_cluster(CLUSTER_ID)

    assert route.called
    assert isinstance(cluster, RedisCluster)
    assert cluster.id == CLUSTER_ID
    assert cluster.port == 6379
    assert cluster.config_group_id == CONFIG_GROUP_ID
    assert cluster.address["master"][0].addr == "10.5.1.20"


@respx.mock
@pytest.mark.asyncio
async def test_get_raises_on_an_empty_payload(handler):
    """Elsewhere in this API a missing resource answers 200 with an empty
    body, which would build a model reading as a real cluster."""
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )
    with pytest.raises(ValueError, match="No Redis Cluster"):
        await handler.get_redis_cluster(CLUSTER_ID)


@respx.mock
@pytest.mark.asyncio
async def test_get_rejects_a_traversing_id(handler):
    with pytest.raises(ValueError):
        await handler.get_redis_cluster("rd-../../etc/passwd")


@respx.mock
@pytest.mark.asyncio
async def test_secrules_are_read_from_the_memory_instance_endpoint(handler):
    """This family's controller has a PUT for the rules but no GET; the memory
    family's instance endpoint, same service, answers for a cluster id."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/database-instances/{CLUSTER_ID}/secrules").mock(
        return_value=httpx.Response(200, json=envelope([_secrule()])),
    )

    result = await handler.list_redis_cluster_secrules(CLUSTER_ID)

    assert route.called
    assert isinstance(result, SecurityRuleListData)
    assert result.count == 1
    assert result.instance_id == CLUSTER_ID
    assert result.items[0].port_range_min == 6379
    assert result.items[0].remote_ip_prefix == "0.0.0.0/0"


# --------------------------------------------------------------------------
# dry runs -- nothing reaches the wire
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_dryrun_previews_the_order_and_sends_nothing(handler):
    result = await handler.create_redis_cluster_dryrun(_valid_create_spec())
    assert not respx.mock.calls, "a dry run must not touch the API"
    assert result.tool == "create_redis_cluster"
    assert result.method == "POST"
    assert result.path == "/v1/redis-cluster"
    assert result.user_type == "IAM_USER"
    assert result.body["packageId"] == PACKAGE_ID
    joined = " ".join(result.warnings)
    assert "BILLABLE" in joined
    assert "per node" in joined
    assert "same zone" in joined


@respx.mock
@pytest.mark.asyncio
async def test_resize_dryrun_body_carries_no_id(handler):
    """The controller fills the id from the path; a body carrying one is not
    what the endpoint takes."""
    result = await handler.resize_redis_cluster_dryrun(
        CLUSTER_ID, ResizeRedisClusterDto(type="FLAVOR", packageId=PACKAGE_ID)
    )
    assert not respx.mock.calls, "a dry run must not touch the API"
    assert result.method == "PUT"
    assert result.path == f"/v1/redis-cluster/{CLUSTER_ID}/resize"
    assert "id" not in result.body


@respx.mock
@pytest.mark.asyncio
async def test_delete_dryrun_previews_the_envelope_the_controller_validates(handler):
    result = await handler.delete_redis_cluster_dryrun(CLUSTER_ID, None)
    assert not respx.mock.calls, "a dry run must not touch the API"
    assert result.user_type is None, "the delete is not a billable order"
    body = result.body
    assert body["action"] == "delete"
    assert body["resType"] == "dbaas"
    assert "engineGroup" not in body, "this family's envelope has no engine group"
    assert len(body["databaseInstances"]) == 1, "the controller rejects any other count"
    assert body["databaseInstances"][0]["instancesId"] == CLUSTER_ID
    assert body["databaseInstances"][0]["config"] == {
        "createFinalBackup": False,
        "deleteAllBackup": False,
    }


@respx.mock
@pytest.mark.asyncio
async def test_delete_dryrun_leads_with_the_backup_warning_when_all_go(handler):
    result = await handler.delete_redis_cluster_dryrun(
        CLUSTER_ID, DeleteRedisClusterDto(deleteAllBackup=True)
    )
    assert "deleteAllBackup" in result.warnings[0]


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_posts_to_the_controller_root_not_a_payment_path(handler):
    """The relational and memory creates post to /payment/database-instances;
    this family's create posts to the controller root -- same order flow,
    different address."""
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/redis-cluster").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    result = await handler.create_redis_cluster(_valid_create_spec(), "IAM_USER")

    assert route.called
    assert isinstance(result, OrderData)
    assert respx.calls.last.request.url.path == "/vdb-memory/v1/redis-cluster"
    assert route.calls[0].request.headers["user-type"] == "IAM_USER"
    body = json.loads(route.calls[0].request.content)
    assert body["packageId"] == PACKAGE_ID
    assert body["deploymentMode"] == "non-sharding"
    assert "redisPassword" in body


@pytest.mark.asyncio
async def test_create_and_resize_default_to_iam_user(handler):
    """ROOT_USER answers 200 and creates nothing, so IAM_USER has to be the
    default. Asserted off the registered tool schema rather than by calling
    the method with the argument omitted: a direct call hands the parameter
    its raw `FieldInfo`, so only the schema shows what an MCP client sends.
    """
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    for name in ("create_redis_cluster", "resize_redis_cluster"):
        schema = tools[name].input_schema
        assert schema["properties"]["user_type"]["default"] == "IAM_USER"


@respx.mock
@pytest.mark.asyncio
async def test_resize_is_a_put_with_no_id_in_the_body(handler):
    """Every other order flow in vDB is a POST; this one is a PUT, and the id
    lives in the path, not the body."""
    mock_iam(respx.mock)
    route = respx.put(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/resize").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    result = await handler.resize_redis_cluster(
        CLUSTER_ID, ResizeRedisClusterDto(type="VOLUME-SIZE", volumeSize=100), "IAM_USER"
    )

    assert route.called
    assert isinstance(result, OrderData)
    assert route.calls[0].request.headers["user-type"] == "IAM_USER"
    body = json.loads(route.calls[0].request.content)
    assert body == {"type": "VOLUME-SIZE", "volumeSize": 100}


@respx.mock
@pytest.mark.asyncio
async def test_resize_nodes_body_carries_deployment_mode(handler):
    """The nodes axis is refused without deploymentMode (measured on staging);
    the wire body must carry it alongside the count."""
    mock_iam(respx.mock)
    route = respx.put(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/resize").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )

    await handler.resize_redis_cluster(
        CLUSTER_ID,
        ResizeRedisClusterDto(
            type="NUMBER-OF-NODES",
            numberOfNodes=5,
            numberOfReplicas=4,
            deploymentMode="non-sharding",
        ),
        "IAM_USER",
    )

    body = json.loads(route.calls[0].request.content)
    assert body == {
        "type": "NUMBER-OF-NODES",
        "numberOfNodes": 5,
        "numberOfReplicas": 4,
        "deploymentMode": "non-sharding",
    }


@respx.mock
@pytest.mark.asyncio
async def test_settings_update_sends_only_what_was_set(handler):
    """The body is a patch: the controller fills the id from the path, and an
    omitted field means 'unchanged'."""
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/settings").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )

    result = await handler.update_redis_cluster_setting(
        CLUSTER_ID, UpdateRedisClusterSettingDto(publicAccess=True)
    )

    body = respx.calls.last.request.read().decode()
    assert "publicAccess" in body
    assert "password" not in body
    assert isinstance(result, SettingUpdateData)
    assert result.instance_id == CLUSTER_ID


@respx.mock
@pytest.mark.asyncio
async def test_config_group_attach_uses_the_familys_own_field_name(handler):
    """This endpoint says `configGroupId`; the family's own create calls the
    same thing `configId` -- the inverse naming, inside one family."""
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/config-group").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )

    await handler.update_redis_cluster_config_group(CLUSTER_ID, CONFIG_GROUP_ID)

    body = respx.calls.last.request.read().decode()
    assert "configGroupId" in body
    assert CONFIG_GROUP_ID in body


@respx.mock
@pytest.mark.asyncio
async def test_an_empty_string_detach_goes_on_the_wire_as_null(handler):
    """The spec says `""` detaches; measured on the sibling families, `""` is
    rejected as `in_valid` and a null detaches, so the tool translates."""
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/config-group").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )

    await handler.update_redis_cluster_config_group(CLUSTER_ID, "")

    assert respx.calls.last.request.read().decode() == '{"configGroupId":null}'


@respx.mock
@pytest.mark.asyncio
async def test_config_group_rejects_a_traversing_id(handler):
    with pytest.raises(ValueError):
        await handler.update_redis_cluster_config_group(CLUSTER_ID, "../../etc/passwd")


@respx.mock
@pytest.mark.asyncio
async def test_secrules_update_sends_a_bare_array(handler):
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/secrules").mock(
        return_value=httpx.Response(200, json=envelope([_secrule(cidr="10.0.0.0/8")]))
    )

    rules = [_full_secrule()]
    result = await handler.update_redis_cluster_secrules(CLUSTER_ID, rules)

    body = respx.calls.last.request.read().decode()
    assert body.startswith("["), "the endpoint takes a bare array, not an object"
    sent = json.loads(body)[0]
    assert sent["direction"] == "ingress"
    assert sent["etherType"] == "IPv4"
    assert sent["protocol"] == "tcp"
    assert isinstance(result, SecurityRuleListData)
    assert result.count == 1


@respx.mock
@pytest.mark.asyncio
async def test_delete_sends_the_envelope_and_reports_the_result(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/delete").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )

    result = await handler.delete_redis_cluster(CLUSTER_ID, None)

    assert route.called
    assert isinstance(result, ActionData)
    assert result.accepted is True
    body = json.loads(respx.calls.last.request.read())
    assert body["action"] == "delete"
    assert body["resType"] == "dbaas"
    assert body["databaseInstances"] == [
        {
            "instancesId": CLUSTER_ID,
            "config": {"createFinalBackup": False, "deleteAllBackup": False},
        }
    ]


@respx.mock
@pytest.mark.asyncio
async def test_an_empty_action_array_is_reported_as_no_effect(handler):
    """HTTP 200 with no rows means the action was silently ignored."""
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/delete").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )

    result = await handler.delete_redis_cluster(CLUSTER_ID, None)

    assert result.accepted is False
    assert "NOT applied" in result.warning


# --------------------------------------------------------------------------
# family separation -- by path, not by host
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_the_reads_stay_inside_the_familys_own_path(handler):
    """The family shares the memory host; only the paths separate the
    families, so the cluster reads are pinned to /v1/redis-cluster -- and the
    memory instance tree is touched only by the borrowed secrules read."""
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/redis-cluster").mock(
        return_value=httpx.Response(200, json=nested_list([], page_object()))
    )
    respx.get(f"{MEMORY}/v1/redis-cluster/{CLUSTER_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_cluster_row()))
    )

    await handler.list_redis_clusters(1, 20)
    await handler.get_redis_cluster(CLUSTER_ID)

    paths = [
        call.request.url.path
        for call in respx.mock.calls
        if call.request.url.host != "iamapis.vngcloud.vn"  # skip the token exchange
    ]
    assert paths == [
        "/vdb-memory/v1/redis-cluster",
        f"/vdb-memory/v1/redis-cluster/{CLUSTER_ID}/detail",
    ]
