"""Typed request DTOs for every vBackup write.

All of them set ``extra="forbid"``: a misspelt field is rejected here rather
than silently dropped by the gateway, which on this product would mean a
policy that does not keep what the user asked for.

Field names are camelCase because that is what the API takes; the bounds come
from the live ``GET /v1/configurations`` payload rather than from the published
spec, which is out of date. ``get_configuration`` remains the authority — it can
change without the API contract changing, so re-read it before a create instead
of trusting these ceilings.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.models.database import DatabaseType
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


BackupType = Literal["FULL", "INCREMENTAL"]

HourlyInterval = Literal[4, 6, 8, 12]

RETENTION_MAX = 30000

MONTHLY_BY = Literal["DAY_OF_MONTH", "DAY_OF_WEEK"]

VAULT_LOCK_MAX_CHANGE_DURATION = 7


class HourlyConfigDto(BaseModel):
    """Hourly cadence of a backup policy."""

    model_config = ConfigDict(extra="forbid")

    interval: HourlyInterval = Field(
        ...,
        description=(
            "Hours between runs. Backup policies accept 4, 6, 8 or 12 — narrower "
            "than the snapshot policies of vServer. Confirm against "
            "get_configuration.backup_policy_hourly_intervals."
        ),
    )
    retention: int = Field(
        ..., ge=1, le=RETENTION_MAX, description="How many hourly restore points to keep"
    )
    backupType: BackupType = Field(
        "INCREMENTAL",
        description=(
            "INCREMENTAL stores only what changed since the previous run and is the "
            "normal choice for an hourly cadence; FULL copies everything every time "
            "and costs proportionally more storage."
        ),
    )
    incrementalQuantity: int = Field(
        0,
        ge=0,
        description=(
            "Incremental runs taken between two FULL runs. Only meaningful when "
            "backupType is INCREMENTAL; leave 0 for FULL."
        ),
    )


class DailyConfigDto(BaseModel):
    """Daily cadence of a backup policy."""

    model_config = ConfigDict(extra="forbid")

    retention: int = Field(
        ..., ge=1, le=RETENTION_MAX, description="How many daily restore points to keep"
    )
    backupType: BackupType = Field("FULL", description="FULL or INCREMENTAL")
    incrementalQuantity: int = Field(
        0, ge=0, description="Incremental runs between two FULL runs; 0 for FULL"
    )


class WeeklyConfigDto(BaseModel):
    """Weekly cadence of a backup policy."""

    model_config = ConfigDict(extra="forbid")

    dayOfWeek: int = Field(..., ge=1, le=7, description="Day the weekly run happens: 1=Mon..7=Sun")
    retention: int = Field(
        ..., ge=1, le=RETENTION_MAX, description="How many weekly restore points to keep"
    )
    backupType: BackupType = Field("FULL", description="FULL or INCREMENTAL")
    incrementalQuantity: int = Field(
        0, ge=0, description="Incremental runs between two FULL runs; 0 for FULL"
    )


class MonthlyConfigDto(BaseModel):
    """Monthly cadence of a backup policy."""

    model_config = ConfigDict(extra="forbid")

    type: MONTHLY_BY = Field(
        "DAY_OF_MONTH",
        description=(
            "How the monthly run is placed: on a fixed day of the month, or on a "
            "weekday within a week of the month."
        ),
    )
    dayOfMonth: int = Field(
        1,
        ge=1,
        le=31,
        description=(
            "Day of month, used when type is DAY_OF_MONTH. Days 29-31 do not exist "
            "in every month — prefer 1-28 unless the user insists."
        ),
    )
    weekOfMonth: int = Field(
        1, ge=1, le=5, description="Week ordinal, used when type is DAY_OF_WEEK"
    )
    dayOfWeek: int = Field(
        1, ge=1, le=7, description="Weekday 1=Mon..7=Sun, used when type is DAY_OF_WEEK"
    )
    retention: int = Field(
        ..., ge=1, le=RETENTION_MAX, description="How many monthly restore points to keep"
    )
    backupType: BackupType = Field("FULL", description="FULL or INCREMENTAL")
    incrementalQuantity: int = Field(
        0, ge=0, description="Incremental runs between two FULL runs; 0 for FULL"
    )


class BackupPolicyConfigDto(BaseModel):
    """The schedule of a backup policy.

    The cadences are four INDEPENDENT switches, not one frequency choice: a
    policy may run hourly and monthly and nothing else. Enabling none of them
    produces a policy that never runs, which the API accepts and no user wants
    — say so before creating one.
    """

    model_config = ConfigDict(extra="forbid")

    hour: int = Field(
        ...,
        ge=0,
        le=23,
        description=(
            "Clock hour the daily/weekly/monthly runs start. The platform disables "
            "some hours — check get_configuration.backup_policy_hours before "
            "choosing, and prefer an off-peak hour."
        ),
    )
    minute: int = Field(0, ge=0, le=59, description="Minute past the hour")
    timeZone: str = Field(
        "Asia/Ho_Chi_Minh",
        description="IANA time zone the clock time is read in, e.g. 'Asia/Ho_Chi_Minh'",
    )
    hourlyEnabled: bool = Field(False, description="Run on an hourly cadence")
    hourlyConfig: HourlyConfigDto | None = Field(
        None, description="Hourly settings; required when hourlyEnabled is true"
    )
    dailyEnabled: bool = Field(False, description="Run once a day at `hour`:`minute`")
    dailyConfig: DailyConfigDto | None = Field(
        None, description="Daily settings; required when dailyEnabled is true"
    )
    weeklyEnabled: bool = Field(False, description="Run once a week")
    weeklyConfig: WeeklyConfigDto | None = Field(
        None, description="Weekly settings; required when weeklyEnabled is true"
    )
    monthlyEnabled: bool = Field(False, description="Run once a month")
    monthlyConfig: MonthlyConfigDto | None = Field(
        None, description="Monthly settings; required when monthlyEnabled is true"
    )
    isProtectedServer: bool = Field(
        True,
        description=(
            "Mark servers using this policy as protected. Leave true unless the "
            "user asks otherwise."
        ),
    )
    statusSendEmail: list[Literal["ERROR", "SUCCESS"]] = Field(
        default_factory=list,
        description="Run outcomes that trigger an email notification, e.g. ['ERROR']",
    )


class CreateBackupPolicyDto(BaseModel):
    """Body of create_backup_policy."""

    model_config = ConfigDict(extra="forbid")

    backendId: str = Field(..., description="Backend ID from list_backends")
    projectId: str = Field(
        ..., description="Project ID; read it off any existing resource in this region"
    )
    name: str = Field(..., min_length=1, description="Policy name, unique within the project")
    config: BackupPolicyConfigDto = Field(..., description="The schedule")


class UpdateBackupPolicyDto(BaseModel):
    """Body of update_backup_policy.

    The update REPLACES the schedule: every cadence the caller omits comes back
    disabled. Read the current policy with get_backup_policy and send the full
    set, not just the part being changed.
    """

    model_config = ConfigDict(extra="forbid")

    backendId: str = Field(..., description="Backend ID from list_backends")
    projectId: str = Field(..., description="Project ID the policy belongs to")
    name: str = Field(..., min_length=1, description="Policy name; required on every update")
    config: BackupPolicyConfigDto = Field(..., description="The complete replacement schedule")


class VolumeSelectionDto(BaseModel):
    """One volume of a server, and whether backups include it."""

    model_config = ConfigDict(extra="forbid")

    volumeId: str = Field(..., description="Volume ID from vServer (`vol-...`)")
    backupEnabled: bool = Field(
        True,
        description=(
            "False excludes this disk from every run. An excluded disk cannot be "
            "restored later — confirm with the user before turning one off."
        ),
    )


class ServerSelectionDto(BaseModel):
    """One server to protect, with its per-volume selection."""

    model_config = ConfigDict(extra="forbid")

    serverId: str = Field(..., description="vServer instance ID (`ins-...`)")
    volumes: list[VolumeSelectionDto] = Field(
        default_factory=list,
        description=(
            "Which of the instance's disks to back up. An empty list means the "
            "platform decides — pass the disks explicitly so the user knows what "
            "is covered."
        ),
    )


class CreateBackupServerDto(BaseModel):
    """Body of create_backup_server."""

    model_config = ConfigDict(extra="forbid")

    backendId: str = Field(..., description="Backend ID from list_backends")
    projectId: str = Field(..., description="Project ID the instances belong to")
    serverConfig: list[ServerSelectionDto] = Field(
        ..., min_length=1, description="The instances to protect and their disk selection"
    )
    backupPolicyId: str = Field(..., description="Policy ID from list_backup_policies")
    backupDestinationId: str = Field(
        ..., description="Destination ID from list_backup_destinations"
    )
    description: str = Field("", description="Free-text description")
    backupEnabled: bool = Field(
        True, description="Start the schedule immediately; false creates it paused"
    )


class UpdateBackupServerVolumesDto(BaseModel):
    """Body of update_backup_server_volumes — one volume's inclusion flag."""

    model_config = ConfigDict(extra="forbid")

    volumeId: str = Field(..., description="Volume ID from list_backup_server_volumes")
    backupEnabled: bool = Field(
        ..., description="True includes the disk in future runs, false excludes it"
    )


