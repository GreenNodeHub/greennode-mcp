"""Tests for the vDB catalogue handler.

Fixtures mirror the live shapes probed on 2026-09-08 (see the plan doc, §4.13).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.mcp_core.auth import TokenManager
from greennode.vdb_mcp_server.catalog_handler import CatalogHandler
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import load_config
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.models import (
    DatastoreListData,
    FlavorListData,
    SubnetListData,
    VolumeTypeListData,
)
from mcp.server.mcpserver import MCPServer
from tests.helpers import (
    KAFKA,
    MEMORY,
    POSTGRESQL,
    RELATIONAL,
    envelope,
    mock_iam,
    nested_list,
)


DATASTORES = [
    {
        "name": "MySQL",
        "type": "mysql",
        "version": "8.0",
        "versionName": "8.0",
        "licenseName": "Community Edition",
        "licenseDesc": "",
    },
    {
        "name": "PostgreSQL",
        "type": "postgresql",
        "version": "15",
        "versionName": "15",
        "licenseName": "Community Edition",
        "licenseDesc": "",
    },
]

FLAVORS = [
    {
        "id": 1,
        "name": "db.s-general-2x4",
        "description": "2 vCPU / 4 GB",
        "vcpus": 2,
        "ram": 4,
        "volumeSize": 20,
        "volumeType": "Gen2-NVMe2-IOPS3000",
        "zoneId": 1,
        "zoneUUID": "HCM03-1A",
        "packageSku": "db.s-general-2x4",
        "familyType": "general",
        "monthlyCost": 100.0,
        "bandwidth": 100,
    }
]

VOLUME_TYPES = [
    {
        "id": 36,
        "iopsId": 3000,
        "type": "Gen2-NVMe2-IOPS3000",
        "description": "NVME",
        "minVolumeSize": 20,
        "maxVolumeSize": 10000,
        "volumeTypeZoneId": "63D9E33A-34F3-11EE-BE56-0242AC120002",
        "volumeTypeSku": "Gen2-NVMe2-IOPS3000.dbaas",
        "iops": 3000,
        "zoneId": "HCM03-1A",
    }
]

NETWORKS_WITH_SUBNETS = [
    {
        "uuid": "net-372ddaf7",
        "createdAt": "2026-06-23T06:57:16.000+00:00",
        "status": "ACTIVE",
        "displayName": "test-10-5",
        "networkId": 22279712,
        "zoneId": "HCM03-1A",
        "subnets": [
            {
                "uuid": "sub-66a5327f",
                "status": "ACTIVE",
                "cidr": "10.5.1.0/24",
                "name": "tf-secondary-test-subnet",
                "zoneId": "HCM03-1A",
            }
        ],
    },
    {
        "uuid": "net-empty",
        "status": "ACTIVE",
        "displayName": "no-subnets-yet",
        "networkId": 1,
        "zoneId": "HCM03-1A",
        "subnets": [],
    },
]


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return CatalogHandler(MCPServer("test"), config, client, DiscoveryCache())


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_catalogue_tool_is_registered(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    for family in ("relational", "memory"):
        for noun in (
            "datastores",
            "engines",
            "instance_families",
            "flavors",
            "flavor_codes",
            "volume_types",
            "networks",
            "subnets",
        ):
            assert f"list_{family}_{noun}" in tools
    assert "list_relational_zones" in tools
    assert "list_memory_zones" not in tools, "the memory family has no zones endpoint"


@pytest.mark.asyncio
async def test_no_status_tool_is_registered(handler):
    """`listDatabaseInstanceStatus` is a dead endpoint -- decision D."""
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert not any("status" in t for t in tools)


@pytest.mark.asyncio
async def test_catalogue_tools_are_read_only(handler):
    for tool in await handler.mcp.list_tools():
        assert tool.annotations.read_only_hint is True, tool.name


# --------------------------------------------------------------------------
# plain-array catalogue endpoints
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_relational_datastores(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/datastore").mock(
        return_value=httpx.Response(200, json=envelope(DATASTORES))
    )
    result = await handler.list_relational_datastores(refresh=False)
    assert isinstance(result, DatastoreListData)
    assert result.count == 2
    assert [d.type for d in result.items] == ["mysql", "postgresql"]
    assert [d.version for d in result.items] == ["8.0", "15"]


@respx.mock
@pytest.mark.asyncio
async def test_memory_datastores_use_the_other_path(handler):
    """The two families disagree on the catalogue path, not just the prefix."""
    mock_iam(respx.mock)
    route = respx.get(f"{MEMORY}/v1/database/datastore").mock(
        return_value=httpx.Response(200, json=envelope(DATASTORES))
    )
    await handler.list_memory_datastores(refresh=False)
    assert route.called


# --------------------------------------------------------------------------
# flavors -- the parameterised one
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_relational_flavors_sends_the_required_pair(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/flavors").mock(
        return_value=httpx.Response(200, json=envelope(FLAVORS))
    )
    result = await handler.list_relational_flavors(
        datastore_type="mysql", version="8.0", zone_id=None, refresh=False
    )
    assert isinstance(result, FlavorListData)
    assert result.items[0].name == "db.s-general-2x4"
    assert dict(route.calls.last.request.url.params) == {"type": "mysql", "version": "8.0"}


@respx.mock
@pytest.mark.asyncio
async def test_list_flavors_passes_zone_only_when_given(handler):
    """`zoneId` genuinely narrows the result, so it must not be sent as empty."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/flavors").mock(
        return_value=httpx.Response(200, json=envelope(FLAVORS))
    )
    await handler.list_relational_flavors(
        datastore_type="mysql", version="8.0", zone_id=None, refresh=False
    )
    assert "zoneId" not in route.calls.last.request.url.params

    await handler.list_relational_flavors(
        datastore_type="mysql", version="8.0", zone_id="HCM03-1B", refresh=False
    )
    assert route.calls.last.request.url.params["zoneId"] == "HCM03-1B"


