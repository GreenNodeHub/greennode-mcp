"""Tests for the relational configuration-group handler.

Fixtures mirror the live shapes probed on 2026-09-09: `/configurations` pages
through `data.content` with `maxSize: 30`, `/configurations/id` takes its id as
a query parameter, and `/configurations/params` echoes `[min, max]` in `values`
for numeric parameters.
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
    ConfigurationDeleteData,
    ConfigurationParamListData,
    ConfigurationUpdateData,
    CreateRelationalConfigurationDto,
    DatabaseConfiguration,
    RelationalConfigurationListData,
    UpdateConfigurationDto,
)
from greennode.vdb_mcp_server.relational_config_handler import RelationalConfigHandler
from mcp.server.mcpserver import MCPServer
from tests.helpers import RELATIONAL, content_list, envelope, mock_iam, page_object


def _config(config_id="cfg-1111", name="tf-mysql8", instances=None, **over):
    row = {
        "id": config_id,
        "name": name,
        "datastoreName": "MySQL",
        "datastoreVersionName": "8.0",
        "description": "app tuning",
        "values": {"autocommit": 1, "max_connections": 500},
        "instanceCount": len(instances or []),
        "instances": instances or [],
        "created": "2026-09-01 10:00:00.0",
        "updated": "2026-09-02 11:00:00.0",
        "deployType": "single_node",
        "sharedBy": None,
        "sharedActions": None,
    }
    row.update(over)
    return row


def _numeric_param(name="innodb_buffer_pool_size", restart=True, lo="5242880", hi="2147483647"):
    """A numeric parameter: upstream `values` is just the two range endpoints."""
    return {
        "name": name,
        "max": hi,
        "min": lo,
        "restartRequired": restart,
        "type": "integer",
        "values": [lo, hi],
        "modifiable": True,
        "description": None,
    }


def _string_param(name="character_set_server", restart=False, values=("utf8mb4", "latin1")):
    """A string parameter: upstream `values` really is the enum."""
    return {
        "name": name,
        "max": None,
        "min": None,
        "restartRequired": restart,
        "type": "string",
        "values": list(values),
        "modifiable": True,
        "description": None,
    }


PARAMS = [
    _numeric_param(),
    _numeric_param("autocommit", restart=False, lo="0", hi="1"),
    _string_param(),
    _string_param("event_scheduler", restart=True, values=("off", "on", "disabled")),
]


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return RelationalConfigHandler(
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
        "list_relational_configurations",
        "get_relational_configuration",
        "list_relational_configuration_params",
    }


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert tools == {
        "list_relational_configurations",
        "get_relational_configuration",
        "list_relational_configuration_params",
        "create_relational_configuration",
        "update_relational_configuration",
        "delete_relational_configurations",
    }


@pytest.mark.asyncio
async def test_delete_is_destructive_and_update_is_not(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    assert tools["delete_relational_configurations"].annotations.destructive_hint is True
    assert tools["update_relational_configuration"].annotations.destructive_hint is False
    assert tools["list_relational_configuration_params"].annotations.read_only_hint is True


@pytest.mark.asyncio
async def test_write_methods_refuse_when_write_is_disabled(readonly_handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.delete_relational_configurations(config_ids=["cfg-1111"])


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_listing_reads_content_and_reports_the_lower_page_ceiling(handler):
    """Configurations cap `maxSize` at 30, where backups say 50 and instances 100."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations").mock(
        return_value=httpx.Response(
            200,
            json=content_list(
                [_config(), _config("cfg-2222", "pg15")],
                {**page_object(total_elements=10, total_pages=2), "maxSize": 30},
            ),
        )
    )
    result = await handler.list_relational_configurations(page=1, page_size=20)
    assert isinstance(result, RelationalConfigurationListData)
    assert [c.id for c in result.items] == ["cfg-1111", "cfg-2222"]
    assert result.page.max_page_size == 30


