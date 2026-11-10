"""Response models for the Redis Cluster family.

Redis Cluster is the fifth vDB family, and the first to overlap another one's
**service**: its controller mounts at `/v1/redis-cluster` on the memory
service, so the family is separated by path, not by host.

Two things the wire shapes forced on this file:

* **Its backups are vBackup resources**, reached through the memory service's
  `/v1/backups` tree -- the very same `BackupDatabase` / `BackupPoint` Java
  classes the PostgreSQL Cluster family reads, so the backup models here
  subclass those projections rather than duplicate them. Only the docstrings
  differ: the advice inside them names this family's tools.
* **The cluster row carries the config group id directly** (`configGroupId`),
  where the relational listing hides it in a nested object and reports null at
  the top level. No nested fallback is needed here.
"""

from __future__ import annotations

from greennode.vdb_mcp_server.models._common import PageInfo
from greennode.vdb_mcp_server.models.postgresql import (
    PostgresqlBackup,
    PostgresqlBackupListData,
    PostgresqlBackupNowData,
    PostgresqlRestorePoint,
    PostgresqlRestorePointListData,
)
from greennode.vdb_mcp_server.statuses import StatusKind, classify, describe
from pydantic import BaseModel, Field
from typing import Any


def _first_int(data: dict, *keys: str) -> int | None:
    """First non-null int among *keys* -- the row names its size twice."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return int(value)
    return None


def _port(value: Any) -> int | None:
    """Parse a port the spec types loosely; null while the cluster is building."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class RedisAddressInfo(BaseModel):
    """One address inside the cluster's `address` map."""

    addr: str = Field("", description="The address itself")
    type: str = Field("", description="Address type as the API words it")
    subnet: str = Field("", description="Subnet the address belongs to")

    @classmethod
    def from_api(cls, data: dict) -> "RedisAddressInfo":
        """Build from one raw `AddressInfo` entry."""
        return cls(
            addr=data.get("addr") or "",
            type=data.get("type") or "",
            subnet=data.get("subnet") or "",
        )


class RedisLoadBalancerIp(BaseModel):
    """One proxy node in front of a cluster deployed with `enableProxies`."""

    id: str = Field("", description="Proxy node ID")
    instance_id: str = Field("", description="Instance the proxy fronts")
    ip: str = Field("", description="Proxy address")
    hostname: str = Field("", description="Proxy hostname")
    is_master: bool | None = Field(None, description="Whether this is the master proxy")
    status: str = Field("", description="Proxy status, verbatim")
    created: str = Field("", description="Creation timestamp, as the API words it")
    updated: str = Field("", description="Last update timestamp, as the API words it")

    @classmethod
    def from_api(cls, data: dict) -> "RedisLoadBalancerIp":
        """Build from one raw `RedisClusterLoadBalancerIP` row."""
        return cls(
            id=data.get("id") or "",
            instance_id=data.get("instanceId") or "",
            ip=data.get("ip") or "",
            hostname=data.get("hostname") or "",
            is_master=data.get("isMaster"),
            status=data.get("status") or "",
            created=str(data.get("createdAt") or ""),
            updated=str(data.get("updatedAt") or ""),
        )


