"""Snapshot management for the vServer MCP server.

vServer snapshots come in two flavours that share one shape:

- a **server** snapshot captures every volume of an instance at one point in
  time, so the whole machine can be rolled back together;
- a **volume** snapshot captures a single disk.

Each resource has at most one *snapshot configuration* — the object that holds
the auto-snapshot policy — and any number of *snapshot points* under it. The
first point is a full copy and later ones are incremental, which is why
deleting an old point does not free as much as its `size_gb` suggests.

Rollback is the sharpest tool here: it discards everything written since the
point was taken, and there is no undo.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VbackupClient, VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateSnapshotDto,
    CreateSnapshotPolicyDto,
    RollbackSnapshotDto,
    SharedSnapshotItem,
    SharedSnapshotListData,
    SnapshotPointItem,
    SnapshotPointListData,
    SnapshotPolicyData,
    SnapshotPolicyItem,
    SnapshotPolicyListData,
    UpdateSnapshotPolicyDto,
)
from greennode.vserver_mcp_server.paging import (
    DEFAULT_PAGE_SIZE,
    as_list,
    fetch_all_items,
    unwrap,
)
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class SnapshotHandler:
    """Register and serve server and volume snapshot MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
        backup_client: VbackupClient,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.backup_client = backup_client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_snapshot_policies", annotations=READ)(self.list_snapshot_policies)
        self.mcp.tool(name="list_server_snapshots", annotations=READ)(self.list_server_snapshots)
        self.mcp.tool(name="get_server_snapshot_policy", annotations=READ)(
            self.get_server_snapshot_policy
        )
        self.mcp.tool(name="list_shared_server_snapshots", annotations=READ)(
            self.list_shared_server_snapshots
        )
        self.mcp.tool(name="list_volume_snapshots", annotations=READ)(self.list_volume_snapshots)
        self.mcp.tool(name="get_volume_snapshot_policy", annotations=READ)(
            self.get_volume_snapshot_policy
        )

        if self.allow_write:
            self.mcp.tool(name="create_server_snapshot", annotations=WRITE)(
                self.create_server_snapshot
            )
            self.mcp.tool(name="create_server_snapshot_policy", annotations=WRITE)(
                self.create_server_snapshot_policy
            )
            self.mcp.tool(name="update_server_snapshot_policy", annotations=WRITE)(
                self.update_server_snapshot_policy
            )
            self.mcp.tool(name="enable_server_auto_snapshot", annotations=WRITE)(
                self.enable_server_auto_snapshot
            )
            self.mcp.tool(name="disable_server_auto_snapshot", annotations=WRITE)(
                self.disable_server_auto_snapshot
            )
            self.mcp.tool(name="create_volume_snapshot", annotations=WRITE)(
                self.create_volume_snapshot
            )
            self.mcp.tool(name="update_volume_snapshot_policy", annotations=WRITE)(
                self.update_volume_snapshot_policy
            )
            self.mcp.tool(name="enable_volume_auto_snapshot", annotations=WRITE)(
                self.enable_volume_auto_snapshot
            )
            self.mcp.tool(name="disable_volume_auto_snapshot", annotations=WRITE)(
                self.disable_volume_auto_snapshot
            )
            self.mcp.tool(name="rollback_server_snapshot", annotations=DESTRUCTIVE)(
                self.rollback_server_snapshot
            )
            self.mcp.tool(name="rollback_volume_snapshot", annotations=DESTRUCTIVE)(
                self.rollback_volume_snapshot
            )
            self.mcp.tool(name="delete_server_snapshot", annotations=DESTRUCTIVE)(
                self.delete_server_snapshot
            )
            self.mcp.tool(name="delete_volume_snapshot", annotations=DESTRUCTIVE)(
                self.delete_volume_snapshot
            )
            self.mcp.tool(name="delete_server_snapshot_policy", annotations=DESTRUCTIVE)(
                self.delete_server_snapshot_policy
            )
            self.mcp.tool(name="delete_volume_snapshot_policy", annotations=DESTRUCTIVE)(
                self.delete_volume_snapshot_policy
            )
            self.mcp.tool(name="delete_shared_server_snapshot", annotations=DESTRUCTIVE)(
                self.delete_shared_server_snapshot
            )

    async def list_server_snapshots(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPointListData:
        """List the snapshot points of one server.

        Returns {region, resource_id, snapshots[{id, name, description, status,
        size_gb, server_id, schedule_type, is_permanent, retained_days,
        created_at}]}.

        `id` is the **snapshot point** id — the one rollback and delete take.
        `schedule_type` tells a manual snapshot apart from one the auto-snapshot
        policy produced. An empty list means the server has no snapshots, which
        is not the same as having no policy — check get_server_snapshot_policy.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/servers/{server_id}/snapshots", region=region
        )
        return SnapshotPointListData(
            region=region or self.config.default_region,
            resource_id=server_id,
            snapshots=[SnapshotPointItem.from_api(s) for s in raw],
        )

    async def get_server_snapshot_policy(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPolicyData:
        """Get the snapshot configuration of one server.

        Returns {region, resource_id, configured, id, name, enabled,
        snapshot_policy_id, snapshot_count, created_at}.

        `configured=false` means the server has never had a snapshot
        configuration — the API answers `null`, which this tool reports rather
        than failing. In that state enable_server_auto_snapshot has nothing to
        enable; call create_server_snapshot_policy first.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/servers/{server_id}/snapshots/detail", region=region
        )
        payload = unwrap(data)
        return SnapshotPolicyData.from_api(
            region or self.config.default_region,
            server_id,
            payload if isinstance(payload, dict) else None,
        )

    async def list_snapshot_policies(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and re-read the catalogue."),
    ) -> SnapshotPolicyListData:
        """List the auto-snapshot schedule policies the project can use.

        Returns {region, policies[{id, name, policy_type, schedule, run_at,
        server_count, volume_count}]}. A policy is the *when*: the time of day
        snapshots are taken, the cadence and how many copies are kept.

        ## Workflow
        - Call this before create_server_snapshot_policy,
          create_volume_snapshot_policy or enable_volume_auto_snapshot — those
          take a `snapshotPolicyId` and this is the only way to discover one.
        - Show `name` with `schedule` when asking the user to choose; the ids
          carry no meaning to them.
        """

        async def fetch():
            return await self.backup_client.get(
                "/v1/snapshot-policies",
                region=region,
                params={"page": 1, "size": DEFAULT_PAGE_SIZE},
            )

        data = await self.cache.get_or_fetch(
            "list_snapshot_policies", (region or self.config.default_region,), fetch, refresh
        )
        return SnapshotPolicyListData(
            region=region or self.config.default_region,
            policies=[SnapshotPolicyItem.from_api(p) for p in as_list(data, "items")],
        )

    async def list_shared_server_snapshots(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SharedSnapshotListData:
        """List the share grants on a server's snapshots.

        Returns {region, server_id, shares[{id, resource_id, resource_type,
        permission, shared_user_id, created_at}]} — who else in the
        organisation can restore from this server's snapshots.

        Note: this endpoint is permission-gated. A `403 IAM_PERMISSION_DENIED`
        means the caller's IAM policy lacks snapshot-sharing rights, not that
        nothing is shared.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/servers/{server_id}/server-snapshots/shared", region=region
        )
        return SharedSnapshotListData(
            region=region or self.config.default_region,
            server_id=server_id,
            shares=[SharedSnapshotItem.from_api(s) for s in as_list(data)],
        )

    async def list_volume_snapshots(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPointListData:
        """List the snapshot points of one volume.

        Same shape as list_server_snapshots, with `volume_id` filled instead of
        `server_id`. A volume that belongs to a server with a server-level
        snapshot policy also shows the points that policy created.
        """
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/volumes/{volume_id}/snapshots", region=region
        )
        return SnapshotPointListData(
            region=region or self.config.default_region,
            resource_id=volume_id,
            snapshots=[SnapshotPointItem.from_api(s) for s in raw],
        )

    async def get_volume_snapshot_policy(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPolicyData:
        """Get the snapshot configuration of one volume.

        Same shape as get_server_snapshot_policy. `configured=false` means the
        volume has no snapshot configuration yet.
        """
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/volumes/{volume_id}/volume-snapshots/detail", region=region
        )
        payload = unwrap(data)
        return SnapshotPolicyData.from_api(
            region or self.config.default_region,
            volume_id,
            payload if isinstance(payload, dict) else None,
        )

    async def create_server_snapshot(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: CreateSnapshotDto = Field(..., description="Snapshot to take now."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPointItem:
        """Take a snapshot of a server right now, across all its volumes.

        ## Requirements
        - Requires `--allow-write`. Snapshot storage is **billed** for as long
          as the snapshot exists.
        - `description` is mandatory — the API rejects the call without it.
        - Set either `retainedDays` or `isPermanently`. A permanent snapshot is
          never cleaned up automatically and bills until someone deletes it, so
          prefer `retainedDays` unless the user explicitly wants permanence.
        - The snapshot is crash-consistent, not application-consistent: a
          database that is not quiesced may need recovery on restore. Say so for
          stateful workloads.

        ## Workflow
        - Ask for a retention period rather than defaulting to permanent.
        - Take a snapshot before any risky operation (resize_server,
          rollback_server_snapshot, an OS upgrade) — that is what makes those
          reversible.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/servers/{server_id}/snapshots",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return SnapshotPointItem.from_api(unwrap(data) or {})

    async def create_volume_snapshot(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        body: CreateSnapshotDto = Field(..., description="Snapshot to take now."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPointItem:
        """Take a snapshot of a single volume right now.

        ## Requirements
        - Requires `--allow-write`. Snapshot storage is **billed** while it
          exists.
        - `description` is mandatory.
        - Set either `retainedDays` or `isPermanently`; prefer `retainedDays`.
        - Snapshotting a mounted, actively written filesystem is
          crash-consistent only. For a clean copy, freeze or unmount inside the
          guest OS first.

        ## Workflow
        - Use this for a data disk; use create_server_snapshot when the whole
          machine, boot disk included, has to be restorable as a unit.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/volumes/{volume_id}/snapshots",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return SnapshotPointItem.from_api(unwrap(data) or {})

    async def create_server_snapshot_policy(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: CreateSnapshotPolicyDto = Field(
            ..., description="Snapshot configuration to create for the server."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPolicyData:
        """Set up the snapshot configuration of a server.

        ## Requirements
        - Requires `--allow-write`. Automatic snapshots create **billable
          storage on a schedule**, so the cost grows over time.
        - `description` is mandatory.
        - `snapshotPolicyId` selects the schedule (frequency and retention);
          get it from list_snapshot_policies. Leave it unset to create the
          configuration without a schedule.
        - `volumeIds` narrows the snapshot to specific disks; omit it to cover
          every volume of the server.

        ## Workflow
        - Call get_server_snapshot_policy first — a server that already has a
          configuration should go through update_server_snapshot_policy instead.
        - Setting `enableSnapshot=true` starts the schedule immediately;
          otherwise turn it on later with enable_server_auto_snapshot.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/servers/{server_id}/server-snapshots",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        payload = unwrap(data)
        return SnapshotPolicyData.from_api(
            region or self.config.default_region,
            server_id,
            payload if isinstance(payload, dict) else None,
        )

    async def update_server_snapshot_policy(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: UpdateSnapshotPolicyDto = Field(..., description="Schedule policy to switch to."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPolicyData:
        """Change which schedule policy a server's automatic snapshots follow.

        ## Requirements
        - Requires `--allow-write`. A more frequent or longer-retaining policy
          **increases storage cost**.
        - `snapshotPolicyId` comes from list_snapshot_policies.
        - The server needs an existing configuration — check
          get_server_snapshot_policy, and use create_server_snapshot_policy when
          `configured` is false.

        ## Workflow
        - Confirm the new frequency and retention with the user before calling;
          this silently changes how much snapshot storage accrues.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/servers/{server_id}/server-snapshots/policy",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        payload = unwrap(data)
        return SnapshotPolicyData.from_api(
            region or self.config.default_region,
            server_id,
            payload if isinstance(payload, dict) else None,
        )

    async def enable_server_auto_snapshot(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Start the automatic snapshot schedule of a server.

        ## Requirements
        - Requires `--allow-write`. Scheduled snapshots accrue **billable
          storage** from here on.
        - The server must already have a snapshot configuration — check
          get_server_snapshot_policy first.

        ## Workflow
        - Tell the user what the schedule will cost in storage terms before
          turning it on.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.put(
            f"/v2/{pid}/servers/{server_id}/server-snapshots/enable-auto", region=region
        )
        return f"Automatic snapshots enabled for server {server_id}."

    async def disable_server_auto_snapshot(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Stop the automatic snapshot schedule of a server.

        ## Requirements
        - Requires `--allow-write`.
        - Existing snapshot points are kept and keep billing — this only stops
          new ones being taken. Use delete_server_snapshot to actually reclaim
          storage.
        - The server loses its rolling recovery point: after this, the newest
          snapshot only ages.

        ## Workflow
        - Make sure the user understands they are giving up scheduled backups,
          not deleting them.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.put(
            f"/v2/{pid}/servers/{server_id}/server-snapshots/disable-auto", region=region
        )
        return f"Automatic snapshots disabled for server {server_id}."

    async def update_volume_snapshot_policy(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        body: UpdateSnapshotPolicyDto = Field(..., description="Schedule policy to switch to."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SnapshotPolicyData:
        """Change which schedule policy a volume's automatic snapshots follow.

        ## Requirements
        - Requires `--allow-write`; a longer-retaining policy **increases
          storage cost**.
        - `snapshotPolicyId` comes from list_snapshot_policies.
        - Check get_volume_snapshot_policy first; the volume needs an existing
          configuration.

        ## Workflow
        - Confirm the new frequency and retention with the user.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/volume-snapshots/policy",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        payload = unwrap(data)
        return SnapshotPolicyData.from_api(
            region or self.config.default_region,
            volume_id,
            payload if isinstance(payload, dict) else None,
        )

    async def enable_volume_auto_snapshot(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        server_id: str = Field(
            ..., description="Server the volume is attached to — the API scopes the call by it."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Start the automatic snapshot schedule of a volume.

        ## Requirements
        - Requires `--allow-write`. Scheduled snapshots accrue **billable
          storage**.
        - The endpoint is scoped by **both** the volume and the server it is
          attached to, so an unattached volume cannot have auto-snapshots. Read
          `server_id` from get_volume.

        ## Workflow
        - Check get_volume_snapshot_policy first; the volume needs a snapshot
          configuration for the schedule to have anything to follow.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/volume-snapshots/servers/{server_id}/enable-auto",
            region=region,
        )
        return f"Automatic snapshots enabled for volume {volume_id}."

    async def disable_volume_auto_snapshot(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        server_id: str = Field(
            ..., description="Server the volume is attached to — the API scopes the call by it."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Stop the automatic snapshot schedule of a volume.

        ## Requirements
        - Requires `--allow-write`.
        - Existing snapshot points stay and keep billing; this only stops new
          ones.
        - Scoped by the attached server, same as the enable call.

        ## Workflow
        - Make sure the user understands scheduled backups stop but nothing is
          deleted.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/volume-snapshots/servers/{server_id}/disable-auto",
            region=region,
        )
        return f"Automatic snapshots disabled for volume {volume_id}."

    async def rollback_server_snapshot(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        snapshot_point_id: str = Field(
            ..., description="Snapshot point ID from list_server_snapshots."
        ),
        body: RollbackSnapshotDto = Field(
            default_factory=RollbackSnapshotDto, description="Rollback options."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Roll a server back to a snapshot point. This DESTROYS newer data.

        ## Requirements
        - Requires `--allow-write`.
        - **Everything written to every volume of the server since the snapshot
          was taken is lost.** There is no undo and no second chance.
        - The server is stopped for the rollback. Set
          `restartServerWhenRevertCompleted=true` to have it powered back on
          afterwards, otherwise it stays off.
        - Data held only in memory or in an application buffer at snapshot time
          was never captured; a database may come back needing recovery.

        ## Workflow
        - Take a fresh snapshot with create_server_snapshot **first** — that is
          the only way back if the rollback target turns out to be wrong.
        - Show the user the snapshot's `created_at` and spell out exactly how
          much time is being discarded, then require an explicit yes.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        validate_id(snapshot_point_id, "snapshot_point_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.post(
            f"/v2/{pid}/servers/{server_id}/snapshots/rollback/{snapshot_point_id}",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return (
            f"Server {server_id} is rolling back to snapshot {snapshot_point_id}. "
            "Poll get_server until its status settles."
        )

    async def rollback_volume_snapshot(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        snapshot_point_id: str = Field(
            ..., description="Snapshot point ID from list_volume_snapshots."
        ),
        body: RollbackSnapshotDto = Field(
            default_factory=RollbackSnapshotDto, description="Rollback options."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Roll a volume back to a snapshot point. This DESTROYS newer data.

        ## Requirements
        - Requires `--allow-write`.
        - **Everything written to the volume since the snapshot was taken is
          lost**, with no undo.
        - The attached server is stopped for the rollback; set
          `restartServerWhenRevertCompleted=true` to power it back on.
        - Rolling back a **boot** volume reverts the operating system — use
          rollback_server_snapshot when the whole machine should move together,
          or the disks end up from different points in time.

        ## Workflow
        - Take a fresh snapshot with create_volume_snapshot first.
        - Show the user the snapshot's `created_at` and how much data is being
          discarded, then require an explicit yes.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        validate_id(snapshot_point_id, "snapshot_point_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.post(
            f"/v2/{pid}/volumes/{volume_id}/snapshots/rollback/{snapshot_point_id}",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return (
            f"Volume {volume_id} is rolling back to snapshot {snapshot_point_id}. "
            "Poll get_volume until its status settles."
        )

    async def delete_server_snapshot(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        snapshot_point_id: str = Field(
            ..., description="Snapshot point ID from list_server_snapshots."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete one snapshot point of a server. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - That recovery point is gone; the server can no longer be rolled back
          to it.
        - Snapshots are incremental, so deleting one frees less than its
          `size_gb` — the blocks later snapshots still need are kept.

        ## Workflow
        - Show the user the snapshot's name and `created_at` and confirm before
          calling. Check it is not the only recovery point the server has.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        validate_id(snapshot_point_id, "snapshot_point_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/servers/{server_id}/snapshots/{snapshot_point_id}", region=region
        )
        return f"Snapshot {snapshot_point_id} of server {server_id} deleted."

    async def delete_volume_snapshot(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        snapshot_point_id: str = Field(
            ..., description="Snapshot point ID from list_volume_snapshots."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete one snapshot point of a volume. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - The recovery point is gone for good.
        - Incremental storage means the space freed is usually less than
          `size_gb`.

        ## Workflow
        - Show the user the snapshot's name and `created_at`, and confirm.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        validate_id(snapshot_point_id, "snapshot_point_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/volumes/{volume_id}/snapshots/{snapshot_point_id}", region=region
        )
        return f"Snapshot {snapshot_point_id} of volume {volume_id} deleted."

    async def delete_server_snapshot_policy(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a server's whole snapshot configuration. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - This removes the configuration **and the snapshot points under it** —
          it is far more destructive than disable_server_auto_snapshot, which
          only stops the schedule.
        - After this the server has no recovery points at all.

        ## Workflow
        - Call list_server_snapshots and show the user every snapshot that would
          be destroyed, then require an explicit yes.
        - If they only want the schedule stopped, use
          disable_server_auto_snapshot instead.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/servers/{server_id}/server-snapshots", region=region)
        return f"Snapshot configuration of server {server_id} deleted."

    async def delete_volume_snapshot_policy(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a volume's whole snapshot configuration. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Removes the configuration **and the snapshot points under it**, unlike
          disable_volume_auto_snapshot which only stops the schedule.

        ## Workflow
        - Call list_volume_snapshots, show the user everything that would be
          destroyed, and require an explicit yes.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/volumes/{volume_id}/volume-snapshots", region=region)
        return f"Snapshot configuration of volume {volume_id} deleted."

    async def delete_shared_server_snapshot(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        shared_snapshot_id: str = Field(
            ..., description="Share ID from list_shared_server_snapshots."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Revoke a share grant on a server's snapshots.

        ## Requirements
        - Requires `--allow-write`.
        - The other user immediately loses access to restore from these
          snapshots. The snapshots themselves are not deleted.

        ## Workflow
        - Confirm with list_shared_server_snapshots which grant the id refers
          to before revoking it.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        validate_id(shared_snapshot_id, "shared_snapshot_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/servers/{server_id}/server-snapshots/shared/{shared_snapshot_id}",
            region=region,
        )
        return f"Snapshot share {shared_snapshot_id} revoked."
