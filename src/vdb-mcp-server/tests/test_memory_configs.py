"""Tests for the MemoryStore (Redis) configuration-group handler.

Three differences from the relational twin get pinned here: `get` is a plain
path lookup, `delete` is a `POST`, and `create` has no `deployType`. The fourth
thing tested is the one that is *not* different -- the re-read after an update,
which exists because a thin echo once made the relational tool claim nothing
was affected while a running database had just been pushed into
`RESTART_REQUIRED`.
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
from greennode.vdb_mcp_server.memory_config_handler import MemoryConfigHandler
from greennode.vdb_mcp_server.models import (
    ConfigurationDeleteData,
    ConfigurationParamListData,
    ConfigurationUpdateData,
    CreateMemoryConfigurationDto,
    DatabaseConfiguration,
    MemoryConfigurationListData,
    UpdateConfigurationDto,
)
from mcp.server.mcpserver import MCPServer
from tests.helpers import MEMORY, content_list, envelope, mock_iam, page_object


CONFIG_ID = "cfg-b634172f-abdb-4cf0-b18b-dacf93a6a22c"
INSTANCE_ID = "db-5a4d26f1-1451-4a93-9d9a-72d117f98831"


def _group(config_id=CONFIG_ID, name="redis72", values=None, instances=None, **over):
    row = {
        "id": config_id,
        "name": name,
        "datastoreName": "Redis",
        "datastoreVersionName": "7.2",
        "description": "test group",
        "values": values if values is not None else {"maxmemory-policy": "allkeys-lru"},
        "instanceCount": 0,
        "instances": instances if instances is not None else [],
        "created": "2026-09-01 10:00:00.0",
        "updated": "2026-09-10 04:00:00.0",
        "deployType": "single_node",
    }
    row.update(over)
    return row


PARAM_ROWS = [
    {
        "name": "maxmemory-policy",
        "type": "string",
        "min": None,
        "max": None,
        "restartRequired": False,
        "modifiable": True,
        "values": ["noeviction", "allkeys-lru", "volatile-lru"],
        "description": "Eviction policy",
    },
    {
        "name": "databases",
        "type": "integer",
        "min": "1",
        "max": "64",
        "restartRequired": True,
        "modifiable": True,
        # Numeric params echo [min, max] here, which is NOT an enum.
        "values": ["1", "64"],
        "description": "Number of logical databases",
    },
]


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return MemoryConfigHandler(
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
    assert "list_memory_configurations" in tools
    assert "list_memory_configuration_params" in tools
    for name in (
        "create_memory_configuration",
        "update_memory_configuration",
        "delete_memory_configurations",
    ):
        assert name not in tools


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    expected = {
        "list_memory_configurations",
        "get_memory_configuration",
        "list_memory_configuration_params",
        "create_memory_configuration",
        "update_memory_configuration",
        "delete_memory_configurations",
    }
    assert expected <= tools
    assert len(expected) == 6


@pytest.mark.asyncio
async def test_write_helpers_refuse_without_allow_write(readonly_handler):
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.create_memory_configuration(
            CreateMemoryConfigurationDto(name="g", datastoreVersion="7.2")
        )
    with pytest.raises(ValueError, match="--allow-write"):
        await readonly_handler.delete_memory_configurations([CONFIG_ID])


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_memory_configurations(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/configurations").mock(
        return_value=httpx.Response(
            200, json=content_list([_group()], page_object(total_elements=6))
        )
    )

    result = await handler.list_memory_configurations(1, 20)

    assert isinstance(result, MemoryConfigurationListData)
    assert result.count == 1
    assert result.items[0].id == CONFIG_ID
    assert result.items[0].datastore_type == "Redis"
    assert result.items[0].datastore_version == "7.2"
    assert result.page.total_items == 6
    assert route.calls[0].request.url.params["pageNumber"] == "1"


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_configuration_uses_a_plain_path_lookup(handler):
    """Memory: /configurations/{id}/detail. Relational: /configurations/id?id=..."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/configurations/{CONFIG_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_group()))
    )

    result = await handler.get_memory_configuration(CONFIG_ID)

    assert isinstance(result, DatabaseConfiguration)
    assert result.id == CONFIG_ID
    url = str(route.calls[0].request.url)
    assert url.endswith(f"/configurations/{CONFIG_ID}/detail")
    assert "id=" not in url, "the id is a path segment here, not a query parameter"