class RedisCluster(BaseModel):
    """One Redis Cluster.

    The row reports its size twice over: `number_of_nodes` for a non-sharding
    cluster, `number_of_shards` does **not** exist on the response (only the
    create takes it) -- a sharding cluster reports `number_of_nodes` as its
    total node count and `number_of_replicas` per shard.
    """

    id: str = Field("", description="Cluster ID")
    name: str = Field("", description="Cluster name")
    status: str = Field("", description="Cluster status, verbatim from the API")
    status_kind: StatusKind = Field(
        "unknown",
        description=(
            "What the status means to act on: settled, transitional (poll again), failed "
            "(a platform fault to report), attention (needs a user decision), or unknown. "
            "'TRASH' is the terminal soft-delete state."
        ),
    )
    status_guidance: str = Field(
        "", description="What to do about this status, derived from status_kind"
    )
    deleted: bool | None = Field(
        None, description="True once the cluster has been soft-deleted into TRASH"
    )
    deployment_mode: str = Field("", description="Deployment mode: 'sharding' or 'non-sharding'")
    deploy_type: str = Field("", description="Deployment type; 'cluster' for this family")
    datastore_type: str = Field("", description="Engine; 'Redis' for this family")
    datastore_version: str = Field(
        "",
        description="Engine version, e.g. 'v7.2.13' -- the catalogue's v-prefixed string",
    )
    version_name: str = Field("", description="Engine version display name")
    number_of_nodes: int | None = Field(
        None,
        description=(
            "Nodes in the cluster. For a sharding cluster this is the total across shards, "
            "not the shard count -- the response does not carry numberOfShards."
        ),
    )
    number_of_replicas: int | None = Field(
        None, description="Replicas per shard (sharding) or per master node (non-sharding)"
    )
    vcpus: int | None = Field(None, description="vCPU count per node")
    ram_gb: int | None = Field(None, description="RAM in GB per node")
    flavor_id: str = Field(
        "",
        description="Current flavour ID, from the raw `packageId`. What a FLAVOR resize expects.",
    )
    flavor_name: str = Field("", description="Current flavour name")
    volume_size_gb: int | None = Field(None, description="Storage size in GB per node")
    volume_type: str = Field("", description="Storage type name")
    volume_type_id: str = Field(
        "", description="Storage type ID -- what a VOLUME-TYPE resize expects"
    )
    encryption: bool | None = Field(None, description="Whether the nodes' volumes are encrypted")
    zone_id: str = Field("", description="Availability zone, e.g. 'HCM03-1A'")
    public_access: bool | None = Field(None, description="Whether public access is enabled")
    port: int | None = Field(None, description="Port the cluster listens on; Redis uses 6379")
    domain_name: str = Field("", description="Cluster DNS name")
    hostname: str = Field("", description="Cluster hostname")
    address: dict[str, list[RedisAddressInfo]] = Field(
        default_factory=dict,
        description=(
            "Addresses keyed by role as the API words it (verbatim keys, unmeasured). "
            "Each value lists that role's addresses."
        ),
    )
    fixed_ip: str = Field("", description="Fixed private address, when the row reports one")
    floating_ip: str = Field("", description="Floating public address, when public access is on")
    lb_info: list[RedisLoadBalancerIp] = Field(
        default_factory=list,
        description="Proxy nodes in front of the cluster, populated when enableProxies is on",
    )
    redis_password_enabled: bool | None = Field(
        None, description="Whether Redis AUTH is enabled on the cluster"
    )
    enable_proxies: bool | None = Field(None, description="Whether connection proxies are on")
    pool_max_connections: int | None = Field(None, description="Proxy pool connection limit")
    enable_slowlog: bool | None = Field(None, description="Whether the Redis slowlog is on")
    slowlog_log_slower_than_us: int | None = Field(
        None,
        description=(
            "Redis 'slowlog-log-slower-than' in microseconds; 0 logs every command, "
            "negative disables logging"
        ),
    )
    config_group_id: str = Field(
        "",
        description=(
            "Attached configuration group ID. Unlike the relational listing this row "
            "carries it directly -- no nested fallback needed."
        ),
    )
    backup_auto: bool | None = Field(
        None, description="Whether the automatic backup schedule is on"
    )
    backup_time: str = Field("", description="Automatic backup start time, HH:mm (UTC+7)")
    backup_duration: int | None = Field(
        None, description="Automatic backup retention in DAYS (2-14 on every write schema)"
    )
    free_backup_size_gb: int | None = Field(
        None, description="Free backup allowance this cluster's flavour grants, in GB"
    )
    replica_source_id: str = Field(
        "", description="Source cluster ID when this cluster is a replica"
    )
    replica_source_name: str = Field(
        "", description="Source cluster name when this cluster is a replica"
    )
    replicas: list[str] = Field(
        default_factory=list, description="Replica references as the API words them"
    )
    created: str = Field("", description="Creation timestamp, as the API words it")
    updated: str = Field("", description="Last update timestamp, as the API words it")

    @classmethod
    def from_api(cls, data: dict) -> "RedisCluster":
        """Build from one raw `RedisCluster` row.

        The raw row names its size twice: `volumeSize` and `storageSize`,
        `volumeTypeId` and `storageTypeId`, `packageName` and `flavor`. Which
        of each pair is populated has not been measured, so each projection
        reads its primary and falls back to its twin rather than guessing.
        """
        status = data.get("status") or ""
        address = {
            key: [RedisAddressInfo.from_api(entry) for entry in values or []]
            for key, values in (data.get("address") or {}).items()
        }
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            deleted=data.get("deleted"),
            deployment_mode=data.get("deploymentMode") or "",
            deploy_type=data.get("deployType") or "",
            datastore_type=data.get("datastoreType") or "",
            datastore_version=str(data.get("datastoreVersion") or ""),
            version_name=data.get("versionName") or "",
            number_of_nodes=data.get("numberOfNodes"),
            number_of_replicas=data.get("numberOfReplicas"),
            vcpus=data.get("vcpus"),
            ram_gb=data.get("ram"),
            flavor_id=data.get("packageId") or "",
            flavor_name=data.get("packageName") or data.get("flavor") or "",
            volume_size_gb=_first_int(data, "volumeSize", "storageSize"),
            volume_type=data.get("volumeType") or "",
            volume_type_id=data.get("volumeTypeId") or data.get("storageTypeId") or "",
            encryption=data.get("encryptVolume"),
            zone_id=data.get("zoneId") or "",
            public_access=data.get("publicAccess"),
            port=_port(data.get("port")),
            domain_name=data.get("domainName") or "",
            hostname=data.get("hostname") or "",
            address=address,
            fixed_ip=data.get("fixedIp") or "",
            floating_ip=data.get("floatingIp") or "",
            lb_info=[RedisLoadBalancerIp.from_api(row) for row in data.get("lbInfo") or []],
            redis_password_enabled=data.get("redisPasswordEnabled"),
            enable_proxies=data.get("enableProxies"),
            pool_max_connections=data.get("poolMaxConnections"),
            enable_slowlog=data.get("enableSlowlog"),
            slowlog_log_slower_than_us=data.get("slowlogLogSlowerThan"),
            config_group_id=data.get("configGroupId") or "",
            backup_auto=data.get("backupAuto"),
            backup_time=data.get("backupTime") or "",
            backup_duration=data.get("backupDuration"),
            free_backup_size_gb=data.get("freeBackupSize"),
            replica_source_id=data.get("replicaSourceId") or "",
            replica_source_name=data.get("replicaSourceName") or "",
            replicas=list(data.get("replicas") or []),
            created=str(data.get("created") or data.get("createdAt") or ""),
            updated=str(data.get("updated") or data.get("updatedAt") or ""),
        )


