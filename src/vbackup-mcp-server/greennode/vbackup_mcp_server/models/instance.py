"""The vServer instance behind a backup server.

This is the only model in the package built from the **vServer** gateway rather
than vBackup, and it is deliberately a narrow view of a very wide payload: the
raw instance object carries flavour zone lists, network interface internals and
image metadata that say nothing about backups and would swamp an agent's
context. Only what helps explain or decide a backup is kept.

Do not confuse it with ``models/vserver.py``, which holds vBackup's own
``/v1/vserver/**`` projection — a different API on a different gateway.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import as_dict, as_int, as_text
from pydantic import BaseModel, Field, computed_field
from typing import Any


class InstanceFlavor(BaseModel):
    """The size of the instance."""

    name: str = Field("", description="Flavour name, e.g. a general-purpose 1x2")
    cpu: int = Field(0, description="vCPU count")
    memory_gb: int = Field(0, description="RAM in GB")
    gpu: int = Field(0, description="GPU count, 0 on a normal instance")

    @classmethod
    def from_api(cls, value: Any) -> InstanceFlavor:
        """Build an InstanceFlavor from the instance's `flavor` object."""
        data = as_dict(value)
        return cls(
            name=as_text(data.get("name")),
            cpu=as_int(data.get("cpu")),
            memory_gb=as_int(data.get("memory")),
            gpu=as_int(data.get("gpu")),
        )


class InstanceImage(BaseModel):
    """The image the instance was built from.

    This is what decides whether an old restore point is still usable: a point
    captured under a different OS version restores to a machine the user may
    not expect.
    """

    id: str = Field("", description="Image ID (`img-...`)")
    type: str = Field("", description="Image family, e.g. Ubuntu or Windows")
    version: str = Field("", description="Image version string")

    @classmethod
    def from_api(cls, value: Any) -> InstanceImage:
        """Build an InstanceImage from the instance's `image` object."""
        data = as_dict(value)
        return cls(
            id=as_text(data.get("id")),
            type=as_text(data.get("imageType")),
            version=as_text(data.get("imageVersion")),
        )


class InstanceAddress(BaseModel):
    """One network address of the instance."""

    fixed_ip: str = Field("", description="Private address on the subnet")
    floating_ip: str = Field("", description="Public address, empty when there is none")
    interface_type: str = Field("", description="Interface role as vServer names it")

    @classmethod
    def from_api(cls, data: dict) -> InstanceAddress:
        """Build an InstanceAddress from one interface entry."""
        return cls(
            fixed_ip=as_text(data.get("fixedIp")),
            floating_ip=as_text(data.get("floatingIp")),
            interface_type=as_text(data.get("interfaceType")),
        )


class VserverInstanceDetail(BaseModel):
    """A vServer instance, trimmed to what matters when talking about its backups."""

    id: str = Field(..., description="Instance ID (`ins-...`) — the `serverId` vBackup records")
    name: str = Field("", description="Instance name as shown in the console")
    status: str = Field(
        "",
        description=(
            "Instance state. get_configuration.allowed_backup_server_status lists the "
            "states that may be added as a backup server; anything else is rejected "
            "by create_backup_server."
        ),
    )
    zone: str = Field("", description="Availability zone the instance runs in")
    flavor: InstanceFlavor = Field(default_factory=InstanceFlavor, description="Size")
    image: InstanceImage = Field(default_factory=InstanceImage, description="Source image")
    boot_volume_id: str = Field(
        "",
        description=(
            "The boot disk. Excluding this volume from a backup leaves a set of "
            "restore points that cannot rebuild a bootable machine."
        ),
    )
    encryption_volume: bool = Field(False, description="Whether its volumes are encrypted")
    addresses: list[InstanceAddress] = Field(
        default_factory=list, description="Network addresses, useful for identifying the machine"
    )
    created_at: str = Field("", description="When the instance was created")

    @classmethod
    def from_api(cls, data: dict) -> VserverInstanceDetail:
        """Build a VserverInstanceDetail from a raw vServer instance dict."""
        interfaces = data.get("internalInterfaces")
        external = data.get("externalInterfaces")
        entries = [
            i
            for group in (interfaces, external)
            if isinstance(group, list)
            for i in group
            if isinstance(i, dict)
        ]
        return cls(
            id=as_text(data.get("uuid")),
            name=as_text(data.get("name")),
            status=as_text(data.get("status")),
            zone=as_text(data.get("zoneId")) or as_text(data.get("zone")),
            flavor=InstanceFlavor.from_api(data.get("flavor")),
            image=InstanceImage.from_api(data.get("image")),
            boot_volume_id=as_text(data.get("bootVolumeId")),
            encryption_volume=bool(data.get("encryptionVolume")),
            addresses=[InstanceAddress.from_api(i) for i in entries],
            created_at=as_text(data.get("createdAt")),
        )


class BackupStatisticData(BaseModel):
    """Account-level backup coverage and outcome counters."""

    region: str = Field(..., description="Region the gateway was called in")
    project_id: str = Field("", description="Project the counters were scoped to")
    total_servers: int = Field(
        0,
        description=(
            "vServer instances in the project. **0 when project_id was not supplied** "
            "— the API cannot count servers without it, and the coverage ratio is then "
            "meaningless."
        ),
    )
    total_protected_servers: int = Field(
        0, description="Instances that currently have a backup server protecting them"
    )
    total_backup_servers: int = Field(
        0,
        description=(
            "Backup servers that exist. This is normally HIGHER than "
            "total_protected_servers, because a backup server whose source instance "
            "was deleted still exists and is still billed."
        ),
    )
    total_backup_completed: int = Field(0, description="Backup runs that succeeded")
    total_backup_failed: int = Field(0, description="Backup runs that failed")
    total_restore_completed: int = Field(0, description="Restores that succeeded")
    total_restore_failed: int = Field(0, description="Restores that failed")

    @computed_field(
        description=(
            "Instances with no backup at all. Meaningless without project_id, "
            "because total_servers is then 0 and this reads as full coverage."
        )
    )
    @property
    def unprotected_servers(self) -> int:
        """Instances with no backup at all; negative counts are clamped to 0."""
        return max(self.total_servers - self.total_protected_servers, 0)

    @computed_field(
        description=(
            "Backup servers whose source instance is gone — still holding restore "
            "points and still billed. The fastest pure-waste number to act on."
        )
    )
    @property
    def orphaned_backup_servers(self) -> int:
        """Backup servers with no live instance behind them — pure cost.

        A plain ``@property`` is NOT serialised by Pydantic, so this and
        ``unprotected_servers`` were absent from every MCP response while the
        docstrings told agents to read them. ``computed_field`` puts them in the
        schema and the payload; do not demote either back to a bare property.
        """
        return max(self.total_backup_servers - self.total_protected_servers, 0)

    @classmethod
    def from_api(cls, region: str, project_id: str, data: Any) -> BackupStatisticData:
        """Build a BackupStatisticData from the raw counter object."""
        raw = as_dict(data)
        return cls(
            region=region,
            project_id=project_id,
            total_servers=as_int(raw.get("totalServers")),
            total_protected_servers=as_int(raw.get("totalProtectedServers")),
            total_backup_servers=as_int(raw.get("totalBackupServers")),
            total_backup_completed=as_int(raw.get("totalBackupCompleted")),
            total_backup_failed=as_int(raw.get("totalBackupFailed")),
            total_restore_completed=as_int(raw.get("totalRestoreCompleted")),
            total_restore_failed=as_int(raw.get("totalRestoreFailed")),
        )
