"""Response models for the PostgreSQL Cluster family.

Two things make this family unlike the other two:

* **It has no listing and no get-by-id endpoint of its own.** Its clusters are
  returned by the *relational* instance listing, mixed in with relational
  instances, and by the relational detail endpoint. So `PostgresqlCluster` is
  built from a relational row -- the same raw shape as
  :class:`RelationalInstance`, projected differently because a cluster has
  fields a single instance never fills (node count, the split read/write
  endpoints) and leaves null the ones it does.
* **Its backups are vBackup resources, not vDB backups.** The rows here carry
  `bk-db-` / `bk-des-` / `bk-pol-` ids and a policy/destination pair, and they
  have nothing in common with :class:`DatabaseBackup`, which is why they are
  modelled separately rather than reusing it.
"""

from __future__ import annotations

from greennode.vdb_mcp_server.statuses import StatusKind, classify, describe
from pydantic import BaseModel, Field


class PostgresqlCluster(BaseModel):
    """One PostgreSQL Cluster, projected from a relational instance row.

    The read/write and read-only endpoints are separate: `private_rw_ip` /
    `port` reach the primary, `private_ro_ip` / `port_ro` reach the replicas.
    On a single-subnet cluster the two IPs are identical and only the port
    differs, so the port is what routes a connection, not the address.
    """

    id: str = Field("", description="Cluster ID, always prefixed 'pg-'")
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
    datastore_type: str = Field("", description="Engine; always 'PostgreSQL' for this family")
    datastore_version: str = Field("", description="Engine version, e.g. '17'")
    deploy_type: str = Field(
        "",
        description="Deployment type; 'cluster' here, against 'single_node' for a relational instance",
    )
    number_of_nodes: int | None = Field(None, description="Nodes in the cluster (2-10)")
    vcpus: int | None = Field(None, description="vCPU count per node")
    ram_gb: int | None = Field(None, description="RAM in GB per node")
    volume_size_gb: int | None = Field(None, description="Storage size in GB")
    volume_type: str = Field("", description="Storage type name")
    volume_type_id: str = Field(
        "", description="Storage type ID ('pgst-...') -- what a VOLUME-TYPE resize expects"
    )
    flavor_id: str = Field(
        "",
        description=(
            "Current flavour ID ('pgp-...'), carried by the raw row as `quotaPackageId`. "
            "Matches an id from list_postgresql_flavors."
        ),
    )
    flavor_name: str = Field("", description="Current flavour name, e.g. 'vdb.s-general-2x4'")
    zone_id: str = Field("", description="Availability zone, e.g. 'HCM03-1A'")
    subnet_id: str = Field("", description="Subnet the cluster sits in")
    public_access: bool | None = Field(None, description="Whether public access is enabled")
    port: int | None = Field(None, description="Read/write port (the primary), e.g. 5432")
    port_ro: int | None = Field(None, description="Read-only port (the replicas), e.g. 15432")
    private_rw_ip: str = Field("", description="Private read/write address")
    public_rw_ip: str = Field("", description="Public read/write address, if public access is on")
    private_ro_ip: str = Field("", description="Private read-only address")
    public_ro_ip: str = Field("", description="Public read-only address, if public access is on")
    domain_name: str = Field("", description="Cluster DNS name")
    config_group_id: str = Field(
        "",
        description=(
            "Attached configuration group ID ('pg-cfg-...'). Read from the nested "
            "`configuration` object: the row's own top-level `configId` is null even when a "
            "group is attached."
        ),
    )
    config_group_name: str = Field("", description="Attached configuration group name")
    enable_proxies: bool | None = Field(None, description="Whether connection proxies are on")
    pool_max_connections: int | None = Field(None, description="Proxy pool connection limit")
    free_backup_size_gb: int | None = Field(
        None, description="Free backup allowance this cluster's flavour grants, in GB"
    )
    created: str = Field("", description="Creation timestamp")
    updated: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "PostgresqlCluster":
        """Build from a raw relational `DatabaseInstancesGateway` row with a `pg-` id."""
        configuration = data.get("configuration") or {}
        status = data.get("status") or ""
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            datastore_type=data.get("datastoreType") or "",
            datastore_version=str(data.get("datastoreVersion") or ""),
            deploy_type=data.get("deployType") or "",
            number_of_nodes=data.get("numberOfNodes"),
            vcpus=data.get("vcpus"),
            ram_gb=data.get("ram"),
            volume_size_gb=data.get("volumeSize"),
            volume_type=data.get("volumeType") or "",
            volume_type_id=data.get("volumeTypeId") or "",
            flavor_id=data.get("quotaPackageId") or "",
            flavor_name=data.get("packageName") or "",
            zone_id=data.get("zoneId") or "",
            subnet_id=data.get("subnetId") or "",
            public_access=data.get("publicAccess"),
            port=data.get("port"),
            port_ro=data.get("portRo"),
            private_rw_ip=data.get("privateRwIp") or "",
            public_rw_ip=data.get("publicRwIp") or "",
            private_ro_ip=data.get("privateRoIp") or "",
            public_ro_ip=data.get("publicRoIp") or "",
            domain_name=data.get("domainName") or "",
            config_group_id=configuration.get("id") or "",
            config_group_name=configuration.get("name") or "",
            enable_proxies=data.get("enableProxies"),
            pool_max_connections=data.get("poolMaxConnections"),
            free_backup_size_gb=data.get("freeBackupSize"),
            created=data.get("created") or "",
            updated=data.get("updated") or "",
        )