@respx.mock
@pytest.mark.asyncio
async def test_get_memory_configuration_raises_on_an_empty_payload(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/configurations/{CONFIG_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )

    with pytest.raises(ValueError, match="not found"):
        await handler.get_memory_configuration(CONFIG_ID)


@pytest.mark.asyncio
async def test_get_memory_configuration_validates_the_id(handler):
    with pytest.raises(ValueError, match="Invalid config_id"):
        await handler.get_memory_configuration("../../etc/passwd")


@respx.mock
@pytest.mark.asyncio
async def test_params_separates_numeric_bounds_from_a_real_enum(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAM_ROWS))
    )

    result = await handler.list_memory_configuration_params("7.2", "Redis", None, None, False)

    assert isinstance(result, ConfigurationParamListData)
    assert result.count == 2
    assert result.restart_required_count == 1
    assert result.unknown_engine is False
    by_name = {p.name: p for p in result.items}
    assert by_name["maxmemory-policy"].allowed_values == [
        "noeviction",
        "allkeys-lru",
        "volatile-lru",
    ]
    numeric = by_name["databases"]
    assert numeric.minimum == "1" and numeric.maximum == "64"
    assert numeric.allowed_values == [], "[min, max] is a range, not an enum"
    params = route.calls[0].request.url.params
    assert params["datastoreType"] == "Redis"
    assert params["datastoreVersion"] == "7.2"
    assert "deployType" not in params, "not sent for this family"


@respx.mock
@pytest.mark.asyncio
async def test_params_flags_an_unrecognised_version_rather_than_looking_empty(handler):
    """An unknown pair returns an empty array, not an error."""
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )

    result = await handler.list_memory_configuration_params("9.9", "Redis", None, None, False)

    assert result.count == 0
    assert result.unknown_engine is True, (
        "must be distinguishable from a version with no settable parameters"
    )


@respx.mock
@pytest.mark.asyncio
async def test_params_filters_client_side(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAM_ROWS))
    )

    restarts = await handler.list_memory_configuration_params("7.2", "Redis", None, True, False)
    assert [p.name for p in restarts.items] == ["databases"]
    assert restarts.filtered is True

    named = await handler.list_memory_configuration_params("7.2", "Redis", "MEMORY", None, False)
    assert [p.name for p in named.items] == ["maxmemory-policy"], "case-insensitive substring"


@pytest.mark.asyncio
async def test_params_rejects_an_empty_version(handler):
    with pytest.raises(ValueError, match="datastore_version must not be empty"):
        await handler.list_memory_configuration_params("  ", "Redis", None, None, False)


