"""The vServer projection of the backup API, plus volume usage.

vBackup grew out of vServer and still exposes a second, vServer-flavoured copy
of the backup-server endpoints under ``/v1/vserver/**``. They are not
duplicates: this family alone reports a restore point's per-volume detail (boot
disk, volume type, disk order) and the image the captured instance was built
from — neither of which the generic family carries.

Three things make this family behave unlike the rest of the API:

- **It renames every field.** ``backupInstanceId`` for ``id``,
  ``backupInstanceName`` for ``name``, ``backupDestination`` for
  ``destination``, and ``protectedVolumes`` is a COUNT where the generic family
  has a list. Status, the schedule flag and the policy are absent entirely, so
  these tools return their own models (``models/vserver.py``) rather than
  pretending to be the generic ones.
- **``projectId`` is effectively required.** ``GET /v1/vserver/backup-instances``
  without it answers ``200`` with an EMPTY array rather than an error, so a tool
  that omits it reports "nothing here" on an account full of backups.
- **The three by-id detail endpoints are IAM-gated.** They answer
  ``403 IAM_PERMISSION_DENIED`` for a caller whose policy does not grant them,
  while the sibling list endpoints work. Each docstring says so, so a 403 is not
  mistaken for "nothing there".
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
from greennode.vbackup_mcp_server.guards import require_write
from greennode.vbackup_mcp_server.models import (
    BackupVolumePointItem,
    BackupVolumePointListData,
    CreateVserverBackupServersDto,
    VolumeUsageItem,
    VolumeUsageListData,
    VolumeUsageQueryDto,
    VserverBackupServerItem,
    VserverBackupServerListData,
    VserverBackupServerPointItem,
    VserverBackupServerPointListData,
    WriteResult,
    missing_ids,
)
from greennode.vbackup_mcp_server.paging import as_list, unwrap
from greennode.vbackup_mcp_server.tool_annotations import READ, WRITE
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field


class VserverHandler:
    """Register and serve the vServer-projection and volume-usage MCP tools."""

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

        self.mcp.tool(name="list_vserver_backup_servers", annotations=READ)(
            self.list_vserver_backup_servers
        )
        self.mcp.tool(name="get_vserver_backup_server", annotations=READ)(
            self.get_vserver_backup_server
        )
        self.mcp.tool(name="list_vserver_backup_server_points", annotations=READ)(
            self.list_vserver_backup_server_points
        )
        self.mcp.tool(name="get_vserver_backup_server_point", annotations=READ)(
            self.get_vserver_backup_server_point
        )
        self.mcp.tool(name="list_vserver_backup_volume_points", annotations=READ)(
            self.list_vserver_backup_volume_points
        )
        self.mcp.tool(name="get_vserver_backup_volume_point", annotations=READ)(
            self.get_vserver_backup_volume_point
        )
        self.mcp.tool(name="list_volume_usage", annotations=READ)(self.list_volume_usage)

        if self.allow_write:
            self.mcp.tool(name="create_vserver_backup_servers", annotations=WRITE)(
                self.create_vserver_backup_servers
            )

    async def list_vserver_backup_servers(
        self,
        project_id: str = Field(
            ...,
            description=(
                "Project ID — REQUIRED here. Without it the API answers 200 with an "
                "empty array instead of an error. Read it off any resource from "
                "list_backup_servers or list_backup_policies."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        backend_id: str | None = Field(
            None, description="Filter by backend ID from list_backends."
        ),
    ) -> VserverBackupServerListData:
        """List backup servers through the vServer projection of the API.

        Returns {region, project_id, total, backup_servers[{id, name,
        server_id, protected_volume_count, destination_id, destination_name,
        vault{...}, latest_record, created_at}]}.

        This is a DIFFERENT shape from list_backup_servers, not the same data
        under another URL. It does not carry `status`, `backup_enabled`,
        `server_deleted`, the policy, or the per-disk list — `protected_volume_count`
        is only a count. For any of those, use `list_backup_servers`, which is
        also the better everyday tool: it needs no project id and filters by
        server.

        Reach for this one when you want the vServer-side view — the vault
        behind the destination is reported inline here.

        An empty result with a project id supplied means the project genuinely
        has no backup servers; an empty result without one means the parameter
        was missing, since the API answers 200 with an empty array rather than
        an error.
        """
        validate_id(project_id, "project_id")
        if backend_id:
            validate_id(backend_id, "backend_id")

        params = {"projectId": project_id}
        if backend_id:
            params["backendId"] = backend_id

        raw = await self.client.get("/v1/vserver/backup-instances", region=region, params=params)
        items = [VserverBackupServerItem.from_api(i) for i in as_list(raw) if isinstance(i, dict)]
        return VserverBackupServerListData(
            region=region or self.config.default_region,
            project_id=project_id,
            total=len(items),
            backup_servers=items,
        )

    async def get_vserver_backup_server(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VserverBackupServerItem:
        """Get one backup server through the vServer projection.

        Returns the projection shape — id, name, server_id,
        protected_volume_count, destination and vault. NOT the generic shape:
        status, the schedule flag and the policy are absent here.

        IAM-GATED: this endpoint answers 403 IAM_PERMISSION_DENIED for callers
        whose policy does not include it, even though the sibling list endpoints
        work. If you get a 403, fall back to `get_backup_server`, which serves a
        richer object from the generic family, and tell the user the grant is
        missing rather than reporting the server as absent.

        Its exact payload has NOT been verified against a live gateway — the
        account used for development lacks the grant — so treat any field that
        comes back empty as unconfirmed rather than as a real empty value.
        """
        validate_id(backup_server_id, "backup_server_id")
        data = await self.client.get(
            f"/v1/vserver/backup-instances/{backup_server_id}", region=region
        )
        return VserverBackupServerItem.from_api(unwrap(data))

    async def list_vserver_backup_server_points(
        self,
        backup_server_id: str = Field(
            ..., description="Backup server ID (`bk-ins-...`) from list_backup_servers."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VserverBackupServerPointListData:
        """List a backup server's restore points through the vServer projection.

        Returns {region, backup_server_id, total, points[{id, snapshot_time,
        size_gb, used_gb, server_info{name, image_id, image_type,
        image_version}, vault{...}}]}.

        The projection is the ONLY family reporting `server_info` — the image
        the captured instance was built from. That is what tells a user whether
        a restore point still matches the OS they run today, so use this tool
        when they ask what a point would actually restore.

        It does NOT carry `status`, `finish_time` or the policy snapshot; for
        those use `list_backup_server_points`.
        """
        validate_id(backup_server_id, "backup_server_id")
        raw = await self.client.get(
            f"/v1/vserver/backup-instances/{backup_server_id}/backup-instance-points",
            region=region,
        )
        items = [
            VserverBackupServerPointItem.from_api(p) for p in as_list(raw) if isinstance(p, dict)
        ]
        return VserverBackupServerPointListData(
            region=region or self.config.default_region,
            backup_server_id=backup_server_id,
            total=len(items),
            points=items,
        )

    async def get_vserver_backup_server_point(
        self,
        point_id: str = Field(
            ..., description="Restore point ID (`bk-ins-pt-...`) from list_backup_server_points."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VserverBackupServerPointItem:
        """Get one restore point by id, through the vServer projection.

        Returns {id, backup_server_id, snapshot_time, size_gb, used_gb,
        server_info{...}, vault{...}}.

        IAM-GATED: answers 403 IAM_PERMISSION_DENIED for callers without the
        grant. On a 403, fall back to list_vserver_backup_server_points, which
        returns the same points with the same fields, and report the missing
        permission instead of an absent point.

        Its exact payload has NOT been verified against a live gateway — the
        account used for development lacks the grant.
        """
        validate_id(point_id, "point_id")
        data = await self.client.get(
            f"/v1/vserver/backup-instance-points/{point_id}", region=region
        )
        return VserverBackupServerPointItem.from_api(unwrap(data))

    async def list_vserver_backup_volume_points(
        self,
        point_id: str = Field(
            ..., description="Restore point ID (`bk-ins-pt-...`) from list_backup_server_points."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupVolumePointListData:
        """List the per-volume slices inside one restore point.

        Returns {region, backup_server_point_id, total, volume_points[{id, name,
        bootable, boot_index, volume_type_id, size_gb}]}.

        This is the tool that answers "what is actually inside this restore
        point?" — which disks were captured, which one was the boot disk
        (`bootable` / `boot_index=0`) and how large each was. Nothing in the
        generic endpoint family reports that.

        Use it before a restore is discussed, so the user can see whether the
        disk they care about is in the point at all. A disk excluded from the
        run (`backup_enabled=false` in list_backup_server_volumes) will be
        missing here.
        """
        validate_id(point_id, "point_id")
        raw = await self.client.get(
            f"/v1/vserver/backup-instance-points/{point_id}/backup-volume-points",
            region=region,
        )
        items = [BackupVolumePointItem.from_api(v) for v in as_list(raw) if isinstance(v, dict)]
        return BackupVolumePointListData(
            region=region or self.config.default_region,
            backup_server_point_id=point_id,
            total=len(items),
            volume_points=items,
        )

    async def get_vserver_backup_volume_point(
        self,
        volume_point_id: str = Field(
            ...,
            description="Volume point ID (`bk-vol-pt-...`) from list_vserver_backup_volume_points.",
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupVolumePointItem:
        """Get one volume point by id.

        Returns one disk's slice of a restore point.

        IAM-GATED: answers 403 IAM_PERMISSION_DENIED for callers without the
        grant. On a 403, fall back to list_vserver_backup_volume_points, which
        returns every slice of the parent point with the same fields.
        """
        validate_id(volume_point_id, "volume_point_id")
        data = await self.client.get(
            f"/v1/vserver/backup-volume-points/{volume_point_id}", region=region
        )
        return BackupVolumePointItem.from_api(unwrap(data))

    async def list_volume_usage(
        self,
        body: VolumeUsageQueryDto = Field(..., description="Which volumes to measure."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeUsageListData:
        """Measure the current size and used space of vServer volumes.

        Returns {region, total, volumes[{volume_id, size_gb, used_gb}],
        missing_volume_ids[]}.

        A read expressed as a POST — it changes nothing. Use it to estimate what
        a backup will transfer and store before creating a backup server:
        `used_gb`, not `size_gb`, is what a run actually moves.

        `backendId` and `projectId` are BOTH required; the API answers
        400 "Missing field" without them.

        A volume whose vServer instance has been deleted no longer exists and
        comes back in `missing_volume_ids` — the API answers 404 for the whole
        request when such a volume is included, so this tool reports which ids
        could not be measured rather than failing the batch silently. Its
        backups still exist and are still billed; measure those through the
        restore points instead.
        """
        payload = body.model_dump()
        raw = await self.client.post("/v1/volume-usage", region=region, json=payload)
        items_raw = [v for v in as_list(raw) if isinstance(v, dict)]
        return VolumeUsageListData(
            region=region or self.config.default_region,
            total=len(items_raw),
            volumes=[VolumeUsageItem.from_api(v) for v in items_raw],
            missing_volume_ids=missing_ids(body.volumeIds, items_raw),
        )

    async def create_vserver_backup_servers(
        self,
        body: CreateVserverBackupServersDto = Field(
            ..., description="The vServer instances to protect."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Protect vServer instances using the platform's default policy and destination.

        Returns {region, resource_id, action, succeeded, detail}. The API
        answers with no body.

        ## Requirements
        - `--allow-write` must be enabled.
        - This is the SHORTCUT create: it takes instance ids only and lets the
          platform choose the policy, the destination and which disks are
          covered. Use `create_backup_server` whenever the user cares about any
          of those — which is most of the time.
        - `projectId` is required, and every instance must already be in a state
          get_configuration.allowed_backup_server_status permits.
        - Instances that are already protected must not be resubmitted — check
          list_protected_servers first.

        ## Workflow
        1. list_protected_servers — drop any instance already covered.
        2. Tell the user which policy and destination the platform will pick
           (the ones flagged `is_default` in list_backup_policies and
           list_backup_destinations) and confirm that is acceptable. If not, use
           create_backup_server instead.
        3. Create, then verify with list_backup_servers and report the new ids
           and the disks that ended up covered.
        """
        require_write(self.allow_write)
        validate_id(body.projectId, "projectId")
        for server_id in body.serverIds:
            validate_id(server_id, "serverIds entry")
        await self.client.post(
            "/v1/vserver/backup-instances", region=region, json=body.model_dump()
        )
        self.cache.invalidate("list_backup_servers")
        self.cache.invalidate("list_protected_servers")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=", ".join(body.serverIds),
            action="created",
            detail=(
                "Created with the platform's default policy and destination. Verify "
                "with list_backup_servers and check list_backup_server_volumes to "
                "report which disks are actually covered."
            ),
        )