class RedisClusterListData(BaseModel):
    """A page of Redis Clusters.

    The listing is single-family (unlike the relational one, which mixes
    `pg-` rows in) and **pagination is mandatory**: `pageNumber` and
    `pageSize` are required query parameters, not options.
    """

    count: int = Field(0, description="Clusters returned on this page")
    page: PageInfo = Field(
        default_factory=PageInfo,
        description="Paging metadata. `page` is 1-based, both here and in the request",
    )
    items: list[RedisCluster] = Field(default_factory=list, description="Clusters")


class RedisBackup(PostgresqlBackup):
    """One backup-enabled Redis Cluster as vBackup sees it.

    The same `BackupDatabase` row the PostgreSQL Cluster family reads, through
    this family's own path (`/v1/backups/backup-vdb`) -- a standing backup
    configuration, not a point-in-time copy. See :class:`PostgresqlBackup` for
    the field meanings.
    """


class RedisBackupListData(PostgresqlBackupListData):
    """Every backup-enabled Redis Cluster in the project.

    Unpaginated and **not** scoped to one cluster: rows for other clusters
    come back too.
    """

    items: list[RedisBackup] = Field(default_factory=list, description="Backup databases")


class RedisRestorePoint(PostgresqlRestorePoint):
    """One point in time a Redis Cluster can be restored from.

    The same `BackupPoint` row the PostgreSQL Cluster family reads. This is
    what a create consumes as `backupPointId` -- restoring is a create that
    carries one, not a separate restore endpoint.
    """

    engine_version: str = Field(
        "",
        description=(
            "Engine version the data was written by. A restore has to target this same "
            "version -- check it against list_redis_datastores."
        ),
    )


class RedisRestorePointListData(PostgresqlRestorePointListData):
    """The restore points of one Redis Cluster, newest last as the API returns them."""

    items: list[RedisRestorePoint] = Field(default_factory=list, description="Restore points")


class RedisBackupNowData(PostgresqlBackupNowData):
    """The result of asking a Redis Cluster to back up now.

    The endpoint answers with the bare string "success" -- no id, no object.
    Confirm with `list_redis_restore_points`: a new point appears there once
    vBackup finishes writing it.
    """
