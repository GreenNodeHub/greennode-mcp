"""Pools, backend members and the health monitor.

Two API shapes make this the sharpest handler in the package, and both are
handled by read-modify-write composites rather than passed on to the agent:

- ``PUT .../members`` **replaces the whole member list**. A request to "add one
  backend" answered with a one-element list silently removes every other
  backend — an outage. ``attach_pool_members`` / ``detach_pool_members`` read
  the current list first; the raw ``update_pool_members`` is marked
  DESTRUCTIVE and says so in its first line.
- **There is no health-monitor write endpoint.** The only way to change a probe
  is ``PUT .../pools/{poolId}``, which requires ``algorithm`` and all four
  thresholds — so a naive partial update resets the load balancing method and
  the thresholds at once. ``update_pool_health_monitor`` therefore reads
  **both** ``GET /pools/{poolId}`` and ``GET /pools/{poolId}/healthMonitor``
  and carries every untouched value forward. Two reads, because the pool
  response declares a nested ``healthMonitor`` and never fills it in.
"""

from __future__ import annotations

import asyncio
from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import Region, VlbConfig
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.guards import require_write
from greennode.vlb_mcp_server.models import (
    ChangeMembersDto,
    CreatedResourceData,
    CreatePoolDto,
    HealthMonitorItem,
    HealthMonitorUpdateData,
    MemberDto,
    MemberTargetDto,
    PoolItem,
    PoolListData,
    PoolMemberItem,
    PoolMemberListData,
    UpdateHealthMonitorDto,
    UpdatePoolDto,
)
from greennode.vlb_mcp_server.paging import as_list, unwrap_one
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vlb_mcp_server.validators import validate_id, validate_lb_id
from pydantic import Field
from typing import Literal


def _member_key(member: MemberDto | MemberTargetDto) -> tuple[str, int]:
    """Identity of a member on the write side: there is no id, only (ip, port)."""
    return (member.ipAddress, member.port)