@respx.mock
@pytest.mark.asyncio
async def test_get_sends_the_id_as_a_query_parameter(handler):
    """`/configurations/id?id=cfg-...` -- the path segment is the literal word 'id'."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/configurations/id").mock(
        return_value=httpx.Response(200, json=envelope(_config()))
    )
    result = await handler.get_relational_configuration(config_id="cfg-1111")
    assert isinstance(result, DatabaseConfiguration)
    assert result.id == "cfg-1111"
    assert route.calls.last.request.url.params["id"] == "cfg-1111"
    assert route.calls.last.request.url.path.endswith("/configurations/id")


@respx.mock
@pytest.mark.asyncio
async def test_get_surfaces_the_attached_instances(handler):
    """The blast radius of an update, reported by the group itself."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/id").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                _config(instances=[{"id": "db-1111", "name": "app-db"}], instanceCount=1)
            ),
        )
    )
    result = await handler.get_relational_configuration(config_id="cfg-1111")
    assert result.instance_count == 1
    assert [i.id for i in result.instances] == ["db-1111"]


@respx.mock
@pytest.mark.asyncio
async def test_an_unknown_group_raises_rather_than_returning_a_blank(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/id").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )
    with pytest.raises(ValueError, match="No configuration group with id"):
        await handler.get_relational_configuration(config_id="cfg-gone")


@pytest.mark.asyncio
async def test_config_id_is_validated(handler):
    with pytest.raises(ValueError):
        await handler.get_relational_configuration(config_id="../../etc/passwd")


# --------------------------------------------------------------------------
# the parameter catalogue
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_params_require_the_engine_pair_and_report_restart_counts(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="MySQL",
        datastore_version="8.0",
        deploy_type=None,
        name=None,
        restart_required=None,
        refresh=False,
    )
    assert isinstance(result, ConfigurationParamListData)
    assert result.count == 4
    assert result.restart_required_count == 2
    assert result.unknown_engine is False
    params = dict(route.calls.last.request.url.params)
    assert params == {"datastoreType": "MySQL", "datastoreVersion": "8.0"}


@respx.mock
@pytest.mark.asyncio
async def test_a_numeric_parameter_exposes_a_RANGE_not_a_two_value_enum(handler):
    """Upstream echoes `[min, max]` into `values`; presenting that as an enum
    would tell a caller the only legal buffer-pool sizes are 5 MB and 2 GB."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="MySQL",
        datastore_version="8.0",
        deploy_type=None,
        name="innodb_buffer_pool_size",
        restart_required=None,
        refresh=False,
    )
    param = result.items[0]
    assert param.type == "integer"
    assert param.minimum == "5242880"
    assert param.maximum == "2147483647"
    assert param.allowed_values == [], "a range is not an enum"


@respx.mock
@pytest.mark.asyncio
async def test_a_string_parameter_keeps_its_real_enum(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="MySQL",
        datastore_version="8.0",
        deploy_type=None,
        name="character_set_server",
        restart_required=None,
        refresh=False,
    )
    assert result.items[0].allowed_values == ["utf8mb4", "latin1"]
    assert result.items[0].minimum is None


@respx.mock
@pytest.mark.asyncio
async def test_params_can_be_filtered_to_the_ones_that_force_a_restart(handler):
    """The question a caller actually has before editing a group."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="MySQL",
        datastore_version="8.0",
        deploy_type=None,
        name=None,
        restart_required=True,
        refresh=False,
    )
    assert {p.name for p in result.items} == {"innodb_buffer_pool_size", "event_scheduler"}
    assert result.filtered is True


