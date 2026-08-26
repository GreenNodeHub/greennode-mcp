"""Backup-policy models — the schedule that decides when a backup server runs."""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import as_dict, as_int, as_text
from pydantic import BaseModel, Field
from typing import Any


CADENCES = ("hourly", "daily", "weekly", "monthly")

_WEEKDAYS = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


class CadenceConfig(BaseModel):
    """One cadence of a policy — hourly, daily, weekly or monthly."""

    enabled: bool = Field(False, description="Whether this cadence runs at all")
    retention: int = Field(0, description="How many restore points of this cadence are kept")
    backup_type: str = Field("", description="FULL or INCREMENTAL")
    incremental_quantity: int = Field(
        0, description="Incremental runs taken between two FULL runs; 0 for a FULL cadence"
    )
    interval_hours: int = Field(0, description="Hours between runs; hourly cadence only")
    day_of_week: int = Field(0, description="1=Mon..7=Sun; weekly and some monthly policies")
    day_of_month: int = Field(0, description="Day of month; monthly cadence only")
    week_of_month: int = Field(0, description="Week ordinal; monthly cadence by weekday")

    @classmethod
    def from_api(cls, config: dict, cadence: str) -> CadenceConfig:
        """Build one cadence from the policy config.

        A DISABLED cadence carries an EMPTY config object rather than a null,
        so the enable flag is the only reliable signal — reading the nested
        object alone reports a disabled cadence as configured.
        """
        cfg = as_dict(config.get(f"{cadence}Config"))
        return cls(
            enabled=bool(config.get(f"{cadence}Enabled")),
            retention=as_int(cfg.get("retention")),
            backup_type=as_text(cfg.get("backupType")),
            incremental_quantity=as_int(cfg.get("incrementalQuantity")),
            interval_hours=as_int(cfg.get("interval")),
            day_of_week=as_int(cfg.get("dayOfWeek")),
            day_of_month=as_int(cfg.get("dayOfMonth")),
            week_of_month=as_int(cfg.get("weekOfMonth")),
        )


class BackupPolicySchedule(BaseModel):
    """The full schedule of a backup policy, flattened into one readable object."""

    run_at: str = Field("", description="Clock time the daily/weekly/monthly runs start, HH:MM")
    time_zone: str = Field("", description="Time zone the clock time is expressed in")
    summary: str = Field(
        "",
        description=(
            "One-line summary of the ENABLED cadences, e.g. 'hourly every 4h keep 1 "
            "(INCREMENTAL); daily at 12:00 keep 7 (FULL)'. Empty means no cadence is "
            "enabled and the policy never runs."
        ),
    )
    hourly: CadenceConfig = Field(default_factory=CadenceConfig, description="Hourly cadence")
    daily: CadenceConfig = Field(default_factory=CadenceConfig, description="Daily cadence")
    weekly: CadenceConfig = Field(default_factory=CadenceConfig, description="Weekly cadence")
    monthly: CadenceConfig = Field(default_factory=CadenceConfig, description="Monthly cadence")
    protects_server: bool = Field(
        False, description="Whether the policy marks its servers as protected"
    )
    email_on: list[str] = Field(
        default_factory=list, description="Run outcomes that trigger an email, e.g. ['ERROR']"
    )

    @classmethod
    def from_api(cls, config: Any) -> BackupPolicySchedule:
        """Build a BackupPolicySchedule from a policy's `config` object."""
        cfg = as_dict(config)
        cadences = {name: CadenceConfig.from_api(cfg, name) for name in CADENCES}
        run_at = f"{as_int(cfg.get('hour')):02d}:{as_int(cfg.get('minute')):02d}"
        emails = cfg.get("statusSendEmail")
        return cls(
            run_at=run_at,
            time_zone=as_text(cfg.get("timeZone")),
            summary=_summarize(cadences, run_at),
            protects_server=bool(cfg.get("isProtectedServer")),
            email_on=[as_text(e) for e in emails] if isinstance(emails, list) else [],
            **cadences,
        )


def _summarize(cadences: dict[str, CadenceConfig], run_at: str) -> str:
    """Render the enabled cadences as one line an agent can read aloud."""
    parts: list[str] = []
    for name in CADENCES:
        cadence = cadences[name]
        if not cadence.enabled:
            continue
        bits = [name]
        if name == "hourly" and cadence.interval_hours:
            bits.append(f"every {cadence.interval_hours}h")
        else:
            bits.append(f"at {run_at}")
        if name == "weekly" and cadence.day_of_week:
            bits.append(f"on {_WEEKDAYS.get(cadence.day_of_week, cadence.day_of_week)}")
        if name == "monthly" and cadence.day_of_month:
            bits.append(f"on day {cadence.day_of_month}")
        if cadence.retention:
            bits.append(f"keep {cadence.retention}")
        if cadence.backup_type:
            bits.append(f"({cadence.backup_type})")
        parts.append(" ".join(bits))
    return "; ".join(parts)


class BackupPolicyItem(BaseModel):
    """One backup policy."""

    id: str = Field(..., description="Policy ID (`bk-pol-...`)")
    name: str = Field("", description="Policy name")
    is_default: bool = Field(
        False,
        description=(
            "True for a platform-owned default policy. Those are shared across the "
            "account — editing one changes every backup server using it."
        ),
    )
    product: str = Field("", description="Product the policy applies to, e.g. vServer")
    backup_server_count: int = Field(
        0,
        description=(
            "How many backup servers use this policy. Non-zero means a delete will "
            "be refused or will orphan those servers — check before offering one."
        ),
    )
    schedule: BackupPolicySchedule = Field(
        default_factory=BackupPolicySchedule, description="When the policy runs and what it keeps"
    )
    backend_id: str = Field("", description="Backend the policy lives in")
    project_id: str = Field("", description="Project the policy belongs to")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last-update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> BackupPolicyItem:
        """Build a BackupPolicyItem from a raw API dict."""
        return cls(
            id=as_text(data.get("id")),
            name=as_text(data.get("name")),
            is_default=bool(data.get("isDefault")),
            product=as_text(data.get("product")),
            backup_server_count=as_int(data.get("backupInstanceCount")),
            schedule=BackupPolicySchedule.from_api(data.get("config")),
            backend_id=as_text(data.get("backendId")),
            project_id=as_text(data.get("projectId")),
            created_at=as_text(data.get("createdAt")),
            updated_at=as_text(data.get("updatedAt")),
        )


class BackupPolicyListData(BaseModel):
    """Structured output of list_backup_policies."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of policies returned")
    policies: list[BackupPolicyItem] = Field(
        default_factory=list, description="Policies matching the filters"
    )


class BackupPolicyRef(BaseModel):
    """A policy as embedded inside a backup server's payload."""

    id: str = Field("", description="Policy ID (`bk-pol-...`)")
    name: str = Field("", description="Policy name")
    is_default: bool = Field(False, description="True for a platform-owned default policy")
    schedule: str = Field(
        "",
        description=(
            "One-line summary of the enabled cadences. Empty means no cadence is "
            "enabled and this server is never backed up on a schedule."
        ),
    )

    @classmethod
    def from_api(cls, data: Any) -> BackupPolicyRef:
        """Build a BackupPolicyRef from an embedded `policy` object."""
        payload = as_dict(data)
        if not payload:
            return cls()
        return cls(
            id=as_text(payload.get("id")),
            name=as_text(payload.get("name")),
            is_default=bool(payload.get("isDefault")),
            schedule=BackupPolicySchedule.from_api(payload.get("config")).summary,
        )
