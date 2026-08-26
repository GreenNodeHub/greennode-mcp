"""History models — the audit trail of backup runs and restores.

vBackup keeps a separate trail per product: ``/v1/histories/backup-instances``
and ``/v1/histories/restoration`` for vServer, ``/v1/histories/backup-databases``
and ``/v1/histories/restoration/databases`` for vDB. The two families do not
share field names — a vDB run reports ``compressedSize`` / ``uncompressedSize``
where a vServer run reports ``size`` / ``usage`` — so they get their own models
rather than a shared one with half its fields empty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from greennode.vbackup_mcp_server.models._common import as_dict, as_gib, as_int, as_text
from pydantic import BaseModel, Field
from typing import TypeVar


_OLDEST = datetime.min.replace(tzinfo=timezone.utc)

_Record = TypeVar("_Record")


def _moment(value: str) -> datetime:
    """Parse an API timestamp into something sortable, never raising.

    A record whose timestamp is missing or unparseable sorts as the oldest
    possible moment rather than sinking the whole sort — one malformed row must
    not decide what a user sees.
    """
    text = (value or "").strip()
    if not text:
        return _OLDEST
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _OLDEST
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def newest_first(records: list[_Record], *fields: str) -> list[_Record]:
    """Sort history records newest first, by the first *fields* value each carries.

    **The API returns history in no particular order.** Every history tool caps
    its result with a ``limit``, so the cap must be applied to sorted records:
    slicing an unordered response keeps an arbitrary subset while presenting it
    as the newest runs, which answers "did last night's backup run?" from
    records that may be months old.
    """

    def key(record: _Record) -> datetime:
        for field in fields:
            moment = _moment(getattr(record, field, ""))
            if moment != _OLDEST:
                return moment
        return _OLDEST

    return sorted(records, key=key, reverse=True)


class BackupHistoryItem(BaseModel):
    """One backup run."""

    id: str = Field(..., description="History record ID; matches the restore point it produced")
    backup_server_id: str = Field("", description="Backup server that ran")
    backup_server_name: str = Field("", description="Backup server name at run time")
    server_id: str = Field("", description="vServer instance that was captured")
    status: str = Field(
        "", description="Run outcome. Anything other than a success carries `error_message`"
    )
    deletion_status: str = Field(
        "", description="Whether the restore point this run produced has since been removed"
    )
    error_message: str = Field("", description="Why the run failed; empty on success")
    snapshot_time: str = Field("", description="When the run started")
    finish_time: str = Field("", description="When the run finished")
    size_gb: float = Field(0, description="Captured size in GiB")
    used_gb: float = Field(0, description="Billable usage in GiB")
    policy_id: str = Field("", description="Policy that triggered the run")
    policy_name_at_run: str = Field(
        "",
        description=(
            "Policy name AS IT WAS at run time, from the embedded snapshot. Use it "
            "to explain an old run — the live policy may have been edited since."
        ),
    )
    destination_id: str = Field("", description="Destination the run wrote to")
    destination_name_at_run: str = Field(
        "", description="Destination name as it was at run time, from the embedded snapshot"
    )
    backend_id: str = Field("", description="Backend the run happened in")
    project_id: str = Field("", description="Project the run belongs to")
    created_at: str = Field("", description="Record creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> BackupHistoryItem:
        """Build a BackupHistoryItem from a raw API dict.

        `policySnapshot` and `destinationSnapshot` arrive as escaped JSON
        STRINGS, not objects — `as_dict` parses either.
        """
        policy = as_dict(data.get("policySnapshot"))
        destination = as_dict(data.get("destinationSnapshot"))
        return cls(
            id=as_text(data.get("id")),
            backup_server_id=as_text(data.get("backupInstanceId")),
            backup_server_name=as_text(data.get("backupInstanceName")),
            server_id=as_text(data.get("serverId")),
            status=as_text(data.get("status")),
            deletion_status=as_text(data.get("deletionStatus")),
            error_message=as_text(data.get("errorMessage")),
            snapshot_time=as_text(data.get("snapshotTime")),
            finish_time=as_text(data.get("finishTime")),
            size_gb=as_gib(data.get("size")),
            used_gb=as_gib(data.get("usage")),
            policy_id=as_text(data.get("policyId") or policy.get("id")),
            policy_name_at_run=as_text(policy.get("name")),
            destination_id=as_text(data.get("destinationId") or destination.get("id")),
            destination_name_at_run=as_text(destination.get("name")),
            backend_id=as_text(data.get("backendId")),
            project_id=as_text(data.get("projectId")),
            created_at=as_text(data.get("createdAt")),
        )


class BackupHistoryListData(BaseModel):
    """Structured output of list_backup_history."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of runs returned")
    runs: list[BackupHistoryItem] = Field(
        default_factory=list, description="Backup runs matching the filters"
    )