@pytest.mark.asyncio
async def test_list_flavors_rejects_a_blank_version(handler):
    """An empty `version` makes the API answer 500 -- catch it before the call."""
    with pytest.raises(ValueError, match="version"):
        await handler.list_relational_flavors(
            datastore_type="mysql", version="  ", zone_id=None, refresh=False
        )


@pytest.mark.asyncio
async def test_list_flavors_rejects_a_blank_datastore_type(handler):
    with pytest.raises(ValueError, match="datastore_type"):
        await handler.list_relational_flavors(
            datastore_type="", version="8.0", zone_id=None, refresh=False
        )


@respx.mock
@pytest.mark.asyncio
async def test_empty_flavor_list_says_the_pair_is_probably_wrong(handler):
    """An invalid type/version pair answers 200 with an empty array, not an error.

    Reporting a bare empty list would read as "this engine has no flavours",
    which is never true; the pair is what is wrong.
    """
    mock_iam(respx.mock)
    respx.get(f"{MEMORY}/v1/database/flavors").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    result = await handler.list_memory_flavors(
        datastore_type="redis", version="7.0", zone_id=None, refresh=False
    )
    assert result.count == 0
    assert "list_memory_datastores" in (result.note or "")


@respx.mock
@pytest.mark.asyncio
async def test_flavor_cache_key_includes_the_pair(handler):
    """Caching on the tool name alone would serve MySQL flavours for PostgreSQL."""
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/flavors").mock(
        side_effect=[
            httpx.Response(200, json=envelope(FLAVORS)),
            httpx.Response(200, json=envelope([])),
        ]
    )
    first = await handler.list_relational_flavors(
        datastore_type="mysql", version="8.0", zone_id=None, refresh=False
    )
    second = await handler.list_relational_flavors(
        datastore_type="postgresql", version="15", zone_id=None, refresh=False
    )
    assert route.call_count == 2
    assert first.count == 1
    assert second.count == 0


@respx.mock
@pytest.mark.asyncio
async def test_repeating_a_lookup_is_served_from_cache(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/datastore").mock(
        return_value=httpx.Response(200, json=envelope(DATASTORES))
    )
    await handler.list_relational_datastores(refresh=False)
    await handler.list_relational_datastores(refresh=False)
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_refresh_bypasses_the_cache(handler):
    mock_iam(respx.mock)
    route = respx.get(f"{RELATIONAL}/v1/database-instances/datastore").mock(
        return_value=httpx.Response(200, json=envelope(DATASTORES))
    )
    await handler.list_relational_datastores(refresh=False)
    await handler.list_relational_datastores(refresh=True)
    assert route.call_count == 2


