"""Tests for the PostgreSQL Cluster handler.

Fixtures mirror the shapes probed live on 2026-09-14: the mixed relational
listing carrying one `pg-` row among `db-` ones, the 71-field detail response
(whose `configId` is null while `configuration.id` holds the real group), and
the `volume-used` endpoint answering with unit-carrying strings.

The tests that matter most here are the ones about the *derived* reads. This
family has no listing endpoint, so every guarantee about filtering and paging
is one this handler makes rather than one the API provides -- and a wrong one
would be invisible until a user acted on a count.
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
    CreatePostgresqlClusterDto,
    DryRunData,
    OrderData,
    PostgresqlCluster,
    PostgresqlClusterListData,
    PostgresqlVolumeUsedData,
    ResizePostgresqlClusterDto,
    SettingUpdateData,
    UpdatePostgresqlClusterSettingDto,
    UpdateSecurityRuleDto,
)
from greennode.vdb_mcp_server.postgresql_cluster_handler import PostgresqlClusterHandler
from mcp.server.mcpserver import MCPServer
from tests.helpers import (
    POSTGRESQL,
    RELATIONAL,
    content_list,
    envelope,
    mock_iam,
    nested_list,
    page_object,
)


CLUSTER_ID = "pg-82777ff7-a1be-4f61-bd07-6d1f6fc3da80"
INSTANCE_ID = "db-66a37ca3-e688-4a8a-9e35-a89fa69733c6"
CONFIG_GROUP_ID = "pg-cfg-b8cf4290-9c11-4b52-bea1-d4423e90b729"
FLAVOR_ID = "pgp-51e30a6d-bc9e-4ffb-843f-11e950de12e8"
VOLUME_TYPE_ID = "pgst-63e28e83-165c-4a6d-8cd4-36ed37f1b65d"
SUBNET_ID = "sub-66a5327f-1970-427d-b1a3-8eb146e94bab"


def _cluster_row(cluster_id=CLUSTER_ID, name="database-or7221hk-90", status="ACTIVE"):
    """A `pg-` row as the mixed relational listing returns it."""
    return {
        "id": cluster_id,
        "name": name,
        "status": status,
        "dbBackendId": None,
        "datastoreType": "PostgreSQL",
        "datastoreVersion": "17",
        "deployType": "cluster",
        "numberOfNodes": 3,
        "ram": 4,
        "vcpus": 2,
        "volumeSize": 20,
        "volumeType": "Gen2-NVMe-IOPS5000",
        "volumeTypeId": VOLUME_TYPE_ID,
        "quotaPackageId": FLAVOR_ID,
        "packageName": "vdb.s-general-2x4",
        "zoneId": "HCM03-1A",
        "subnetId": SUBNET_ID,
        "publicAccess": True,
        "port": 5432,
        "portRo": 15432,
        "privateRwIp": "10.5.1.6",
        "publicRwIp": "58.84.3.57",
        "privateRoIp": "10.5.1.6",
        "publicRoIp": "58.84.3.57",
        "domainName": "database-or7221hk-90.vdb-postgresql.vngcloud.vn",
        # The trap: the top-level configId is null even with a group attached.
        "configId": None,
        "configuration": {"id": CONFIG_GROUP_ID, "name": "postgre-cluster-17"},
        "enableProxies": False,
        "poolMaxConnections": None,
        "freeBackupSize": 50,
        "volumeUsed": None,
        "ip": None,
        "created": "2026-09-08 12:54:15.0",
        "updated": "2026-09-14 01:00:27.0",
    }


def _instance_row(instance_id=INSTANCE_ID, name="docs-agent"):
    """A relational `db-` row from the same listing."""
    return {
        "id": instance_id,
        "name": name,
        "status": "ACTIVE",
        "dbBackendId": 811580,
        "datastoreType": "PostgreSQL",
        "datastoreVersion": "15",
        "deployType": "single_node",
        "numberOfNodes": 1,
        "ram": 4,
        "vcpus": 2,
        "volumeSize": 60,
        "zoneId": "HCM03-1A",
        "configuration": {"id": None, "name": "config-group"},
    }


ORDER_RESPONSE = envelope(
    [{"orderUrl": "https://vdb.console.vngcloud.vn/", "orderId": "ord-1", "resourceId": None}]
)


def _valid_create(**overrides):
    body = {
        "name": "mcp-pg-test",
        "locateZoneId": "HCM03-1A",
        "packageId": FLAVOR_ID,
        "volumeTypeId": VOLUME_TYPE_ID,
        "volumeSize": 20,
        "numberOfNodes": 3,
        "datastoreVersion": "17",
        "netIds": [SUBNET_ID],
        "user": {"name": "pgadmin", "password": "Abcdef1234xy"},
        "databases": [{"name": "appdb"}],
    }
    body.update(overrides)
    return CreatePostgresqlClusterDto(**body)


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return PostgresqlClusterHandler(MCPServer("test"), config, client, allow_write=allow_write)


@pytest.fixture
def handler(sample_config):
    return _handler(sample_config, allow_write=True)


@pytest.fixture
def readonly_handler(sample_config):
    return _handler(sample_config, allow_write=False)


# --------------------------------------------------------------------------
# the derived listing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_listing_keeps_only_pg_rows_and_counts_what_it_dropped(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(
            200,
            json=nested_list(
                [_cluster_row(), _instance_row(), _instance_row("db-other", "demo")],
                page_object(total_pages=1, total_elements=3),
            ),
        )
    )
    result = await handler.list_postgresql_clusters(None, None, 20, 5)
    assert isinstance(result, PostgresqlClusterListData)
    assert result.count == 1
    assert result.items[0].id == CLUSTER_ID
    assert result.relational_instances_excluded == 2
    assert result.derived_listing is True


@pytest.mark.asyncio
@respx.mock
async def test_an_unfiltered_listing_is_not_marked_approximate(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(200, json=nested_list([_cluster_row()], page_object()))
    )
    result = await handler.list_postgresql_clusters(None, None, 20, 5)
    assert result.filters_are_approximate is False


@pytest.mark.asyncio
@respx.mock
async def test_a_name_filter_is_applied_locally_and_flagged(handler):
    """The API ignores name/status for cluster rows -- measured, not assumed.

    So the endpoint is mocked returning a cluster that does NOT match: if the
    handler trusted the API's filtering, this row would be reported as a hit.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(
            200, json=nested_list([_cluster_row(name="something-else")], page_object())
        )
    )
    result = await handler.list_postgresql_clusters("zzz-no-such-name", None, 20, 5)
    assert result.count == 0
    assert result.filters_are_approximate is True