class UpdateBackupServerPolicyDto(BaseModel):
    """Body of update_backup_server_policy — the policy to attach."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Policy ID from list_backup_policies")


class CreateBackupDatabaseDto(BaseModel):
    """Body of create_backup_database.

    Deliberately flatter than ``CreateBackupServerDto``: a server create nests
    its instances so each one can carry a disk selection, but a database is
    captured whole, so the API takes a single ``databaseId`` at the top level.
    Sending ``databaseIds`` or a nested ``databaseConfig`` is rejected.

    Neither ``backendId`` nor ``projectId`` appears here — the gateway resolves
    both from the token for this route.
    """

    model_config = ConfigDict(extra="forbid")

    databaseId: str = Field(
        ...,
        description=(
            "The vDB instance to protect (`pg-...` or `rd-...`) from list_databases. "
            "One per create — this is not a list."
        ),
    )
    databaseType: DatabaseType = Field(
        ...,
        description=(
            "Engine family of the instance, spelled exactly 'PostgresCluster' or "
            "'RedisCluster'. It must match the instance behind `databaseId`."
        ),
    )
    backupPolicyId: str = Field(..., description="Policy ID from list_backup_policies")
    backupDestinationId: str = Field(
        ...,
        description=(
            "Destination ID from list_backup_destinations. It must be a destination "
            "whose `product` is vDB — a vServer destination cannot store a database."
        ),
    )
    description: str = Field("", description="Free-text note shown against the backup database")
    backupEnabled: bool = Field(
        True, description="Start the schedule immediately; false creates it paused"
    )


class UpdateBackupDatabasePolicyDto(BaseModel):
    """Body of update_backup_database_policy — the policy to attach."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Policy ID from list_backup_policies")