class RestoreHistoryItem(BaseModel):
    """One restore operation."""

    id: str = Field(..., description="Restore record ID")
    type: str = Field("", description="What was restored — a whole server or a single volume")
    status: str = Field("", description="Restore outcome")
    backup_server_id: str = Field("", description="Backup server the data came from")
    backup_server_point_id: str = Field("", description="Restore point that was used")
    backup_volume_point_id: str = Field(
        "", description="Volume point used, for a single-volume restore"
    )
    destination_server_id: str = Field("", description="vServer instance the data was written to")
    destination_volume_id: str = Field("", description="Volume the data was written to")
    config: str = Field("", description="Restore configuration as the API reports it, verbatim")
    finish_at: str = Field("", description="When the restore finished")
    backend_id: str = Field("", description="Backend the restore ran in")
    project_id: str = Field("", description="Project the restore belongs to")
    created_at: str = Field("", description="When the restore was requested")
    updated_at: str = Field("", description="Last-update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> RestoreHistoryItem:
        """Build a RestoreHistoryItem from a raw API dict."""
        return cls(
            id=as_text(data.get("id")),
            type=as_text(data.get("type")),
            status=as_text(data.get("status")),
            backup_server_id=as_text(data.get("backupInstanceId")),
            backup_server_point_id=as_text(data.get("backupInstancePointId")),
            backup_volume_point_id=as_text(data.get("backupVolumePointId")),
            destination_server_id=as_text(data.get("destinationServerId")),
            destination_volume_id=as_text(data.get("destinationVolumeId")),
            config=as_text(data.get("config")),
            finish_at=as_text(data.get("finishAt")),
            backend_id=as_text(data.get("backendId")),
            project_id=as_text(data.get("projectId")),
            created_at=as_text(data.get("createdAt")),
            updated_at=as_text(data.get("updatedAt")),
        )


class RestoreHistoryListData(BaseModel):
    """Structured output of list_restore_history."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of restores returned")
    restores: list[RestoreHistoryItem] = Field(
        default_factory=list, description="Restores matching the filters"
    )


class DatabaseBackupHistoryItem(BaseModel):
    """One vDB backup run."""

    id: str = Field(..., description="History record ID (`bk-db-pt-...`)")
    backup_database_id: str = Field("", description="vDB backup resource that ran (`bk-db-...`)")
    backup_database_name: str = Field("", description="Backup resource name at run time")
    database_id: str = Field("", description="vDB instance that was captured, e.g. `pg-...`")
    status: str = Field("", description="Run outcome; a failure carries `error_message`")
    deletion_status: str = Field(
        "", description="Whether the backup this run produced has since been removed"
    )
    error_message: str = Field("", description="Why the run failed; empty on success")
    compressed_gb: float = Field(
        0, description="Size actually stored, in GiB — this is what the vault bills"
    )
    uncompressed_gb: float = Field(
        0, description="Size of the source data before compression, in GiB"
    )
    compressed_bytes: int = Field(0, description="Stored size in bytes, as the API reports it")
    uncompressed_bytes: int = Field(0, description="Source size in bytes")
    policy_name_at_run: str = Field(
        "", description="Policy name as it was at run time, from the embedded snapshot"
    )
    destination_id: str = Field("", description="Destination the run wrote to")
    destination_name_at_run: str = Field(
        "", description="Destination name as it was at run time, from the embedded snapshot"
    )
    created_at: str = Field("", description="When the run started")

    @classmethod
    def from_api(cls, data: dict) -> DatabaseBackupHistoryItem:
        """Build a DatabaseBackupHistoryItem from a raw API dict.

        Like the vServer trail, `policySnapshot` and `destinationSnapshot`
        arrive as escaped JSON strings.
        """
        policy = as_dict(data.get("policySnapshot"))
        destination = as_dict(data.get("destinationSnapshot"))
        return cls(
            id=as_text(data.get("id")),
            backup_database_id=as_text(data.get("backupDatabaseId")),
            backup_database_name=as_text(data.get("backupDatabaseName")),
            database_id=as_text(data.get("databaseId")),
            status=as_text(data.get("status")),
            deletion_status=as_text(data.get("deletionStatus")),
            error_message=as_text(data.get("errorMessage")),
            compressed_gb=as_gib(data.get("compressedSize")),
            uncompressed_gb=as_gib(data.get("uncompressedSize")),
            compressed_bytes=as_int(data.get("compressedSize")),
            uncompressed_bytes=as_int(data.get("uncompressedSize")),
            policy_name_at_run=as_text(policy.get("name")),
            destination_id=as_text(data.get("destinationId") or destination.get("id")),
            destination_name_at_run=as_text(destination.get("name")),
            created_at=as_text(data.get("createdAt")),
        )


class DatabaseBackupHistoryListData(BaseModel):
    """Structured output of list_database_backup_history."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of runs returned")
    runs: list[DatabaseBackupHistoryItem] = Field(
        default_factory=list, description="vDB backup runs matching the filters"
    )


