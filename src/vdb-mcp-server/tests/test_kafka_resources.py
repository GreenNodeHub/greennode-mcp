"""Tests for the Kafka topic, user and configuration-group handlers.

Three rules are what these tests exist to hold:

* a user's permission is a **list or a flag**, never both, because the platform
  ignores the list when the flag is set;
* a configuration group is **versioned and immutable**, so a cluster attaches
  to a `cgroupver-…` and a change means a new version;
* the credential download is a **ZIP of private keys**, so it is written to disk
  and gated, never returned.
"""

from __future__ import annotations

import httpx
import io
import json
import pydantic
import pytest
import respx
import zipfile
from greennode.mcp_core.auth import TokenManager
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import load_config
from greennode.vdb_mcp_server.kafka_config_handler import KafkaConfigHandler
from greennode.vdb_mcp_server.kafka_topic_handler import KafkaTopicHandler
from greennode.vdb_mcp_server.kafka_user_handler import KafkaUserHandler
from greennode.vdb_mcp_server.models import (
    CreateKafkaConfigGroupDto,
    CreateKafkaConfigGroupVersionDto,
    CreateKafkaTopicDto,
    CreateKafkaUserDto,
    KafkaConfigGroup,
    KafkaConfigGroupListData,
    KafkaCredentialBundleData,
    KafkaTopic,
    KafkaTopicListData,
    KafkaUser,
    KafkaUserListData,
    UpdateKafkaTopicDto,
    UpdateKafkaUserDto,
)
from mcp.server.mcpserver import MCPServer
from tests.helpers import KAFKA, mock_iam


CLUSTER_ID = "clus-4747994e-dcf6-487e-8ea6-7dd1f5ab21fd"
TOPIC_ID = "topic-1111-2222"
USER_ID = "user-6488b67f-1899-4f55-9f23-37de8bdf6de2"
GROUP_ID = "cgroup-a8aba2c4-c33b-4374-b155-0c6eaec74244"
VERSION_ID = "cgroupver-7748ded8-f6d2-479b-8901-472ed5167348"


def _topic(topic_id=TOPIC_ID, name="orders", partitions=3, replicas=3):
    return {
        "id": topic_id,
        "clusterId": CLUSTER_ID,
        "name": name,
        "partitions": partitions,
        "replicas": replicas,
        "retentionSeconds": 604800,
        "retentionBytes": -1,
        "portalUserId": 54549,
        "status": "ACTIVE",
        "createdAt": "2026-09-14T00:00:00.000+00:00",
    }


def _user(user_id=USER_ID, produce_all=False, produce=None):
    return {
        "id": user_id,
        "clusterId": CLUSTER_ID,
        "name": "user-d72b9fa3-05",
        "produceTopicNames": produce or [],
        "produceAll": produce_all,
        "consumeTopicNames": [],
        "consumeAll": False,
        "produceConsumeTopicNames": [],
        "produceConsumeAll": False,
        "adminTopicNames": [],
        "adminAll": False,
        "mtlsAuthen": True,
        "mtlsKafkaUser": None,
        "saslAuthen": False,
        "saslKafkaUser": None,
        "portalUserId": 54549,
        "oldStatus": None,
        "status": "ACTIVE",
        "createdAt": "2026-08-13T07:26:49.000+00:00",
    }


def _version(version_id=VERSION_ID, properties=None, clusters=None):
    return {
        "id": version_id,
        "configGroupId": GROUP_ID,
        "configGroupName": None,
        "version": 1,
        "properties": properties,
        "associatedClusterIds": clusters,
        "associatedClusterNames": None,
        "portalUserId": 54549,
        "createdAt": "2026-05-07T04:09:11.000+00:00",
    }


def _group(versions=None):
    return {
        "id": GROUP_ID,
        "name": "config-72d3f039-5f",
        "description": "",
        "versions": versions if versions is not None else [_version()],
        "portalUserId": 54549,
        "status": "ACTIVE",
        "createdAt": "2026-05-07T04:09:11.000+00:00",
    }


def _client(sample_config):
    config = load_config(sample_config)
    return config, VdbClient(config, TokenManager(config))


@pytest.fixture
def topics(sample_config):
    config, client = _client(sample_config)
    return KafkaTopicHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.fixture
def users(sample_config):
    config, client = _client(sample_config)
    return KafkaUserHandler(
        MCPServer("test"), config, client, allow_write=True, allow_sensitive_data_access=True
    )