class PostgresqlClusterListData(BaseModel):
    """The PostgreSQL Clusters of the project, derived from the relational listing."""

    count: int = Field(0, description="Clusters returned after filtering to 'pg-' rows")
    relational_instances_excluded: int = Field(
        0,
        description=(
            "Relational rows dropped from the page. The endpoint is a mixed listing; use the "
            "relational tools to see them."
        ),
    )
    derived_listing: bool = Field(
        True,
        description=(
            "Always true. This family has no listing endpoint: the rows come from the "
            "relational listing filtered by the 'pg-' id prefix."
        ),
    )
    filters_are_approximate: bool = Field(
        False,
        description=(
            "True when a name or status filter was sent. The API does not apply those "
            "filters to the cluster rows at all -- they are matched here instead, and only "
            "within the pages fetched -- so a filtered count is never exact."
        ),
    )
    pages_scanned: int = Field(
        0, description="Pages of the mixed listing read to assemble this result"
    )
    more_pages_exist: bool = Field(
        False,
        description=(
            "True when the mixed listing had further pages that were not read. Raise "
            "max_pages to keep scanning."
        ),
    )
    items: list[PostgresqlCluster] = Field(default_factory=list, description="Clusters")


class PostgresqlVolumeUsedData(BaseModel):
    """Disk actually used by one cluster.

    The endpoint answers with an array of **strings carrying their own unit**
    (``["219M"]``), not a number of GB. They are passed through verbatim:
    parsing them into a number would invent a precision and a unit the API
    never stated.

    What the array means per element is **not** documented and does not follow
    the node count: measured on a three-node cluster, it returned a single
    value. So read it as "what the API reports", and do not present an element
    as one node's usage.
    """

    cluster_id: str = Field("", description="Cluster the usage belongs to")
    values: list[str] = Field(
        default_factory=list,
        description=(
            "Used space as the API words it, e.g. '219M' or '1.2G'. A three-node cluster "
            "returned one value, so an element is not a node."
        ),
    )