class DatabaseRestoreHistoryItem(BaseModel):
    """One vDB restore operation."""

    id: str = Field(..., description="Restore record ID (`db-res-...`)")
    status: str = Field("", description="Restore outcome")
    backup_database_id: str = Field("", description="Backup resource the data came from")
    backup_database_name: str = Field("", description="Backup resource name")
    backup_database_point_id: str = Field(
        "", description="Backup point that was used (`bk-db-pt-...`)"
    )
    destination_database_id: str = Field(
        "", description="vDB instance the data was written INTO, e.g. `pg-...`"
    )
    finish_at: str = Field("", description="When the restore finished")
    created_at: str = Field("", description="When the restore was requested")
    updated_at: str = Field("", description="Last-update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> DatabaseRestoreHistoryItem:
        """Build a DatabaseRestoreHistoryItem from a raw API dict."""
        return cls(
            id=as_text(data.get("id")),
            status=as_text(data.get("status")),
            backup_database_id=as_text(data.get("backupDatabaseId")),
            backup_database_name=as_text(data.get("backupDatabaseName")),
            backup_database_point_id=as_text(data.get("backupDatabasePointId")),
            destination_database_id=as_text(data.get("destinationDatabaseId")),
            finish_at=as_text(data.get("finishAt")),
            created_at=as_text(data.get("createdAt")),
            updated_at=as_text(data.get("updatedAt")),
        )


class DatabaseRestoreHistoryListData(BaseModel):
    """Structured output of list_database_restore_history."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of restores returned")
    restores: list[DatabaseRestoreHistoryItem] = Field(
        default_factory=list, description="vDB restores matching the filters"
    )


MIGRATION_ACTIONS = ("START-MIGRATING", "COMPLETE-MIGRATING", "ROLLBACK")
"""The three steps a server migration records, in the order they happen.

``START-MIGRATING`` is the cutover that brings the server up at the new site,
``COMPLETE-MIGRATING`` confirms it and releases the source, and ``ROLLBACK``
abandons the migration and returns the server to where it came from.
"""


class ServerMigrationHistoryItem(BaseModel):
    """One step of one server migration."""

    id: str = Field(..., description="Migration history record ID")
    server_id: str = Field("", description="The instance being migrated (`ins-...`)")
    server_name: str = Field("", description="Instance name as it was at the time of the step")
    action: str = Field(
        "",
        description=(
            "What was attempted: START-MIGRATING (cutover to the new site), "
            "COMPLETE-MIGRATING (confirm and release the source) or ROLLBACK "
            "(abandon and return to the source). This is the field that says what "
            "actually happened to the server."
        ),
    )
    status: str = Field(
        "",
        description=(
            "Outcome of the step. It reports the PHASE reached, not the action: a "
            "ROLLBACK finishes as COMPLETE-MIGRATING-SUCCESS, so this field alone "
            "cannot distinguish a completed migration from an abandoned one. Always "
            "read it together with `action`."
        ),
    )
    project_id: str = Field("", description="Project the migration belongs to")
    created_at: str = Field("", description="When the step started")
    updated_at: str = Field("", description="When the step reached this status")

    @classmethod
    def from_api(cls, data: dict) -> ServerMigrationHistoryItem:
        """Build a ServerMigrationHistoryItem from a raw API dict."""
        return cls(
            id=as_text(data.get("id")),
            server_id=as_text(data.get("serverId")),
            server_name=as_text(data.get("serverName")),
            action=as_text(data.get("action")),
            status=as_text(data.get("status")),
            project_id=as_text(data.get("projectId")),
            created_at=as_text(data.get("createdAt")),
            updated_at=as_text(data.get("updatedAt")),
        )


class ServerMigrationHistoryListData(BaseModel):
    """Structured output of list_server_migration_history."""

    region: str = Field(..., description="Region the gateway was called in")
    project_id: str = Field(..., description="Project the migrations belong to")
    total: int = Field(0, description="Number of migration steps returned")
    total_available: int = Field(
        0,
        description=(
            "How many records the API reports for these filters, before `limit` and "
            "any client-side action filter. A larger number than `total` means the "
            "result was capped."
        ),
    )
    migrations: list[ServerMigrationHistoryItem] = Field(
        default_factory=list, description="Migration steps, newest first"
    )


def count_failures(runs: list[BackupHistoryItem]) -> int:
    """Count runs that reported an error message."""
    return sum(1 for run in runs if run.error_message)