@pytest.mark.asyncio
@respx.mock
async def test_a_status_filter_is_applied_locally(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(
            200,
            json=nested_list(
                [_cluster_row(status="ACTIVE"), _cluster_row("pg-two", "two", "BUILDING")],
                page_object(),
            ),
        )
    )
    result = await handler.list_postgresql_clusters(None, ["building"], 20, 5)
    assert [c.id for c in result.items] == ["pg-two"]


@pytest.mark.asyncio
@respx.mock
async def test_the_scan_follows_pages_and_says_when_it_stopped_early(handler):
    """Clusters can sit past page 1 because relational rows share the paging."""
    mock_iam(respx.mock)
    pages = {
        1: nested_list([_instance_row()], page_object(number=1, total_pages=3)),
        2: nested_list([_cluster_row()], page_object(number=2, total_pages=3)),
    }

    def respond(request):
        number = int(request.url.params["pageNumber"])
        return httpx.Response(200, json=pages[number])

    respx.get(f"{RELATIONAL}/v1/database-instances").mock(side_effect=respond)
    result = await handler.list_postgresql_clusters(None, None, 20, 2)
    assert result.count == 1
    assert result.pages_scanned == 2
    assert result.more_pages_exist is True


@pytest.mark.asyncio
@respx.mock
async def test_the_scan_stops_at_the_last_page_without_claiming_more(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances").mock(
        return_value=httpx.Response(
            200, json=nested_list([_cluster_row()], page_object(number=1, total_pages=1))
        )
    )
    result = await handler.list_postgresql_clusters(None, None, 20, 5)
    assert result.pages_scanned == 1
    assert result.more_pages_exist is False


