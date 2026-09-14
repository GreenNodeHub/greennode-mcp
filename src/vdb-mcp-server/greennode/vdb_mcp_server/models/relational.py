"""Response models for the relational family (MySQL, MariaDB, standalone PostgreSQL)."""

from __future__ import annotations

from greennode.vdb_mcp_server.models._common import (
    DatabaseBackup,
    DatabaseConfiguration,
    PageInfo,
)
from greennode.vdb_mcp_server.statuses import StatusKind, classify, describe
from pydantic import BaseModel, Field


class RelationalInstance(BaseModel):
    """One relational database instance -- a projection of a 68-field row.

    The raw row carries billing fields (`cost`, `period`, `enableAutoRenew`,
    `packageName`), sharing metadata and a dozen nulls that only apply to
    PostgreSQL Clusters. What is kept is what identifies the instance, what it
    runs, how big it is and how to reach it.
    """

    id: str = Field(
        "",
        description=(
            "Instance ID. A relational instance is prefixed 'db-', but so is a MemoryStore "
            "one, and get_relational_instance also resolves 'pg-' clusters -- so the prefix "
            "does not identify the family. Read datastore_type for that."
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
    datastore_type: str = Field("", description="Engine, e.g. 'MySQL' (capitalised here)")
    datastore_version: str = Field("", description="Engine version")
    vcpus: int | None = Field(None, description="vCPU count")
    ram_gb: int | None = Field(None, description="RAM in GB")
    volume_size_gb: int | None = Field(None, description="Storage size in GB")
    volume_type: str = Field("", description="Storage type (zone-suffixed outside HCM03-1A)")
    zone_id: str = Field("", description="Availability zone")
    subnet_id: str = Field("", description="Subnet the instance sits in")
    ip_addresses: list[str] = Field(default_factory=list, description="Fixed and floating IPs")
    port: int | None = Field(None, description="Listening port")
    public_access: bool | None = Field(None, description="Whether public access is enabled")
    backup_auto: bool | None = Field(None, description="Whether automatic backup is on")
    backup_time: str = Field("", description="Automatic backup window start, e.g. '00:00'")
    backup_duration_days: int | None = Field(
        None, description="Automatic backup retention in DAYS (2-14), not a window length"
    )
    config_group_id: str = Field("", description="Attached configuration group ID, if any")
    config_group_name: str = Field("", description="Attached configuration group name")
    replica_source_id: str = Field("", description="Source instance ID when this is a replica")
    created: str = Field("", description="Creation timestamp")
    updated: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "RelationalInstance":
        """Build from a raw `DatabaseInstancesGateway` row."""
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
            backup_auto=data.get("backupAuto"),
            backup_time=data.get("backupTime") or "",
            backup_duration_days=data.get("backupDuration"),
            config_group_id=configuration.get("id") or "",
            config_group_name=configuration.get("name") or "",
            replica_source_id=data.get("replicaSourceId") or "",
            created=data.get("created") or "",
            updated=data.get("updated") or "",
        )


class RelationalInstanceListData(BaseModel):
    """A page of relational instances, with the caveats the raw listing hides."""

    count: int = Field(0, description="Instances returned on this page after filtering")
    page: PageInfo | None = Field(None, description="Paging metadata as the API reported it")
    postgresql_clusters_excluded: int = Field(
        0,
        description=(
            "PostgreSQL Cluster rows dropped from this page. The endpoint is a mixed listing; "
            "use the postgresql tools to see them."
        ),
    )
    filters_are_approximate: bool = Field(
        False,
        description=(
            "True when a name/status filter was sent. The API does not apply those filters to "
            "the PostgreSQL Cluster rows it mixes in, so counts must not be presented as exact."
        ),
    )
    items: list[RelationalInstance] = Field(default_factory=list, description="Instances")


class ReplicaListData(BaseModel):
    """The replicas of one instance.

    The spec does not describe this response; live it is an array inside the
    usual envelope. Rows are projected as instances, which is what they are.
    """

    source_instance_id: str = Field("", description="Instance the replicas belong to")
    count: int = Field(0, description="Number of replicas")
    items: list[RelationalInstance] = Field(default_factory=list, description="Replicas")


class RelationalBackupListData(BaseModel):
    """A page of relational backups across every instance in the project.

    The rows are **thin**: this endpoint leaves the sizing, networking and
    credential fields null and lowercases `datastore_type`. Treat a row as an
    index entry and call `get_relational_backup` for anything else.
    """

    count: int = Field(0, description="Backups on this page")
    page: PageInfo | None = Field(None, description="Paging metadata as the API reported it")
    rows_are_summaries: bool = Field(
        True,
        description=(
            "Always true. Sizing, flavour, network and credential fields come back null on "
            "this endpoint; get_relational_backup fills them in."
        ),
    )
    items: list[DatabaseBackup] = Field(default_factory=list, description="Backups")


class RelationalInstanceBackupListData(BaseModel):
    """Every backup of one instance.

    This endpoint does **not** paginate -- it answers with the whole array -- so
    there is no `page` here. An empty list means the instance has no backups.
    """

    instance_id: str = Field("", description="Instance the backups belong to")
    count: int = Field(0, description="Number of backups")
    rows_are_summaries: bool = Field(
        True,
        description=(
            "Always true. Like the project-wide listing, this endpoint returns the sizing, "
            "flavour, network and credential fields as null -- and its rows do not even "
            "carry dbInstanceId or instanceName -- while lowercasing the engine name. Call "
            "get_relational_backup before planning a restore from a row."
        ),
    )
    items: list[DatabaseBackup] = Field(default_factory=list, description="Backups")


class RelationalConfigurationListData(BaseModel):
    """A page of relational configuration groups."""

    count: int = Field(0, description="Groups on this page")
    page: PageInfo | None = Field(None, description="Paging metadata as the API reported it")
    items: list[DatabaseConfiguration] = Field(
        default_factory=list, description="Configuration groups"
    )