@respx.mock
@pytest.mark.asyncio
async def test_an_unknown_engine_pair_is_flagged_not_reported_as_no_parameters(handler):
    """The API answers a wrong pair with an empty list and no error."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="MySQL",
        datastore_version="99.9",
        deploy_type=None,
        name=None,
        restart_required=None,
        refresh=False,
    )
    assert result.count == 0
    assert result.unknown_engine is True


@pytest.mark.asyncio
async def test_an_empty_version_is_rejected_before_the_call(handler):
    with pytest.raises(ValueError, match="datastore_version"):
        await handler.list_relational_configuration_params(
            datastore_type="MySQL",
            datastore_version="   ",
            deploy_type=None,
            name=None,
            restart_required=None,
            refresh=False,
        )


@respx.mock
@pytest.mark.asyncio
async def test_the_cache_key_includes_the_engine_pair(handler):
    """Caching on the tool name alone would serve MySQL's knobs for PostgreSQL."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    kwargs = {"deploy_type": None, "name": None, "restart_required": None, "refresh": False}
    await handler.list_relational_configuration_params(
        datastore_type="MySQL", datastore_version="8.0", **kwargs
    )
    await handler.list_relational_configuration_params(
        datastore_type="MySQL", datastore_version="8.0", **kwargs
    )
    assert route.calls.call_count == 1, "second identical call is served from cache"
    await handler.list_relational_configuration_params(
        datastore_type="PostgreSQL", datastore_version="15", **kwargs
    )
    assert route.calls.call_count == 2, "a different engine must miss the cache"


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_sends_the_canonical_engine_and_omits_absent_fields(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/configurations/create").mock(
        return_value=httpx.Response(200, json=envelope(_config(values={}, instanceCount=0)))
    )
    result = await handler.create_relational_configuration(
        spec=CreateRelationalConfigurationDto(
            name="tf-mysql8", datastoreType="mysql", datastoreVersion="8.0"
        )
    )
    assert isinstance(result, DatabaseConfiguration)
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "name": "tf-mysql8",
        "datastoreType": "MySQL",
        "datastoreVersion": "8.0",
        "deployType": "single_node",
    }


@respx.mock
@pytest.mark.asyncio
async def test_deploy_type_is_always_sent(handler):
    """The spec calls it optional with a default of single_node; the API does not.

    Verified live: creating a MySQL group without `deployType` returns
    `400 bad_request`, with or without a description. Sending it succeeds. So
    the DTO defaults it rather than omitting it.
    """
    mock_iam(respx.mock)
    route = respx.post(f"{RELATIONAL}/v1/configurations/create").mock(
        return_value=httpx.Response(200, json=envelope(_config()))
    )
    await handler.create_relational_configuration(
        spec=CreateRelationalConfigurationDto(
            name="no-deploy-type-given", datastoreType="MySQL", datastoreVersion="8.0"
        )
    )
    body = json.loads(route.calls.last.request.content)
    assert body["deployType"] == "single_node"


@respx.mock
@pytest.mark.asyncio
async def test_create_rereads_the_group_because_the_response_omits_the_name(handler):
    """Measured live: the create response carries the id but an empty `name`.

    Returning it as-is hands the caller a group that looks unnamed, so the
    handler re-reads it.
    """
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/configurations/create").mock(
        return_value=httpx.Response(200, json=envelope(_config(name="")))
    )
    detail = respx.get(f"{RELATIONAL}/v1/configurations/id").mock(
        return_value=httpx.Response(200, json=envelope(_config(name="tf-mysql8")))
    )
    result = await handler.create_relational_configuration(
        spec=CreateRelationalConfigurationDto(
            name="tf-mysql8", datastoreType="MySQL", datastoreVersion="8.0"
        )
    )
    assert result.name == "tf-mysql8"
    assert detail.calls.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_a_failing_reread_still_returns_the_created_group(handler):
    """The re-read is a nicety; losing it must not lose the creation."""
    mock_iam(respx.mock)
    respx.post(f"{RELATIONAL}/v1/configurations/create").mock(
        return_value=httpx.Response(200, json=envelope(_config(name="")))
    )
    respx.get(f"{RELATIONAL}/v1/configurations/id").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    result = await handler.create_relational_configuration(
        spec=CreateRelationalConfigurationDto(
            name="tf-mysql8", datastoreType="MySQL", datastoreVersion="8.0"
        )
    )
    assert result.id == "cfg-1111", "the group exists even if the re-read failed"


def test_a_cluster_group_keeps_its_deploy_type():
    spec = CreateRelationalConfigurationDto(
        name="pg17", datastoreType="PostgreSQL", datastoreVersion="17", deployType="cluster"
    )
    assert spec.deployType == "cluster"