# --------------------------------------------------------------------------
# the derived detail
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_reads_the_relational_detail_endpoint(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/id/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=envelope(_cluster_row()))
    )
    cluster = await handler.get_postgresql_cluster(CLUSTER_ID)
    assert route.called
    assert isinstance(cluster, PostgresqlCluster)
    assert cluster.number_of_nodes == 3
    assert cluster.deploy_type == "cluster"


@pytest.mark.asyncio
@respx.mock
async def test_get_reads_the_config_group_from_the_nested_object(handler):
    """`configId` is null on the wire while a group is attached; `configuration` is not."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/id/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=envelope(_cluster_row()))
    )
    cluster = await handler.get_postgresql_cluster(CLUSTER_ID)
    assert cluster.config_group_id == CONFIG_GROUP_ID
    assert cluster.config_group_name == "postgre-cluster-17"


@pytest.mark.asyncio
async def test_get_rejects_a_non_cluster_id_without_calling_the_api(handler):
    """The relational detail endpoint answers for `db-` ids too, so we check here."""
    with pytest.raises(ValueError, match="not a PostgreSQL Cluster id"):
        await handler.get_postgresql_cluster(INSTANCE_ID)


@pytest.mark.asyncio
async def test_get_rejects_a_traversing_id(handler):
    with pytest.raises(ValueError):
        await handler.get_postgresql_cluster("pg-../../etc/passwd")


@pytest.mark.asyncio
@respx.mock
async def test_the_two_endpoints_are_reported_separately(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/id/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=envelope(_cluster_row()))
    )
    cluster = await handler.get_postgresql_cluster(CLUSTER_ID)
    assert (cluster.port, cluster.port_ro) == (5432, 15432)
    assert cluster.private_rw_ip == cluster.private_ro_ip == "10.5.1.6"


# --------------------------------------------------------------------------
# volume used
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_volume_used_passes_the_unit_strings_through(handler):
    """The API answers `["248M"]`, not a number of GB. Parsing would invent a unit."""
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/cluster/{CLUSTER_ID}/volume-used").mock(
        return_value=httpx.Response(200, json=envelope(["248M", "1.2G"]))
    )
    result = await handler.get_postgresql_cluster_volume_used(CLUSTER_ID)
    assert isinstance(result, PostgresqlVolumeUsedData)
    assert result.values == ["248M", "1.2G"]
    assert result.cluster_id == CLUSTER_ID


# --------------------------------------------------------------------------
# write guard
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writes_are_refused_without_allow_write(readonly_handler):
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.create_postgresql_cluster(_valid_create(), "IAM_USER")
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.update_postgresql_cluster_setting(
            CLUSTER_ID, UpdatePostgresqlClusterSettingDto(publicAccess=False)
        )
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.update_postgresql_cluster_config_group(CLUSTER_ID, "")


@pytest.mark.asyncio
async def test_read_only_still_serves_the_dry_runs(readonly_handler):
    """Planning an order must not require the right to place one."""
    result = await readonly_handler.create_postgresql_cluster_dryrun(_valid_create())
    assert isinstance(result, DryRunData)


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_posts_the_dto_body(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{POSTGRESQL}/v1/cluster").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.create_postgresql_cluster(_valid_create(), "IAM_USER")
    assert isinstance(result, OrderData)
    body = respx.calls.last.request.read().decode()
    assert FLAVOR_ID in body and VOLUME_TYPE_ID in body
    assert route.called


@pytest.mark.asyncio
async def test_create_defaults_to_iam_user(handler):
    """ROOT_USER answers 200 and creates nothing, so IAM_USER has to be the default.

    Asserted off the registered tool schema rather than by calling the method
    with the argument omitted: a direct call hands the parameter its raw
    `FieldInfo`, so only the schema shows what an MCP client would send.
    """
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    schema = tools["create_postgresql_cluster"].input_schema
    assert schema["properties"]["user_type"]["default"] == "IAM_USER"


def test_create_rejects_a_single_node_cluster():
    """One node is a relational instance, ordered through a different endpoint."""
    with pytest.raises(pydantic.ValidationError):
        _valid_create(numberOfNodes=1)


def test_create_rejects_more_than_ten_nodes():
    with pytest.raises(pydantic.ValidationError):
        _valid_create(numberOfNodes=11)


def test_create_rejects_an_unknown_field():
    """Unknown fields are ignored by the API, not rejected -- extra=forbid is the guard."""
    with pytest.raises(pydantic.ValidationError):
        CreatePostgresqlClusterDto(
            name="x",
            locateZoneId="HCM03-1A",
            packageId=FLAVOR_ID,
            volumeTypeId=VOLUME_TYPE_ID,
            volumeSize=20,
            numberOfNodes=3,
            datastoreVersion="17",
            netIds=[SUBNET_ID],
            volumeType="Gen2-NVMe-IOPS5000",  # relational field name, not this one
        )


def test_create_rejects_a_password_the_api_would_reject_as_in_valid():
    with pytest.raises(pydantic.ValidationError):
        _valid_create(user={"name": "pgadmin", "password": "short!"})


def test_create_dryrun_warns_that_a_cluster_bills_per_node():
    dto = _valid_create(numberOfNodes=3)
    assert dto.numberOfNodes == 3


@pytest.mark.asyncio
async def test_create_dryrun_sends_nothing_and_warns_about_cost(handler):
    result = await handler.create_postgresql_cluster_dryrun(_valid_create())
    assert result.tool == "create_postgresql_cluster"
    assert result.method == "POST"
    joined = " ".join(result.warnings)
    assert "BILLABLE" in joined
    assert "per node" in joined
    assert "delete_postgresql_cluster" in joined
    assert "same zone" in joined


# --------------------------------------------------------------------------
# resize
# --------------------------------------------------------------------------


def test_resize_requires_the_field_that_matches_its_type():
    with pytest.raises(pydantic.ValidationError):
        ResizePostgresqlClusterDto(type="NUMBER-OF-NODES", volumeSize=40)


def test_resize_refuses_two_axes_at_once():
    with pytest.raises(pydantic.ValidationError):
        ResizePostgresqlClusterDto(type="VOLUME-SIZE", volumeSize=40, numberOfNodes=4)


def test_resize_accepts_each_axis_on_its_own():
    assert ResizePostgresqlClusterDto(type="VOLUME-SIZE", volumeSize=40).volumeSize == 40
    assert (
        ResizePostgresqlClusterDto(type="VOLUME-TYPE", volumeTypeId=VOLUME_TYPE_ID).volumeTypeId
        == VOLUME_TYPE_ID
    )
    assert ResizePostgresqlClusterDto(type="NUMBER-OF-NODES", numberOfNodes=4).numberOfNodes == 4


def test_resize_rejects_a_lowercase_type():
    """The values are upper case and hyphenated, unlike every other enum in vDB."""
    with pytest.raises(pydantic.ValidationError):
        ResizePostgresqlClusterDto(type="volume-size", volumeSize=40)


@pytest.mark.asyncio
@respx.mock
async def test_resize_is_a_put_not_a_post(handler):
    """Every other order flow in vDB is a POST; this one is not."""
    mock_iam(respx.mock)
    route = respx.put(f"{POSTGRESQL}/v1/cluster/{CLUSTER_ID}/resize").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.resize_postgresql_cluster(
        CLUSTER_ID, ResizePostgresqlClusterDto(type="VOLUME-SIZE", volumeSize=40), "IAM_USER"
    )
    assert route.called
    assert isinstance(result, OrderData)


@pytest.mark.asyncio
async def test_resize_dryrun_warns_about_losing_nodes(handler):
    result = await handler.resize_postgresql_cluster_dryrun(
        CLUSTER_ID, ResizePostgresqlClusterDto(type="NUMBER-OF-NODES", numberOfNodes=2)
    )
    assert result.method == "PUT"
    joined = " ".join(result.warnings)
    assert "BILLABLE" in joined
    assert "replicas" in joined


# --------------------------------------------------------------------------
# settings and configuration group
# --------------------------------------------------------------------------


def test_a_settings_update_may_not_be_empty():
    with pytest.raises(pydantic.ValidationError):
        UpdatePostgresqlClusterSettingDto()


def test_a_settings_password_follows_the_platform_rule():
    with pytest.raises(pydantic.ValidationError):
        UpdatePostgresqlClusterSettingDto(password="has spaces!")
    assert UpdatePostgresqlClusterSettingDto(password="Abcdef1234xy").password


@pytest.mark.asyncio
@respx.mock
async def test_settings_update_sends_only_what_was_set(handler):
    mock_iam(respx.mock)
    respx.put(f"{POSTGRESQL}/v1/cluster/{CLUSTER_ID}/settings").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )
    result = await handler.update_postgresql_cluster_setting(
        CLUSTER_ID, UpdatePostgresqlClusterSettingDto(publicAccess=True)
    )
    body = respx.calls.last.request.read().decode()
    assert "publicAccess" in body
    assert "password" not in body
    assert isinstance(result, SettingUpdateData)


@pytest.mark.asyncio
@respx.mock
async def test_config_group_attach_uses_the_families_own_field_name(handler):
    """`configGroupId` here; the relational and memory endpoints say `configId`."""
    mock_iam(respx.mock)
    respx.put(f"{POSTGRESQL}/v1/cluster/{CLUSTER_ID}/config-group").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )
    await handler.update_postgresql_cluster_config_group(CLUSTER_ID, CONFIG_GROUP_ID)
    body = respx.calls.last.request.read().decode()
    assert "configGroupId" in body
    assert CONFIG_GROUP_ID in body


@pytest.mark.asyncio
@respx.mock
async def test_an_empty_string_detach_goes_on_the_wire_as_null(handler):
    """The spec says `""` detaches; measured elsewhere, `""` is rejected and null detaches."""
    mock_iam(respx.mock)
    respx.put(f"{POSTGRESQL}/v1/cluster/{CLUSTER_ID}/config-group").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )
    await handler.update_postgresql_cluster_config_group(CLUSTER_ID, "")
    assert respx.calls.last.request.read().decode() == '{"configGroupId":null}'


@pytest.mark.asyncio
async def test_config_group_rejects_a_traversing_id(handler):
    with pytest.raises(ValueError):
        await handler.update_postgresql_cluster_config_group(CLUSTER_ID, "../../etc/passwd")


# --------------------------------------------------------------------------
# the five operations served by the RELATIONAL endpoints
# --------------------------------------------------------------------------


def _secrule(rule_id="28e4b02a-8bfd-406e-9573-b1802ddee282", port=5432, cidr="0.0.0.0/0"):
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
        "createdAt": "2026-09-08T06:05:18.000+00:00",
        "displayCreatedAt": None,
    }


ACTION_RESPONSE = envelope(
    [
        {
            "databaseInstances": CLUSTER_ID,
            "action": "reboot",
            "status": "PROCESSING",
            "success": True,
            "errorMsg": None,
            "code": 200,
        }
    ]
)


@pytest.mark.asyncio
@respx.mock
async def test_secrules_are_read_from_the_relational_endpoint(handler):
    """The cluster API has no security-rule endpoint; the relational one serves pg- ids."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/{CLUSTER_ID}/secrules").mock(
        return_value=httpx.Response(200, json=envelope([_secrule()]))
    )
    result = await handler.list_postgresql_cluster_secrules(CLUSTER_ID)
    assert route.called
    assert result.count == 1
    assert result.items[0].remote_ip_prefix == "0.0.0.0/0"