# --------------------------------------------------------------------------
# volume types -- the nested shape
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_volume_types_read_the_nested_data_key(handler):
    """`data` is an object here, not an array -- `as_list` would silently return []."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/volume/types").mock(
        return_value=httpx.Response(200, json=nested_list(VOLUME_TYPES))
    )
    result = await handler.list_relational_volume_types(zone_id=None, refresh=False)
    assert isinstance(result, VolumeTypeListData)
    assert result.count == 1
    assert result.items[0].type == "Gen2-NVMe2-IOPS3000"
    assert result.items[0].min_volume_size == 20
    assert result.items[0].max_volume_size == 10000


# --------------------------------------------------------------------------
# subnets -- the endpoint that returns networks
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_subnets_are_flattened_out_of_their_networks(handler):
    """`networks/subnets` returns NETWORKS carrying a nested `subnets` array.

    A create needs a `subnetId`, so the tool flattens them and keeps the parent
    network on each row.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/networks/subnets").mock(
        return_value=httpx.Response(200, json=envelope(NETWORKS_WITH_SUBNETS))
    )
    result = await handler.list_relational_subnets(refresh=False)
    assert isinstance(result, SubnetListData)
    assert result.count == 1, "a network with no subnets contributes no rows"
    row = result.items[0]
    assert row.subnet_id == "sub-66a5327f"
    assert row.cidr == "10.5.1.0/24"
    assert row.network_id == "net-372ddaf7"
    assert row.network_name == "test-10-5"
    assert row.zone_id == "HCM03-1A"


# --------------------------------------------------------------------------
# the two fields the raw payload gets wrong if taken at face value
# --------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_flavor_zone_comes_from_locate_zone_id(handler):
    """Three raw fields look like the zone and only `locateZoneId` is one.

    `zoneId` is an internal integer and `zoneUUID` identifies a zone *group*;
    projecting either as the zone hands back a value that no other tool and no
    create accepts.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/flavors").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "name": "db.a-general-2x4",
                        "zoneId": 251,
                        "zoneUUID": "561407CF-4F4A-409F-A0B3-4139EDB635EB",
                        "locateZoneId": "HCM03-1A",
                        "familyType": "general-purpose",
                        "platformType": "code-a",
                    }
                ]
            ),
        )
    )
    result = await handler.list_relational_flavors(
        datastore_type="mysql", version="8.0", zone_id=None, refresh=False
    )
    row = result.items[0]
    assert row.zone_id == "HCM03-1A"
    assert row.zone_group_id == "561407CF-4F4A-409F-A0B3-4139EDB635EB"
    assert row.platform_type == "code-a"


@respx.mock
@pytest.mark.asyncio
async def test_unzoned_flavor_result_reports_which_zone_it_describes(handler):
    """Omitting `zone_id` returns the DEFAULT zone, not every zone.

    The answer therefore has to say which zone it is talking about, otherwise a
    caller reads a one-zone catalogue as the whole platform.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/flavors").mock(
        return_value=httpx.Response(200, json=envelope(FLAVORS))
    )
    unzoned = await handler.list_relational_flavors(
        datastore_type="mysql", version="8.0", zone_id=None, refresh=False
    )
    assert unzoned.zone_id is None

    zoned = await handler.list_relational_flavors(
        datastore_type="mysql", version="8.0", zone_id="HCM03-1B", refresh=False
    )
    assert zoned.zone_id == "HCM03-1B"


@respx.mock
@pytest.mark.asyncio
async def test_volume_types_echo_the_zone_they_describe(handler):
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/volume/types").mock(
        return_value=httpx.Response(200, json=nested_list(VOLUME_TYPES))
    )
    result = await handler.list_relational_volume_types(zone_id="HCM03-1B", refresh=False)
    assert result.zone_id == "HCM03-1B"


