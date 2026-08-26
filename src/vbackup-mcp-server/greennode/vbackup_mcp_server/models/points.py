"""Restore-point models — what a backup run actually captured.

Two different volume-point shapes exist and both reach these models:

- the generic one (``id``, ``volumeId``, ``volumeSize``, ``volumeUsage``,
  ``parentId``), and
- the vServer projection (``backupVolumePointId``, ``name``, ``size``,
  ``bootIndex``, ``volumeTypeId``, ``bootable``), which is the one that tells a
  caller which disk was the boot disk.

``BackupVolumePointItem`` reads both, so a handler never has to know which
endpoint its payload came from.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import (
    as_dict,
    as_gib,
    as_int,
    as_text,
    resource_id,
)
from pydantic import BaseModel, Field
from typing import Any


class BackupVolumePointItem(BaseModel):
    """One volume's slice of a restore point."""

    id: str = Field(..., description="Volume point ID (`bk-vol-pt-...`)")
    name: str = Field("", description="Volume name at capture time, when reported")
    volume_id: str = Field("", description="Source volume ID, when reported")
    backup_server_point_id: str = Field("", description="The restore point this slice belongs to")
    status: str = Field("", description="Volume point status")
    bootable: bool = Field(False, description="True when this was the boot disk")
    boot_index: int = Field(
        0, description="Disk order at capture time; 0 is the boot disk when bootable"
    )
    volume_type_id: str = Field("", description="vServer volume type the disk used")
    size_gb: float = Field(0, description="Captured volume size in GiB")
    size_bytes: int = Field(0, description="Captured volume size in bytes")
    used_gb: float = Field(0, description="Space in use at capture time, in GiB")
    snapshot_time: str = Field("", description="When the capture started")
    finish_time: str = Field("", description="When the capture finished")

    @classmethod
    def from_api(cls, data: dict) -> BackupVolumePointItem:
        """Build a BackupVolumePointItem from either volume-point shape."""
        size = data.get("volumeSize") if data.get("volumeSize") is not None else data.get("size")
        return cls(
            id=resource_id(data, "backupVolumePointId", "id"),
            name=as_text(data.get("name")),
            volume_id=as_text(data.get("volumeId")),
            backup_server_point_id=as_text(
                data.get("backupInstancePointId") or data.get("parentId")
            ),
            status=as_text(data.get("status")),
            bootable=bool(data.get("bootable")),
            boot_index=as_int(data.get("bootIndex")),
            volume_type_id=as_text(data.get("volumeTypeId")),
            size_gb=as_gib(size),
            size_bytes=as_int(size),
            used_gb=as_gib(data.get("volumeUsage")),
            snapshot_time=as_text(data.get("snapshotTime")),
            finish_time=as_text(data.get("finishTime")),
        )


class BackupServerPointItem(BaseModel):
    """One restore point — everything a single backup run captured."""

    id: str = Field(
        ..., description="Restore point ID (`bk-ins-pt-...`) — the id a restore would take"
    )
    backup_server_id: str = Field("", description="Backup server that produced it")
    server_id: str = Field("", description="vServer instance that was captured")
    status: str = Field("", description="Point status, e.g. ACTIVE")
    snapshot_time: str = Field("", description="When the run started")
    finish_time: str = Field("", description="When the run finished")
    size_gb: float = Field(0, description="Captured size in GiB")
    used_gb: float = Field(0, description="Billable usage in GiB")
    size_bytes: int = Field(0, description="Captured size in bytes")
    used_bytes: int = Field(0, description="Billable usage in bytes")
    destination_id: str = Field("", description="Destination the point is stored in")
    policy_name_at_run: str = Field(
        "",
        description=(
            "Name of the policy AS IT WAS when the run happened, read from the "
            "embedded snapshot. It stays readable after the policy is edited or "
            "deleted, so use it to explain an old run rather than re-reading the "
            "policy today."
        ),
    )
    volume_points: list[BackupVolumePointItem] = Field(
        default_factory=list,
        description="Per-volume slices of this point, when the API includes them",
    )
    backend_id: str = Field("", description="Backend the point lives in")
    project_id: str = Field("", description="Project the point belongs to")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> BackupServerPointItem:
        """Build a BackupServerPointItem from a raw API dict."""
        volume_points = data.get("backupVolumePoints")
        destination = as_dict(data.get("destination"))
        policy_snapshot = as_dict(data.get("policySnapshot"))
        return cls(
            id=as_text(data.get("id")),
            backup_server_id=as_text(data.get("backupInstanceId")),
            server_id=as_text(data.get("serverId")),
            status=as_text(data.get("status")),
            snapshot_time=as_text(data.get("snapshotTime")),
            finish_time=as_text(data.get("finishTime")),
            size_gb=as_gib(data.get("size")),
            used_gb=as_gib(data.get("usage")),
            size_bytes=as_int(data.get("size")),
            used_bytes=as_int(data.get("usage")),
            destination_id=as_text(destination.get("id") or data.get("destinationId")),
            policy_name_at_run=as_text(policy_snapshot.get("name")),
            volume_points=[
                BackupVolumePointItem.from_api(v)
                for v in (volume_points if isinstance(volume_points, list) else [])
                if isinstance(v, dict)
            ],
            backend_id=as_text(data.get("backendId")),
            project_id=as_text(data.get("projectId")),
            created_at=as_text(data.get("createdAt")),
        )