def test_create_rejects_an_unknown_deploy_type():
    with pytest.raises(pydantic.ValidationError):
        CreateRelationalConfigurationDto(
            name="x", datastoreType="PostgreSQL", datastoreVersion="15", deployType="replica"
        )


def test_create_dto_forbids_unknown_fields():
    with pytest.raises(pydantic.ValidationError):
        CreateRelationalConfigurationDto(
            name="x", datastoreType="MySQL", datastoreVersion="8.0", values={}
        )


# --------------------------------------------------------------------------
# update -- the RESTART_REQUIRED path
# --------------------------------------------------------------------------


def _mock_update(mock, *, instances, values):
    mock.put(f"{RELATIONAL}/v1/configurations/update").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                _config(instances=instances, instanceCount=len(instances), values=values)
            ),
        )
    )
    mock.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )


@respx.mock
@pytest.mark.asyncio
async def test_update_injects_the_id_and_sends_the_values(handler):
    mock_iam(respx.mock)
    _mock_update(respx.mock, instances=[], values={"autocommit": 0})
    route = respx.put(f"{RELATIONAL}/v1/configurations/update")
    await handler.update_relational_configuration(
        config_id="cfg-1111",
        spec=UpdateConfigurationDto(values={"autocommit": 0}),
    )
    body = json.loads(route.calls.last.request.content)
    assert body == {"id": "cfg-1111", "values": {"autocommit": 0}}


def test_the_update_dto_has_no_id_of_its_own():
    """Two sources for one value is two chances to disagree."""
    assert "id" not in UpdateConfigurationDto.model_fields


@respx.mock
@pytest.mark.asyncio
async def test_update_names_the_parameters_that_force_a_restart(handler):
    """The user-visible consequence: attached instances go RESTART_REQUIRED and
    keep serving the OLD value until someone reboots them."""
    mock_iam(respx.mock)
    instances = [{"id": "db-1111", "name": "app-db"}, {"id": "db-2222", "name": "reports"}]
    _mock_update(respx.mock, instances=instances, values={"innodb_buffer_pool_size": 134217728})
    result = await handler.update_relational_configuration(
        config_id="cfg-1111",
        spec=UpdateConfigurationDto(values={"innodb_buffer_pool_size": 134217728}),
    )
    assert isinstance(result, ConfigurationUpdateData)
    assert result.changed_parameters == ["innodb_buffer_pool_size"]
    assert result.restart_required_parameters == ["innodb_buffer_pool_size"]
    assert [i.id for i in result.affected_instances] == ["db-1111", "db-2222"]
    assert "RESTART_REQUIRED" in result.next_step
    assert "OLD value" in result.next_step
    assert "reboot_relational_instance" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_update_of_a_live_parameter_does_not_claim_a_restart(handler):
    mock_iam(respx.mock)
    _mock_update(
        respx.mock, instances=[{"id": "db-1111", "name": "app-db"}], values={"autocommit": 0}
    )
    result = await handler.update_relational_configuration(
        config_id="cfg-1111",
        spec=UpdateConfigurationDto(values={"autocommit": 0}),
    )
    assert result.restart_required_parameters == []
    assert "RESTART_REQUIRED" in result.next_step, "still tells the caller to check"
    assert "reboot_relational_instance" not in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_update_of_an_unattached_group_says_nothing_restarted(handler):
    mock_iam(respx.mock)
    _mock_update(respx.mock, instances=[], values={"innodb_buffer_pool_size": 134217728})
    result = await handler.update_relational_configuration(
        config_id="cfg-1111",
        spec=UpdateConfigurationDto(values={"innodb_buffer_pool_size": 134217728}),
    )
    assert result.affected_instances == []
    assert "No instance uses this group" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_update_rereads_the_group_because_the_response_is_thin(handler):
    """Measured live: the update echoes `values: {}` and no `instances`.

    Trusting it reported "no instance uses this group" at the exact moment a
    running database had been pushed into RESTART_REQUIRED -- the worst
    possible wrong answer, so the group is re-read before deciding.
    """
    mock_iam(respx.mock)
    respx.put(f"{RELATIONAL}/v1/configurations/update").mock(
        return_value=httpx.Response(
            200,
            json=envelope({"id": "cfg-1111", "values": {}, "instances": [], "instanceCount": 0}),
        )
    )
    detail = respx.get(f"{RELATIONAL}/v1/configurations/id").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                _config(instances=[{"id": "db-1111", "name": "app-db"}], instanceCount=1)
            ),
        )
    )
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    result = await handler.update_relational_configuration(
        config_id="cfg-1111",
        spec=UpdateConfigurationDto(values={"innodb_buffer_pool_size": 134217728}),
    )
    assert detail.calls.call_count == 1, "the thin echo cannot answer the restart question"
    assert result.restart_required_parameters == ["innodb_buffer_pool_size"]
    assert [i.id for i in result.affected_instances] == ["db-1111"]
    assert "RESTART_REQUIRED" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_update_separates_restarting_parameters_from_live_ones(handler):
    """Only the parameters that actually force a restart are named."""
    mock_iam(respx.mock)
    _mock_update(
        respx.mock,
        instances=[{"id": "db-1111", "name": "app-db"}],
        values={"innodb_buffer_pool_size": 134217728, "autocommit": 1},
    )
    result = await handler.update_relational_configuration(
        config_id="cfg-1111",
        spec=UpdateConfigurationDto(
            values={"innodb_buffer_pool_size": 134217728, "autocommit": 1}
        ),
    )
    assert result.changed_parameters == ["autocommit", "innodb_buffer_pool_size"]
    assert result.restart_required_parameters == ["innodb_buffer_pool_size"]


