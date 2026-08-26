"""Models for the ``/v1/vserver/**`` projection of the backup API.

The projection is NOT the generic family under another URL — it renames every
field and drops several. Verified against the live gateway:

| Generic family | vServer projection |
|---|---|
| ``id`` | ``backupInstanceId`` |
| ``name`` | ``backupInstanceName`` |
| ``destination`` | ``backupDestination`` |
| ``volumes`` (a list) | ``protectedVolumes`` (a COUNT) |
| ``status``, ``backupEnabled``, ``policy``, ``serverDeleted`` | absent |

Reusing the generic models here produced empty ids and a `backup_enabled` of
false on servers whose schedule was running — a missing field read as "paused".
These models therefore expose only what the projection actually carries, and
say where to go for the rest.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import (
    as_dict,
    as_gib,
    as_int,
    as_text,
    resource_id,
)
from greennode.vbackup_mcp_server.models.catalogue import VaultInfo
from pydantic import BaseModel, Field
from typing import Any


class VserverServerInfo(BaseModel):
    """The vServer instance as it was when the point was captured.

    Only the projection reports this: the image the machine was built from is
    what tells a user whether a restore point still matches the OS they run.
    """

    name: str = Field("", description="Instance name at capture time")
    image_id: str = Field("", description="Image the instance was built from")
    image_type: str = Field("", description="Image family, e.g. Ubuntu_GPU")
    image_version: str = Field("", description="Image version string")
    encryption_volume: bool = Field(False, description="Whether the disks were encrypted")

    @classmethod
    def from_api(cls, data: Any) -> VserverServerInfo:
        """Build a VserverServerInfo from an embedded `serverInfo` object."""
        info = as_dict(data)
        return cls(
            name=as_text(info.get("name")),
            image_id=as_text(info.get("imageId")),
            image_type=as_text(info.get("imageType")),
            image_version=as_text(info.get("imageVersion")),
            encryption_volume=bool(info.get("encryptionVolume")),
        )


class VserverBackupServerItem(BaseModel):
    """One protected instance, as the vServer projection reports it."""

    id: str = Field(..., description="Backup server ID (`bk-ins-...`)")
    name: str = Field("", description="Backup server name")
    server_id: str = Field("", description="The protected vServer instance ID (`ins-...`)")
    protected_volume_count: int = Field(
        0,
        description=(
            "How many disks are covered — a COUNT, not a list. For the per-disk "
            "detail call list_backup_server_volumes."
        ),
    )
    destination_id: str = Field("", description="Destination the backups are written to")
    destination_name: str = Field("", description="Destination name")
    vault: VaultInfo = Field(
        default_factory=VaultInfo, description="Underlying vStorage vault of the destination"
    )
    products: list[str] = Field(
        default_factory=list, description="Products this backup covers, e.g. ['vServer']"
    )
    latest_record: str = Field("", description="Timestamp of the most recent successful run")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> VserverBackupServerItem:
        """Build a VserverBackupServerItem from a raw projection dict."""
        destination = as_dict(data.get("backupDestination"))
        products = data.get("products")
        return cls(
            id=resource_id(data, "backupInstanceId", "id"),
            name=as_text(data.get("backupInstanceName") or data.get("name")),
            server_id=as_text(data.get("serverId")),
            protected_volume_count=as_int(data.get("protectedVolumes")),
            destination_id=as_text(destination.get("id")),
            destination_name=as_text(destination.get("name")),
            vault=VaultInfo.from_api(destination.get("config") or destination),
            products=[as_text(p) for p in products] if isinstance(products, list) else [],
            latest_record=as_text(data.get("latestRecord")),
            created_at=as_text(data.get("createdAt")),
        )


class VserverBackupServerListData(BaseModel):
    """Structured output of list_vserver_backup_servers."""

    region: str = Field(..., description="Region the gateway was called in")
    project_id: str = Field("", description="Project the listing was scoped to")
    total: int = Field(0, description="Number of backup servers returned")
    backup_servers: list[VserverBackupServerItem] = Field(
        default_factory=list, description="Protected instances in this project"
    )


class VserverBackupServerPointItem(BaseModel):
    """One restore point, as the vServer projection reports it."""

    id: str = Field(..., description="Restore point ID (`bk-ins-pt-...`)")
    backup_server_id: str = Field("", description="Backup server that produced it")
    snapshot_time: str = Field("", description="When the run started")
    size_gb: float = Field(0, description="Captured size in GiB")
    used_gb: float = Field(0, description="Billable usage in GiB")
    size_bytes: int = Field(0, description="Captured size in bytes")
    used_bytes: int = Field(0, description="Billable usage in bytes")
    server_info: VserverServerInfo = Field(
        default_factory=VserverServerInfo,
        description="The instance as it was at capture time, including its image",
    )
    vault: VaultInfo = Field(default_factory=VaultInfo, description="Vault the point is stored in")

    @classmethod
    def from_api(cls, data: dict) -> VserverBackupServerPointItem:
        """Build a VserverBackupServerPointItem from a raw projection dict."""
        destination = as_dict(data.get("backupDestination"))
        return cls(
            id=resource_id(data, "backupInstancePointId", "id"),
            backup_server_id=as_text(data.get("backupInstanceId")),
            snapshot_time=as_text(data.get("snapshotTime")),
            size_gb=as_gib(data.get("size")),
            used_gb=as_gib(data.get("usage")),
            size_bytes=as_int(data.get("size")),
            used_bytes=as_int(data.get("usage")),
            server_info=VserverServerInfo.from_api(data.get("serverInfo")),
            vault=VaultInfo.from_api(destination.get("config") or destination),
        )


class VserverBackupServerPointListData(BaseModel):
    """Structured output of list_vserver_backup_server_points."""

    region: str = Field(..., description="Region the gateway was called in")
    backup_server_id: str = Field("", description="Backup server the points belong to")
    total: int = Field(0, description="Number of restore points returned")
    points: list[VserverBackupServerPointItem] = Field(
        default_factory=list, description="Restore points as the projection reports them"
    )
