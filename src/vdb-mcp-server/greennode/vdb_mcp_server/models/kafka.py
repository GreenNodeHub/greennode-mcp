"""Response models for the Kafka family.

Kafka is the odd one out in three ways that shape everything here:

* **27 of its 36 operations carry no envelope.** Only the five catalogue reads
  and the four order flows answer `{code, message, data}`; the rest return a
  bare object, a bare array, or a bare string. `unwrap_wrapped` passes an
  unwrapped payload through untouched, so the same helpers still work -- but a
  response being "just the object" is the norm here, not the exception.
* **It has resources the other families do not**: topics, users with per-topic
  permissions, and configuration groups that are *versioned* -- a cluster
  attaches to a `cgroupver-…`, never to the group itself.
* **Its limits are published.** `GET /database/configs` returns the regexes and
  bounds the platform enforces, so :class:`KafkaLimits` is a real catalogue
  rather than a guess. Everything in it arrives as a **string**, and several
  values are JSON encoded *inside* that string.
"""

from __future__ import annotations

import json
from greennode.vdb_mcp_server.statuses import StatusKind, classify, describe
from pydantic import BaseModel, Field
from typing import Any


def _int_or_none(value: Any) -> int | None:
    """Parse one of the configs catalogue's string numbers."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _json_or_default(value: Any, default: Any) -> Any:
    """Parse a value that is JSON encoded inside a JSON string.

    Six fields of the configs catalogue are shaped that way. A parse failure
    returns the default rather than raising: this is a catalogue read, and one
    malformed field must not cost the caller the other twenty-six.
    """
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class KafkaCluster(BaseModel):
    """One Kafka cluster.

    `storage_type_id` and `flavor_id` are the ids a **create** takes, and
    neither is the id the catalogue lists first: a flavour row carries both an
    integer `id` and a `flav-…` `flavorId`, and a volume-type row carries an
    integer `id`, a `type` name and a `vtype-…` `kafkaUuid`. The `flav-` and
    `vtype-` forms are the ones that belong in a request.
    """

    id: str = Field("", description="Cluster ID, prefixed 'clus-'")
    name: str = Field("", description="Cluster name")
    status: str = Field("", description="Cluster status, verbatim from the API")
    status_kind: StatusKind = Field(
        "unknown",
        description=(
            "What the status means to act on: settled, transitional (poll again), failed "
            "(a platform fault to report), attention (needs a user decision), or unknown."
        ),
    )
    status_guidance: str = Field(
        "", description="What to do about this status, derived from status_kind"
    )
    error_message: str = Field("", description="Platform error text, when the status is a failure")
    kafka_version: str = Field("", description="Kafka version, e.g. '3.7.0'")
    broker_count: int | None = Field(None, description="Brokers in the cluster (3-10)")
    flavor_id: str = Field(
        "", description="Broker flavour ID ('flav-…') -- a create's serverFlavorId"
    )
    vcpus: int | None = Field(None, description="vCPU per broker")
    ram_gb: int | None = Field(None, description="RAM in GB per broker")
    instance_type: str = Field("", description="Broker instance type name")
    storage_type_id: str = Field(
        "", description="Storage type ID ('vtype-…') -- a create's kafkaStorageType"
    )
    storage_type: str = Field("", description="Storage type display name")
    storage_size_gb: int | None = Field(None, description="Storage per broker in GB")
    storage_used_bytes: list[int] = Field(
        default_factory=list,
        description="Bytes used, one entry per broker -- not a single total",
    )
    iops: int | None = Field(None, description="Provisioned IOPS")
    encryption_volume: bool | None = Field(None, description="Whether the volume is encrypted")
    network_id: str = Field("", description="Network the cluster sits in")
    subnet_id: str = Field("", description="Subnet the cluster sits in")
    fixed_ips: list[str] = Field(default_factory=list, description="Private address per broker")
    floating_ips: list[str] = Field(
        default_factory=list,
        description="Public address per broker; entries are null while public access is off",
    )
    public_access: bool | None = Field(None, description="Whether public access is enabled")
    mtls_authen: bool | None = Field(None, description="Whether mTLS authentication is enabled")
    sasl_authen: bool | None = Field(None, description="Whether SASL authentication is enabled")
    config_group_version_id: str = Field(
        "",
        description=(
            "Attached configuration group VERSION ('cgroupver-…'), empty when none. A cluster "
            "never attaches to a group, only to one of its versions."
        ),
    )
    security_group_rules: list["KafkaSecurityRule"] = Field(
        default_factory=list,
        description=(
            "Firewall rules. Present only on get_kafka_cluster -- the listing omits the "
            "field entirely, and there is no endpoint that lists rules on their own."
        ),
    )
    tags: dict[str, Any] = Field(default_factory=dict, description="Cluster tags")
    created: str = Field(
        "",
        description=(
            "Creation timestamp. Formatted 'Aug 12, 2026, 3:10:00 PM' here, where every other "
            "Kafka timestamp is ISO 8601 -- passed through as the API wrote it."
        ),
    )

    @classmethod
    def from_api(cls, data: dict) -> "KafkaCluster":
        """Build from a raw `KafkaCluster` row (listing: 29 fields, detail: 30)."""
        status = data.get("status") or ""
        rules = data.get("securityGroupRules") or []
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            error_message=data.get("errorMessage") or "",
            kafka_version=data.get("kafkaVersion") or "",
            broker_count=data.get("kafkaBrokerCount"),
            flavor_id=data.get("serverFlavorId") or "",
            vcpus=data.get("vcpus"),
            ram_gb=data.get("ram"),
            instance_type=data.get("instanceType") or "",
            storage_type_id=data.get("kafkaStorageType") or "",
            storage_type=data.get("volumeType") or "",
            storage_size_gb=data.get("kafkaStorageSize"),
            storage_used_bytes=[v for v in (data.get("kafkaStorageUsage") or []) if v is not None],
            iops=data.get("iops"),
            encryption_volume=data.get("encryptionVolume"),
            network_id=data.get("networkId") or "",
            subnet_id=data.get("subnetId") or "",
            fixed_ips=[ip for ip in (data.get("fixedIps") or []) if ip],
            floating_ips=[ip for ip in (data.get("floatingIps") or []) if ip],
            public_access=data.get("publicAccess"),
            mtls_authen=data.get("mtlsAuthen"),
            sasl_authen=data.get("saslAuthen"),
            config_group_version_id=data.get("configGroupVersionId") or "",
            security_group_rules=[KafkaSecurityRule.from_api(r) for r in rules],
            tags=data.get("tags") or {},
            created=data.get("createdAt") or "",
        )


class KafkaClusterListData(BaseModel):
    """Every Kafka cluster in the project.

    Unpaginated and unfiltered -- the endpoint answers with the whole array.
    Rows omit `securityGroupRules`; `get_kafka_cluster` is the only way to see
    them.
    """

    count: int = Field(0, description="Clusters returned")
    items: list[KafkaCluster] = Field(default_factory=list, description="Clusters")


class KafkaSecurityRule(BaseModel):
    """One firewall rule on a Kafka cluster."""

    id: str = Field("", description="Rule ID")
    remote_ip: str = Field("", description="CIDR allowed to connect")
    port: int | None = Field(None, description="Port allowed")
    status: str = Field("", description="Rule status")
    created: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "KafkaSecurityRule":
        """Build from a raw `SecurityGroupRuleDto`."""
        return cls(
            id=data.get("id") or "",
            remote_ip=data.get("remoteIp") or "",
            port=data.get("port"),
            status=data.get("status") or "",
            created=data.get("createdAt") or "",
        )


class KafkaTopic(BaseModel):
    """One Kafka topic."""

    id: str = Field("", description="Topic ID")
    cluster_id: str = Field("", description="Cluster the topic belongs to")
    name: str = Field("", description="Topic name")
    partitions: int | None = Field(None, description="Partition count")
    replicas: int | None = Field(None, description="Replication factor")
    retention_seconds: int | None = Field(None, description="Retention time in seconds")
    retention_bytes: int | None = Field(
        None, description="Retention size in bytes; -1 means unlimited"
    )
    status: str = Field("", description="Topic status, verbatim")
    status_kind: StatusKind = Field("unknown", description="What the status means to act on")
    status_guidance: str = Field("", description="What to do about this status")
    created: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "KafkaTopic":
        """Build from a raw `TopicDto`."""
        status = data.get("status") or ""
        return cls(
            id=data.get("id") or "",
            cluster_id=data.get("clusterId") or "",
            name=data.get("name") or "",
            partitions=data.get("partitions"),
            replicas=data.get("replicas"),
            retention_seconds=data.get("retentionSeconds"),
            retention_bytes=data.get("retentionBytes"),
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            created=data.get("createdAt") or "",
        )


class KafkaTopicListData(BaseModel):
    """The topics of one cluster. Unpaginated."""

    cluster_id: str = Field("", description="Cluster the topics belong to")
    count: int = Field(0, description="Topics returned")
    items: list[KafkaTopic] = Field(default_factory=list, description="Topics")


class KafkaUser(BaseModel):
    """One Kafka user, with its per-topic permissions.

    Permissions come in four kinds -- produce, consume, produce+consume and
    admin -- and each is either a list of topic names or an `*_all` flag. When
    the flag is set the list is **ignored** by the platform, so a user showing
    `produce_all` with a non-empty `produce_topics` has wider access than the
    list suggests.
    """

    id: str = Field("", description="User ID, prefixed 'user-'")
    cluster_id: str = Field("", description="Cluster the user belongs to")
    name: str = Field("", description="User name")
    produce_topics: list[str] = Field(
        default_factory=list, description="Topics the user may produce to"
    )
    produce_all: bool | None = Field(
        None, description="May produce to every topic; overrides produce_topics"
    )
    consume_topics: list[str] = Field(
        default_factory=list, description="Topics the user may consume from"
    )
    consume_all: bool | None = Field(
        None, description="May consume from every topic; overrides consume_topics"
    )
    produce_consume_topics: list[str] = Field(
        default_factory=list, description="Topics the user may both produce to and consume from"
    )
    produce_consume_all: bool | None = Field(
        None, description="May produce and consume on every topic"
    )
    admin_topics: list[str] = Field(
        default_factory=list, description="Topics the user administers"
    )
    admin_all: bool | None = Field(None, description="Administers every topic")
    mtls_authen: bool | None = Field(None, description="Whether the user authenticates by mTLS")
    sasl_authen: bool | None = Field(None, description="Whether the user authenticates by SASL")
    status: str = Field("", description="User status, verbatim")
    status_kind: StatusKind = Field("unknown", description="What the status means to act on")
    status_guidance: str = Field("", description="What to do about this status")
    created: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "KafkaUser":
        """Build from a raw `UserDto`.

        `mtlsKafkaUser` / `saslKafkaUser` are dropped: they are the broker-side
        principal names, and on this account both are null even for a user with
        mTLS enabled.
        """
        status = data.get("status") or ""
        return cls(
            id=data.get("id") or "",
            cluster_id=data.get("clusterId") or "",
            name=data.get("name") or "",
            produce_topics=[t for t in (data.get("produceTopicNames") or []) if t],
            produce_all=data.get("produceAll"),
            consume_topics=[t for t in (data.get("consumeTopicNames") or []) if t],
            consume_all=data.get("consumeAll"),
            produce_consume_topics=[t for t in (data.get("produceConsumeTopicNames") or []) if t],
            produce_consume_all=data.get("produceConsumeAll"),
            admin_topics=[t for t in (data.get("adminTopicNames") or []) if t],
            admin_all=data.get("adminAll"),
            mtls_authen=data.get("mtlsAuthen"),
            sasl_authen=data.get("saslAuthen"),
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            created=data.get("createdAt") or "",
        )


class KafkaUserListData(BaseModel):
    """The users of one cluster. Unpaginated."""

    cluster_id: str = Field("", description="Cluster the users belong to")
    count: int = Field(0, description="Users returned")
    items: list[KafkaUser] = Field(default_factory=list, description="Users")


class KafkaCredentialBundleData(BaseModel):
    """What a user's credential download contains -- never the material itself.

    The endpoint answers with a **ZIP archive** of mTLS keystores, certificates
    and their passwords, not the `array of string` the spec describes. Those
    bytes are private keys: this model reports what the archive holds and where
    it was saved, and the content never passes through the conversation.
    """

    cluster_id: str = Field("", description="Cluster the user belongs to")
    user_id: str = Field("", description="User the credentials belong to")
    saved_to: str = Field("", description="Absolute path the archive was written to")
    size_bytes: int | None = Field(None, description="Size of the archive")
    entries: list[str] = Field(default_factory=list, description="File names inside the archive")
    warning: str = Field(
        "",
        description=(
            "Always set: the archive holds private keys and passwords. Say where the file is "
            "and what it contains; never read it back into the conversation."
        ),
    )


class KafkaConfigGroupVersion(BaseModel):
    """One version of a Kafka configuration group.

    A cluster attaches to a **version**, never to the group: `create_kafka_…`
    and `update_kafka_cluster_config_group` both take a `cgroupver-…` id.
    Versions are immutable -- changing settings means creating another one.
    """

    id: str = Field("", description="Version ID, prefixed 'cgroupver-'")
    config_group_id: str = Field("", description="Group this version belongs to")
    config_group_name: str = Field("", description="Group name, when the API fills it in")
    version: int | None = Field(None, description="Version number, counting from 1")
    properties: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The broker settings this version sets, flattened from the API's "
            "[{key, value}] list. Empty on a listing row **and** on the group detail -- "
            "get_kafka_config_group_version is the only endpoint that returns them."
        ),
    )
    associated_cluster_ids: list[str] = Field(
        default_factory=list, description="Clusters currently using this version"
    )
    associated_cluster_names: list[str] = Field(
        default_factory=list, description="Names of those clusters"
    )
    created: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "KafkaConfigGroupVersion":
        """Build from a raw `ConfigGroupVersionDto`."""
        raw = data.get("properties") or []
        properties = {
            str(p.get("key")): str(p.get("value"))
            for p in raw
            if isinstance(p, dict) and p.get("key") is not None
        }
        return cls(
            id=data.get("id") or "",
            config_group_id=data.get("configGroupId") or "",
            config_group_name=data.get("configGroupName") or "",
            version=data.get("version"),
            properties=properties,
            associated_cluster_ids=[c for c in (data.get("associatedClusterIds") or []) if c],
            associated_cluster_names=[c for c in (data.get("associatedClusterNames") or []) if c],
            created=data.get("createdAt") or "",
        )


class KafkaConfigGroup(BaseModel):
    """One Kafka configuration group, with its versions nested."""

    id: str = Field("", description="Group ID, prefixed 'cgroup-'")
    name: str = Field("", description="Group name")
    description: str = Field("", description="Group description")
    status: str = Field("", description="Group status")
    versions: list[KafkaConfigGroupVersion] = Field(
        default_factory=list,
        description=(
            "Versions of this group, **newest first** -- measured. A cluster attaches to one "
            "of these, not to the group. A create response carries none of them even though "
            "version 1 exists; the tool re-reads the group so this is populated."
        ),
    )
    created: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "KafkaConfigGroup":
        """Build from a raw `ConfigGroupDto`."""
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            description=data.get("description") or "",
            status=data.get("status") or "",
            versions=[KafkaConfigGroupVersion.from_api(v) for v in (data.get("versions") or [])],
            created=data.get("createdAt") or "",
        )


class KafkaConfigGroupListData(BaseModel):
    """Every Kafka configuration group in the project. Unpaginated."""

    count: int = Field(0, description="Groups returned")
    rows_are_summaries: bool = Field(
        True,
        description=(
            "Always true. A listing row's nested versions report `properties` and "
            "`associatedClusterIds` as null. get_kafka_config_group fills in the cluster "
            "ids but NOT the properties -- only get_kafka_config_group_version does."
        ),
    )
    items: list[KafkaConfigGroup] = Field(default_factory=list, description="Configuration groups")


class KafkaHistoryEntry(BaseModel):
    """One entry of a cluster's history.

    As in the other families, this is where an asynchronous failure is
    recorded: `description` says what the platform understood the request to
    be, and `error_message` says why it stopped.
    """

    id: str = Field("", description="History entry ID")
    cluster_id: str = Field("", description="Cluster the entry belongs to")
    action: str = Field("", description="What was requested, e.g. 'Delete topic'")
    description: str = Field("", description="What the platform understood the request to be")
    status: str = Field("", description="Outcome, e.g. 'FINISHED'")
    error_message: str = Field("", description="Why it failed, when it did")
    started: str = Field("", description="Start timestamp")
    finished: str = Field("", description="Finish timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "KafkaHistoryEntry":
        """Build from a raw `HistoryDto`."""
        return cls(
            id=data.get("id") or "",
            cluster_id=data.get("clusterId") or "",
            action=data.get("action") or "",
            description=data.get("description") or "",
            status=data.get("status") or "",
            error_message=data.get("errorMessage") or "",
            started=data.get("startedAt") or "",
            finished=data.get("finishedAt") or "",
        )


class KafkaHistoryListData(BaseModel):
    """One cluster's history. Unpaginated, newest first."""

    cluster_id: str = Field("", description="Cluster the history belongs to")
    count: int = Field(0, description="Entries returned")
    items: list[KafkaHistoryEntry] = Field(default_factory=list, description="History entries")