@pytest.fixture
def users_no_secrets(sample_config):
    config, client = _client(sample_config)
    return KafkaUserHandler(
        MCPServer("test"), config, client, allow_write=True, allow_sensitive_data_access=False
    )


@pytest.fixture
def groups(sample_config):
    config, client = _client(sample_config)
    return KafkaConfigHandler(MCPServer("test"), config, client, allow_write=True)


# --------------------------------------------------------------------------
# topics
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_topics_list_unenveloped(topics):
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}/topics").mock(
        return_value=httpx.Response(200, json=[_topic(), _topic("topic-2", "events")])
    )
    result = await topics.list_kafka_topics(CLUSTER_ID)
    assert isinstance(result, KafkaTopicListData)
    assert result.count == 2
    assert result.cluster_id == CLUSTER_ID


@pytest.mark.asyncio
@respx.mock
async def test_creating_a_topic_returns_the_topic(topics):
    mock_iam(respx.mock)
    respx.post(f"{KAFKA}/clusters/{CLUSTER_ID}/topics").mock(
        return_value=httpx.Response(200, json=_topic())
    )
    topic = await topics.create_kafka_topic(
        CLUSTER_ID, CreateKafkaTopicDto(name="orders", partitions=3, replicas=3)
    )
    assert isinstance(topic, KafkaTopic)
    assert topic.partitions == 3


def test_a_topic_name_follows_the_published_regex():
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaTopicDto(name="ab")
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaTopicDto(name="has spaces")
    # dots are allowed in a topic name and nowhere else
    assert CreateKafkaTopicDto(name="orders.v1").name == "orders.v1"


def test_retention_bytes_takes_minus_one_for_unlimited_and_not_zero():
    assert CreateKafkaTopicDto(name="orders", retentionBytes=-1).retentionBytes == -1
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaTopicDto(name="orders", retentionBytes=0)
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaTopicDto(name="orders", retentionBytes=-5)


def test_retention_seconds_is_bounded_by_the_published_range():
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaTopicDto(name="orders", retentionSeconds=60)
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaTopicDto(name="orders", retentionSeconds=99999999)


def test_a_topic_update_requires_partitions_and_replicas():
    """Measured: omitting either produces a 400 that blames the value sent.

    Without `replicas` the API says "Can't update replicas for topic"; without
    `partitions` it complains the partition count is out of a range it was
    never given. Requiring both turns those into a schema error that names the
    missing field.
    """
    with pytest.raises(pydantic.ValidationError):
        UpdateKafkaTopicDto()
    with pytest.raises(pydantic.ValidationError):
        UpdateKafkaTopicDto(partitions=6)
    with pytest.raises(pydantic.ValidationError):
        UpdateKafkaTopicDto(replicas=1)
    with pytest.raises(pydantic.ValidationError):
        UpdateKafkaTopicDto(retentionSeconds=3600)
    dto = UpdateKafkaTopicDto(partitions=6, replicas=1)
    assert dto.model_dump(exclude_none=True) == {"partitions": 6, "replicas": 1}


@pytest.mark.asyncio
@respx.mock
async def test_a_topic_update_answers_with_a_bare_string(topics):
    mock_iam(respx.mock)
    respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/topics/{TOPIC_ID}").mock(
        return_value=httpx.Response(200, text="success")
    )
    result = await topics.update_kafka_topic(
        CLUSTER_ID, TOPIC_ID, UpdateKafkaTopicDto(partitions=6, replicas=3)
    )
    assert result.message == "success"
    assert result.resource_id == TOPIC_ID


@pytest.mark.asyncio
@respx.mock
async def test_a_json_encoded_string_response_is_read_too(topics):
    """The schema says only `type: string`; both encodings must work."""
    mock_iam(respx.mock)
    respx.delete(f"{KAFKA}/clusters/{CLUSTER_ID}/topics/{TOPIC_ID}").mock(
        return_value=httpx.Response(200, json="deleted")
    )
    result = await topics.delete_kafka_topic(CLUSTER_ID, TOPIC_ID)
    assert result.message == "deleted"


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_users_list_with_their_permissions(users):
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}/users").mock(
        return_value=httpx.Response(200, json=[_user()])
    )
    result = await users.list_kafka_users(CLUSTER_ID)
    assert isinstance(result, KafkaUserListData)
    assert result.items[0].mtls_authen is True