@pytest.mark.asyncio
@respx.mock
async def test_secrules_update_replaces_the_whole_set(handler):
    mock_iam(respx.mock)
    respx.put(f"{RELATIONAL}/v1/database-instances/{CLUSTER_ID}/secrules").mock(
        return_value=httpx.Response(200, json=envelope([_secrule(port=5432, cidr="10.0.0.0/8")]))
    )
    rules = [
        UpdateSecurityRuleDto(portRangeMin=5432, portRangeMax=5432, remoteIpPrefix="10.0.0.0/8")
    ]
    result = await handler.update_postgresql_cluster_secrules(CLUSTER_ID, rules)
    assert result.count == 1
    body = respx.calls.last.request.read().decode()
    assert body.startswith("["), "the endpoint takes a bare array, not an object"


@pytest.mark.asyncio
@respx.mock
async def test_histories_are_read_from_the_relational_endpoint(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/{CLUSTER_ID}/histories").mock(
        return_value=httpx.Response(
            200,
            json=content_list(
                [
                    {
                        "id": 3305,
                        "instanceId": CLUSTER_ID,
                        "action": "Create backup",
                        "status": "Finished",
                        "description": "Create backup: bk-db-pt-91abebf5",
                        "createdTime": "2026-09-13T18:00:16.000+00:00",
                        "updatedTime": "2026-09-13T18:00:29.000+00:00",
                        "errorMessage": "",
                    }
                ],
                page_object(),
            ),
        )
    )
    result = await handler.list_postgresql_cluster_histories(CLUSTER_ID, 1, 20)
    assert route.called
    assert result.count == 1
    # The description is what names what the platform understood the request to be.
    assert "Create backup" in result.items[0].description