@respx.mock
@pytest.mark.asyncio
async def test_a_failing_param_lookup_does_not_fail_the_update(handler):
    """The extra call only enriches a warning; it must not lose a successful write."""
    mock_iam(respx.mock)
    respx.put(f"{RELATIONAL}/v1/configurations/update").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                _config(instances=[{"id": "db-1111", "name": "app-db"}], instanceCount=1)
            ),
        )
    )
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    result = await handler.update_relational_configuration(
        config_id="cfg-1111",
        spec=UpdateConfigurationDto(values={"innodb_buffer_pool_size": 1}),
    )
    assert result.configuration.id == "cfg-1111"
    assert result.restart_required_parameters == [], "could not tell, so claims nothing"
    assert "RESTART_REQUIRED" in result.next_step


def test_update_rejects_an_empty_value_set():
    with pytest.raises(pydantic.ValidationError):
        UpdateConfigurationDto(values={})


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_delete_sends_a_json_array_of_ids(handler):
    mock_iam(respx.mock)
    route = respx.delete(f"{RELATIONAL}/v1/configurations/delete").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {"configId": "cfg-1111", "success": True, "code": 200, "errorMsg": None},
                    {"configId": "cfg-2222", "success": True, "code": 200, "errorMsg": None},
                ]
            ),
        )
    )
    result = await handler.delete_relational_configurations(config_ids=["cfg-1111", "cfg-2222"])
    assert isinstance(result, ConfigurationDeleteData)
    assert result.accepted is True
    body = json.loads(route.calls.last.request.content)
    assert body == [{"id": "cfg-1111"}, {"id": "cfg-2222"}]


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_a_failure_carried_inside_a_200(handler):
    """A group still attached to an instance cannot be deleted."""
    mock_iam(respx.mock)
    respx.delete(f"{RELATIONAL}/v1/configurations/delete").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "configId": "cfg-1111",
                        "success": False,
                        "code": 400,
                        "errorMsg": "Config group is in use",
                    }
                ]
            ),
        )
    )
    result = await handler.delete_relational_configurations(config_ids=["cfg-1111"])
    assert result.accepted is False
    assert "Config group is in use" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_delete_treats_an_empty_result_as_not_deleted(handler):
    mock_iam(respx.mock)
    respx.delete(f"{RELATIONAL}/v1/configurations/delete").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.delete_relational_configurations(config_ids=["cfg-1111"])
    assert result.accepted is False
    assert "NOT deleted" in result.warning


