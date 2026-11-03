"""Response models for the MemoryStore family (Redis).

Upstream, a memory instance and a relational instance are the **same two
schemas** (`DatabaseInstancesGateway` for a listing row,
`DbInstanceInfo` for a detail). What differs is which fields carry meaning, so
this is a different projection of a shared shape rather than a different shape:

* `redisPasswordEnabled` is real here and always `false` on a relational row.
* `volumeType` / `volumeSize` are **reported but not chosen**: the memory
  create body has no volume fields at all -- the flavour decides the disk. A
  caller who reads them as configurable will look for a knob that does not
  exist, and there is no `resize-storage` in this family either.
* `ram_gb` is the sizing dimension that matters for Redis, not `volume_size_gb`.

Verified live against `cli-redis-test` on 2026-09-10.
"""

from __future__ import annotations

from greennode.vdb_mcp_server.models._common import (
    DatabaseBackup,
    DatabaseConfiguration,
    PageInfo,
)
from greennode.vdb_mcp_server.statuses import StatusKind, classify, describe
from pydantic import BaseModel, Field


class MemoryInstance(BaseModel):
    """One MemoryStore (Redis) instance -- a projection of a 68-field row.

    **The id prefix is `db-`, exactly like a relational instance.** It carries
    no family information: `db-5a4d26f1-...` is a Redis instance and
    `db-ceb5fd7b-...` is a MySQL one. The only reliable discriminators are
    which family's listing returned the row and `datastore_type`.
    """

    id: str = Field(
        "",
        description=(
            "Instance ID. Starts with 'db-' -- the same prefix relational instances use, so "
            "the id alone does not say which family the instance belongs to."
        ),
    )
    name: str = Field("", description="Instance name")
    status: str = Field("", description="Instance status, verbatim from the API")
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
    datastore_type: str = Field(
        "",
        description=(
            "Engine -- 'Redis' here. Case is not normalised upstream (a listing echoes "
            "whatever created the instance), so compare it case-insensitively."
        ),
    )
    datastore_version: str = Field("", description="Redis version, e.g. '7.2'")
    vcpus: int | None = Field(None, description="vCPU count")
    ram_gb: int | None = Field(
        None,
        description="RAM in GB -- the sizing dimension that matters for Redis, set by the flavour",
    )
    volume_size_gb: int | None = Field(
        None,
        description=(
            "Disk size in GB. Derived from the flavour, NOT chosen: the memory create body "
            "has no volume fields, and this family has no resize-storage operation."
        ),
    )
    volume_type: str = Field(
        "", description="Storage type the flavour selected (zone-suffixed outside HCM03-1A)"
    )
    zone_id: str = Field("", description="Availability zone, e.g. 'HCM03-1A'")
    subnet_id: str = Field("", description="Subnet the instance sits in")
    ip_addresses: list[str] = Field(default_factory=list, description="Fixed and floating IPs")
    port: int | None = Field(None, description="Listening port -- 6379 for Redis")
    public_access: bool | None = Field(
        None,
        description=(
            "Whether a floating IP is assigned. This governs the IP, not the firewall: a "
            "new instance still gets a 0.0.0.0/0 security rule on its port."
        ),
    )
    redis_password_enabled: bool | None = Field(
        None,
        description=(
            "Whether the Redis master password is enabled. The platform requires it enabled "
            "whenever public_access is on."
        ),
    )
    backup_auto: bool | None = Field(None, description="Whether automatic backup is on")
    backup_time: str = Field("", description="Automatic backup window start, e.g. '05:30'")
    backup_duration_days: int | None = Field(
        None, description="Automatic backup retention in DAYS (2-14), not a window length"
    )
    config_group_id: str = Field("", description="Attached configuration group ID, if any")
    config_group_name: str = Field("", description="Attached configuration group name")
    replica_source_id: str = Field("", description="Source instance ID when this is a replica")
    created: str = Field("", description="Creation timestamp")
    updated: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "MemoryInstance":
        """Build from a raw `DatabaseInstancesGateway` / `DbInstanceInfo` row."""
        configuration = data.get("configuration") or {}
        raw_ips = data.get("ip")
        status = data.get("status") or ""
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            datastore_type=data.get("datastoreType") or "",
            datastore_version=str(data.get("datastoreVersion") or ""),
            vcpus=data.get("vcpus"),
            ram_gb=data.get("ram"),
            volume_size_gb=data.get("volumeSize"),
            volume_type=data.get("volumeType") or "",
            zone_id=data.get("zoneId") or "",
            subnet_id=data.get("subnetId") or "",
            ip_addresses=[str(ip) for ip in (raw_ips or []) if ip],
            port=data.get("port"),
            public_access=data.get("publicAccess"),
            redis_password_enabled=data.get("redisPasswordEnabled"),
            backup_auto=data.get("backupAuto"),
            backup_time=data.get("backupTime") or "",
            backup_duration_days=data.get("backupDuration"),
            # The listing fills `configuration` and leaves the top-level
            # `configId` null; the detail does the reverse on some rows.
            config_group_id=configuration.get("id") or data.get("configId") or "",
            config_group_name=configuration.get("name") or "",
            replica_source_id=data.get("replicaSourceId") or "",
            created=data.get("created") or "",
            updated=data.get("updated") or "",
        )