@respx.mock
@pytest.mark.asyncio
async def test_families_endpoint_mixes_zone_groups_with_real_families(handler):
    """`group == 'family_custom'` rows are zones, not families.

    They carry no `key`, so a caller that assumes every row is a family shows
    the user a list of blanks.
    """
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/families").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "id": "9FB3B850-1E92-11F1-B4AC-0800200C9A66",
                        "name": "ENG DEV 1a",
                        "key": None,
                        "value": None,
                        "condition": None,
                        "description": "ENG DEV 1a",
                        "group": "family_custom",
                    },
                    {
                        "id": None,
                        "name": None,
                        "key": "general-purpose",
                        "value": "General Purpose",
                        "condition": {"codes": ["code-s2", "code-s", "code-a"]},
                        "description": "General Purpose",
                        "group": "family_general",
                    },
                ]
            ),
        )
    )
    result = await handler.list_relational_instance_families(refresh=False)
    zone_group, family = result.items
    assert zone_group.group == "family_custom"
    assert zone_group.key == "" and zone_group.id == "9FB3B850-1E92-11F1-B4AC-0800200C9A66"
    assert family.key == "general-purpose"
    assert family.codes == ["code-s2", "code-s", "code-a"]


# --------------------------------------------------------------------------
# PostgreSQL Cluster catalogue
# --------------------------------------------------------------------------


def _pg_flavor(flavor_id="pgp-cd1958e4", zone="HCM03-1A"):
    return {
        "id": flavor_id,
        "name": "db.s2-general-2x4",
        "isDefault": False,
        "description": "Instance types for common workloads",
        "networkPerformance": "Up to 1 Gbps",
        "packageSku": "db.s2-general-2x4",
        "cesSku": "ces.db.s2-general-2x4",
        "ram": 4,
        "vcpus": 2,
        "platformType": "code-s2",
        "order": 1,
        "status": "ACTIVE",
        "locateZoneId": zone,
        "zoneUUID": "5D55DD40-5D93-11F1-B7ED-0800200C9A66",
        "backupSize": 50,
    }


def _pg_volume_type(type_id="pgst-63e28e83", zone="HCM03-1A"):
    return {
        "id": type_id,
        "name": "Gen2-NVMe-IOPS5000",
        "type": "Gen2-NVMe-IOPS5000",
        "volumeTypeSku": "Gen2-NVMe-IOPS5000.dbaas",
        "order": 2,
        "status": "ACTIVE",
        "minVolumeSize": 20,
        "maxVolumeSize": 5000,
        "zoneId": zone,
        "iops": 5000,
        "description": "NVME",
        "volumeTypeZoneId": "63D9E33A-34F3-11EE-BE56-0242AC120002",
    }


@pytest.mark.asyncio
@respx.mock
async def test_postgresql_datastores_are_their_own_version_set(handler):
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/cluster/datastore").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "versionName": "PostgreSQL 17",
                        "version": "17",
                        "name": "PostgreSQL",
                        "type": "postgresql",
                        "licenseName": "Community Edition",
                        "licenseDesc": "Community Edition",
                    }
                ]
            ),
        )
    )
    result = await handler.list_postgresql_datastores(refresh=False)
    assert result.count == 1
    assert result.items[0].version == "17"


@pytest.mark.asyncio
@respx.mock
async def test_postgresql_flavors_need_no_engine_or_version(handler):
    """Unlike the other two families, this catalogue has one engine and calls bare."""
    mock_iam(respx.mock)
    route = respx.get(f"{POSTGRESQL}/v1/cluster/flavors").mock(
        return_value=httpx.Response(200, json=envelope([_pg_flavor()]))
    )
    result = await handler.list_postgresql_flavors(zone_id=None, multi_zone=False, refresh=False)
    assert result.count == 1
    assert result.items[0].id == "pgp-cd1958e4"
    assert result.items[0].zone_id == "HCM03-1A"
    assert "type" not in route.calls.last.request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_postgresql_flavors_are_keyed_per_zone_in_the_cache(handler):
    """Each zone returns the same sizes under different ids, so the key must carry it."""
    mock_iam(respx.mock)

    def respond(request):
        zone = request.url.params.get("zoneId", "HCM03-1A")
        return httpx.Response(200, json=envelope([_pg_flavor(f"pgp-{zone}", zone)]))

    respx.get(f"{POSTGRESQL}/v1/cluster/flavors").mock(side_effect=respond)
    first = await handler.list_postgresql_flavors(
        zone_id="HCM03-1A", multi_zone=False, refresh=False
    )
    second = await handler.list_postgresql_flavors(
        zone_id="HCM03-1B", multi_zone=False, refresh=False
    )
    assert first.items[0].id != second.items[0].id