@pytest.mark.asyncio
@respx.mock
async def test_a_flag_and_a_list_are_both_surfaced_so_the_wider_one_is_visible(users):
    """The platform ignores the list when the flag is set; hiding either would lie."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}/users/{USER_ID}").mock(
        return_value=httpx.Response(200, json=_user(produce_all=True, produce=["orders"]))
    )
    user = await users.get_kafka_user(CLUSTER_ID, USER_ID)
    assert isinstance(user, KafkaUser)
    assert user.produce_all is True
    assert user.produce_topics == ["orders"]


def test_a_user_dto_refuses_a_flag_and_a_list_together():
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaUserDto(name="app-user", produceAll=True, produceTopicNames=["orders"])
    with pytest.raises(pydantic.ValidationError):
        UpdateKafkaUserDto(adminAll=True, adminTopicNames=["orders"])
    assert CreateKafkaUserDto(name="app-user", produceAll=True).produceAll is True


def test_a_user_name_follows_the_common_regex():
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaUserDto(name="ab")
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaUserDto(name="dots.not.allowed")
    assert CreateKafkaUserDto(name="app_user-1").name == "app_user-1"


@pytest.mark.asyncio
@respx.mock
async def test_regenerating_credentials_says_the_old_ones_stop_working(users):
    mock_iam(respx.mock)
    respx.put(f"{KAFKA}/clusters/{CLUSTER_ID}/users/{USER_ID}/regenerate-creds").mock(
        return_value=httpx.Response(200, text="success")
    )
    result = await users.update_kafka_user_credentials(CLUSTER_ID, USER_ID)
    assert "no longer work" in result.next_step


# --------------------------------------------------------------------------
# the credential archive
# --------------------------------------------------------------------------


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{USER_ID}/mtls/user.p12", "not-a-real-key")
        archive.writestr(f"{USER_ID}/mtls/user.password", "not-a-real-password")
        archive.writestr(f"{USER_ID}/mtls/ca.crt", "not-a-real-cert")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_the_credential_archive_is_refused_without_the_sensitive_flag(users_no_secrets):
    with pytest.raises(Exception, match="sensitive"):
        await users_no_secrets.get_kafka_user_credentials(CLUSTER_ID, USER_ID, "/tmp/x.zip")


@pytest.mark.asyncio
async def test_the_credential_tool_is_not_even_registered_without_the_flag(users_no_secrets):
    names = {t.name for t in await users_no_secrets.mcp.list_tools()}
    assert "get_kafka_user_credentials" not in names


@pytest.mark.asyncio
@respx.mock
async def test_the_credential_archive_is_written_to_disk_not_returned(users, tmp_path):
    """It is a ZIP of private keys -- the content must never reach the caller."""
    mock_iam(respx.mock)
    payload = _zip_bytes()
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}/users/{USER_ID}/authen-creds").mock(
        return_value=httpx.Response(
            200, content=payload, headers={"content-type": "application/octet-stream"}
        )
    )
    destination = tmp_path / "creds" / "user.zip"
    result = await users.get_kafka_user_credentials(CLUSTER_ID, USER_ID, str(destination))

    assert isinstance(result, KafkaCredentialBundleData)
    assert destination.read_bytes() == payload
    assert result.size_bytes == len(payload)
    assert any(name.endswith("user.p12") for name in result.entries)
    assert "private keys" in result.warning

    # The model must carry no field holding the bytes themselves.
    dumped = json.dumps(result.model_dump())
    assert "not-a-real-key" not in dumped
    assert "not-a-real-password" not in dumped


@pytest.mark.asyncio
@respx.mock
async def test_a_non_zip_body_is_still_saved_and_reported(users, tmp_path):
    """The bytes are on disk either way; the caller needs to know they are odd."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/clusters/{CLUSTER_ID}/users/{USER_ID}/authen-creds").mock(
        return_value=httpx.Response(200, content=b"not a zip at all")
    )
    destination = tmp_path / "user.zip"
    result = await users.get_kafka_user_credentials(CLUSTER_ID, USER_ID, str(destination))
    assert result.entries == []
    assert result.size_bytes == len(b"not a zip at all")