class BackupServerPointListData(BaseModel):
    """Structured output of the restore-point listings."""

    region: str = Field(..., description="Region the gateway was called in")
    backup_server_id: str = Field("", description="Backup server the points belong to")
    total: int = Field(0, description="Number of restore points returned")
    points: list[BackupServerPointItem] = Field(
        default_factory=list, description="Restore points, newest first as the API orders them"
    )


class BackupVolumePointListData(BaseModel):
    """Structured output of list_vserver_backup_volume_points."""

    region: str = Field(..., description="Region the gateway was called in")
    backup_server_point_id: str = Field("", description="Restore point the slices belong to")
    total: int = Field(0, description="Number of volume points returned")
    volume_points: list[BackupVolumePointItem] = Field(
        default_factory=list, description="Per-volume slices of the restore point"
    )


class VolumeUsageItem(BaseModel):
    """Current size and usage of one volume, as vBackup sees it."""

    volume_id: str = Field(..., description="Volume ID (`vol-...`)")
    size_gb: float = Field(0, description="Volume size in GiB")
    used_gb: float = Field(0, description="Space in use in GiB — what a backup would transfer")
    size_bytes: int = Field(0, description="Volume size in bytes")
    used_bytes: int = Field(0, description="Space in use in bytes")
    backend_id: str = Field("", description="Backend the volume is measured in")
    project_id: str = Field("", description="Project the volume belongs to")

    @classmethod
    def from_api(cls, data: dict) -> VolumeUsageItem:
        """Build a VolumeUsageItem from a raw API dict."""
        return cls(
            volume_id=as_text(data.get("volumeId")),
            size_gb=as_gib(data.get("volumeSize")),
            used_gb=as_gib(data.get("volumeUsage")),
            size_bytes=as_int(data.get("volumeSize")),
            used_bytes=as_int(data.get("volumeUsage")),
            backend_id=as_text(data.get("backendId")),
            project_id=as_text(data.get("projectId")),
        )


class VolumeUsageListData(BaseModel):
    """Structured output of list_volume_usage."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of volumes measured")
    volumes: list[VolumeUsageItem] = Field(
        default_factory=list, description="Usage per requested volume"
    )
    missing_volume_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Requested volumes the API did not return. A volume whose server was "
            "deleted no longer exists in vServer and cannot be measured, even "
            "though its backups remain."
        ),
    )


def missing_ids(requested: list[str], returned: list[Any]) -> list[str]:
    """Return the requested volume ids absent from the API's answer."""
    found = {as_text(item.get("volumeId")) for item in returned if isinstance(item, dict)}
    return [vid for vid in requested if vid not in found]


class VolumePointDownloadUrls(BaseModel):
    """Signed download links for one volume inside a restore point."""

    volume_point_id: str = Field("", description="Volume point the links belong to")
    volume_id: str = Field("", description="Source volume the data came from (`vol-...`)")
    urls: list[str] = Field(
        default_factory=list,
        description=(
            "Pre-signed download links, in order. A volume is split across several "
            "links when it is large — all of them are needed to reconstruct the disk, "
            "so never present one as 'the download'."
        ),
    )

    @classmethod
    def from_api(cls, data: dict) -> VolumePointDownloadUrls:
        """Build a VolumePointDownloadUrls from one entry of the pre-signed response."""
        raw = data.get("preSignedUrl")
        urls = [u for u in raw if isinstance(u, str)] if isinstance(raw, list) else []
        if isinstance(raw, str) and raw:
            urls = [raw]
        return cls(
            volume_point_id=as_text(data.get("id")),
            volume_id=as_text(data.get("volumeId")),
            urls=urls,
        )


class BackupServerPointDownloadData(BaseModel):
    """Structured output of get_backup_server_point_download_urls.

    Every link in here is a **bearer credential**: anyone holding one can
    download the backup without authenticating again. Treat the whole object as
    secret.
    """

    region: str = Field(..., description="Region the gateway was called in")
    point_id: str = Field("", description="Restore point the links belong to")
    backup_server_id: str = Field("", description="Backup server the point belongs to")
    total_volumes: int = Field(0, description="Volumes covered by the links")
    volumes: list[VolumePointDownloadUrls] = Field(
        default_factory=list, description="Per-volume download links"
    )
    warning: str = Field(
        "These links grant access to the backup data without further authentication. "
        "Do not paste them into shared chats, tickets or logs, and expect them to expire.",
        description="Handling warning to repeat to the user alongside the links",
    )