class CreateVserverBackupServersDto(BaseModel):
    """Body of create_vserver_backup_servers.

    The vServer projection of a create: it takes instance ids only and lets the
    platform pick the default policy and destination. Use create_backup_server
    when the user cares which policy or destination is used.
    """

    model_config = ConfigDict(extra="forbid")

    projectId: str = Field(..., description="Project ID the instances belong to")
    serverIds: list[str] = Field(
        ..., min_length=1, description="vServer instance IDs to protect (`ins-...`)"
    )


class VolumeUsageQueryDto(BaseModel):
    """Body of list_volume_usage — a read expressed as a POST."""

    model_config = ConfigDict(extra="forbid")

    backendId: str = Field(
        ..., description="Backend ID from list_backends; the API rejects the call without it"
    )
    projectId: str = Field(..., description="Project ID; the API rejects the call without it")
    volumeIds: list[str] = Field(
        default_factory=list, description="Volumes to measure; an empty list returns nothing"
    )


class MaxQuotaDto(BaseModel):
    """Capacity ceiling of a backup destination.

    The API nests this one level (``{"maxQuota": {"unlimited": ..., "maxQuota":
    ...}}``) on both the create and the update, so the outer and inner names
    are the same word at two different depths.
    """

    model_config = ConfigDict(extra="forbid")

    unlimited: bool = Field(
        True,
        description=(
            "True lets the destination grow without a ceiling and makes maxQuota "
            "meaningless. False enforces maxQuota, and runs FAIL once it is reached "
            "rather than being throttled."
        ),
    )
    maxQuota: int = Field(
        0,
        ge=0,
        description=(
            "Ceiling in GB, used only when unlimited is false. Set it above the "
            "destination's current `vault.used_gb`, or every subsequent run fails."
        ),
    )


class SoftDeleteDto(BaseModel):
    """Recycle-bin configuration of a backup destination.

    Used both at create time and by update_backup_destination_soft_delete.
    """

    model_config = ConfigDict(extra="forbid")

    enable: bool = Field(..., description="Whether a deleted backup goes to the recycle bin")
    retainDays: int = Field(
        0,
        ge=0,
        description=(
            "Days a deleted backup stays recoverable. It is STILL BILLED for that "
            "whole window, so a long retention makes 'delete to save money' do "
            "nothing for that many days. Ignored when enable is false."
        ),
    )