# --------------------------------------------------------------------------
# configuration groups
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_config_group_listing_rows_are_summaries(groups):
    """A listing row's version reports properties and clusters as null."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/config-groups").mock(return_value=httpx.Response(200, json=[_group()]))
    result = await groups.list_kafka_config_groups()
    assert isinstance(result, KafkaConfigGroupListData)
    assert result.rows_are_summaries is True
    assert result.items[0].versions[0].properties == {}


@pytest.mark.asyncio
@respx.mock
async def test_the_group_detail_adds_clusters_but_still_not_the_properties(groups):
    """Measured 2026-09-14: the detail fills associatedClusterIds and leaves
    properties null, exactly as the listing does. Only the version endpoint
    returns settings -- so a caller reading the group alone sees an empty set
    and could conclude the group configures nothing."""
    mock_iam(respx.mock)
    respx.get(f"{KAFKA}/config-groups/{GROUP_ID}").mock(
        return_value=httpx.Response(
            200, json=_group(versions=[_version(properties=None, clusters=[CLUSTER_ID])])
        )
    )
    group = await groups.get_kafka_config_group(GROUP_ID)
    assert isinstance(group, KafkaConfigGroup)
    version = group.versions[0]
    assert version.properties == {}, "the detail does not return settings"
    assert version.associated_cluster_ids == [CLUSTER_ID]


@pytest.mark.asyncio
@respx.mock
async def test_a_version_is_fetched_under_its_group(groups):
    mock_iam(respx.mock)
    route = respx.get(f"{KAFKA}/config-groups/{GROUP_ID}/versions/{VERSION_ID}").mock(
        return_value=httpx.Response(
            200, json=_version(properties=[{"key": "num.io.threads", "value": "8"}])
        )
    )
    version = await groups.get_kafka_config_group_version(GROUP_ID, VERSION_ID)
    assert route.called
    # The only endpoint in this family that returns a version's settings.
    assert version.properties == {"num.io.threads": "8"}


def test_a_config_group_name_may_contain_spaces():
    """The one name in this family whose regex allows them."""
    assert CreateKafkaConfigGroupDto(name="my kafka group").name == "my kafka group"
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaConfigGroupDto(name="1-starts-with-digit")


def test_a_version_needs_at_least_one_property():
    """A version is a complete set, so an empty one would silently unset everything."""
    with pytest.raises(pydantic.ValidationError):
        CreateKafkaConfigGroupVersionDto(properties=[])


@pytest.mark.asyncio
@respx.mock
async def test_creating_a_group_re_reads_it_because_the_response_omits_the_version(groups):
    """Measured 2026-09-14: the create answers `versions: []` even though
    version 1 exists. Returning that verbatim would hand back a group with no
    version id -- and a version id is the only thing a cluster can attach to."""
    mock_iam(respx.mock)
    respx.post(f"{KAFKA}/config-groups").mock(
        return_value=httpx.Response(200, json=_group(versions=[]))
    )
    detail = respx.get(f"{KAFKA}/config-groups/{GROUP_ID}").mock(
        return_value=httpx.Response(200, json=_group(versions=[_version()]))
    )
    group = await groups.create_kafka_config_group(
        CreateKafkaConfigGroupDto(
            name="my kafka group", properties=[{"key": "num.io.threads", "value": "8"}]
        )
    )
    assert detail.called, "the create response is too thin to return as-is"
    assert group.versions[0].id == VERSION_ID


@pytest.mark.asyncio
@respx.mock
async def test_a_failed_re_read_still_returns_the_created_group(groups):
    """The group exists whatever the second call does; losing its id would be worse."""
    mock_iam(respx.mock)
    respx.post(f"{KAFKA}/config-groups").mock(
        return_value=httpx.Response(200, json=_group(versions=[]))
    )
    respx.get(f"{KAFKA}/config-groups/{GROUP_ID}").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    group = await groups.create_kafka_config_group(
        CreateKafkaConfigGroupDto(name="my kafka group")
    )
    assert group.id == GROUP_ID
    assert group.versions == []


@pytest.mark.asyncio
@respx.mock
async def test_versions_are_reported_newest_first(groups):
    """Measured: a group with two versions lists version 2 before version 1."""
    mock_iam(respx.mock)
    v2 = _version(version_id="cgroupver-second")
    v2["version"] = 2
    respx.get(f"{KAFKA}/config-groups/{GROUP_ID}").mock(
        return_value=httpx.Response(200, json=_group(versions=[v2, _version()]))
    )
    group = await groups.get_kafka_config_group(GROUP_ID)
    assert [v.version for v in group.versions] == [2, 1]


@pytest.mark.asyncio
@respx.mock
async def test_deleting_a_group_answers_with_an_empty_body(groups):
    """Measured: no word, no document -- nothing. The listing is the evidence."""
    mock_iam(respx.mock)
    respx.delete(f"{KAFKA}/config-groups/{GROUP_ID}").mock(
        return_value=httpx.Response(200, content=b"")
    )
    result = await groups.delete_kafka_config_group(GROUP_ID)
    assert result.message == ""
    assert result.accepted is True
    assert result.resource_id == GROUP_ID