@pytest.mark.asyncio
@respx.mock
async def test_postgresql_volume_types_are_a_bare_array(handler):
    """The relational and memory twins wrap theirs in `{projectId, data[]}`; this one does not."""
    mock_iam(respx.mock)
    respx.get(f"{POSTGRESQL}/v1/cluster/volume-types").mock(
        return_value=httpx.Response(200, json=envelope([_pg_volume_type()]))
    )
    result = await handler.list_postgresql_volume_types(
        zone_id=None, multi_zone=False, refresh=False
    )
    assert result.count == 1
    row = result.items[0]
    assert row.id == "pgst-63e28e83"
    assert (row.min_volume_size, row.max_volume_size) == (20, 5000)


@pytest.mark.asyncio
@respx.mock
async def test_postgresql_catalogues_send_no_multi_zone_flag_by_default(handler):
    mock_iam(respx.mock)
    flavors = respx.get(f"{POSTGRESQL}/v1/cluster/flavors").mock(
        return_value=httpx.Response(200, json=envelope([_pg_flavor()]))
    )
    volumes = respx.get(f"{POSTGRESQL}/v1/cluster/volume-types").mock(
        return_value=httpx.Response(200, json=envelope([_pg_volume_type()]))
    )
    flavor_result = await handler.list_postgresql_flavors(
        zone_id="HCM03-1A", multi_zone=False, refresh=False
    )
    volume_result = await handler.list_postgresql_volume_types(
        zone_id="HCM03-1A", multi_zone=False, refresh=False
    )
    assert "multiZone" not in flavors.calls.last.request.url.params
    assert "multiZone" not in volumes.calls.last.request.url.params
    assert flavor_result.multi_zone is False and volume_result.multi_zone is False


@pytest.mark.asyncio
@respx.mock
async def test_postgresql_catalogues_pass_the_multi_zone_flag_without_a_zone(handler):
    """With `multiZone` the API replaces `zoneId` by the Multi-AZ default zone, so none is sent."""
    mock_iam(respx.mock)
    flavors = respx.get(f"{POSTGRESQL}/v1/cluster/flavors").mock(
        return_value=httpx.Response(200, json=envelope([_pg_flavor()]))
    )
    volumes = respx.get(f"{POSTGRESQL}/v1/cluster/volume-types").mock(
        return_value=httpx.Response(200, json=envelope([_pg_volume_type()]))
    )
    flavor_result = await handler.list_postgresql_flavors(
        zone_id=None, multi_zone=True, refresh=False
    )
    volume_result = await handler.list_postgresql_volume_types(
        zone_id=None, multi_zone=True, refresh=False
    )
    for route in (flavors, volumes):
        params = route.calls.last.request.url.params
        assert params["multiZone"] == "true"
        assert "zoneId" not in params
    assert flavor_result.multi_zone is True and volume_result.multi_zone is True
    # The rows say which zone the Multi-AZ catalogue describes.
    assert flavor_result.items[0].zone_id == "HCM03-1A"


@pytest.mark.asyncio
async def test_postgresql_catalogues_refuse_a_zone_with_the_multi_zone_flag(handler):
    """The API would silently swap the zone for HCM03-1A; refuse rather than mislead."""
    with pytest.raises(ValueError, match="cannot be combined"):
        await handler.list_postgresql_flavors(zone_id="HCM03-1B", multi_zone=True, refresh=False)
    with pytest.raises(ValueError, match="cannot be combined"):
        await handler.list_postgresql_volume_types(
            zone_id="HCM03-1B", multi_zone=True, refresh=False
        )


