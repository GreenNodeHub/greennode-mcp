"""Backup-database models — a protected vDB instance and its restore points.

The vDB half of vBackup, and the sibling of ``backup_server``. The two share
an envelope and most field names, but differ in three ways worth knowing
before reading a payload:

- A database has **no volumes**. Where a backup server splits into per-disk
  slices a caller must include or exclude, a database is captured whole, so
  there is no equivalent of ``update_backup_server_volumes``.
- The engine is part of the resource: ``engine`` / ``engineVersion`` say what
  was captured, and the engine family decides the ``databaseType`` a create
  must send.
- A restore point reports **two** sizes — what was stored and what it was
  before compression — where a server point reports captured and billable.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import as_dict, as_gib, as_int, as_text
from greennode.vbackup_mcp_server.models.backup_server import BackupDestinationRef
from greennode.vbackup_mcp_server.models.policy import BackupPolicyRef
from pydantic import BaseModel, Field
from typing import Literal


DatabaseType = Literal["PostgresCluster", "RedisCluster"]
"""The two values ``databaseType`` accepts, spelled exactly.

Not an engine name: ``PostgresCluster``, not ``PostgreSQL`` or ``POSTGRESQL``.
The API does not reject an unknown spelling — ``/v1/protected-resources/databases``
answers a wrong one with an empty list, which reads as "nothing is protected"
— so the value is constrained here rather than at the gateway.
"""

MEMORY_TYPES = ("RedisCluster",)
"""Database types served by the vDB *memory* gateway rather than the relational one."""


def is_memory_type(database_type: str) -> bool:
    """True when *database_type* lives behind the vDB memory gateway."""
    return database_type in MEMORY_TYPES


class BackupDatabaseItem(BaseModel):
    """One protected database — the join of a vDB instance, a policy and a destination."""

    id: str = Field(
        ...,
        description=(
            "Backup database ID (`bk-db-...`) — the id every other backup-database "
            "tool takes. Not the vDB instance id."
        ),
    )
    name: str = Field("", description="Backup database name")
    database_id: str = Field(
        "",
        description=(
            "The protected vDB instance ID (`pg-...` for PostgreSQL, `rd-...` for "
            "Redis); not interchangeable with `id`"
        ),
    )
    database_deleted: bool = Field(
        False,
        description=(
            "True when the source vDB instance no longer exists. Its restore points "
            "survive and are STILL BILLED — report this, never hide it."
        ),
    )
    engine: str = Field("", description="Database engine, e.g. Redis or PostgreSQL")
    engine_version: str = Field("", description="Engine version captured, e.g. v7.2.13")
    status: str = Field("", description="Backup database status, e.g. ACTIVE")
    backup_enabled: bool = Field(
        False,
        description=(
            "False means the schedule is paused: no new runs happen, existing "
            "restore points are untouched."
        ),
    )
    description: str = Field(
        "",
        description=(
            "Free-text note. 'Created by vDB.' marks a resource the vDB product "
            "created itself rather than one an operator added here."
        ),
    )
    policy: BackupPolicyRef = Field(
        default_factory=BackupPolicyRef, description="The attached backup policy"
    )
    destination: BackupDestinationRef = Field(
        default_factory=BackupDestinationRef, description="Where the backups are stored"
    )
    backup_policy_id: str = Field("", description="Attached policy ID")
    backup_destination_id: str = Field("", description="Attached destination ID")
    total_backup_size_gb: float = Field(
        0, description="Total stored across every restore point, in GiB"
    )
    total_backup_size_bytes: int = Field(0, description="Same total in bytes, as the API reports")
    free_usage_gb: int = Field(
        0,
        description=(
            "Free backup allowance in GiB that comes with the vDB instance. Storage "
            "beyond it is billed."
        ),
    )
    latest_record: str = Field("", description="Timestamp of the most recent successful run")
    next_schedule: str = Field("", description="When the next scheduled run is due")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last-update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> BackupDatabaseItem:
        """Build a BackupDatabaseItem from a raw API dict.

        The sub-resource listing under a destination returns the same item with
        `policy` and `destination` nulled out, so both nested refs fall back to
        empty rather than assuming they are present.
        """
        return cls(
            id=as_text(data.get("id")),
            name=as_text(data.get("name")),
            database_id=as_text(data.get("databaseId")),
            database_deleted=bool(data.get("databaseDeleted")),
            engine=as_text(data.get("engine")),
            engine_version=as_text(data.get("engineVersion")),
            status=as_text(data.get("status")),
            backup_enabled=bool(data.get("backupEnabled")),
            description=as_text(data.get("description")),
            policy=BackupPolicyRef.from_api(data.get("policy")),
            destination=BackupDestinationRef.from_api(data.get("backupDestination")),
            backup_policy_id=as_text(data.get("backupPolicyId")),
            backup_destination_id=as_text(data.get("backupDestinationId")),
            total_backup_size_gb=as_gib(data.get("totalBackupSize")),
            total_backup_size_bytes=as_int(data.get("totalBackupSize")),
            free_usage_gb=as_int(data.get("freeUsage")),
            latest_record=as_text(data.get("latestRecord")),
            next_schedule=as_text(data.get("nextSchedule")),
            created_at=as_text(data.get("createdAt")),
            updated_at=as_text(data.get("updatedAt")),
        )


class BackupDatabaseListData(BaseModel):
    """Structured output of list_backup_databases and of the destination sub-resource."""

    region: str = Field(..., description="Region the gateway was called in")
    destination_id: str = Field(
        "", description="Destination the resources are stored in, when scoped to one"
    )
    total: int = Field(0, description="Number of backup databases returned")
    databases: list[BackupDatabaseItem] = Field(
        default_factory=list, description="The protected databases"
    )


class BackupDatabasePointItem(BaseModel):
    """One restore point of a protected database — everything a single run captured."""

    id: str = Field(
        ..., description="Restore point ID (`bk-db-pt-...`) — the id a delete or restore takes"
    )
    backup_database_id: str = Field("", description="Backup database that produced it")
    database_id: str = Field("", description="vDB instance that was captured")
    backup_name: str = Field(
        "",
        description=(
            "The name the engine gave the dump: a bare number on Redis, a WAL "
            "base name such as `base_00000001...` on PostgreSQL. Either way it is "
            "an identifier, not a timestamp — use `time` for when the run happened."
        ),
    )
    status: str = Field(
        "",
        description=(
            "Point status. Only ACTIVE is complete. A point that is still uploading "
            "OR still covered by the destination's vault-lock retention cannot be "
            "deleted; the two cases answer with different 409 messages and only the "
            "first is worth retrying."
        ),
    )
    error_message: str = Field("", description="Why the run failed, when it did")
    time: str = Field("", description="When the run captured the database")
    finish_time: str = Field("", description="When the point finished uploading")
    size_gb: float = Field(0, description="Stored (compressed) size in GiB — what is billed")
    uncompressed_size_gb: float = Field(0, description="Size before compression, in GiB")
    size_bytes: int = Field(0, description="Stored size in bytes, as the API reports it")
    uncompressed_size_bytes: int = Field(0, description="Uncompressed size in bytes")
    destination_id: str = Field("", description="Destination the point is stored in")
    backup_type_at_run: str = Field(
        "",
        description=(
            "The kind of run that produced it, read from the embedded policy "
            "snapshot — e.g. MANUAL_FULL for a start_database_backup. It stays "
            "readable after the policy is edited, so use it to explain an old run."
        ),
    )
    restoring: bool = Field(
        False, description="True while a restore from this point is in progress"
    )
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> BackupDatabasePointItem:
        """Build a BackupDatabasePointItem from a raw API dict."""
        destination = as_dict(data.get("destination"))
        policy_snapshot = as_dict(data.get("policySnapshot"))
        return cls(
            id=as_text(data.get("id")),
            backup_database_id=as_text(data.get("backupDatabaseId")),
            database_id=as_text(data.get("databaseId")),
            backup_name=as_text(data.get("backupName")),
            status=as_text(data.get("status")),
            error_message=as_text(data.get("errorMessage")),
            time=as_text(data.get("time")),
            finish_time=as_text(data.get("updatedAt")),
            size_gb=as_gib(data.get("compressedSize")),
            uncompressed_size_gb=as_gib(data.get("uncompressedSize")),
            size_bytes=as_int(data.get("compressedSize")),
            uncompressed_size_bytes=as_int(data.get("uncompressedSize")),
            destination_id=as_text(destination.get("id")),
            backup_type_at_run=as_text(policy_snapshot.get("backupType")),
            restoring=bool(data.get("isRestoring")),
            created_at=as_text(data.get("createdAt")),
        )


class BackupDatabasePointListData(BaseModel):
    """Structured output of list_backup_database_points."""

    region: str = Field(..., description="Region the gateway was called in")
    backup_database_id: str = Field(..., description="Backup database the points belong to")
    total: int = Field(0, description="Number of restore points")
    points: list[BackupDatabasePointItem] = Field(
        default_factory=list, description="The restore points, as the API ordered them"
    )


class ProtectedDatabaseListData(BaseModel):
    """Structured output of list_protected_databases."""

    region: str = Field(..., description="Region the gateway was called in")
    database_type: str = Field(..., description="The database type the list was scoped to")
    total: int = Field(0, description="Number of protected vDB instance IDs")
    database_ids: list[str] = Field(
        default_factory=list, description="vDB instance IDs that already have a backup database"
    )


class DatabaseInstanceItem(BaseModel):
    """One vDB instance as the vDB gateway describes it, narrowed to what a backup needs.

    vDB answers with roughly seventy fields per instance, most of them about
    billing, networking and replication that a backup decision never consults.
    Only the fields that decide *whether this database can be backed up* and
    *how to tell it apart from its siblings* are kept.
    """

    id: str = Field(..., description="vDB instance ID (`pg-...` or `rd-...`)")
    name: str = Field("", description="Instance name as shown in the vDB console")
    engine: str = Field("", description="Engine, e.g. PostgreSQL or Redis")
    engine_version: str = Field("", description="Engine version")
    status: str = Field("", description="Instance status; only ACTIVE can be backed up")
    deploy_type: str = Field(
        "",
        description=(
            "Topology as vDB spells it — `cluster` / `single` for PostgreSQL, "
            "`sharding` / `non-sharding` for Redis. Single-node PostgreSQL cannot "
            "be backed up."
        ),
    )
    node_count: int = Field(0, description="Number of nodes in the instance")
    zone_id: str = Field("", description="Availability zone the instance runs in")
    project_id: str = Field("", description="Project the instance belongs to")
    vcpus: int = Field(0, description="vCPUs per node")
    ram_gb: int = Field(0, description="RAM per node in GB")
    volume_size_gb: int = Field(0, description="Provisioned storage in GB; 0 on in-memory engines")
    backup_auto: bool = Field(
        False, description="vDB's own auto-backup flag, separate from vBackup's schedule"
    )
    created_at: str = Field("", description="Creation timestamp")
    already_protected: bool = Field(
        False,
        description=(
            "True when this instance already has a backup database, resolved "
            "against list_protected_databases. A protected instance cannot be "
            "protected twice."
        ),
    )
    eligible: bool = Field(
        False,
        description=(
            "True when create_backup_database can accept this instance: ACTIVE, not "
            "already protected, and — for PostgreSQL — a cluster deployment."
        ),
    )
    ineligible_reason: str = Field(
        "", description="Why `eligible` is false; empty when the instance can be backed up"
    )

    @classmethod
    def from_api(
        cls, data: dict, protected_ids: frozenset[str] = frozenset()
    ) -> DatabaseInstanceItem:
        """Build a DatabaseInstanceItem, resolving eligibility against *protected_ids*."""
        instance_id = as_text(data.get("id"))
        status = as_text(data.get("status"))
        deploy_type = as_text(data.get("deployType"))
        engine = as_text(data.get("datastoreType"))
        protected = instance_id in protected_ids

        reason = ""
        if protected:
            reason = "Already has a backup database"
        elif status != "ACTIVE":
            reason = f"Instance status is {status or 'unknown'}, not ACTIVE"
        elif engine.lower().startswith("postgre") and deploy_type != "cluster":
            reason = f"PostgreSQL deployment is '{deploy_type or 'unknown'}', not a cluster"

        return cls(
            id=instance_id,
            name=as_text(data.get("name")),
            engine=engine,
            engine_version=as_text(data.get("datastoreVersion")),
            status=status,
            deploy_type=deploy_type,
            node_count=as_int(data.get("numberOfNodes")),
            zone_id=as_text(data.get("zoneId")),
            project_id=as_text(data.get("projectId")),
            vcpus=as_int(data.get("vcpus")),
            ram_gb=as_int(data.get("ram")),
            volume_size_gb=as_int(data.get("volumeSize")),
            backup_auto=bool(data.get("backupAuto")),
            created_at=as_text(data.get("created")),
            already_protected=protected,
            eligible=not reason,
            ineligible_reason=reason,
        )


class DatabaseInstanceListData(BaseModel):
    """Structured output of list_databases."""

    region: str = Field(..., description="Region the caller asked for")
    database_type: str = Field(..., description="The database type the list was scoped to")
    total: int = Field(0, description="Number of vDB instances found")
    eligible_total: int = Field(
        0, description="How many of them create_backup_database would accept"
    )
    databases: list[DatabaseInstanceItem] = Field(
        default_factory=list, description="The vDB instances, eligible ones first"
    )
    project_id: str = Field("", description="Project the vDB gateway resolved from the token")