class PostgresqlBackup(BaseModel):
    """One backup-enabled cluster as vBackup sees it.

    This is **not** a point-in-time copy -- that is a restore point. A row here
    is the standing backup configuration of one cluster: which policy runs,
    where it writes, and when it last ran.
    """

    id: str = Field("", description="Backup database ID ('bk-db-...')")
    name: str = Field("", description="Backup database name")
    cluster_id: str = Field(
        "", description="Cluster this backup belongs to, from the raw `databaseId`"
    )
    description: str = Field("", description="Description")
    status: str = Field("", description="Backup status, verbatim")
    status_kind: StatusKind = Field("unknown", description="What the status means to act on")
    status_guidance: str = Field("", description="What to do about this status")
    backup_enabled: bool | None = Field(None, description="Whether the schedule is active")
    latest_record: str = Field("", description="Timestamp of the most recent successful backup")
    policy_id: str = Field("", description="Backup policy ID ('bk-pol-...')")
    policy_name: str = Field("", description="Backup policy name")
    location_id: str = Field("", description="Backup destination (location) ID ('bk-des-...')")
    location_name: str = Field("", description="Backup destination name")
    total_backup_size: int | None = Field(
        None, description="Total stored size in bytes, as reported"
    )
    cluster_deleted: bool | None = Field(
        None,
        description=(
            "True when the cluster is gone but its backups remain. Such a row cannot be "
            "backed up again; it is kept so the restore points stay reachable."
        ),
    )
    created: str = Field("", description="Creation timestamp")
    updated: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "PostgresqlBackup":
        """Build from a raw `BackupDatabase` row.

        The policy and destination arrive twice: once as a flat name field and
        once as a nested object whose own name field is populated even when the
        flat one is not. The nested object wins, with the flat field as backup.
        """
        status = data.get("status") or ""
        policy = data.get("policy") or {}
        destination = data.get("backupDestination") or {}
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            cluster_id=data.get("databaseId") or "",
            description=data.get("description") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            backup_enabled=data.get("backupEnabled"),
            latest_record=data.get("latestRecord") or "",
            policy_id=data.get("backupPolicyId") or "",
            policy_name=policy.get("name") or data.get("backupPolicyName") or "",
            location_id=data.get("backupDestinationId") or "",
            location_name=destination.get("name") or data.get("backupDestinationName") or "",
            total_backup_size=data.get("totalBackupSize"),
            cluster_deleted=data.get("databaseDeleted"),
            created=data.get("createdAt") or "",
            updated=data.get("updatedAt") or "",
        )


class PostgresqlBackupListData(BaseModel):
    """Every backup-enabled cluster in the project.

    Unpaginated: the endpoint answers with the whole array, and it is **not**
    scoped to one cluster, so rows for other clusters come back too.
    """

    count: int = Field(0, description="Rows returned")
    items: list[PostgresqlBackup] = Field(default_factory=list, description="Backup databases")


class PostgresqlRestorePoint(BaseModel):
    """One point in time a cluster can be restored from.

    This is what a create consumes as `backupPointId`, and the only kind of row
    in this family that is an actual copy of data.
    """

    id: str = Field("", description="Restore point ID ('bk-db-pt-...'); a create's backupPointId")
    backup_database_id: str = Field("", description="Backup database this point belongs to")
    cluster_id: str = Field("", description="Cluster the data came from")
    backup_name: str = Field("", description="Underlying backup name, e.g. a WAL base name")
    status: str = Field("", description="Restore point status, verbatim")
    status_kind: StatusKind = Field("unknown", description="What the status means to act on")
    status_guidance: str = Field("", description="What to do about this status")
    engine_version: str = Field(
        "",
        description=(
            "Engine version the data was written by. A restore has to target this same "
            "version -- check it against list_postgresql_datastores."
        ),
    )
    time: str = Field("", description="Point in time this restores to")
    compressed_size: int | None = Field(None, description="Stored size in bytes")
    uncompressed_size: int | None = Field(None, description="Original size in bytes")
    created: str = Field("", description="Creation timestamp")
    updated: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "PostgresqlRestorePoint":
        """Build from a raw `BackupPoint` row.

        `destinationSnapshot` and `policySnapshot` are dropped: both are JSON
        **encoded as a string** inside the JSON response, and both only repeat
        what the location and policy endpoints already return.
        """
        status = data.get("status") or ""
        return cls(
            id=data.get("id") or "",
            backup_database_id=data.get("backupDatabaseId") or "",
            cluster_id=data.get("databaseId") or "",
            backup_name=data.get("backupName") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            engine_version=str(data.get("engineVersion") or ""),
            time=data.get("time") or "",
            compressed_size=data.get("compressedSize"),
            uncompressed_size=data.get("uncompressedSize"),
            created=data.get("createdAt") or "",
            updated=data.get("updatedAt") or "",
        )


class PostgresqlRestorePointListData(BaseModel):
    """The restore points of one cluster, newest last as the API returns them."""

    cluster_id: str = Field("", description="Cluster the points belong to")
    count: int = Field(0, description="Restore points returned")
    items: list[PostgresqlRestorePoint] = Field(default_factory=list, description="Restore points")


