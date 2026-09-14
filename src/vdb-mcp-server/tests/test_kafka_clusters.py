"""Tests for the Kafka cluster handler.

Fixtures mirror the shapes probed live on 2026-09-14. What these tests mostly
defend is that Kafka's disagreements with the rest of vDB survive a refactor:
no envelope, no `/v1`, query parameters instead of bodies, a real `DELETE`, and
security rules that exist only inside the cluster detail.
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
from greennode.vdb_mcp_server.kafka_cluster_handler import KafkaClusterHandler
from greennode.vdb_mcp_server.models import (
    CreateKafkaClusterDto,
    CreateKafkaSecurityRuleDto,
    DryRunData,
    KafkaActionData,
    KafkaCluster,
    KafkaClusterListData,
    KafkaHistoryListData,
    OrderData,
)
from mcp.server.mcpserver import MCPServer
from tests.helpers import KAFKA, envelope, mock_iam


CLUSTER_ID = "clus-4747994e-dcf6-487e-8ea6-7dd1f5ab21fd"
FLAVOR_ID = "flav-c81e9472-a63f-42d5-9b18-7ef34a0dc596"
STORAGE_TYPE_ID = "vtype-93a22a9f-1ec0-4e61-84fb-75ac181c13dc"
NETWORK_ID = "net-372ddaf7-6933-4f86-b0f9-564db84a67d4"
SUBNET_ID = "sub-66a5327f-1970-427d-b1a3-8eb146e94bab"
VERSION_ID = "cgroupver-7748ded8-f6d2-479b-8901-472ed5167348"


def _cluster(cluster_id=CLUSTER_ID, name="nhontt-test", status="ACTIVE", with_rules=False):
    """A cluster row. The listing has 29 fields; the detail adds securityGroupRules."""
    row = {
        "id": cluster_id,
        "name": name,
        "tags": {},
        "kafkaVersion": "3.7.0",
        "serverFlavorId": FLAVOR_ID,
        "kafkaBrokerCount": 3,
        "kafkaStorageType": STORAGE_TYPE_ID,
        "kafkaStorageSize": 20,
        "kafkaStorageUsage": [28053504, 950272, 1130496],
        "vserverProjectId": "pro-test-0001",
        "networkId": NETWORK_ID,
        "subnetId": SUBNET_ID,
        "fixedIps": ["10.5.1.7", "10.5.1.9", "10.5.1.8"],
        "floatingIps": [None, None, None],
        "publicAccess": False,
        "mtlsAuthen": True,
        "saslAuthen": False,
        "configGroupVersionId": "",
        "portalUserId": 54549,
        "status": status,
        "errorMessage": "",
        # Not ISO 8601, unlike every other timestamp in this family.
        "createdAt": "Aug 12, 2026, 3:10:00 PM",
        "encryptionVolume": False,
        "iops": 3000,
        "volumeType": "Gen2-NVMe2-IOPS3000",
        "volumeTypeZoneId": "63D9E33A-34F3-11EE-BE56-0242AC120002",
        "ram": 4,
        "vcpus": 2,
        "instanceType": "db-kafka.s-general-2x4",
    }
    if with_rules:
        row["securityGroupRules"] = [
            {
                "id": "sgrule-1111",
                "remoteIp": "10.0.0.0/8",
                "port": 9092,
                "status": "ACTIVE",
                "createdAt": "2026-08-13T07:26:49.000+00:00",
            }
        ]
    return row


ORDER_RESPONSE = envelope(
    [
        {
            "orderUrl": "https://vdb.console.vngcloud.vn/",
            "orderId": "ord-1",
            "resourceId": CLUSTER_ID,
        }
    ]
)


def _valid_create(**overrides):
    body = {
        "name": "mcp-kafka-test",
        "kafkaVersion": "3.7.0",
        "serverFlavorId": FLAVOR_ID,
        "kafkaBrokerCount": 3,
        "kafkaStorageType": STORAGE_TYPE_ID,
        "kafkaStorageSize": 20,
        "vserverProjectId": "pro-test-0001",
        "networkId": NETWORK_ID,
        "subnetId": SUBNET_ID,
    }
    body.update(overrides)
    return CreateKafkaClusterDto(**body)


def _handler(sample_config, allow_write):
    config = load_config(sample_config)
    client = VdbClient(config, TokenManager(config))
    return KafkaClusterHandler(MCPServer("test"), config, client, allow_write=allow_write)


@pytest.fixture
def handler(sample_config):
    return _handler(sample_config, allow_write=True)


@pytest.fixture
def readonly_handler(sample_config):
    return _handler(sample_config, allow_write=False)


# --------------------------------------------------------------------------
# the family's own conventions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_paths_carry_no_v1_prefix(handler):
    """Kafka is the only vDB family with unversioned paths."""
    mock_iam(respx.mock)
    route = respx.get(f"{KAFKA}/clusters").mock(return_value=httpx.Response(200, json=[]))
    await handler.list_kafka_clusters()
    assert route.called
    assert "/v1/" not in str(route.calls.last.request.url)


@pytest.mark.asyncio
@respx.mock
async def test_a_bare_array_with_no_envelope_is_read(handler):
    """The listing answers with the array itself, not {code, message, data}."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters").mock(
        return_value=httpx.Response(200, json=[_cluster(), _cluster("clus-two", "other")])
    )
    result = await handler.list_kafka_clusters()
    assert isinstance(result, KafkaClusterListData)
    assert result.count == 2


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_a_cluster_reports_its_create_ids_not_its_display_names(handler):
    """serverFlavorId and kafkaStorageType are what a create takes back."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=_cluster(with_rules=True))
    )
    cluster = await handler.get_kafka_cluster(CLUSTER_ID)
    assert isinstance(cluster, KafkaCluster)
    assert cluster.flavor_id == FLAVOR_ID
    assert cluster.storage_type_id == STORAGE_TYPE_ID
    assert cluster.storage_type == "Gen2-NVMe2-IOPS3000"


@pytest.mark.asyncio
@respx.mock
async def test_security_rules_come_only_from_the_detail(handler):
    """There is no endpoint that lists rules, and the listing omits the field."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters").mock(return_value=httpx.Response(200, json=[_cluster()]))
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=_cluster(with_rules=True))
    )
    listed = (await handler.list_kafka_clusters()).items[0]
    detailed = await handler.get_kafka_cluster(CLUSTER_ID)
    assert listed.security_group_rules == []
    assert len(detailed.security_group_rules) == 1
    assert detailed.security_group_rules[0].port == 9092