class VaultLockDto(BaseModel):
    """Retention lock of a backup destination — the console's Location Lock.

    Used both at create time and by update_backup_destination_vault_lock. The
    bounds are enforced here rather than left to the API, which reports any
    violation as an opaque ``vault_locked_invalid`` naming no field.
    """

    model_config = ConfigDict(extra="forbid")

    enable: bool = Field(..., description="Whether to enforce the retention window")
    changeDuration: int = Field(
        ...,
        ge=0,
        le=VAULT_LOCK_MAX_CHANGE_DURATION,
        description=(
            "Days from enabling during which the lock can still be edited or turned "
            "off, 0-7. **0 makes the lock permanent immediately** — every later edit, "
            "including disabling it, is refused with 'Cannot edit vault lock', by this "
            "server and by the console alike. Never send 0 unless the user has asked "
            "for exactly that."
        ),
    )
    minRetention: int = Field(
        0,
        ge=0,
        description=(
            "Days a backup must be kept; deleting one sooner is refused. Must not "
            "exceed maxRetention."
        ),
    )
    maxRetention: int = Field(
        0,
        ge=0,
        description="Days after which a backup is deleted automatically",
    )

    @model_validator(mode="after")
    def _retention_window_is_ordered(self) -> VaultLockDto:
        """Reject a window whose minimum exceeds its maximum.

        The API answers that case with `vault_locked_invalid`, which names
        neither field; catching it here says which number to change.
        """
        if self.enable and self.minRetention > self.maxRetention:
            raise ValueError(
                "minRetention must not exceed maxRetention "
                f"(got minRetention={self.minRetention}, maxRetention={self.maxRetention})"
            )
        return self


class CreateBackupDestinationDto(BaseModel):
    """Body of create_backup_destination."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Destination name, unique within the account")
    regionId: str = Field(
        ...,
        description=(
            "Backup region to store in — the `region_id` field of a "
            "list_backup_regions entry, NOT its `id`."
        ),
    )
    product: Literal["vServer", "vDB"] = Field(
        ...,
        description=(
            "Product the destination stores backups for. It cannot be changed later, "
            "and a vServer backup cannot be written to a vDB destination."
        ),
    )
    maxQuota: MaxQuotaDto = Field(
        default_factory=MaxQuotaDto,
        description=(
            "Capacity ceiling, unlimited by default. REQUIRED by the API even when "
            "unlimited — omitting it is a 400 `missing_required_field`."
        ),
    )
    softDeleteConfig: SoftDeleteDto = Field(
        default_factory=lambda: SoftDeleteDto(enable=False),
        description=(
            "Recycle-bin configuration. REQUIRED by the API: send it with "
            "`enable: false` to leave soft delete off, never omit it."
        ),
    )
    vaultLock: VaultLockDto = Field(
        default_factory=lambda: VaultLockDto(
            enable=False, changeDuration=VAULT_LOCK_MAX_CHANGE_DURATION
        ),
        description=(
            "Retention lock. REQUIRED by the API: send it with `enable: false` to "
            "leave the destination unlocked. Enabling it here starts the "
            "change-duration clock immediately."
        ),
    )
    isDefault: bool = Field(
        False,
        description=(
            "Make this the destination a create uses when none is named. Setting it "
            "REMOVES default from whichever destination of the same product holds it. "
            "Always send it: the API treats an ABSENT isDefault as true, and then "
            "refuses the create because the product already has a default."
        ),
    )


class UpdateBackupDestinationNameDto(BaseModel):
    """Body of update_backup_destination_name."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="The new destination name")


class UpdateMaxQuotaDto(BaseModel):
    """Body of update_backup_destination_max_quota."""

    model_config = ConfigDict(extra="forbid")

    maxQuota: MaxQuotaDto = Field(..., description="The replacement capacity ceiling")


class BackupNowDto(BaseModel):
    """Body of start_backup — the console's "Back now"."""

    model_config = ConfigDict(extra="forbid")

    backendId: str = Field(
        ...,
        description=(
            "Backend the server's backup lives in. Read it off the backup server "
            "(list_backup_servers), not from list_backends — a wrong backend is "
            "rejected rather than ignored."
        ),
    )
    projectId: str = Field(..., description="Project the instance belongs to")


class UpdateBackupServerDestinationDto(BaseModel):
    """Body of update_backup_server_destination — the destination to write to next."""

    model_config = ConfigDict(extra="forbid")

    backupDestinationId: str = Field(
        ...,
        description=(
            "Destination ID from list_backup_destinations. It must serve the same "
            "product (vServer) as the backup server."
        ),
    )
