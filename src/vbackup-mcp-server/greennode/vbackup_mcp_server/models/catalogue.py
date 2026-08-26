"""Catalogue models: backends, backup destinations and the platform configuration."""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import as_dict, as_gib, as_int, as_text
from pydantic import BaseModel, Field
from typing import Any


class BackendItem(BaseModel):
    """One vBackup backend — the region-local service a resource is stored in."""

    id: str = Field(..., description="Backend ID, used as `backendId` when filtering")
    name: str = Field("", description="Backend name, e.g. HCM-03")

    @classmethod
    def from_api(cls, data: dict) -> BackendItem:
        """Build a BackendItem from a raw API dict."""
        return cls(id=as_text(data.get("id")), name=as_text(data.get("name")))


class BackendListData(BaseModel):
    """Structured output of list_backends."""

    region: str = Field(..., description="Region the gateway was called in")
    backends: list[BackendItem] = Field(
        default_factory=list, description="Backends visible to the caller"
    )


class VaultInfo(BaseModel):
    """The storage behind a destination, whichever backend serves it.

    A destination of type ``VAULT`` carries its numbers under ``config.vault``
    and one of type ``VSTORAGE`` under ``config.vstorage``; the other key is
    null. Reading only ``vault`` reports an empty, apparently unused store for
    every VSTORAGE destination, so both are tried here and the populated one
    wins.
    """

    region_id: str = Field("", description="Backup region the store lives in")
    region_name: str = Field("", description="Backup region name, e.g. HCM04")
    container_name: str = Field("", description="Container the backups land in, when there is one")
    project_name: str = Field("", description="Storage project name, on a VSTORAGE destination")
    storage_service: str = Field("", description="Backing storage service, e.g. vstorage")
    sku: str = Field("", description="Billing SKU in use, when the API reports one")
    used_gb: float = Field(0, description="Space consumed by backups, in GiB")
    used_bytes: int = Field(0, description="Space consumed in bytes, as the API reports it")
    total_gb: float = Field(0, description="Space the store provides, in GiB; 0 when uncapped")
    total_bytes: int = Field(0, description="Space the store provides, in bytes")

    @classmethod
    def from_api(cls, config: Any) -> VaultInfo:
        """Build a VaultInfo from a destination's `config` field.

        `config` arrives as a dict on some endpoints and as a JSON string on
        others, and the useful values sit one level down under either `vault`
        or `vstorage`.
        """
        wrapper = as_dict(config)
        store = as_dict(wrapper.get("vault")) or as_dict(wrapper.get("vstorage"))
        return cls(
            region_id=as_text(store.get("regionId")),
            region_name=as_text(store.get("regionName")),
            container_name=as_text(store.get("containerName")),
            project_name=as_text(store.get("projectName")),
            storage_service=as_text(store.get("storageService")),
            sku=as_text(store.get("skuUsage")),
            used_gb=as_gib(store.get("used")),
            used_bytes=as_int(store.get("used")),
            total_gb=as_gib(store.get("total")),
            total_bytes=as_int(store.get("total")),
        )


class SoftDeleteInfo(BaseModel):
    """A destination's soft-delete configuration.

    Soft delete is the recycle bin: a deleted restore point is retained for
    ``retain_days`` before it is destroyed for good, and it is **still billed**
    for that whole window. Deleting backups to free storage therefore does
    nothing until the retention elapses.
    """

    enabled: bool = Field(False, description="Whether deleted backups go to the recycle bin")
    retain_days: int = Field(
        0, description="Days a deleted backup is recoverable, and still billed"
    )
    created_at: str = Field("", description="When the configuration was last set")

    @classmethod
    def from_api(cls, value: Any) -> SoftDeleteInfo | None:
        """Build a SoftDeleteInfo, or None when the destination has none set."""
        data = as_dict(value)
        if not data:
            return None
        return cls(
            enabled=bool(data.get("enable")),
            retain_days=as_int(data.get("retainDays")),
            created_at=as_text(data.get("createdAt")),
        )