class PoolHandler:
    """Register and serve the pool, member and health monitor MCP tools."""

    def __init__(
        self,
        mcp,
        config: VlbConfig,
        client: VlbClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_pools", annotations=READ)(self.list_pools)
        self.mcp.tool(name="get_pool", annotations=READ)(self.get_pool)
        self.mcp.tool(name="list_pool_members", annotations=READ)(self.list_pool_members)
        self.mcp.tool(name="get_pool_health_monitor", annotations=READ)(
            self.get_pool_health_monitor
        )

        if self.allow_write:
            self.mcp.tool(name="create_pool", annotations=WRITE)(self.create_pool)
            self.mcp.tool(name="update_pool", annotations=WRITE)(self.update_pool)
            self.mcp.tool(name="delete_pool", annotations=DESTRUCTIVE)(self.delete_pool)
            self.mcp.tool(name="update_pool_members", annotations=DESTRUCTIVE)(
                self.update_pool_members
            )
            self.mcp.tool(name="attach_pool_members", annotations=WRITE)(self.attach_pool_members)
            self.mcp.tool(name="detach_pool_members", annotations=DESTRUCTIVE)(
                self.detach_pool_members
            )
            self.mcp.tool(name="update_pool_health_monitor", annotations=WRITE)(
                self.update_pool_health_monitor
            )

    # --- internals -------------------------------------------------------------

    async def _pools_path(self, load_balancer_id: str, region: str | None) -> str:
        """Validate the load balancer id and build the pools collection path."""
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        return f"/v2/{pid}/loadBalancers/{load_balancer_id}/pools"

    async def _read_health_monitor(
        self, path: str, pool_id: str, region: str | None
    ) -> UpdateHealthMonitorDto | None:
        """Read the pool's probe in write shape, for resending unchanged.

        ``PUT .../pools/{poolId}`` re-validates the probe whether or not the
        body carries it, so every pool update has to send the current one back.
        Returns ``None`` for a pool that genuinely has no monitor.
        """
        raw = await self.client.get(f"{path}/{pool_id}/healthMonitor", region=region)
        current = HealthMonitorItem.from_api(unwrap_one(raw))
        if not current.health_check_protocol:
            return None
        return UpdateHealthMonitorDto(
            healthyThreshold=current.healthy_threshold or 3,
            unhealthyThreshold=current.unhealthy_threshold or 3,
            timeout=current.timeout or 5,
            interval=current.interval or 30,
            healthCheckPath=current.health_check_path or None,
            healthCheckMethod=current.health_check_method or None,
            successCode=current.success_code or None,
            domainName=current.domain_name or None,
            httpVersion=current.http_version or None,
        )

    async def _read_members(self, path: str, pool_id: str, region: str | None) -> list[MemberDto]:
        """Read the pool's current members in write shape, for a read-modify-write."""
        raw = await self.client.get(f"{path}/{pool_id}/members", region=region)
        return [
            PoolMemberItem.from_api(m).to_member_dto() for m in as_list(raw) if isinstance(m, dict)
        ]

    @staticmethod
    def _member_list_data(
        members: list[MemberDto],
        pool_id: str,
        load_balancer_id: str,
        *,
        confirmed: bool,
    ) -> PoolMemberListData:
        """Project a desired member list into the read-shaped response model."""
        return PoolMemberListData(
            load_balancer_id=load_balancer_id,
            pool_id=pool_id,
            members=[
                PoolMemberItem(
                    ip_address=m.ipAddress,
                    port=m.port,
                    weight=m.weight,
                    name=m.name or "",
                    monitor_port=m.monitorPort,
                    backup=m.backup,
                )
                for m in members
            ],
            member_count=len(members),
            confirmed=confirmed,
        )

    async def _put_members(
        self,
        path: str,
        pool_id: str,
        members: list[MemberDto],
        load_balancer_id: str,
        region: str | None,
    ) -> PoolMemberListData:
        """Send the full desired member list and echo it back as unconfirmed.

        The write is asynchronous, so the returned list is what was requested,
        not a read — hence ``confirmed=False``.
        """
        payload = ChangeMembersDto(members=members).model_dump(exclude_none=True)
        await self.client.put(f"{path}/{pool_id}/members", region=region, json=payload)
        return self._member_list_data(members, pool_id, load_balancer_id, confirmed=False)

    # --- reads -------------------------------------------------------------------

    async def list_pools(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PoolListData:
        """List the pools of a load balancer.

        Returns {load_balancer_id, pools[{id, name, protocol, algorithm,
        display_status, ...}]}. Not cached: pools are read inside
        read-modify-write cycles and while polling a write, where a stale
        answer means writing back stale state.
        """
        path = await self._pools_path(load_balancer_id, region)
        raw = await self.client.get(path, region=region)
        return PoolListData(
            load_balancer_id=load_balancer_id,
            pools=[PoolItem.from_api(p) for p in as_list(raw) if isinstance(p, dict)],
        )

    async def get_pool(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PoolItem:
        """Get one pool's configuration.

        Returns {id, name, protocol, algorithm, stickiness, tls_encryption,
        health_monitor, members, display_status, progress_status}.

        `health_monitor` and `members` come back **empty**: the API declares
        them on the pool object but never populates them. Use
        `get_pool_health_monitor` and `list_pool_members` — they are separate
        endpoints and the only ones that answer.
        """
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")
        data = await self.client.get(f"{path}/{pool_id}", region=region)
        return PoolItem.from_api(unwrap_one(data))

    async def list_pool_members(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PoolMemberListData:
        """List the backend members of a pool.

        Returns {load_balancer_id, pool_id, members[{ip_address, port, weight,
        backup, display_status}], member_count}. The API spells these `address`
        and `protocolPort`; they are mapped to the names the write tools take.
        """
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")
        raw = await self.client.get(f"{path}/{pool_id}/members", region=region)
        members = [PoolMemberItem.from_api(m) for m in as_list(raw) if isinstance(m, dict)]
        return PoolMemberListData(
            load_balancer_id=load_balancer_id,
            pool_id=pool_id,
            members=members,
            member_count=len(members),
            confirmed=True,
        )

    async def get_pool_health_monitor(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> HealthMonitorItem:
        """Get the health monitor of a pool.

        Returns {health_check_protocol, health_check_path, healthy_threshold,
        unhealthy_threshold, timeout, interval, ...}.

        `health_check_protocol` is fixed at pool creation — changing it means
        deleting and recreating the pool. Everything else is editable through
        `update_pool_health_monitor`.
        """
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")
        data = await self.client.get(f"{path}/{pool_id}/healthMonitor", region=region)
        return HealthMonitorItem.from_api(unwrap_one(data))

    # --- writes -------------------------------------------------------------------

    async def create_pool(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        body: CreatePoolDto = Field(..., description="Pool definition."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CreatedResourceData:
        """Create a pool on a load balancer.

        ## Requirements
        - Requires `--allow-write`.
        - `healthMonitor` is mandatory — there is no endpoint to add one later.
        - `poolProtocol` and `poolName` are permanent; `update_pool` changes
          neither.
        - A 'Layer 4' load balancer cannot use an HTTP pool.
        - Member `ipAddress` values must be backend **private** IPs, from
          `list_servers` → `private_ip`.

        ## Workflow
        - Create the pool before the listener that references it as
          `defaultPoolId`.
        - Poll `get_load_balancer_status` until `settled` before the next write.
        - If the returned `id` is empty the create still SUCCEEDED — confirm
          with `list_pools` and never retry.
        """
        require_write(self.allow_write)
        path = await self._pools_path(load_balancer_id, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(path, region=region, json=payload)
        return CreatedResourceData.from_api(data, "list_pools")

    async def update_pool(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        algorithm: Literal["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"] = Field(
            ...,
            description=(
                "Load balancing algorithm. REQUIRED by the API even if unchanged — "
                "read the current value from get_pool and resend it."
            ),
        ),
        stickiness: bool | None = Field(
            None,
            description=(
                "Pin a client to one backend. HTTP pools only — leave unset on a "
                "TCP/UDP/PROXY pool, or the gateway rejects the call."
            ),
        ),
        tls_encryption: bool | None = Field(
            None,
            description=(
                "Encrypt traffic to the backends. HTTP pools only — leave unset on a "
                "TCP/UDP/PROXY pool, or the gateway rejects the call."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Update a pool's algorithm, stickiness and TLS setting.

        ## Requirements
        - Requires `--allow-write`.
        - `algorithm` is required by the API; passing a different value than the
          pool currently has silently changes how traffic is distributed. Read
          it from `get_pool` first.
        - The pool's health monitor is read and resent unchanged, so this
          cannot disturb the probe. To change the probe itself use
          `update_pool_health_monitor`.
        - Neither the pool name nor its protocol can be changed here; recreate
          the pool for that.

        ## Workflow
        - Poll `get_load_balancer_status` until `settled`.
        """
        require_write(self.allow_write)
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")

        # The monitor has to be resent even though nothing here changes it.
        # Omitting it does not "leave the probe alone": the gateway validates
        # the pool's existing protocol against the absent fields and rejects
        # the whole call with "If healthCheckProtocol field is HTTP, following
        # fields must be specified: …". A TCP probe happens to survive the
        # omission — which is exactly why this was easy to miss.
        monitor = await self._read_health_monitor(path, pool_id, region)
        payload = UpdatePoolDto(
            algorithm=algorithm,
            stickiness=stickiness,
            tlsEncryption=tls_encryption,
            healthMonitor=monitor,
        ).model_dump(exclude_none=True)
        await self.client.put(f"{path}/{pool_id}", region=region, json=payload)
        return f"Pool {pool_id} updated. Poll get_load_balancer_status until settled."

    async def delete_pool(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a pool and its members.

        ## Requirements
        - Requires `--allow-write`.
        - Irreversible, and it takes the members and the health monitor with it.
        - A listener using this pool as `defaultPoolId`, or an L7 policy
          redirecting to it, will stop routing. Check `list_listeners` and
          `list_l7_policies` first.

        ## Workflow
        - The API answers 202; poll `list_pools` to confirm it is gone.
        """
        require_write(self.allow_write)
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")
        await self.client.delete(f"{path}/{pool_id}", region=region)
        return (
            f"Pool {pool_id} deletion accepted. It is removed asynchronously — "
            "poll list_pools to confirm."
        )

    async def update_pool_members(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        members: list[MemberDto] = Field(
            ...,
            description=(
                "The COMPLETE desired member list. This is not a delta: any existing "
                "member missing from this list is REMOVED from the pool."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PoolMemberListData:
        """Replace a pool's ENTIRE member list. Every member you omit is REMOVED.

        ## Requirements
        - Requires `--allow-write`.
        - Use `attach_pool_members` or `detach_pool_members` instead unless you
          genuinely mean to overwrite the whole set — sending a partial list
          here takes the omitted backends out of service immediately.
        - If you do use this, read the current members with `list_pool_members`
          and include every one you intend to keep.
        - `ipAddress` must be a backend **private** IP (`list_servers` →
          `private_ip`).

        ## Workflow
        - Poll `get_load_balancer_status` until `settled`.
        """
        require_write(self.allow_write)
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")
        return await self._put_members(path, pool_id, members, load_balancer_id, region)

    async def attach_pool_members(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        members: list[MemberDto] = Field(
            ..., description="Members to ADD. Existing members are kept."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PoolMemberListData:
        """Add backend members to a pool, keeping the ones already there.

        ## Requirements
        - Requires `--allow-write`.
        - `ipAddress` must be a backend **private** IP (`list_servers` →
          `private_ip`); a public IP produces a pool that never turns healthy.
        - A member with the same (ipAddress, port) as an existing one REPLACES
          it — that is how you change a weight or the backup flag.

        ## Workflow
        - This reads the current members and writes the merged list back, so it
          is not atomic: do not run two member changes on the same pool at once.
        - Adding members that are all already present is a no-op and performs
          no write.
        - Poll `get_load_balancer_status` until `settled`.
        """
        require_write(self.allow_write)
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")

        existing = await self._read_members(path, pool_id, region)
        incoming = {_member_key(m): m for m in members}
        merged = [incoming.pop(_member_key(m), m) for m in existing]
        merged.extend(m for m in members if _member_key(m) in incoming)

        # The gateway refuses a member list identical to the stored one with
        # 400 "The members provided are identical to the existing members in
        # the pool". Re-adding a member that is already there, unchanged, is a
        # no-op — reporting it as an error would make an idempotent retry look
        # like a failure.
        if merged == existing:
            # Nothing was written, so this list IS the live state — the one
            # case where a member write can honestly report confirmed=True.
            return self._member_list_data(merged, pool_id, load_balancer_id, confirmed=True)
        return await self._put_members(path, pool_id, merged, load_balancer_id, region)

    async def detach_pool_members(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        targets: list[MemberTargetDto] = Field(
            ..., description="Members to remove, identified by {ipAddress, port}."
        ),
        allow_empty: bool = Field(
            False,
            description=(
                "Permit removing the last member. Refused by default: an empty pool "
                "serves no traffic."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PoolMemberListData:
        """Remove specific backend members from a pool, keeping the rest.

        ## Requirements
        - Requires `--allow-write`.
        - Every target must currently be in the pool; a target that matches
          nothing is an error rather than a silent no-op, so a typo cannot read
          as a successful removal.
        - Emptying the pool needs `allow_empty=true` — a pool with no members
          serves no traffic.
        - Removing a backend takes it out of service immediately. Confirm with
          the user.

        ## Workflow
        - This reads the current members and writes the remainder back, so it is
          not atomic: do not run two member changes on the same pool at once.
        - Poll `get_load_balancer_status` until `settled`.
        """
        require_write(self.allow_write)
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")

        existing = await self._read_members(path, pool_id, region)
        present = {_member_key(m) for m in existing}
        wanted = {_member_key(t) for t in targets}
        missing = sorted(f"{ip}:{port}" for ip, port in wanted - present)
        if missing:
            raise ValueError(
                f"These members are not in the pool, so nothing was changed: "
                f"{', '.join(missing)}. Read the current list with list_pool_members."
            )

        remaining = [m for m in existing if _member_key(m) not in wanted]
        if not remaining and not allow_empty:
            raise ValueError(
                "Removing these members would leave the pool empty and it would stop "
                "serving traffic. Pass allow_empty=true if that is intended."
            )
        return await self._put_members(path, pool_id, remaining, load_balancer_id, region)

    async def update_pool_health_monitor(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        pool_id: str = Field(..., description="Pool ID."),
        health_check_path: str | None = Field(
            None, description="Path to probe (HTTP/HTTPS), e.g. '/healthz'."
        ),
        health_check_method: Literal["GET", "POST", "PUT"] | None = Field(
            None, description="HTTP method for the probe."
        ),
        healthy_threshold: int | None = Field(
            None, ge=2, le=10, description="Successes before a member is healthy."
        ),
        unhealthy_threshold: int | None = Field(
            None, ge=2, le=10, description="Failures before a member is unhealthy."
        ),
        timeout: int | None = Field(None, ge=2, le=120, description="Probe timeout in seconds."),
        interval: int | None = Field(
            None, ge=5, le=3600, description="Probe interval in seconds."
        ),
        success_code: str | None = Field(
            None, description="Accepted HTTP status codes, e.g. '200' or '200-299'."
        ),
        domain_name: str | None = Field(None, description="Host header for the probe."),
        http_version: Literal["1.0", "1.1"] | None = Field(
            None, description="HTTP version to probe with."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> HealthMonitorUpdateData:
        """Change a pool's health check, leaving everything else as it is.

        ## Requirements
        - Requires `--allow-write`.
        - The probe **protocol cannot be changed** — there is no parameter for
          it. To move a pool from TCP to HTTP checks, delete and recreate the
          pool.
        - The pool must already have a health monitor; one cannot be added to a
          pool created without it.
        - Only the fields you pass change. This reads the pool first and carries
          its algorithm, the untouched probe values and — on an HTTP pool only —
          its stickiness and TLS setting forward, because the underlying
          `PUT .../pools/{poolId}` requires them and would otherwise reset them.

        ## Workflow
        - The result is the configuration that was SENT, not a fresh read: the
          update is asynchronous, so reading back immediately would report the
          old values. Poll `get_load_balancer_status` until `settled`, then
          confirm with `get_pool_health_monitor`.
        """
        require_write(self.allow_write)
        path = await self._pools_path(load_balancer_id, region)
        validate_id(pool_id, "pool_id")

        # Two reads, because the monitor is not where the spec implies it is.
        # `PoolDto` declares a nested `healthMonitor`, but the live
        # `GET /pools/{poolId}` never populates it — the monitor is only served
        # by `GET /pools/{poolId}/healthMonitor`. Reading the pool alone made
        # this tool raise "has no health monitor" for every pool that had one.
        pool_raw, monitor_raw = await asyncio.gather(
            self.client.get(f"{path}/{pool_id}", region=region),
            self.client.get(f"{path}/{pool_id}/healthMonitor", region=region),
        )
        pool = PoolItem.from_api(unwrap_one(pool_raw))
        current = pool.health_monitor or HealthMonitorItem.from_api(unwrap_one(monitor_raw))
        if not current.health_check_protocol:
            raise ValueError(
                f"Pool {pool_id} has no health monitor, and the API offers no way to add "
                "one to an existing pool. Recreate the pool with a healthMonitor instead."
            )

        def pick(new, old, fallback):
            """Prefer the caller's value, then the pool's, then the API minimum."""
            if new is not None:
                return new
            return old if old is not None else fallback

        monitor = UpdateHealthMonitorDto(
            healthyThreshold=pick(healthy_threshold, current.healthy_threshold, 3),
            unhealthyThreshold=pick(unhealthy_threshold, current.unhealthy_threshold, 3),
            timeout=pick(timeout, current.timeout, 5),
            interval=pick(interval, current.interval, 30),
            healthCheckPath=health_check_path or current.health_check_path or None,
            healthCheckMethod=health_check_method or current.health_check_method or None,
            successCode=success_code or current.success_code or None,
            domainName=domain_name or current.domain_name or None,
            httpVersion=http_version or current.http_version or None,
        )
        # The gateway requires `stickiness` and `tlsEncryption` on an HTTP pool
        # and rejects them on any other protocol — even as `false`, which is
        # what the pool read gives back for a TCP pool. Sending them
        # unconditionally broke this tool for every non-HTTP pool.
        is_http = pool.protocol.upper() == "HTTP"
        payload = UpdatePoolDto(
            algorithm=pool.algorithm,
            stickiness=pool.stickiness if is_http else None,
            tlsEncryption=pool.tls_encryption if is_http else None,
            healthMonitor=monitor,
        ).model_dump(exclude_none=True)
        await self.client.put(f"{path}/{pool_id}", region=region, json=payload)

        # Deliberately not a HealthMonitorItem: the lifecycle fields would be
        # pre-update values, and mixing those with the requested settings in a
        # read-shaped object is how a caller ends up reporting state that does
        # not exist yet.
        return HealthMonitorUpdateData(
            pool_id=pool_id,
            health_check_protocol=current.health_check_protocol,
            health_check_path=monitor.healthCheckPath or "",
            health_check_method=monitor.healthCheckMethod or "",
            healthy_threshold=monitor.healthyThreshold,
            unhealthy_threshold=monitor.unhealthyThreshold,
            timeout=monitor.timeout,
            interval=monitor.interval,
            success_code=monitor.successCode or "",
            domain_name=monitor.domainName or "",
            http_version=monitor.httpVersion or "",
            hint=(
                "This is the requested configuration. Poll get_load_balancer_status "
                "until settled, then confirm with get_pool_health_monitor."
            ),
        )