@pytest.mark.asyncio
@respx.mock
async def test_postgresql_flavors_are_keyed_per_multi_zone_flag_in_the_cache(handler):
    """The flag changes the answer, so a cached single-zone list must not serve it.

    This is exactly the bug the API's own cache had on 2026-10-01: keyed on the
    zone alone, it served Multi-AZ and single-zone answers for one another.
    """
    mock_iam(respx.mock)

    def respond(request):
        flag = request.url.params.get("multiZone", "false")
        return httpx.Response(200, json=envelope([_pg_flavor(f"pgp-{flag}")]))

    respx.get(f"{POSTGRESQL}/v1/cluster/flavors").mock(side_effect=respond)
    single = await handler.list_postgresql_flavors(zone_id=None, multi_zone=False, refresh=False)
    multi = await handler.list_postgresql_flavors(zone_id=None, multi_zone=True, refresh=False)
    assert single.items[0].id != multi.items[0].id


# --------------------------------------------------------------------------
# Kafka catalogue
# --------------------------------------------------------------------------


KAFKA_CONFIGS = {
    # Every value is a string, and six of them are JSON inside that string.
    "kafkaVersions": '["3.6.0","3.6.1","3.7.0"]',
    "minKafkaBrokers": "3",
    "maxKafkaBrokers": "10",
    "minKafkaStorageSize": "20",
    "maxKafkaStorageSize": "5000",
    "maxClusterByUser": "10",
    "maxTopic": "50",
    "maxUser": "50",
    "maxConfigGroupByUser": "30",
    "maxClusterTags": "5",
    "minTopicPartitions": "1",
    "maxTopicPartitions": "2048",
    "minTopicReplicas": "1",
    "minTopicRetentionHours": "1",
    "maxTopicRetentionHours": "2160",
    "minTopicRetentionBytes": "1",
    "maxTopicRetentionBytes": "1099511627776",
    "secGroupRulePorts": "[9092,9094,9096,9194,9196]",
    "clusterNameRegex": "[a-zA-Z0-9][a-zA-Z0-9-]{3,48}[a-zA-Z0-9]",
    "topicNameRegex": "[a-zA-Z0-9][a-zA-Z0-9-_.]{3,247}[a-zA-Z0-9]",
    "configGroupNameRegex": "[a-zA-Z][a-zA-Z0-9-_ ]{3,48}[a-zA-Z0-9]",
    "commonValidateRegex": "[a-zA-Z0-9][a-zA-Z0-9-_]{3,48}[a-zA-Z0-9]",
    "defaultTopicSettingsByVersion": '{"3.7.0":{"partitions":1,"replicas":1}}',
    "configsForcedValues": '{"auto.create.topics.enable":{"value":"false"}}',
}


@pytest.mark.asyncio
@respx.mock
async def test_kafka_limits_decode_strings_and_nested_json(handler):
    """The one endpoint where the platform publishes its own validation rules."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/database/configs").mock(
        return_value=httpx.Response(200, json=envelope(KAFKA_CONFIGS))
    )
    limits = await handler.get_kafka_limits(refresh=False)
    # strings decoded to numbers
    assert limits.min_brokers == 3
    assert limits.max_brokers == 10
    assert limits.max_topic_retention_bytes == 1099511627776
    # JSON inside a string decoded to real structures
    assert limits.kafka_versions == ["3.6.0", "3.6.1", "3.7.0"]
    assert limits.security_rule_ports == [9092, 9094, 9096, 9194, 9196]
    assert limits.default_topic_settings_by_version["3.7.0"]["partitions"] == 1
    assert "auto.create.topics.enable" in limits.configs_forced_values
    assert limits.cluster_name_regex.startswith("[a-zA-Z0-9]")


@pytest.mark.asyncio
@respx.mock
async def test_kafka_limits_survive_one_malformed_field(handler):
    """A catalogue read must not lose 26 fields because one is unparseable."""
    mock_iam(respx.mock)
    broken = {**KAFKA_CONFIGS, "secGroupRulePorts": "not json at all"}
    respx.get(f"{KAFKA}/database/configs").mock(
        return_value=httpx.Response(200, json=envelope(broken))
    )
    limits = await handler.get_kafka_limits(refresh=False)
    assert limits.security_rule_ports == []
    assert limits.min_brokers == 3


@pytest.mark.asyncio
@respx.mock
async def test_kafka_flavors_expose_the_flav_id_a_create_needs(handler):
    """The row's own `id` is an integer and is NOT what a create takes."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/database/flavors").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "id": 173,
                        "name": "db-kafka.s-general-2x4-n10",
                        "description": "Instance types for common workloads",
                        "vcpus": 2,
                        "ram": 4,
                        "flavorId": "flav-cb5a525d-7378-4f87-a3aa-38781e36d1f6",
                        "locateZoneId": "HCM03-1A",
                        "packageSku": "db-kafka.s-general-2x4-n10",
                        "monthlyCost": 0.0,
                    }
                ]
            ),
        )
    )
    result = await handler.list_kafka_flavors(datastore_type=None, version=None, refresh=False)
    row = result.items[0]
    assert row.id == "173", "the raw integer id, kept as a string"
    assert row.flavor_id == "flav-cb5a525d-7378-4f87-a3aa-38781e36d1f6"


