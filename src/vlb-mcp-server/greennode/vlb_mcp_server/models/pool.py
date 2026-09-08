"""Pool, member and health monitor response models.

Three name drifts live here and nowhere else:

===============  =====================  ===================
Concept          Request field          Response field
===============  =====================  ===================
pool name        ``poolName``           ``name``
pool protocol    ``poolProtocol``       ``protocol``
pool algorithm   ``algorithm``          ``loadBalanceMethod``
member IP        ``ipAddress``          ``address``
member port      ``port``               ``protocolPort``
===============  =====================  ===================

Model fields always take the **request** name, so a caller reads back the same
word it must write. :meth:`PoolMemberItem.to_member_dto` is the single place
the member direction is reversed — the read-modify-write member tools depend on
it, and ``MemberDto`` forbidding extras means any drift fails in tests rather
than as a 400 in production.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.models._common import _resource_id
from greennode.vlb_mcp_server.models.requests import MemberDto
from pydantic import BaseModel, Field


class HealthMonitorItem(BaseModel):
    """The health check a pool applies to its members."""

    id: str = Field("", description="Health monitor ID")
    health_check_protocol: str = Field(
        "", description="Probe protocol — immutable once the pool exists"
    )
    health_check_path: str = Field("", description="Path probed (HTTP/HTTPS)")
    health_check_method: str = Field("", description="HTTP method used to probe")
    healthy_threshold: int | None = Field(None, description="Successes before healthy")
    unhealthy_threshold: int | None = Field(None, description="Failures before unhealthy")
    timeout: int | None = Field(None, description="Probe timeout in seconds")
    interval: int | None = Field(None, description="Probe interval in seconds")
    success_code: str = Field("", description="Accepted HTTP status codes")
    domain_name: str = Field("", description="Host header used when probing")
    http_version: str = Field("", description="HTTP version used when probing")
    display_status: str = Field("", description="Lifecycle status")
    progress_status: str = Field("", description="In-flight operation, if any")

    @classmethod
    def from_api(cls, data: dict) -> "HealthMonitorItem":
        """Build a HealthMonitorItem from a raw HMonitorDto."""
        return cls(
            id=_resource_id(data),
            health_check_protocol=data.get("healthCheckProtocol") or "",
            health_check_path=data.get("healthCheckPath") or "",
            health_check_method=data.get("healthCheckMethod") or "",
            healthy_threshold=data.get("healthyThreshold"),
            unhealthy_threshold=data.get("unhealthyThreshold"),
            timeout=data.get("timeout"),
            interval=data.get("interval"),
            success_code=data.get("successCode") or "",
            domain_name=data.get("domainName") or "",
            http_version=data.get("httpVersion") or "",
            display_status=data.get("displayStatus") or "",
            progress_status=data.get("progressStatus") or "",
        )


class PoolMemberItem(BaseModel):
    """One backend member of a pool."""

    id: str = Field("", description="Member ID")
    ip_address: str = Field(
        "", description="Backend private IP (the API returns this as `address`)"
    )
    port: int | None = Field(
        None, description="Backend port (the API returns this as `protocolPort`)"
    )
    weight: int | None = Field(None, description="Relative share of requests")
    name: str = Field("", description="Friendly name")
    monitor_port: int | None = Field(None, description="Port the health monitor probes")
    backup: bool = Field(False, description="Only receives traffic when others are down")
    subnet_id: str = Field("", description="Subnet the backend sits on")
    display_status: str = Field("", description="Lifecycle status")
    progress_status: str = Field("", description="In-flight operation, if any")

    @classmethod
    def from_api(cls, data: dict) -> "PoolMemberItem":
        """Build a PoolMemberItem from a raw PoolMemberDto."""
        return cls(
            id=_resource_id(data),
            ip_address=data.get("address") or "",
            port=data.get("protocolPort"),
            weight=data.get("weight"),
            name=data.get("name") or "",
            monitor_port=data.get("monitorPort"),
            backup=bool(data.get("backup")),
            subnet_id=data.get("subnetId") or "",
            display_status=data.get("displayStatus") or "",
            progress_status=data.get("progressStatus") or "",
        )

    def to_member_dto(self) -> MemberDto:
        """Convert back to the write shape, for a read-modify-write member change.

        Only the fields the API accepts survive: ``uuid``, ``subnetId``,
        ``poolId``, ``typeCreate``, ``createdBy`` and the status pair are
        read-only and would be rejected. ``weight`` and ``monitorPort`` are
        carried through rather than defaulted, so re-sending an unchanged member
        does not quietly reset its tuning.
        """
        return MemberDto(
            ipAddress=self.ip_address,
            port=self.port or 0,
            backup=self.backup,
            weight=self.weight,
            name=self.name or None,
            monitorPort=self.monitor_port,
        )


class PoolItem(BaseModel):
    """One pool."""

    id: str = Field(..., description="Pool ID — pass this as pool_id")
    name: str = Field("", description="Pool name (the API returns this as `name`)")
    protocol: str = Field("", description="Backend protocol (request spells it `poolProtocol`)")
    algorithm: str = Field(
        "",
        description="Load balancing algorithm (the API returns this as `loadBalanceMethod`)",
    )
    description: str = Field("", description="Description")
    stickiness: bool = Field(False, description="Whether clients are pinned to one backend")
    tls_encryption: bool = Field(False, description="Whether backend traffic is encrypted")
    display_status: str = Field("", description="Lifecycle status")
    progress_status: str = Field("", description="In-flight operation, if any")
    health_monitor: HealthMonitorItem | None = Field(
        None, description="Health check applied to the members"
    )
    members: list[PoolMemberItem] = Field(
        default_factory=list, description="Backend members, when the endpoint includes them"
    )
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "PoolItem":
        """Build a PoolItem from a raw PoolDto."""
        monitor = data.get("healthMonitor")
        members = data.get("members")
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            protocol=data.get("protocol") or "",
            algorithm=data.get("loadBalanceMethod") or "",
            description=data.get("description") or "",
            stickiness=bool(data.get("stickiness")),
            tls_encryption=bool(data.get("tlsEncryption")),
            display_status=data.get("displayStatus") or "",
            progress_status=data.get("progressStatus") or "",
            health_monitor=(
                HealthMonitorItem.from_api(monitor) if isinstance(monitor, dict) else None
            ),
            members=[
                PoolMemberItem.from_api(m)
                for m in (members if isinstance(members, list) else [])
                if isinstance(m, dict)
            ],
            created_at=str(data.get("createdAt") or ""),
            updated_at=str(data.get("updatedAt") or ""),
        )


class PoolListData(BaseModel):
    """Structured response for list_pools."""

    load_balancer_id: str = Field(..., description="Load balancer the pools belong to")
    pools: list[PoolItem] = Field(default_factory=list, description="Pools of the load balancer")


class PoolMemberListData(BaseModel):
    """Structured response for list_pool_members and the member write tools.

    ``confirmed`` separates the two: a read reports live state, while the value
    returned by a member write is the list that was **requested**. Member writes
    are asynchronous, so reading back immediately would report the old set —
    echoing the request is useful, but only if it is not mistaken for a read.
    """

    load_balancer_id: str = Field(..., description="Load balancer the pool belongs to")
    pool_id: str = Field(..., description="Pool the members belong to")
    members: list[PoolMemberItem] = Field(
        default_factory=list, description="Backend members of the pool"
    )
    member_count: int = Field(0, description="Number of members in `members`")
    confirmed: bool = Field(
        True,
        description=(
            "True when this is a live read. False when it echoes what a write "
            "requested: the change is still being applied, so confirm with "
            "list_pool_members once get_load_balancer_status reports settled."
        ),
    )


class HealthMonitorUpdateData(BaseModel):
    """What update_pool_health_monitor asked the API to apply.

    Deliberately **not** a :class:`HealthMonitorItem`. The underlying
    ``PUT .../pools/{poolId}`` is asynchronous, so reading the monitor back
    immediately reports the pre-update values; and returning a read-shaped
    object holding some old fields and some requested ones would be worse than
    either. This type carries only the configuration that was sent, plus the
    immutable probe protocol for context, and says how to confirm.
    """

    pool_id: str = Field(..., description="Pool whose health monitor was updated")
    health_check_protocol: str = Field(
        "",
        description=(
            "The pool's probe protocol. Immutable — reported so the caller knows which "
            "probe these settings apply to, not because it was changed."
        ),
    )
    health_check_path: str = Field("", description="Path that will be probed")
    health_check_method: str = Field("", description="HTTP method that will be used")
    healthy_threshold: int = Field(..., description="Successes before a member is healthy")
    unhealthy_threshold: int = Field(..., description="Failures before a member is unhealthy")
    timeout: int = Field(..., description="Probe timeout in seconds")
    interval: int = Field(..., description="Probe interval in seconds")
    success_code: str = Field("", description="Accepted HTTP status codes")
    domain_name: str = Field("", description="Host header used when probing")
    http_version: str = Field("", description="HTTP version used when probing")
    hint: str = Field(
        "",
        description="How to confirm the change actually landed",
    )
