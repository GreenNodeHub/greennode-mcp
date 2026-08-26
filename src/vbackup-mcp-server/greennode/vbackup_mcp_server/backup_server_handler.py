"""Backup servers — the protected vServer instances that are the heart of vBackup.

A "backup server" is what the console and the API's own tag call a protected
instance; the path spells it ``backup-instances`` and its ids start with
``bk-ins-``. It joins three things: the vServer instance being protected, the
policy that schedules the runs, and the destination the data lands in.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
from greennode.vbackup_mcp_server.guards import require_write
from greennode.vbackup_mcp_server.models import (
    BackupNowDto,
    BackupServerItem,
    BackupServerListData,
    BackupServerPointDownloadData,
    BackupServerPointItem,
    BackupServerPointListData,
    BackupServerVolumeItem,
    BackupServerVolumeListData,
    BackupStatisticData,
    CreateBackupServerDto,
    UpdateBackupServerDestinationDto,
    UpdateBackupServerPolicyDto,
    UpdateBackupServerVolumesDto,
    VolumePointDownloadUrls,
    VserverInstanceDetail,
    WriteResult,
    as_text,
)
from greennode.vbackup_mcp_server.paging import as_list, fetch_all_items, unwrap
from greennode.vbackup_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field


class BackupServerHandler:
    """Register and serve backup-server MCP tools."""

    def __init__(
        self,
        mcp,
        config: VbackupConfig,
        client: VbackupClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_backup_servers", annotations=READ)(self.list_backup_servers)
        self.mcp.tool(name="get_backup_server", annotations=READ)(self.get_backup_server)
        self.mcp.tool(name="list_backup_server_volumes", annotations=READ)(
            self.list_backup_server_volumes
        )
        self.mcp.tool(name="get_backup_statistics", annotations=READ)(self.get_backup_statistics)
        self.mcp.tool(name="get_vserver_instance", annotations=READ)(self.get_vserver_instance)
        self.mcp.tool(name="get_backup_server_point_download_urls", annotations=READ)(
            self.get_backup_server_point_download_urls
        )
        self.mcp.tool(name="list_backup_server_points", annotations=READ)(
            self.list_backup_server_points
        )

        if self.allow_write:
            self.mcp.tool(name="create_backup_server", annotations=WRITE)(
                self.create_backup_server
            )
            self.mcp.tool(name="update_backup_server_volumes", annotations=WRITE)(
                self.update_backup_server_volumes
            )
            self.mcp.tool(name="start_backup", annotations=WRITE)(self.start_backup)
            self.mcp.tool(name="update_backup_server_destination", annotations=WRITE)(
                self.update_backup_server_destination
            )
            self.mcp.tool(name="delete_backup_server_point", annotations=DESTRUCTIVE)(
                self.delete_backup_server_point
            )
            self.mcp.tool(name="update_backup_server_policy", annotations=WRITE)(
                self.update_backup_server_policy
            )
            self.mcp.tool(name="enable_backup_server", annotations=WRITE)(
                self.enable_backup_server
            )
            self.mcp.tool(name="disable_backup_server", annotations=WRITE)(
                self.disable_backup_server
            )
            self.mcp.tool(name="delete_backup_server", annotations=DESTRUCTIVE)(
                self.delete_backup_server
            )

    async def list_backup_servers(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Region to query ('HCM-3' or 'HAN'); defaults to 'HCM-3'. Backup "
                "servers are region-scoped — if the user's server isn't here, try "
                "the other region before concluding it is unprotected."
            ),
        ),
        server_id: str | None = Field(
            None,
            description=(
                "Filter by the protected vServer instance ID (`ins-...`). This is "
                "the direct answer to 'is this server backed up?' — an empty "
                "result means the instance has no backup server at all."
            ),
        ),
        name: str | None = Field(None, description="Filter by backup server name."),
        backend_id: str | None = Field(
            None, description="Filter by backend ID from list_backends."
        ),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> BackupServerListData:
        """List the protected servers (backup servers) in a region.

        Returns {region, total, backup_servers[{id, name, server_id,
        server_deleted, status, backup_enabled, policy{...}, destination{...},
        volumes[...], ...}]}.

        `id` (`bk-ins-...`) is the backup server id every other backup-server
        tool takes; `server_id` (`ins-...`) is the vServer instance it protects
        — the two are different and are not interchangeable.

        Read these three flags together, they mean different things:
        - `backup_enabled=false` — the schedule is paused, existing restore
          points are untouched.
        - `volumes[].backup_enabled=false` — that specific disk is excluded
          from every run, even while the server itself is enabled.
        - `server_deleted=true` — the source instance is gone, yet the restore
          points remain and are still billed. Always surface this one.

        `policy.schedule` is a flattened summary of the enabled cadences; an
        empty string means no cadence is enabled and the server is never backed
        up on a schedule.
        """
        for value, label in ((server_id, "server_id"), (backend_id, "backend_id")):
            if value:
                validate_id(value, label)

        params: dict[str, str] = {}
        if server_id:
            params["serverId"] = server_id
        if name:
            params["name"] = name
        if backend_id:
            params["backendId"] = backend_id

        resolved_region = region or self.config.default_region

        async def fetch() -> BackupServerListData:
            raw = await fetch_all_items(
                self.client, "/v1/backup-instances", region=region, params=params or None
            )
            items = [BackupServerItem.from_api(i) for i in raw if isinstance(i, dict)]
            return BackupServerListData(
                region=resolved_region, total=len(items), backup_servers=items
            )

        key = ("list_backup_servers", resolved_region, tuple(sorted(params.items())))
        return await self.cache.get_or_fetch("list_backup_servers", key, fetch, refresh)

    async def get_backup_server(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupServerItem:
        """Get one backup server by id.

        Returns the same shape as one entry of list_backup_servers, read fresh
        — use it to confirm state right after a write, since the list is cached.
        """
        validate_id(backup_server_id, "backup_server_id")
        data = await self.client.get(f"/v1/backup-instances/{backup_server_id}", region=region)
        return BackupServerItem.from_api(unwrap(data))

    async def list_backup_server_volumes(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupServerVolumeListData:
        """List the volumes of one backup server and whether each is backed up.

        Returns {region, backup_server_id, total, volumes[{volume_id,
        backup_enabled, size_gb, used_gb, latest_record}]}.

        `backup_enabled=false` on a volume means that disk is skipped by every
        run and cannot be restored later — the single most common cause of an
        "incomplete" restore. Show this list before a user relies on a backup
        covering a whole machine.

        Sizes are reported in both GiB and bytes; `used_gb` is what a run
        actually transfers and bills.

        An empty list is normal when the source instance has been deleted.
        """
        validate_id(backup_server_id, "backup_server_id")
        raw = await self.client.get(
            f"/v1/backup-instances/{backup_server_id}/volumes", region=region
        )
        items = [BackupServerVolumeItem.from_api(v) for v in as_list(raw) if isinstance(v, dict)]
        return BackupServerVolumeListData(
            region=region or self.config.default_region,
            backup_server_id=backup_server_id,
            total=len(items),
            volumes=items,
        )

    async def list_backup_server_points(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupServerPointListData:
        """List the restore points of one backup server.

        Returns {region, backup_server_id, total, points[{id, status,
        snapshot_time, finish_time, size_gb, used_gb, policy_name_at_run,
        volume_points[...]}]}.

        `id` (`bk-ins-pt-...`) identifies the restore point — it is what a
        restore consumes, and what list_vserver_backup_volume_points takes to
        show which disks are inside it.

        An empty list means no run has ever completed for this server. That is
        NOT the same as having no schedule: check `backup_enabled` and
        `policy.schedule` on the server, and list_backup_history for runs that
        started and failed.

        `policy_name_at_run` is the policy as it was when the point was taken,
        so it stays accurate after the policy is edited.
        """
        validate_id(backup_server_id, "backup_server_id")
        raw = await fetch_all_items(
            self.client,
            f"/v1/backup-instances/{backup_server_id}/backup-instance-points",
            region=region,
        )
        items = [BackupServerPointItem.from_api(p) for p in raw if isinstance(p, dict)]
        return BackupServerPointListData(
            region=region or self.config.default_region,
            backup_server_id=backup_server_id,
            total=len(items),
            points=items,
        )

    async def create_backup_server(
        self,
        body: CreateBackupServerDto = Field(..., description="The instances to protect and how."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Protect one or more vServer instances by creating backup servers.

        Returns {region, resource_id, action, succeeded, detail}. The API
        answers 201 with no body, so verify with list_backup_servers afterwards.

        ## Requirements
        - `--allow-write` must be enabled.
        - `backendId` from list_backends, `projectId` from any existing resource
          in the region, `backupPolicyId` from list_backup_policies and
          `backupDestinationId` from list_backup_destinations. Never invent one.
        - The instance must not already be protected — check
          list_backup_servers(server_id=...) first, or list_protected_servers.
        - The instance's state must be one of
          get_configuration.allowed_backup_server_status.
        - `serverConfig[].volumes` decides which disks are covered. Pass them
          explicitly: an omitted disk is a disk that cannot be restored.

        ## Workflow
        1. list_backends, list_backup_destinations, list_backup_policies —
           gather the three ids. Present the policy's `schedule.summary` and the
           destination, and let the user choose. Do NOT pick silently.
        2. Read the instance's disks (list_backup_server_volumes on an existing
           backup server, or vServer's own volume tools) and confirm which ones
           to include.
        3. Estimate what it will store: list_volume_usage on the chosen volumes
           gives the used size each run transfers.
        4. Summarise instance, policy, destination and disk list, then confirm.
        5. Create, then verify with list_backup_servers(server_id=...).
        """
        require_write(self.allow_write)
        payload = body.model_dump(exclude_none=True)
        await self.client.post("/v1/backup-instances", region=region, json=payload)
        self.cache.invalidate("list_backup_servers")
        self.cache.invalidate("list_protected_servers")
        server_ids = ", ".join(s.serverId for s in body.serverConfig)
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=server_ids,
            action="created",
            detail=(
                "The API returns no body on create. Confirm with "
                "list_backup_servers(server_id=...) and report the new `bk-ins-` id. "
                "The first run happens at the policy's next scheduled time, not now."
            ),
        )

    async def update_backup_server_volumes(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        body: UpdateBackupServerVolumesDto = Field(
            ..., description="The volume to include or exclude."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Include or exclude one volume of a backup server from future runs.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - `volumeId` must belong to this backup server — read it from
          list_backup_server_volumes, not from vServer.
        - Excluding a disk affects FUTURE runs only. Restore points already
          taken keep the disk; new ones will not have it.

        ## Workflow
        1. list_backup_server_volumes — show the current inclusion per disk.
        2. If the user is excluding a disk, say plainly that the disk will be
           missing from every future restore point, and confirm.
        3. Update, then re-read list_backup_server_volumes to verify.
        """
        require_write(self.allow_write)
        validate_id(backup_server_id, "backup_server_id")
        await self.client.put(
            f"/v1/backup-instances/{backup_server_id}/volumes",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_servers")
        action = "volume included" if body.backupEnabled else "volume excluded"
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_server_id,
            action=action,
            detail=(
                f"{body.volumeId} now "
                f"{'included in' if body.backupEnabled else 'excluded from'} future runs. "
                "Existing restore points are unchanged."
            ),
        )

    async def update_backup_server_policy(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        body: UpdateBackupServerPolicyDto = Field(..., description="The policy to attach, by id."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Attach a different backup policy to a backup server.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - The policy id comes from list_backup_policies and must live in the
          same region and project as the backup server.
        - Changing the policy changes the cadence and the retention. A shorter
          retention means existing restore points beyond the new limit will be
          pruned — say so before doing it.

        ## Workflow
        1. get_backup_server — the current policy and its schedule.
        2. list_backup_policies — present the candidates with their
           `schedule.summary`, and let the user choose.
        3. Compare retentions. If the new one keeps fewer points, state which
           points will be lost and confirm.
        4. Update, then get_backup_server to verify the new policy is attached.
        """
        require_write(self.allow_write)
        validate_id(backup_server_id, "backup_server_id")
        validate_id(body.id, "policy id")
        await self.client.put(
            f"/v1/backup-instances/{backup_server_id}/policies",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_servers")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_server_id,
            action="policy attached",
            detail=(
                f"Policy {body.id} is now the schedule for this server. A shorter "
                "retention prunes older restore points at the next run."
            ),
        )

    async def enable_backup_server(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Resume the backup schedule of a backup server.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - The server must have a policy attached, otherwise there is no
          schedule to resume — check `policy.schedule` with get_backup_server.

        ## Workflow
        1. get_backup_server — confirm it is currently paused and has a policy.
        2. Enable, then verify `backup_enabled` is true.
        3. Tell the user when the next run is due, from the policy's schedule —
           enabling does not trigger a run immediately.
        """
        require_write(self.allow_write)
        validate_id(backup_server_id, "backup_server_id")
        await self.client.put(f"/v1/backup-instances/{backup_server_id}/enabled", region=region)
        self.cache.invalidate("list_backup_servers")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_server_id,
            action="enabled",
            detail=(
                "The schedule is active again. The next run happens at the policy's "
                "next scheduled time, not immediately."
            ),
        )

    async def disable_backup_server(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Pause the backup schedule of a backup server.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - Pausing stops NEW runs. Existing restore points stay and keep being
          billed — this is not a way to reduce storage cost.

        ## Workflow
        1. get_backup_server — confirm which instance this protects.
        2. Say plainly that the machine stops being backed up from now on, and
           that existing points are kept and still charged. Confirm.
        3. Disable, then verify `backup_enabled` is false.
        """
        require_write(self.allow_write)
        validate_id(backup_server_id, "backup_server_id")
        await self.client.put(f"/v1/backup-instances/{backup_server_id}/disabled", region=region)
        self.cache.invalidate("list_backup_servers")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_server_id,
            action="disabled",
            detail=(
                "No new runs will happen. Existing restore points are kept and still "
                "billed; deleting the backup server is what removes them."
            ),
        )

    async def delete_backup_server(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Delete a backup server AND every restore point it holds.

        Returns {region, resource_id, action, succeeded, detail}. The API
        answers 204 with no body.

        ## Requirements
        - `--allow-write` must be enabled.
        - This is IRREVERSIBLE and it destroys data: the restore points go with
          the backup server. The protected vServer instance itself is not
          touched, but the ability to recover it from these backups is gone.
        - To stop backups without losing history, use disable_backup_server
          instead. Confirm which one the user means before proceeding.

        ## Workflow
        1. get_backup_server and list_backup_server_points — state the instance
           name, how many restore points exist and the oldest/newest dates.
        2. Ask for an explicit confirmation that naming this data loss. Do not
           accept a generic "yes, delete" gathered before those numbers were
           shown.
        3. Delete, then verify with list_backup_servers(server_id=...).
        """
        require_write(self.allow_write)
        validate_id(backup_server_id, "backup_server_id")
        await self.client.delete(f"/v1/backup-instances/{backup_server_id}", region=region)
        self.cache.invalidate("list_backup_servers")
        self.cache.invalidate("list_protected_servers")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_server_id,
            action="deleted",
            detail=(
                "The backup server and its restore points are gone. The protected "
                "vServer instance is unaffected but can no longer be recovered from "
                "these backups."
            ),
        )

    async def get_backup_statistics(
        self,
        project_id: str | None = Field(
            None,
            description=(
                "Project to scope the counters to. STRONGLY recommended: without it "
                "the API reports total_servers as 0 and the coverage ratio cannot be "
                "computed. Read one off any backup server."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupStatisticData:
        """Get the account's backup coverage and outcome counters.

        Returns {region, project_id, total_servers, total_protected_servers,
        total_backup_servers, total_backup_completed, total_backup_failed,
        total_restore_completed, total_restore_failed}.

        The one-call answer to "how are we doing on backups". Use it to open a
        review before drilling into individual servers.

        Read three things out of it rather than reciting the numbers:

        - **Coverage**: `total_protected_servers` against `total_servers`. The
          difference is instances with no backup at all.
        - **Waste**: `total_backup_servers` is normally HIGHER than
          `total_protected_servers`, and the gap is backup servers whose source
          instance is gone — still holding restore points and still billed.
          Chase them with list_backup_servers and `server_deleted`.
        - **Reliability**: `total_backup_failed` against `total_backup_completed`.
          Anything non-zero deserves list_backup_history to see which server.

        **`total_servers` is 0 when `project_id` is omitted** — the counter needs
        the project to know what to count. Never present a coverage ratio built
        on a zero; pass the project id or say the ratio is unavailable.

        The counters are per region: run it for both before reporting an account
        total.
        """
        if project_id:
            validate_id(project_id, "project_id")

        resolved_project = project_id or self.config.project_id or ""
        params = {"projectId": resolved_project} if resolved_project else None
        data = await self.client.get("/v1/backup-statistic", region=region, params=params)
        return BackupStatisticData.from_api(
            region or self.config.default_region, resolved_project, data
        )

    async def get_vserver_instance(
        self,
        server_id: str = Field(
            ...,
            description="vServer instance ID (`ins-...`), the `server_id` a backup server records.",
        ),
        project_id: str = Field(
            ...,
            description=(
                "Project the instance belongs to (`pro-...`). Required — the vServer "
                "gateway carries it in the path. Read it off the backup server."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VserverInstanceDetail:
        """Describe the vServer instance behind a backup server.

        Returns {id, name, status, zone, flavor{name, cpu, memory_gb, gpu},
        image{id, type, version}, boot_volume_id, encryption_volume,
        addresses[], created_at}.

        **This is the only tool here that calls the vServer product**, not
        vBackup. vBackup stores a bare `serverId` and nothing about the machine,
        so this is what turns `ins-a1b2c3...` into a name a user recognises.

        Use it to:

        - Name the machine when reporting backups, instead of quoting an id.
        - Check `status` against get_configuration.allowed_backup_server_status
          before create_backup_server, so the create is not attempted on an
          instance the platform will refuse.
        - Read `image` when judging an old restore point: a point captured under
          a different OS version restores to something the user may not expect.
        - Identify `boot_volume_id` before excluding a disk with
          update_backup_server_volumes — excluding the boot disk leaves restore
          points that cannot rebuild a bootable machine.

        A 404 means the instance no longer exists, which is the same thing a
        backup server's `server_deleted=true` reports. Its restore points survive
        and are still billed.

        This server only READS vServer. Creating, resizing or deleting an
        instance belongs to `vserver-mcp-server`.
        """
        validate_id(server_id, "server_id")
        validate_id(project_id, "project_id")
        data = await self.client.get_vserver(
            f"/v2/{project_id}/servers/{server_id}", region=region
        )
        return VserverInstanceDetail.from_api(unwrap(data))

    async def get_backup_server_point_download_urls(
        self,
        point_id: str = Field(
            ..., description="Restore point ID from list_backup_server_points (`bk-ins-pt-...`)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupServerPointDownloadData:
        """Get signed download links for the disks inside one restore point.

        Returns {region, point_id, backup_server_id, total_volumes,
        volumes[{volume_point_id, volume_id, urls[]}], warning}.

        This is how backup data leaves the platform — the only export route
        vBackup offers, and the closest thing to a restore this server can do,
        since no endpoint can start one.

        ## Handling the links
        **Every URL is a bearer credential.** Anyone holding one downloads the
        disk image without authenticating. Give them to the user who asked and
        say so; do not paste them into a shared channel, a ticket, a commit or a
        log, and do not repeat them in a summary once they have been delivered.
        They expire, so fetch them when the user is ready to download rather
        than in advance.

        ## Reading the result
        `volumes` has one entry per disk in the point, and each entry's `urls`
        may hold SEVERAL links — a large disk is split into parts and all of them
        are needed to reconstruct it. Report the count per volume and never
        present the first link as "the download".

        Map `volume_id` back through list_backup_server_volumes to tell the user
        which disk is which; the ids alone will not mean anything to them.

        **A point that is still being written returns zero links.** The call
        succeeds, `volumes` lists the disk, and its `urls` is empty — verified
        live against a point in `UPLOADING`. Empty links mean "not ready yet",
        never "no data": wait for the point's `status` to reach `ACTIVE` in
        list_backup_server_points, then ask again.
        """
        validate_id(point_id, "point_id")
        data = await self.client.get(
            f"/v1/backup-instance-points/{point_id}/pre-signed-url", region=region
        )
        payload = unwrap(data)
        raw = payload.get("backupVolumePointPreSignedUrls")
        volumes = [
            VolumePointDownloadUrls.from_api(v)
            for v in (raw if isinstance(raw, list) else [])
            if isinstance(v, dict)
        ]
        return BackupServerPointDownloadData(
            region=region or self.config.default_region,
            point_id=as_text(payload.get("id")) or point_id,
            backup_server_id=as_text(payload.get("backupInstanceId")),
            total_volumes=len(volumes),
            volumes=volumes,
        )

    async def start_backup(
        self,
        server_id: str = Field(
            ...,
            description=(
                "vServer instance to back up now (`ins-...`). Note this takes the "
                "INSTANCE id, not the backup server id (`bk-ins-...`)."
            ),
        ),
        body: BackupNowDto = Field(..., description="Backend and project of the instance."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Trigger an immediate backup run, outside the schedule.

        Returns {region, resource_id, action, succeeded, detail}. The run starts
        asynchronously; nothing about its outcome is known when this returns.

        The console calls this **Back now**. It is the tool for "back this up
        before I change something", and it does not disturb the schedule — the
        next scheduled run still happens.

        ## Requirements
        - `--allow-write` must be enabled.
        - The instance must ALREADY have a backup server. This triggers the
          existing configuration; it does not create one. Check with
          list_backup_servers filtered by `server_id` first.
        - This call addresses the **INSTANCE**, not a specific backup server.
          One instance normally has exactly one backup server — the API refuses
          a second with `Conflict: The backup server for server <id> already
          exists` — so the two are usually interchangeable here. If
          list_backup_servers ever does return several for one `server_id`, say
          so and let the user resolve which configuration runs rather than
          guessing.
        - The path takes the **instance** id and the body takes `backendId` and
          `projectId` — read all three off that backup server rather than
          assembling them from separate lookups.
        - The run consumes destination quota like any other. If the destination
          is near `max_quota_gb`, an extra full run is what pushes it over and
          starts failing every server writing there.

        ## Workflow
        1. list_backup_servers `server_id=<ins-...>` — confirm it is protected
           and read `backend_id`, `project_id` and the destination.
        2. Check the destination's headroom with get_backup_destination.
        3. Trigger, then tell the user it runs in the background.
        4. Verify with list_backup_history `server_id=<ins-...>`. The record
           takes a few seconds to appear and then walks `BACKING_UP` →
           `UPLOADING` → `ACTIVE`, which for a 20 GB boot disk is minutes, not
           seconds. Only `ACTIVE` means the restore point is usable. Do not
           report success from this call alone; it only says the request was
           accepted.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        await self.client.post(
            f"/v1/backup-instances/backup-now/{server_id}",
            region=region,
            json=body.model_dump(),
        )
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=server_id,
            action="backup started",
            detail=(
                "The run was accepted and continues in the background. Confirm it with "
                "list_backup_history for this server — this call does not report the "
                "outcome."
            ),
        )

    async def update_backup_server_destination(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID from list_backup_servers (`bk-ins-...`)."
        ),
        body: UpdateBackupServerDestinationDto = Field(
            ..., description="The destination future runs should write to."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Point a backup server at a different destination.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - **Only FUTURE runs move.** Restore points already taken stay in the old
          destination, stay billed there, and are still restorable from there.
          The backup history is therefore split across two destinations from this
          moment on — say so plainly, because "moved the backups" is what a user
          will assume happened.
        - The new destination must serve the same product (vServer) and must have
          headroom: check `vault.used_gb` against `max_quota_gb` with
          get_backup_destination, remembering the next run may be a full copy.
        - A destination under a vault lock imposes its retention rules on
          everything written there afterwards.

        ## Workflow
        1. get_backup_server — report the current destination by name.
        2. list_backup_destinations — pick the target, check product, quota and
           `vault_lock`.
        3. State explicitly that existing restore points stay where they are and
           keep costing money in the old destination, then confirm.
        4. Update, then get_backup_server to verify, and consider start_backup if
           the user wants a copy in the new destination immediately.
        """
        require_write(self.allow_write)
        validate_id(backup_server_id, "backup_server_id")
        await self.client.put(
            f"/v1/backup-instances/{backup_server_id}/destination",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_servers")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_server_id,
            action="destination updated",
            detail=(
                "Future runs only. Restore points already taken remain in the previous "
                "destination and are still billed there."
            ),
        )

    async def delete_backup_server_point(
        self,
        point_id: str = Field(
            ..., description="Restore point ID from list_backup_server_points (`bk-ins-pt-...`)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Delete ONE restore point.

        Returns {region, resource_id, action, succeeded, detail}.

        **Irreversible.** The point cannot be recovered and the moment in time it
        captured is gone. This is the fine-grained alternative to
        delete_backup_server, which destroys every point a server holds.

        ## Requirements
        - `--allow-write` must be enabled.
        - Check the destination's `soft_delete` with get_backup_destination
          FIRST. With soft delete on, the point moves to the recycle bin and
          **keeps being billed for `retain_days`** — so deleting it does not free
          storage today, which matters when the user's goal is cost.
        - A destination under a vault lock refuses to delete a point younger than
          `min_retention_days`.
        - **A point whose run is still in progress cannot be deleted.** The API
          answers `409 Conflict: Your resource is being processed.` — verified
          live. That is a "wait", not a failure: check `status` with
          list_backup_server_points and retry once the run has settled. The same
          409 blocks delete_backup_server while a run is active.
        - Deleting an incremental point can invalidate the chain of points taken
          after it. Prefer removing the OLDEST points, and never remove one from
          the middle of a run of incrementals without saying what it may cost.

        ## Workflow
        1. list_backup_server_points — show the point's date, size and which
           server it belongs to, so the user knows exactly what is going.
        2. get_backup_destination — report soft delete and vault lock, and say
           whether storage is actually freed now or only after the retention.
        3. Get an explicit confirmation naming the date of the point, not a bare
           "yes".
        4. Delete, then re-list to confirm it is gone and report how many points
           remain.
        """
        require_write(self.allow_write)
        validate_id(point_id, "point_id")
        await self.client.delete(f"/v1/backup-instance-points/{point_id}", region=region)
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=point_id,
            action="restore point deleted",
            detail=(
                "That moment in time can no longer be restored. If the destination has "
                "soft delete enabled the storage is only freed after its retention "
                "window elapses."
            ),
        )