@pytest.mark.asyncio
async def test_delete_validates_every_id(handler):
    with pytest.raises(ValueError):
        await handler.delete_relational_configurations(config_ids=["cfg-1111", "cfg-2/../x"])


# --------------------------------------------------------------------------
# deploy_type: the same endpoint serves cluster configuration groups
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_deploy_type_cluster_reaches_the_wire(handler):
    """A cluster group's parameters live behind deployType=cluster.

    Measured 2026-09-14: PostgreSQL 17 answers 0 parameters without it and 25
    with it, so dropping the argument would make cluster groups unconfigurable.
    """
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="PostgreSQL",
        datastore_version="17",
        deploy_type="cluster",
        name=None,
        restart_required=None,
        refresh=False,
    )
    params = dict(route.calls.last.request.url.params)
    assert params["deployType"] == "cluster"
    assert result.deploy_type == "cluster"


@respx.mock
@pytest.mark.asyncio
async def test_the_two_deploy_types_are_cached_separately(handler):
    """PostgreSQL 15 exposes 41 single-node parameters against 25 cluster ones.

    One cache key for both would answer a cluster question with single-node
    parameters, which read as perfectly plausible.
    """
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    common = {
        "datastore_type": "PostgreSQL",
        "datastore_version": "15",
        "name": None,
        "restart_required": None,
        "refresh": False,
    }
    await handler.list_relational_configuration_params(deploy_type=None, **common)
    await handler.list_relational_configuration_params(deploy_type="cluster", **common)
    assert route.call_count == 2
    await handler.list_relational_configuration_params(deploy_type="cluster", **common)
    assert route.call_count == 2, "the second cluster call must be served from cache"


@respx.mock
@pytest.mark.asyncio
async def test_an_empty_postgresql_result_blames_deploy_type_not_the_version(handler):
    """PostgreSQL 17 without deploy_type is the single most likely empty result.

    Reporting that as 'unrecognised engine/version' sends the caller to fix a
    version that was right all along.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="PostgreSQL",
        datastore_version="17",
        deploy_type=None,
        name=None,
        restart_required=None,
        refresh=False,
    )
    assert result.unknown_engine is True
    assert "deploy_type='cluster'" in result.empty_result_hint
    assert "single_node" in result.empty_result_hint


@respx.mock
@pytest.mark.asyncio
async def test_an_empty_cluster_result_points_the_other_way(handler):
    """PostgreSQL 14 has no cluster parameters; the hint must say so."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="PostgreSQL",
        datastore_version="14",
        deploy_type="cluster",
        name=None,
        restart_required=None,
        refresh=False,
    )
    assert "without deploy_type" in result.empty_result_hint
    assert "list_postgresql_datastores" in result.empty_result_hint


@respx.mock
@pytest.mark.asyncio
async def test_a_non_empty_result_carries_no_hint(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAMS))
    )
    result = await handler.list_relational_configuration_params(
        datastore_type="MySQL",
        datastore_version="8.0",
        deploy_type=None,
        name=None,
        restart_required=None,
        refresh=False,
    )
    assert result.empty_result_hint == ""


@respx.mock
@pytest.mark.asyncio
async def test_a_cluster_group_keeps_its_pg_cfg_id_through_the_listing(handler):
    """A cluster group is listed by the relational tool, prefixed pg-cfg-."""
    mock_iam(respx.mock)
    cluster_group = _config(
        config_id="pg-cfg-b8cf4290-9c11-4b52-bea1-d4423e90b729",
        name="postgre-cluster-17",
        datastoreName="PostgreSQL",
        datastoreVersionName="17",
        deployType="cluster",
    )
    respx.get(f"{RELATIONAL}/v1/configurations").mock(
        return_value=httpx.Response(200, json=content_list([cluster_group], page_object()))
    )
    result = await handler.list_relational_configurations(page=1, page_size=20)
    group = result.items[0]
    assert group.id.startswith("pg-cfg-")
    assert group.deploy_type == "cluster"