class KafkaLimits(BaseModel):
    """The bounds and name rules the Kafka platform enforces.

    From `GET /database/configs`, which is the only place in vDB where the
    platform publishes its own validation rules instead of answering a
    violation with an opaque error. Read it before building a create.

    Every value arrives as a **string**, and six of them are JSON encoded
    inside that string; both are decoded here.
    """

    kafka_versions: list[str] = Field(
        default_factory=list, description="Versions a cluster can be created with"
    )
    min_brokers: int | None = Field(
        None, description="Fewest brokers a cluster may have -- measured 3, not 2"
    )
    max_brokers: int | None = Field(None, description="Most brokers a cluster may have")
    min_storage_gb: int | None = Field(None, description="Smallest storage per broker")
    max_storage_gb: int | None = Field(None, description="Largest storage per broker")
    max_clusters: int | None = Field(None, description="Clusters one account may hold")
    max_topics: int | None = Field(None, description="Topics one cluster may hold")
    max_users: int | None = Field(None, description="Users one cluster may hold")
    max_config_groups: int | None = Field(
        None, description="Configuration groups one account may hold"
    )
    max_cluster_tags: int | None = Field(None, description="Tags one cluster may carry")
    min_topic_partitions: int | None = Field(
        None, description="Fewest partitions a topic may have"
    )
    max_topic_partitions: int | None = Field(None, description="Most partitions a topic may have")
    min_topic_replicas: int | None = Field(
        None,
        description=(
            "Fewest replicas a topic may have. The ceiling is not published here: a topic's "
            "replication factor may not exceed the cluster's broker count."
        ),
    )
    min_topic_retention_hours: int | None = Field(None, description="Shortest topic retention")
    max_topic_retention_hours: int | None = Field(None, description="Longest topic retention")
    min_topic_retention_bytes: int | None = Field(
        None, description="Smallest topic retention size"
    )
    max_topic_retention_bytes: int | None = Field(None, description="Largest topic retention size")
    security_rule_ports: list[int] = Field(
        default_factory=list,
        description=("The ONLY ports a security-group rule may open. Any other port is rejected."),
    )
    cluster_name_regex: str = Field("", description="Pattern a cluster name must match")
    topic_name_regex: str = Field("", description="Pattern a topic name must match")
    config_group_name_regex: str = Field(
        "", description="Pattern a configuration group name must match"
    )
    common_name_regex: str = Field(
        "", description="Pattern other names (users among them) must match"
    )
    default_topic_settings_by_version: dict[str, Any] = Field(
        default_factory=dict,
        description="Partition and replica defaults the platform applies, keyed by Kafka version",
    )
    configs_forced_values: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Broker settings the platform overrides whatever a configuration group says, "
            "keyed by setting name. Setting one of these in a group has no effect."
        ),
    )

    @classmethod
    def from_api(cls, data: dict) -> "KafkaLimits":
        """Build from the raw configs map, decoding strings and nested JSON."""
        return cls(
            kafka_versions=[str(v) for v in _json_or_default(data.get("kafkaVersions"), [])],
            min_brokers=_int_or_none(data.get("minKafkaBrokers")),
            max_brokers=_int_or_none(data.get("maxKafkaBrokers")),
            min_storage_gb=_int_or_none(data.get("minKafkaStorageSize")),
            max_storage_gb=_int_or_none(data.get("maxKafkaStorageSize")),
            max_clusters=_int_or_none(data.get("maxClusterByUser")),
            max_topics=_int_or_none(data.get("maxTopic")),
            max_users=_int_or_none(data.get("maxUser")),
            max_config_groups=_int_or_none(data.get("maxConfigGroupByUser")),
            max_cluster_tags=_int_or_none(data.get("maxClusterTags")),
            min_topic_partitions=_int_or_none(data.get("minTopicPartitions")),
            max_topic_partitions=_int_or_none(data.get("maxTopicPartitions")),
            min_topic_replicas=_int_or_none(data.get("minTopicReplicas")),
            min_topic_retention_hours=_int_or_none(data.get("minTopicRetentionHours")),
            max_topic_retention_hours=_int_or_none(data.get("maxTopicRetentionHours")),
            min_topic_retention_bytes=_int_or_none(data.get("minTopicRetentionBytes")),
            max_topic_retention_bytes=_int_or_none(data.get("maxTopicRetentionBytes")),
            security_rule_ports=[
                p
                for p in _json_or_default(data.get("secGroupRulePorts"), [])
                if isinstance(p, int)
            ],
            cluster_name_regex=data.get("clusterNameRegex") or "",
            topic_name_regex=data.get("topicNameRegex") or "",
            config_group_name_regex=data.get("configGroupNameRegex") or "",
            common_name_regex=data.get("commonValidateRegex") or "",
            default_topic_settings_by_version=_json_or_default(
                data.get("defaultTopicSettingsByVersion"), {}
            ),
            configs_forced_values=_json_or_default(data.get("configsForcedValues"), {}),
        )


class KafkaActionData(BaseModel):
    """The result of a Kafka operation that answers with a bare string.

    Most of Kafka's writes return neither an object nor an id -- just a word.
    The string is reported verbatim rather than interpreted, and `next_step`
    says where to confirm what actually happened.
    """

    cluster_id: str = Field("", description="Cluster the action was sent to")
    resource_id: str = Field("", description="Topic, user or rule the action was sent to, if any")
    message: str = Field("", description="What the API answered, verbatim")
    accepted: bool = Field(
        True,
        description=(
            "The request was accepted, not that it finished -- Kafka applies changes "
            "asynchronously."
        ),
    )
    next_step: str = Field("", description="How to confirm the change actually landed")