class MemoryInstanceListData(BaseModel):
    """A page of MemoryStore instances.

    Unlike the relational listing this one is **single-family and its filters
    are exact** -- verified live: a name that matches nothing returns zero rows
    and `totalElements: 0`, where the relational endpoint still hands back its
    PostgreSQL Cluster rows. So there is no approximation caveat here and no
    client-side filtering: every row the API returns is a Redis instance.
    """

    count: int = Field(0, description="Instances returned on this page")
    page: PageInfo | None = Field(None, description="Paging metadata as the API reported it")
    items: list[MemoryInstance] = Field(default_factory=list, description="Instances")


class MemoryReplicaListData(BaseModel):
    """The replicas of one MemoryStore instance.

    The spec declares this response as a bare `object` and describes nothing;
    live it is an array of instance rows inside the usual envelope, empty when
    the instance has no replicas. An empty list is an answer, not a failure.

    **The rows are thinner than a listing row**: measured on a real replica,
    `zoneId`, `subnetId`, `port` and even `replicaSourceId` come back null, so
    a replica's zone cannot be read here. Call `get_memory_instance` on the
    replica id for that. The rows also carry `dbBackendId: null`, which is one
    of the markers a `pg-` cluster row has — so that marker does not identify a
    cluster either.
    """

    source_instance_id: str = Field("", description="Instance the replicas belong to")
    count: int = Field(0, description="Number of replicas")
    rows_are_summaries: bool = Field(
        True,
        description=(
            "Always true. These rows omit zone_id, subnet_id, port and replica_source_id. "
            "Call get_memory_instance on a replica's id for its full detail."
        ),
    )
    items: list[MemoryInstance] = Field(default_factory=list, description="Replicas")


class MemoryInstanceBackupListData(BaseModel):
    """Every backup of one MemoryStore instance.

    This endpoint does **not** paginate -- it answers with the whole array -- so
    there is no `page` here. An empty list means the instance has no backups.
    """

    instance_id: str = Field("", description="Instance the backups belong to")
    count: int = Field(0, description="Number of backups")
    rows_are_summaries: bool = Field(
        True,
        description=(
            "Always true. This listing leaves instance_id, instance_name and the sizing, "
            "flavour, network and credential fields null, and reports datastore_type in "
            "lowercase. Call get_memory_backup before planning a restore from a row."
        ),
    )
    items: list[DatabaseBackup] = Field(default_factory=list, description="Backups")


class MemoryBackupListData(BaseModel):
    """A page of MemoryStore backups across every instance in the project.

    The rows are **thin**, exactly as in the relational family: this endpoint
    leaves the sizing, networking and credential fields null and lowercases
    `datastore_type`. Treat a row as an index entry and call
    `get_memory_backup` for anything else.
    """

    count: int = Field(0, description="Backups on this page")
    page: PageInfo | None = Field(None, description="Paging metadata as the API reported it")
    rows_are_summaries: bool = Field(
        True,
        description=(
            "Always true. Sizing, flavour, network and credential fields come back null on "
            "this endpoint; get_memory_backup fills them in."
        ),
    )
    items: list[DatabaseBackup] = Field(default_factory=list, description="Backups")


class MemoryConfigurationListData(BaseModel):
    """A page of MemoryStore configuration groups.

    `pageObject.maxSize` is 30 on this resource -- a third ceiling, alongside
    100 for instance listings and 50 for backups.
    """

    count: int = Field(0, description="Groups on this page")
    page: PageInfo | None = Field(None, description="Paging metadata as the API reported it")
    items: list[DatabaseConfiguration] = Field(
        default_factory=list, description="Configuration groups"
    )