class BackupLocationOption(BaseModel):
    """One vBackup destination a cluster's backups can be written to."""

    id: str = Field("", description="Location ID ('bk-des-...'); a create's backupLocationId")
    name: str = Field("", description="Location name")
    type: str = Field("", description="Storage kind, e.g. 'VAULT'")
    status: str = Field("", description="Location status")
    is_default: bool = Field(False, description="Whether this is the account default")
    region_name: str = Field(
        "",
        description=(
            "Region the vault sits in, e.g. 'HCM04'. It is a vBackup region and need not "
            "match the cluster's zone."
        ),
    )
    used_bytes: int | None = Field(None, description="Space used, in bytes")
    unlimited_quota: bool | None = Field(None, description="Whether the quota is unlimited")
    max_quota: int | None = Field(None, description="Quota ceiling when it is not unlimited")
    backup_instance_count: int | None = Field(None, description="Databases currently writing here")

    @classmethod
    def from_api(cls, data: dict) -> "BackupLocationOption":
        """Build from a raw `BackupLocation` row."""
        config = data.get("config") or {}
        vault = config.get("vault") or {}
        quota = data.get("maxQuota") or {}
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            type=data.get("type") or "",
            status=data.get("status") or "",
            is_default=bool(data.get("isDefault")),
            region_name=vault.get("regionName") or "",
            used_bytes=vault.get("used"),
            unlimited_quota=quota.get("unlimited"),
            max_quota=quota.get("maxQuota"),
            backup_instance_count=data.get("numberOfBackupInstances"),
        )


class BackupLocationListData(BaseModel):
    """The vBackup destinations available to this project."""

    count: int = Field(0, description="Locations returned")
    items: list[BackupLocationOption] = Field(default_factory=list, description="Locations")


class BackupPolicyOption(BaseModel):
    """One vBackup schedule a cluster's backups can follow."""

    id: str = Field("", description="Policy ID ('bk-pol-...'); a create's backupPolicyId")
    name: str = Field("", description="Policy name")
    is_default: bool = Field(False, description="Whether this is the account default")
    hour: int | None = Field(None, description="Hour the schedule fires")
    minute: int | None = Field(None, description="Minute the schedule fires")
    time_zone: str = Field("", description="Time zone the schedule is expressed in")
    hourly_enabled: bool | None = Field(None, description="Whether the hourly schedule is on")
    daily_enabled: bool | None = Field(None, description="Whether the daily schedule is on")
    weekly_enabled: bool | None = Field(None, description="Whether the weekly schedule is on")
    monthly_enabled: bool | None = Field(None, description="Whether the monthly schedule is on")
    daily_retention: int | None = Field(
        None, description="Daily copies kept, when the daily schedule is on"
    )
    daily_backup_type: str = Field(
        "", description="Daily backup kind, e.g. 'FULL', when the daily schedule is on"
    )
    backup_instance_count: int | None = Field(
        None, description="Databases currently following this policy"
    )

    @classmethod
    def from_api(cls, data: dict) -> "BackupPolicyOption":
        """Build from a raw `BackupPolicy` row.

        Each frequency has an ``*Enabled`` flag and a ``*Config`` object, and a
        disabled frequency's config is ``{}`` rather than null -- so the flag,
        not the presence of the config, says whether a schedule runs.
        """
        config = data.get("config") or {}
        daily = config.get("dailyConfig") or {}
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            is_default=bool(data.get("isDefault")),
            hour=config.get("hour"),
            minute=config.get("minute"),
            time_zone=config.get("timeZone") or "",
            hourly_enabled=config.get("hourlyEnabled"),
            daily_enabled=config.get("dailyEnabled"),
            weekly_enabled=config.get("weeklyEnabled"),
            monthly_enabled=config.get("monthlyEnabled"),
            daily_retention=daily.get("retention"),
            daily_backup_type=daily.get("backupType") or "",
            backup_instance_count=data.get("backupInstanceCount"),
        )


class BackupPolicyListData(BaseModel):
    """The vBackup schedules available to this project."""

    count: int = Field(0, description="Policies returned")
    items: list[BackupPolicyOption] = Field(default_factory=list, description="Policies")


class PostgresqlBackupNowData(BaseModel):
    """The result of asking for an immediate backup.

    The endpoint answers with a bare string, not an object, so there is no id
    to poll. Confirm with `list_postgresql_restore_points`: a new point appears
    there once the backup completes.
    """

    cluster_id: str = Field("", description="Cluster that was asked to back up")
    message: str = Field("", description="What the API answered, verbatim")
    accepted: bool = Field(
        True,
        description=(
            "The request was accepted, not that the backup finished -- vBackup writes it "
            "asynchronously."
        ),
    )
    next_step: str = Field("", description="How to confirm the backup actually landed")