class VaultLockInfo(BaseModel):
    """A destination's lock configuration — the console calls it Location Lock.

    The lock enforces a retention window on everything stored here and, once
    ``change_duration_days`` has elapsed since it was enabled, **the settings
    can no longer be changed or turned off**. Read it before promising a user
    that a backup or a destination can be removed.
    """

    enabled: bool = Field(False, description="Whether the lock is in force")
    change_duration_days: int = Field(
        0,
        description=(
            "Grace period, in days from when the lock was enabled, during which the "
            "retention bounds can still be edited or the lock disabled. After it "
            "expires the configuration is permanent."
        ),
    )
    min_retention_days: int = Field(
        0, description="Days a backup must be kept; deletion before this is refused"
    )
    max_retention_days: int = Field(
        0, description="Days after which a backup is removed automatically"
    )
    created_at: str = Field("", description="When the lock was enabled")

    @classmethod
    def from_api(cls, value: Any) -> VaultLockInfo | None:
        """Build a VaultLockInfo, or None when the destination has no lock."""
        data = as_dict(value)
        if not data:
            return None
        return cls(
            enabled=bool(data.get("enable")),
            change_duration_days=as_int(data.get("changeDuration")),
            min_retention_days=as_int(data.get("minRetention")),
            max_retention_days=as_int(data.get("maxRetention")),
            created_at=as_text(data.get("createdAt")),
        )


class BackupDestinationItem(BaseModel):
    """One backup destination — the vault backups are written to.

    The GreenNode console calls this a **Backup Location**, and the API tags
    its resources ``BACKUP_LOCATION``; the path and every id field say
    `destination`. They are the same object.
    """

    id: str = Field(..., description="Destination ID (`bk-des-...`)")
    name: str = Field("", description="Destination name")
    status: str = Field("", description="Destination status, e.g. ACTIVE")
    type: str = Field("", description="Storage backend: 'VAULT' or 'VSTORAGE'")
    is_default: bool = Field(
        False, description="True for the destination used when a create omits one"
    )
    product: str = Field(
        "",
        description=(
            "Product this destination stores backups for: 'vServer' or 'vDB'. A "
            "destination only accepts resources of its own product."
        ),
    )
    backup_server_count: int = Field(
        0, description="Backup servers currently writing to this destination"
    )
    quota_unlimited: bool = Field(
        True, description="True when the destination has no capacity ceiling"
    )
    max_quota_gb: int = Field(
        0,
        description=(
            "Capacity ceiling in GB. Meaningless when quota_unlimited is true, where "
            "the API reports 0."
        ),
    )
    vault: VaultInfo = Field(
        default_factory=VaultInfo, description="Underlying storage and how full it is"
    )
    soft_delete: SoftDeleteInfo | None = Field(
        None, description="Recycle-bin configuration, or null when soft delete is off"
    )
    vault_lock: VaultLockInfo | None = Field(
        None,
        description=(
            "Retention lock, or null when unlocked. A locked destination refuses "
            "deletions until its minimum retention has passed."
        ),
    )
    backend_id: str = Field("", description="Backend the destination lives in")
    project_id: str = Field("", description="Project the destination belongs to")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last-update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> BackupDestinationItem:
        """Build a BackupDestinationItem from a raw API dict."""
        quota = as_dict(data.get("maxQuota"))
        return cls(
            id=as_text(data.get("id")),
            name=as_text(data.get("name")),
            status=as_text(data.get("status")),
            type=as_text(data.get("type")),
            is_default=bool(data.get("isDefault")),
            product=as_text(data.get("product")),
            backup_server_count=as_int(data.get("numberOfBackupInstances")),
            quota_unlimited=bool(quota.get("unlimited", True)),
            max_quota_gb=as_int(quota.get("maxQuota")),
            vault=VaultInfo.from_api(data.get("config")),
            soft_delete=SoftDeleteInfo.from_api(data.get("softDeleteConfig")),
            vault_lock=VaultLockInfo.from_api(data.get("vaultLock")),
            backend_id=as_text(data.get("backendId")),
            project_id=as_text(data.get("projectId")),
            created_at=as_text(data.get("createdAt")),
            updated_at=as_text(data.get("updatedAt")),
        )


