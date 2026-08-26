"""Backup-server models — a protected vServer instance and its volumes."""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import as_dict, as_gib, as_int, as_text
from greennode.vbackup_mcp_server.models.policy import BackupPolicyRef
from pydantic import BaseModel, Field
from typing import Any


class BackupDestinationRef(BaseModel):
    """A destination as embedded inside a backup server's payload."""

    id: str = Field("", description="Destination ID (`bk-des-...`)")
    name: str = Field("", description="Destination name")
    status: str = Field("", description="Destination status, e.g. ACTIVE")
    type: str = Field("", description="Destination type, e.g. VAULT")
    is_default: bool = Field(False, description="True for the account's default destination")

    @classmethod
    def from_api(cls, data: Any) -> BackupDestinationRef:
        """Build a BackupDestinationRef from an embedded `destination` object."""
        payload = as_dict(data)
        if not payload:
            return cls()
        return cls(
            id=as_text(payload.get("id")),
            name=as_text(payload.get("name")),
            status=as_text(payload.get("status")),
            type=as_text(payload.get("type")),
            is_default=bool(payload.get("isDefault")),
        )


class BackupServerVolumeItem(BaseModel):
    """One volume of a protected server, and whether it is included in backups."""

    volume_id: str = Field(..., description="Volume ID on vServer (`vol-...`)")
    backup_enabled: bool = Field(
        False,
        description=(
            "False means this disk is EXCLUDED from every run, even while the "
            "backup server itself is enabled. Restoring will not bring it back."
        ),
    )
    size_gb: float = Field(0, description="Volume size in GiB")
    used_gb: float = Field(0, description="Space in use on the volume, in GiB")
    size_bytes: int = Field(0, description="Volume size in bytes, as the API reports it")
    used_bytes: int = Field(0, description="Space in use in bytes, as the API reports it")
    latest_record: str = Field("", description="Timestamp of this volume's most recent backup")

    @classmethod
    def from_api(cls, data: dict) -> BackupServerVolumeItem:
        """Build a BackupServerVolumeItem from a raw API dict."""
        return cls(
            volume_id=as_text(data.get("volumeId")),
            backup_enabled=bool(data.get("backupEnabled")),
            size_gb=as_gib(data.get("volumeSize")),
            used_gb=as_gib(data.get("volumeUsage")),
            size_bytes=as_int(data.get("volumeSize")),
            used_bytes=as_int(data.get("volumeUsage")),
            latest_record=as_text(data.get("latestRecord")),
        )


class BackupServerVolumeListData(BaseModel):
    """Structured output of list_backup_server_volumes."""

    region: str = Field(..., description="Region the gateway was called in")
    backup_server_id: str = Field(..., description="Backup server the volumes belong to")
    total: int = Field(0, description="Number of volumes")
    volumes: list[BackupServerVolumeItem] = Field(
        default_factory=list, description="The protected server's volumes"
    )


class BackupServerItem(BaseModel):
    """One protected server — the join of a vServer instance, a policy and a destination."""

    id: str = Field(
        ...,
        description="Backup server ID (`bk-ins-...`) — the id every other backup-server tool takes",
    )
    name: str = Field("", description="Backup server name")
    server_id: str = Field(
        "",
        description="The protected vServer instance ID (`ins-...`); not interchangeable with `id`",
    )
    server_deleted: bool = Field(
        False,
        description=(
            "True when the source vServer instance no longer exists. Its restore "
            "points survive and are STILL BILLED — report this, never hide it."
        ),
    )
    status: str = Field("", description="Backup server status, e.g. ACTIVE")
    backup_enabled: bool = Field(
        False,
        description=(
            "False means the schedule is paused: no new runs happen, existing "
            "restore points are untouched."
        ),
    )
    description: str = Field("", description="Free-text description")
    policy: BackupPolicyRef = Field(
        default_factory=BackupPolicyRef, description="The attached backup policy"
    )
    destination: BackupDestinationRef = Field(
        default_factory=BackupDestinationRef, description="Where the backups are stored"
    )
    volumes: list[BackupServerVolumeItem] = Field(
        default_factory=list, description="The server's volumes and their per-disk backup flag"
    )
    backup_policy_id: str = Field("", description="Attached policy ID")
    backup_destination_id: str = Field("", description="Attached destination ID")
    backend_id: str = Field("", description="Backend the resource lives in")
    project_id: str = Field("", description="Project the resource belongs to")
    latest_record: str = Field("", description="Timestamp of the most recent successful run")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last-update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> BackupServerItem:
        """Build a BackupServerItem from a raw API dict."""
        volumes = data.get("volumes")
        return cls(
            id=as_text(data.get("id")),
            name=as_text(data.get("name")),
            server_id=as_text(data.get("serverId")),
            server_deleted=bool(data.get("serverDeleted")),
            status=as_text(data.get("status")),
            backup_enabled=bool(data.get("backupEnabled")),
            description=as_text(data.get("description")),
            policy=BackupPolicyRef.from_api(data.get("policy")),
            destination=BackupDestinationRef.from_api(data.get("destination")),
            volumes=[
                BackupServerVolumeItem.from_api(v)
                for v in (volumes if isinstance(volumes, list) else [])
                if isinstance(v, dict)
            ],
            backup_policy_id=as_text(data.get("backupPolicyId")),
            backup_destination_id=as_text(data.get("backupDestinationId")),
            backend_id=as_text(data.get("backendId")),
            project_id=as_text(data.get("projectId")),
            latest_record=as_text(data.get("latestRecord")),
            created_at=as_text(data.get("createdAt")),
            updated_at=as_text(data.get("updatedAt")),
        )


class BackupServerListData(BaseModel):
    """Structured output of list_backup_servers."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of backup servers returned")
    backup_servers: list[BackupServerItem] = Field(
        default_factory=list, description="Protected servers matching the filters"
    )


class WriteResult(BaseModel):
    """Outcome of a write that the API answers without a body.

    Most vBackup mutations reply 200/204 with nothing in them, so the tool
    reports what it did rather than echoing a payload that does not exist.
    """

    region: str = Field(..., description="Region the write was applied in")
    resource_id: str = Field("", description="ID of the resource that was changed")
    action: str = Field(..., description="What was done, e.g. 'enabled' or 'deleted'")
    succeeded: bool = Field(True, description="True when the API accepted the change")
    detail: str = Field(
        "", description="Any follow-up the caller should know about, e.g. what to verify next"
    )
