"""Snapshot models: point-in-time copies of a server or a volume.

A *snapshot point* is one recovery point; a *snapshot policy* is the
configuration that schedules them. The two carry different ids and are
deleted by different tools.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.models._common import _resource_id
from pydantic import BaseModel, Field


class SnapshotPointItem(BaseModel):
    """One snapshot point of a server or a volume."""

    id: str = Field(..., description="Snapshot point ID — pass this to rollback and delete")
    name: str = Field("", description="Snapshot name")
    description: str = Field("", description="Free-text description")
    status: str = Field("", description="Status; only a completed snapshot can be rolled back")
    size_gb: int = Field(0, description="Size of the snapshot data")
    server_id: str = Field("", description="Server the snapshot belongs to (server snapshots)")
    volume_id: str = Field("", description="Volume the snapshot belongs to (volume snapshots)")
    schedule_type: str = Field(
        "", description="How it was taken — manual ('now') or by the auto-snapshot policy"
    )
    is_permanent: bool = Field(
        False, description="True when the snapshot never expires; otherwise retained_days applies"
    )
    retained_days: int | None = Field(
        None, description="Days the snapshot is kept before automatic deletion"
    )
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "SnapshotPointItem":
        """Build a SnapshotPointItem from a raw server or volume snapshot point."""
        config = data.get("snapshotConfig") or {}
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            description=data.get("description") or "",
            status=data.get("status") or "",
            size_gb=data.get("size") or 0,
            server_id=data.get("serverId") or "",
            volume_id=data.get("volumeId") or "",
            schedule_type=data.get("scheduleType") or "",
            is_permanent=bool(config.get("isPermanently", False)),
            retained_days=config.get("retainedDays"),
            created_at=data.get("createdAt") or "",
        )


class SnapshotPointListData(BaseModel):
    """Structured response for list_server_snapshots / list_volume_snapshots."""

    region: str = Field(..., description="Region the snapshots were fetched from")
    resource_id: str = Field(..., description="Server or volume the snapshots belong to")
    snapshots: list[SnapshotPointItem] = Field(
        default_factory=list, description="Snapshot points, newest first as the API returns them"
    )


class SnapshotPolicyData(BaseModel):
    """Structured response for the snapshot-policy detail endpoints."""

    region: str = Field(..., description="Region the policy was fetched from")
    resource_id: str = Field(..., description="Server or volume the policy belongs to")
    configured: bool = Field(
        ..., description="False when no snapshot policy has ever been set up for the resource"
    )
    id: str = Field("", description="Snapshot configuration ID")
    name: str = Field("", description="Snapshot configuration name")
    description: str = Field("", description="Free-text description")
    enabled: bool = Field(False, description="Whether automatic snapshots are currently running")
    snapshot_policy_id: str = Field(
        "", description="Schedule policy in force (frequency/retention live in the policy)"
    )
    snapshot_count: int = Field(0, description="Number of snapshot points the resource holds")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, region: str, resource_id: str, data: dict | None) -> "SnapshotPolicyData":
        """Build a SnapshotPolicyData, tolerating the null the API returns when unset."""
        if not data:
            return cls(region=region, resource_id=resource_id, configured=False)
        points = data.get("snapshotServerPoints") or data.get("snapshotVolumePoints") or []
        return cls(
            region=region,
            resource_id=resource_id,
            configured=True,
            id=data.get("id") or "",
            name=data.get("name") or "",
            description=data.get("description") or "",
            enabled=bool(data.get("enableSnapshot", False)),
            snapshot_policy_id=data.get("snapshotPolicyId") or "",
            snapshot_count=len(points),
            created_at=data.get("createdAt") or "",
        )


class SnapshotPolicyItem(BaseModel):
    """One schedule policy from the backup catalogue.

    A policy is the *when and how long*: at what time of day snapshots are
    taken, how often, and how many are kept. Resources reference one by id
    through `snapshotPolicyId`.
    """

    id: str = Field(..., description="Policy ID — pass this as snapshotPolicyId")
    name: str = Field("", description="Policy name")
    policy_type: str = Field("", description="Policy family, e.g. DEFAULT or ENHANCED")
    schedule: str = Field(
        "", description="Human-readable summary of the cadence and how many copies are kept"
    )
    run_at: str = Field("", description="Local time of day the schedule fires, with its time zone")
    server_count: int = Field(0, description="Servers currently using this policy")
    volume_count: int = Field(0, description="Volumes currently using this policy")

    @classmethod
    def from_api(cls, data: dict) -> "SnapshotPolicyItem":
        """Build a SnapshotPolicyItem, flattening the nested cadence config.

        The cadence arrives as four enable flags each with its own config
        object, and the numbers are floats — summarising it here saves every
        caller from walking that structure to answer "how often, kept for how
        long".
        """
        config = data.get("config")
        config = config if isinstance(config, dict) else {}
        parts: list[str] = []
        for period, label in (
            ("hourly", "hourly"),
            ("daily", "daily"),
            ("weekly", "weekly"),
            ("monthly", "monthly"),
        ):
            if not config.get(f"{period}Enabled"):
                continue
            detail = config.get(f"{period}Config")
            detail = detail if isinstance(detail, dict) else {}
            interval = detail.get("interval")
            retention = detail.get("retention")
            text = label
            if interval:
                text += f" every {int(interval)}"
            if retention:
                text += f", keep {int(retention)}"
            parts.append(text)
        hour, minute = config.get("hour"), config.get("minute")
        run_at = ""
        if hour is not None and minute is not None:
            run_at = f"{int(hour):02d}:{int(minute):02d}"
            zone = config.get("timeZone")
            if zone:
                run_at += f" {zone}"
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            policy_type=data.get("policyType") or "",
            schedule="; ".join(parts),
            run_at=run_at,
            server_count=int(data.get("snapshotServerCount") or 0),
            volume_count=int(data.get("snapshotVolumeCount") or 0),
        )


class SnapshotPolicyListData(BaseModel):
    """Structured response for list_snapshot_policies."""

    region: str = Field(..., description="Region the policies were fetched from")
    policies: list[SnapshotPolicyItem] = Field(
        default_factory=list, description="Schedule policies the project can use"
    )


class SharedSnapshotItem(BaseModel):
    """One share grant on a server snapshot."""

    id: str = Field(..., description="Share ID — pass this to delete_shared_server_snapshot")
    resource_id: str = Field("", description="Snapshot resource that is shared")
    resource_type: str = Field("", description="Kind of resource shared")
    permission: str = Field("", description="Permission granted to the recipient")
    shared_user_id: int | None = Field(None, description="User the snapshot is shared with")
    created_at: str = Field("", description="When the share was granted")

    @classmethod
    def from_api(cls, data: dict) -> "SharedSnapshotItem":
        """Build a SharedSnapshotItem from a raw share object."""
        return cls(
            id=_resource_id(data),
            resource_id=data.get("resourceId") or "",
            resource_type=data.get("resourceType") or "",
            permission=data.get("permission") or "",
            shared_user_id=data.get("sharedUserId"),
            created_at=data.get("createdAt") or "",
        )


class SharedSnapshotListData(BaseModel):
    """Structured response for list_shared_server_snapshots."""

    region: str = Field(..., description="Region the shares were fetched from")
    server_id: str = Field(..., description="Server whose snapshot shares these are")
    shares: list[SharedSnapshotItem] = Field(default_factory=list, description="Share grants")
