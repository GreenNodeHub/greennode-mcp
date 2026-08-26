"""Block-storage models: volumes, their history, user images, Kubernetes PVs."""

from __future__ import annotations

from greennode.vserver_mcp_server.models._common import _resource_id, _zone_id
from pydantic import BaseModel, Field


class VolumeItem(BaseModel):
    """One block-storage volume."""

    id: str = Field(..., description="Volume ID")
    name: str = Field("", description="Volume name")
    size_gb: int = Field(0, description="Size in GiB")
    status: str = Field("", description="Lifecycle status, e.g. AVAILABLE, IN-USE")
    volume_type_id: str = Field("", description="Volume type (IOPS tier) of the volume")
    zone_id: str = Field("", description="Availability zone; a volume only attaches inside it")
    server_id: str = Field("", description="Server the volume is attached to, if any")
    bootable: bool = Field(False, description="True when this is a server's root volume")
    multiattach: bool = Field(False, description="True when the volume may attach to many servers")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "VolumeItem":
        """Build a VolumeItem from a raw vServer volume object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            size_gb=int(data.get("size") or 0),
            status=data.get("status") or "",
            volume_type_id=data.get("volumeTypeId") or "",
            zone_id=_zone_id(data),
            server_id=data.get("serverId") or "",
            bootable=bool(data.get("bootable", False)),
            multiattach=bool(data.get("multiattach", data.get("multiAttach", False))),
            created_at=data.get("createdAt") or "",
        )


class VolumeListData(BaseModel):
    """Structured response for list_volumes."""

    region: str = Field(..., description="Region the volumes were fetched from")
    volumes: list[VolumeItem] = Field(default_factory=list, description="Volumes in the project")


class VolumeHistoryItem(BaseModel):
    """One size/IOPS change in a volume's history."""

    type: str = Field("", description="What happened, e.g. CREATE or RESIZE")
    size_gb: int = Field(0, description="Size the volume had after the change")
    iops: str = Field("", description="IOPS tier the volume had after the change")
    started_at: str = Field("", description="When the change took effect")

    @classmethod
    def from_api(cls, data: dict) -> "VolumeHistoryItem":
        """Build a VolumeHistoryItem from a raw volume-history entry."""
        return cls(
            type=data.get("type") or "",
            size_gb=data.get("size") or 0,
            iops=str(data.get("iops") or ""),
            started_at=data.get("start") or "",
        )


class VolumeHistoryListData(BaseModel):
    """Structured response for list_volume_history."""

    region: str = Field(..., description="Region the history was fetched from")
    volume_id: str = Field(..., description="Volume the history belongs to")
    history: list[VolumeHistoryItem] = Field(
        default_factory=list, description="Size and IOPS changes over the volume's life"
    )


class UserImageItem(BaseModel):
    """One user image (a custom image captured from a server)."""

    id: str = Field(..., description="User image ID — usable as imageId in create_server")
    name: str = Field("", description="Image name")
    status: str = Field("", description="Lifecycle status")
    size_gb: int = Field(0, description="Image size in GiB")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "UserImageItem":
        """Build a UserImageItem from a raw vServer user-image object.

        User images report their id as ``uuid`` and their size as
        ``imageSize``, unlike the volume and server objects next to them.
        """
        return cls(
            id=_resource_id(data),
            name=data.get("name") or data.get("displayName") or "",
            status=data.get("status") or "",
            size_gb=int(data.get("imageSize") or data.get("size") or 0),
            created_at=data.get("createdAt") or "",
        )


class UserImageListData(BaseModel):
    """Structured response for list_user_images."""

    region: str = Field(..., description="Region the images were fetched from")
    user_images: list[UserImageItem] = Field(
        default_factory=list, description="User images in the project"
    )


class PersistentVolumeItem(BaseModel):
    """One Kubernetes persistent volume backed by vServer block storage."""

    id: str = Field(..., description="Underlying volume ID")
    name: str = Field("", description="Persistent volume name")
    status: str = Field("", description="Provisioning status")
    size_gb: int = Field(0, description="Size in GiB")
    cluster_id: str = Field("", description="VKS cluster that provisioned it")
    server_id: str = Field("", description="Node the volume is attached to")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "PersistentVolumeItem":
        """Build a PersistentVolumeItem from a raw persistent-volume object."""
        return cls(
            id=data.get("volumeId") or data.get("uuid") or "",
            name=data.get("name") or "",
            status=data.get("status") or "",
            size_gb=data.get("size") or 0,
            cluster_id=data.get("clusterId") or "",
            server_id=data.get("vmId") or "",
            created_at=data.get("createdAt") or "",
        )


class PersistentVolumeListData(BaseModel):
    """Structured response for list_persistent_volumes."""

    region: str = Field(..., description="Region the persistent volumes were fetched from")
    persistent_volumes: list[PersistentVolumeItem] = Field(
        default_factory=list, description="Kubernetes-managed volumes"
    )