@respx.mock
@pytest.mark.asyncio
async def test_params_caches_per_engine_pair(handler):
    """Caching on the tool name alone would serve 7.2's parameters for 4.0."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAM_ROWS))
    )

    await handler.list_memory_configuration_params("7.2", "Redis", None, None, False)
    await handler.list_memory_configuration_params("7.2", "Redis", None, None, False)
    assert route.call_count == 1, "second call served from the cache"

    await handler.list_memory_configuration_params("4.0", "Redis", None, None, False)
    assert route.call_count == 2, "a different version must not reuse the entry"

    await handler.list_memory_configuration_params("7.2", "Redis", None, None, True)
    assert route.call_count == 3, "refresh=True bypasses the cache"


# --------------------------------------------------------------------------
# DTO validation
# --------------------------------------------------------------------------


def test_create_dto_sends_a_deploy_type_the_schema_does_not_declare():
    """`deployType` is absent from CreateMemConfigGroupRequest and still required.

    Measured live: a body with only the four declared fields is rejected as
    `400 bad_request`; adding `deployType: single_node` makes it succeed. A DTO
    built faithfully from the schema could not create anything.
    """
    spec = CreateMemoryConfigurationDto(name="g", datastoreVersion="7.2")
    assert spec.deployType == "single_node"
    assert "deployType" in spec.model_dump(), "must reach the wire"
    with pytest.raises(pydantic.ValidationError):
        CreateMemoryConfigurationDto(name="g", datastoreVersion="7.2", deployType="nonsense")


def test_create_dto_defaults_and_normalises_the_engine():
    spec = CreateMemoryConfigurationDto(name="g", datastoreVersion="7.2")
    assert spec.datastoreType == "Redis", "the spec names Redis as the only allowed value"
    assert (
        CreateMemoryConfigurationDto(
            name="g", datastoreVersion="7.2", datastoreType="redis"
        ).datastoreType
        == "Redis"
    )


def test_create_dto_requires_a_name_and_version():
    for kwargs in ({"name": "", "datastoreVersion": "7.2"}, {"name": "g", "datastoreVersion": ""}):
        with pytest.raises(pydantic.ValidationError):
            CreateMemoryConfigurationDto(**kwargs)


def test_update_dto_has_no_id():
    """The handler fills it from its own config_id argument."""
    with pytest.raises(pydantic.ValidationError, match="id"):
        UpdateConfigurationDto(id=CONFIG_ID, values={"databases": 16})


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_rereads_when_the_response_omits_the_name(handler):
    mock_iam(respx.mock)
    create = respx.post(f"{MEMORY}/v1/configurations/create").mock(
        return_value=httpx.Response(200, json=envelope(_group(name="", values={})))
    )
    detail = respx.get(f"{MEMORY}/v1/configurations/{CONFIG_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_group(name="redis72")))
    )

    result = await handler.create_memory_configuration(
        CreateMemoryConfigurationDto(name="redis72", datastoreVersion="7.2")
    )

    assert result.name == "redis72", "the create echo omits it; the re-read supplies it"
    assert detail.called
    body = json.loads(create.calls[0].request.content)
    assert body["deployType"] == "single_node", (
        "required even though the schema does not declare it -- omitting it is 400"
    )
    assert body["datastoreType"] == "Redis", "this endpoint rejects a lowercase 'redis'"


@respx.mock
@pytest.mark.asyncio
async def test_create_falls_back_to_the_request_when_the_reread_is_not_ready(handler):
    """Measured live: the detail endpoint is empty for seconds after a create.

    The re-read then raises, and returning the bare echo would show a group
    with no name and no engine. The request's own values are known-good, so
    they fill the gap.
    """
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/configurations/create").mock(
        return_value=httpx.Response(
            200, json=envelope({"id": CONFIG_ID, "deployType": "single_node"})
        )
    )
    detail = respx.get(f"{MEMORY}/v1/configurations/{CONFIG_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope({}))
    )

    result = await handler.create_memory_configuration(
        CreateMemoryConfigurationDto(
            name="mcpcfg-redis72", datastoreVersion="7.2", description="live test"
        )
    )

    assert detail.called, "it tries the re-read first"
    assert result.id == CONFIG_ID
    assert result.name == "mcpcfg-redis72"
    assert result.datastore_type == "Redis"
    assert result.datastore_version == "7.2"
    assert result.description == "live test"
    assert result.deploy_type == "single_node"


@respx.mock
@pytest.mark.asyncio
async def test_update_rereads_the_group_instead_of_trusting_a_thin_echo(handler):
    """The bug this guards: reporting "nothing affected" from an empty echo.

    Measured on the relational twin, the update response comes back with
    `values: {}` and no `instances` -- which once produced "no instance uses
    this group yet" at the moment a running database had been pushed into
    RESTART_REQUIRED.
    """
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/configurations/update").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                {"id": CONFIG_ID, "name": "", "values": {}, "instances": None, "deployType": None}
            ),
        )
    )
    respx.get(f"{MEMORY}/v1/configurations/{CONFIG_ID}/detail").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                _group(
                    values={"databases": 16},
                    instances=[{"id": INSTANCE_ID, "name": "cli-redis-test", "status": "ACTIVE"}],
                    instanceCount=0,
                )
            ),
        )
    )
    respx.get(f"{MEMORY}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAM_ROWS))
    )

    result = await handler.update_memory_configuration(
        CONFIG_ID, UpdateConfigurationDto(values={"databases": 16})
    )

    assert isinstance(result, ConfigurationUpdateData)
    assert result.changed_parameters == ["databases"]
    assert result.restart_required_parameters == ["databases"]
    assert [i.id for i in result.affected_instances] == [INSTANCE_ID], (
        "trust the instances list, not instanceCount, which reported 0"
    )
    assert "RESTART_REQUIRED" in result.next_step
    assert "reboot" in result.next_step.lower()


@respx.mock
@pytest.mark.asyncio
async def test_update_says_nothing_restarted_only_when_nothing_is_attached(handler):
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/configurations/update").mock(
        return_value=httpx.Response(200, json=envelope(_group(values={"databases": 16})))
    )
    respx.get(f"{MEMORY}/v1/configurations/{CONFIG_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_group(values={"databases": 16})))
    )
    respx.get(f"{MEMORY}/v1/configurations/params").mock(
        return_value=httpx.Response(200, json=envelope(PARAM_ROWS))
    )

    result = await handler.update_memory_configuration(
        CONFIG_ID, UpdateConfigurationDto(values={"databases": 16})
    )

    assert result.affected_instances == []
    assert "No instance uses this group" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_update_still_succeeds_when_the_param_lookup_fails(handler):
    """The restart lookup enriches a warning; it must not fail the update."""
    mock_iam(respx.mock)
    respx.put(f"{MEMORY}/v1/configurations/update").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                _group(
                    values={"databases": 16},
                    instances=[{"id": INSTANCE_ID, "name": "cli-redis-test"}],
                )
            ),
        )
    )
    respx.get(f"{MEMORY}/v1/configurations/params").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )

    result = await handler.update_memory_configuration(
        CONFIG_ID, UpdateConfigurationDto(values={"databases": 16})
    )

    assert result.changed_parameters == ["databases"]
    assert result.restart_required_parameters == [], "could not tell -- not a claim of safety"
    assert "RESTART_REQUIRED" in result.next_step or "Confirm" in result.next_step


@respx.mock
@pytest.mark.asyncio
async def test_delete_is_a_post_with_a_json_array(handler):
    """Relational uses DELETE with a body; this family uses POST."""
    mock_iam(respx.mock)
    route = respx.post(f"{MEMORY}/v1/configurations/delete").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {"configId": "cfg-1", "success": True, "code": 200, "errorMsg": None},
                    {"configId": "cfg-2", "success": True, "code": 200, "errorMsg": None},
                ]
            ),
        )
    )

    result = await handler.delete_memory_configurations(["cfg-1", "cfg-2"])

    assert isinstance(result, ConfigurationDeleteData)
    assert json.loads(route.calls[0].request.content) == [{"id": "cfg-1"}, {"id": "cfg-2"}]
    assert route.calls[0].request.method == "POST"
    assert result.accepted is True
    assert result.config_ids == ["cfg-1", "cfg-2"]


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_a_group_still_in_use_as_not_deleted(handler):
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/configurations/delete").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "configId": CONFIG_ID,
                        "success": False,
                        "code": 400,
                        "errorMsg": "Config group is in use",
                    }
                ]
            ),
        )
    )

    result = await handler.delete_memory_configurations([CONFIG_ID])

    assert result.accepted is False
    assert result.warning and "did NOT happen" in result.warning
    assert "Config group is in use" in result.warning


@respx.mock
@pytest.mark.asyncio
async def test_delete_reports_an_empty_response_as_not_deleted(handler):
    mock_iam(respx.mock)
    respx.post(f"{MEMORY}/v1/configurations/delete").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )

    result = await handler.delete_memory_configurations([CONFIG_ID])

    assert result.accepted is False
    assert result.warning and "NOT deleted" in result.warning


@pytest.mark.asyncio
async def test_delete_validates_every_id_and_rejects_an_empty_list(handler):
    with pytest.raises(ValueError, match="Invalid config_ids"):
        await handler.delete_memory_configurations(["cfg-1", "../../secrets"])
    with pytest.raises(ValueError, match="at least one configuration group"):
        await handler.delete_memory_configurations([])


# --------------------------------------------------------------------------
# family separation
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_every_call_goes_to_the_memory_service(handler):
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/configurations/{CONFIG_ID}/detail").mock(
        return_value=httpx.Response(200, json=envelope(_group()))
    )

    await handler.get_memory_configuration(CONFIG_ID)

    urls = [str(c.request.url) for c in respx.mock.calls if "vdb-" in str(c.request.url)]
    assert urls and all("/vdb-memory/" in u for u in urls)