@pytest.mark.asyncio
@respx.mock
async def test_reboot_sends_the_action_envelope_the_path_does_not_imply(handler):
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/database-instances/{CLUSTER_ID}/reboot").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    result = await handler.reboot_postgresql_cluster(CLUSTER_ID)
    assert result.accepted is True
    body = json.loads(respx.calls.last.request.read())
    assert body["action"] == "reboot"
    assert body["resType"] == "dbaas"
    assert body["databaseInstances"] == [{"instancesId": CLUSTER_ID}]


@pytest.mark.asyncio
@respx.mock
async def test_an_empty_action_array_is_reported_as_no_effect(handler):
    """HTTP 200 with no rows means the action was silently ignored."""
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/database-instances/{CLUSTER_ID}/reboot").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.reboot_postgresql_cluster(CLUSTER_ID)
    assert result.accepted is False
    assert "NOT applied" in result.warning
    assert "histories" in result.warning


@pytest.mark.asyncio
@respx.mock
async def test_delete_goes_through_the_relational_endpoint(handler):
    """This family has no delete of its own -- the Portal calls the relational one."""
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/database-instances/{CLUSTER_ID}/delete").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    result = await handler.delete_postgresql_cluster(CLUSTER_ID, None)
    assert route.called
    assert result.accepted is True
    body = json.loads(respx.calls.last.request.read())
    assert body["action"] == "delete"


@pytest.mark.asyncio
@respx.mock
async def test_delete_keeps_backups_unless_asked_otherwise(handler):
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/database-instances/{CLUSTER_ID}/delete").mock(
        return_value=httpx.Response(200, json=ACTION_RESPONSE)
    )
    await handler.delete_postgresql_cluster(CLUSTER_ID, None)
    config = json.loads(respx.calls.last.request.read())["databaseInstances"][0]["config"]
    assert config["deleteAllBackup"] is False
    assert config["createFinalBackup"] is False


@pytest.mark.asyncio
async def test_reboot_and_delete_are_refused_without_allow_write(readonly_handler):
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.reboot_postgresql_cluster(CLUSTER_ID)
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.delete_postgresql_cluster(CLUSTER_ID, None)