@pytest.mark.asyncio
@respx.mock
async def test_kafka_flavors_can_be_called_bare(handler):
    """Unlike the relational and memory catalogues, type/version are optional."""
    mock_iam(respx.mock)
    route = respx.get(f"{KAFKA}/database/flavors").mock(
        return_value=httpx.Response(200, json=envelope([]))
    )
    await handler.list_kafka_flavors(datastore_type=None, version=None, refresh=False)
    assert dict(route.calls.last.request.url.params) == {}


@pytest.mark.asyncio
@respx.mock
async def test_kafka_volume_types_are_nested_like_the_relational_ones(handler):
    """`{projectId, data[]}` here -- the PostgreSQL Cluster one is the bare array."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/database/volume-types").mock(
        return_value=httpx.Response(
            200,
            json=nested_list(
                [
                    {
                        "id": 40,
                        "type": "kafka.Gen2-NVMe2-IOPS3000",
                        "description": "NVME",
                        "minVolumeSize": 20,
                        "maxVolumeSize": 5000,
                        "kafkaUuid": "vtype-93a22a9f-1ec0-4e61-84fb-75ac181c13dc",
                        "iops": 3000,
                        "zoneId": "HCM03-1A",
                    }
                ]
            ),
        )
    )
    result = await handler.list_kafka_volume_types(refresh=False)
    assert result.count == 1
    assert result.items[0].min_volume_size == 20


@pytest.mark.asyncio
@respx.mock
async def test_kafka_volume_types_expose_the_vtype_id_a_create_needs(handler):
    """The row's own `id` is an integer; only `kafkaUuid` works in a request."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/database/volume-types").mock(
        return_value=httpx.Response(
            200,
            json=nested_list(
                [
                    {
                        "id": 40,
                        "type": "kafka.Gen2-NVMe2-IOPS3000",
                        "minVolumeSize": 20,
                        "maxVolumeSize": 5000,
                        "kafkaUuid": "vtype-93a22a9f-1ec0-4e61-84fb-75ac181c13dc",
                        "iops": 3000,
                    }
                ]
            ),
        )
    )
    row = (await handler.list_kafka_volume_types(refresh=False)).items[0]
    assert row.id == "40", "the raw integer id, kept as a string"
    assert row.kafka_uuid == "vtype-93a22a9f-1ec0-4e61-84fb-75ac181c13dc"


@pytest.mark.asyncio
@respx.mock
async def test_other_families_report_no_kafka_uuid(handler):
    """The field is Kafka's; a relational row must not invent one."""
    mock_iam(respx.mock)
    respx.get(f"{RELATIONAL}/v1/database-instances/volume/types").mock(
        return_value=httpx.Response(
            200,
            json=nested_list(
                [{"id": 1, "type": "Gen2-NVMe-IOPS5000", "minVolumeSize": 20, "iops": 5000}]
            ),
        )
    )
    row = (await handler.list_relational_volume_types(zone_id=None, refresh=False)).items[0]
    assert row.kafka_uuid == ""
    assert row.type == "Gen2-NVMe-IOPS5000"