class BackupDestinationListData(BaseModel):
    """Structured output of list_backup_destinations."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of destinations returned")
    destinations: list[BackupDestinationItem] = Field(
        default_factory=list, description="Destinations matching the filters"
    )


class RetentionLimits(BaseModel):
    """Maximum retention the platform accepts, per cadence."""

    hourly: int = Field(0, description="Maximum hourly restore points kept")
    daily: int = Field(0, description="Maximum daily restore points kept")
    weekly: int = Field(0, description="Maximum weekly restore points kept")
    monthly: int = Field(0, description="Maximum monthly restore points kept")

    @classmethod
    def from_api(cls, data: Any) -> RetentionLimits:
        """Build RetentionLimits from a per-cadence limit object."""
        limits = as_dict(data)
        return cls(
            hourly=as_int(limits.get("hourly")),
            daily=as_int(limits.get("daily")),
            weekly=as_int(limits.get("weekly")),
            monthly=as_int(limits.get("monthly")),
        )


class ConfigurationData(BaseModel):
    """Platform limits a backup policy must respect.

    This is the authority for what a policy may contain. Validate a create or
    update against these values instead of hardcoding bounds — they differ
    between backup policies and snapshot policies, and they can change without
    the API contract changing.
    """

    region: str = Field(..., description="Region the gateway was called in")
    backup_policy_hourly_intervals: list[int] = Field(
        default_factory=list,
        description="The only intervals (in hours) an hourly BACKUP policy may use",
    )
    backup_policy_retention_limits: RetentionLimits = Field(
        default_factory=RetentionLimits, description="Maximum retention per cadence for backups"
    )
    backup_policy_hours: list[str] = Field(
        default_factory=list,
        description=(
            "Clock hours a backup may be scheduled at. Hours the platform has "
            "disabled are excluded — an existing policy can still sit on one, but "
            "a new policy must not pick it."
        ),
    )
    allowed_backup_server_status: list[str] = Field(
        default_factory=list,
        description="vServer instance states that can be added as a backup server",
    )
    snapshot_policy_hourly_intervals: list[int] = Field(
        default_factory=list,
        description="Intervals for vServer SNAPSHOT policies — a different product, listed for contrast",
    )
    snapshot_policy_retention_limits: RetentionLimits = Field(
        default_factory=RetentionLimits, description="Maximum retention per cadence for snapshots"
    )

    @classmethod
    def from_api(cls, region: str, data: Any) -> ConfigurationData:
        """Build ConfigurationData from the `{configs: {...}}` response."""
        configs = as_dict(as_dict(data).get("configs")) or as_dict(data)
        ranges = configs.get("backup_policy_time_ranges")
        hours = [
            as_text(r.get("value"))
            for r in (ranges if isinstance(ranges, list) else [])
            if isinstance(r, dict) and r.get("enable")
        ]
        statuses = as_text(configs.get("allowed_backup_server_status"))
        return cls(
            region=region,
            backup_policy_hourly_intervals=_int_list(configs.get("backup_policy_hourly_interval")),
            backup_policy_retention_limits=RetentionLimits.from_api(
                configs.get("backup_policy_retention_limit")
            ),
            backup_policy_hours=hours,
            allowed_backup_server_status=[s for s in statuses.split(",") if s],
            snapshot_policy_hourly_intervals=_int_list(
                configs.get("snapshot_policy_hourly_interval")
            ),
            snapshot_policy_retention_limits=RetentionLimits.from_api(
                configs.get("snapshot_policy_retention_limit")
            ),
        )


def _int_list(value: Any) -> list[int]:
    """Coerce an API array of numbers to a list of ints."""
    if not isinstance(value, list):
        return []
    return [as_int(v) for v in value]


class ProtectedServerListData(BaseModel):
    """Structured output of list_protected_servers."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of protected instances")
    server_ids: list[str] = Field(
        default_factory=list,
        description=(
            "vServer instance IDs that already have a backup server. Use it to "
            "avoid offering to protect an instance twice; it carries no other detail."
        ),
    )