@pytest.mark.asyncio
@respx.mock
async def test_storage_usage_is_per_broker(handler):
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=_cluster())
    )
    cluster = await handler.get_kafka_cluster(CLUSTER_ID)
    assert len(cluster.storage_used_bytes) == cluster.broker_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_null_floating_ips_are_dropped_rather_than_reported(handler):
    """While public access is off the API sends [null, null, null]."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=_cluster())
    )
    cluster = await handler.get_kafka_cluster(CLUSTER_ID)
    assert cluster.floating_ips == []
    assert len(cluster.fixed_ips) == 3


@pytest.mark.asyncio
@respx.mock
async def test_histories_are_the_record_of_what_happened(handler):
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}/history").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "hist-1",
                    "clusterId": CLUSTER_ID,
                    "action": "Delete topic",
                    "description": "Name: cli-probe-two",
                    "status": "FINISHED",
                    "errorMessage": "",
                    "startedAt": "2026-08-17T03:09:05.000+00:00",
                    "finishedAt": "2026-08-17T03:09:36.000+00:00",
                }
            ],
        )
    )
    result = await handler.list_kafka_cluster_histories(CLUSTER_ID)
    assert isinstance(result, KafkaHistoryListData)
    assert result.items[0].action == "Delete topic"
    assert result.items[0].description == "Name: cli-probe-two"


@pytest.mark.asyncio
async def test_reads_reject_a_traversing_id(handler):
    with pytest.raises(ValueError):
        await handler.get_kafka_cluster("../../etc/passwd")


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


def test_create_enforces_the_three_broker_floor():
    """Kafka needs a quorum: 3, not the 2 a PostgreSQL Cluster allows."""
    with pytest.raises(pydantic.ValidationError):
        _valid_create(kafkaBrokerCount=2)
    with pytest.raises(pydantic.ValidationError):
        _valid_create(kafkaBrokerCount=11)


def test_create_enforces_the_published_name_rule():
    with pytest.raises(pydantic.ValidationError):
        _valid_create(name="ab")
    with pytest.raises(pydantic.ValidationError):
        _valid_create(name="has spaces")
    assert _valid_create(name="kafka-01").name == "kafka-01"


def test_create_rejects_an_unknown_field():
    with pytest.raises(pydantic.ValidationError):
        _valid_create(zoneId="HCM03-1A")  # Kafka has no zone


def test_create_always_sends_tags_even_when_empty():
    """Omitting `tags` makes the backend dereference null and answer a bare 500.

    Tagging was designed in and then dropped, but the validator still calls
    getTags().size(). The Portal sends `{}` on every create; so must we, which
    is why the field defaults to an empty map rather than to None -- a None
    would be dropped by exclude_none and bring the crash back.
    """
    body = _valid_create().model_dump(exclude_none=True)
    assert body["tags"] == {}


def test_create_keeps_tags_a_caller_supplies():
    body = _valid_create(tags={"env": "test"}).model_dump(exclude_none=True)
    assert body["tags"] == {"env": "test"}


@pytest.mark.asyncio
@respx.mock
async def test_the_create_on_the_wire_carries_tags(handler):
    mock_iam(respx.mock)
    respx.post(f"{KAFKA}/clusters").mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    await handler.create_kafka_cluster(_valid_create(), "IAM_USER")
    body = json.loads(respx.calls.last.request.read())
    assert "tags" in body, "a body without tags is answered with an empty 500"


@pytest.mark.asyncio
@respx.mock
async def test_create_posts_and_defaults_to_iam_user(handler):
    mock_iam(respx.mock)
    route = respx.post(f"{KAFKA}/clusters").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    result = await handler.create_kafka_cluster(_valid_create(), "IAM_USER")
    assert route.called
    assert isinstance(result, OrderData)
    assert respx.calls.last.request.headers["user-type"] == "IAM_USER"
    assert result.orders[0].resource_id == CLUSTER_ID


@pytest.mark.asyncio
async def test_create_dryrun_warns_about_the_per_broker_bill(handler):
    result = await handler.create_kafka_cluster_dryrun(_valid_create())
    assert isinstance(result, DryRunData)
    joined = " ".join(result.warnings)
    assert "BILLABLE" in joined
    assert "per broker" in joined
    assert "flav-" in joined and "vtype-" in joined
    assert "cgroupver-" in joined


# --------------------------------------------------------------------------
# the writes that use query parameters rather than a body
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_broker_resize_sends_query_parameters_and_no_body(handler):
    mock_iam(respx.mock)
    route = respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/kafka-broker-count").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    await handler.resize_kafka_cluster_brokers(CLUSTER_ID, 5, True, "IAM_USER")
    request = route.calls.last.request
    params = dict(request.url.params)
    assert params["count"] == "5"
    assert params["rebalance"] == "true"
    assert not request.read(), "this endpoint takes no body"


@pytest.mark.asyncio
@respx.mock
async def test_storage_resize_sends_the_size_as_a_query_parameter(handler):
    mock_iam(respx.mock)
    route = respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/kafka-storage-size").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    await handler.resize_kafka_cluster_storage(CLUSTER_ID, 100, "IAM_USER")
    assert dict(route.calls.last.request.url.params)["size"] == "100"


@pytest.mark.asyncio
@respx.mock
async def test_authentication_sends_both_flags_every_time(handler):
    """The endpoint replaces the whole setting, so both always go on the wire."""
    mock_iam(respx.mock)
    route = respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/authentication").mock(
        return_value=httpx.Response(200, text="success")
    )
    result = await handler.update_kafka_cluster_authentication(CLUSTER_ID, True, False)
    params = dict(route.calls.last.request.url.params)
    assert params == {"mtlsAuthen": "true", "saslAuthen": "false"}
    assert isinstance(result, KafkaActionData)


@pytest.mark.asyncio
@respx.mock
async def test_public_access_sends_a_query_flag(handler):
    mock_iam(respx.mock)
    route = respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/public-access").mock(
        return_value=httpx.Response(200, text="success")
    )
    await handler.update_kafka_cluster_public_access(CLUSTER_ID, True)
    assert dict(route.calls.last.request.url.params)["enable"] == "true"


@pytest.mark.asyncio
@respx.mock
async def test_config_group_attach_takes_a_version_id(handler):
    mock_iam(respx.mock)
    route = respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/config-group").mock(
        return_value=httpx.Response(200, text="success")
    )
    await handler.update_kafka_cluster_config_group(CLUSTER_ID, VERSION_ID)
    assert dict(route.calls.last.request.url.params)["configGroupVersionId"] == VERSION_ID


@pytest.mark.asyncio
@respx.mock
async def test_a_bare_string_response_is_reported_verbatim(handler):
    """Most Kafka writes answer with a word, not an object or an id."""
    mock_iam(respx.mock)
    respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/public-access").mock(
        return_value=httpx.Response(200, text="success")
    )
    result = await handler.update_kafka_cluster_public_access(CLUSTER_ID, False)
    assert result.message == "success"
    assert result.accepted is True
    assert "list_kafka_cluster_histories" in result.next_step


# --------------------------------------------------------------------------
# security rules and delete
# --------------------------------------------------------------------------


def test_a_security_rule_may_only_open_a_published_port():
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaSecurityRuleDto(remoteIp="10.0.0.0/8", port=8080)
    assert CreateKafkaSecurityRuleDto(remoteIp="10.0.0.0/8", port=9094).port == 9094


@pytest.mark.asyncio
@respx.mock
async def test_creating_a_rule_returns_the_rule_itself(handler):
    mock_iam(respx.mock)
    respx.post(f"{KAFKA}/clusters/{CLUSTER_ID}/security-group-rules").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "sgrule-9",
                "remoteIp": "10.0.0.0/8",
                "port": 9092,
                "status": "ACTIVE",
                "createdAt": "2026-09-14T00:00:00.000+00:00",
            },
        )
    )
    rule = await handler.create_kafka_cluster_secrule(
        CLUSTER_ID, CreateKafkaSecurityRuleDto(remoteIp="10.0.0.0/8", port=9092)
    )
    assert rule.id == "sgrule-9"
    body = json.loads(respx.calls.last.request.read())
    assert body == {"remoteIp": "10.0.0.0/8", "port": 9092}


@pytest.mark.asyncio
@respx.mock
async def test_delete_is_a_real_delete_with_no_action_envelope(handler):
    """Every other vDB family posts an {action, resType} envelope to /delete."""
    mock_iam(respx.mock)
    route = respx.delete(f"{KAFKA}/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, text="success")
    )
    result = await handler.delete_kafka_cluster(CLUSTER_ID)
    assert route.called
    assert not route.calls.last.request.read(), "no envelope, no body"
    assert isinstance(result, KafkaActionData)


# --------------------------------------------------------------------------
# write guard
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writes_are_refused_without_allow_write(readonly_handler):
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.create_kafka_cluster(_valid_create(), "IAM_USER")
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.delete_kafka_cluster(CLUSTER_ID)
    with pytest.raises(Exception, match="read-only|allow-write|write"):
        await readonly_handler.update_kafka_cluster_public_access(CLUSTER_ID, True)


@pytest.mark.asyncio
async def test_read_only_still_serves_the_dry_run(readonly_handler):
    assert await readonly_handler.create_kafka_cluster_dryrun(_valid_create())
