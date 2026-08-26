"""Block-storage volume management for the vServer MCP server.

Mirrors the `grn vserver volume` command group, plus the attach/detach pair the
CLI does not expose but which every real workflow needs.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    ChangeVolumeTypeDto,
    CreateVolumeDto,
    DeletePersistentVolumeDto,
    PersistentVolumeItem,
    PersistentVolumeListData,
    RenameVolumeDto,
    ResizeVolumeDto,
    VolumeHistoryItem,
    VolumeHistoryListData,
    VolumeItem,
    VolumeListData,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class VolumeHandler:
    """Register and serve volume MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_volumes", annotations=READ)(self.list_volumes)
        self.mcp.tool(name="get_volume", annotations=READ)(self.get_volume)
        self.mcp.tool(name="list_server_volumes", annotations=READ)(self.list_server_volumes)
        self.mcp.tool(name="get_server_boot_volume", annotations=READ)(self.get_server_boot_volume)
        self.mcp.tool(name="list_volume_history", annotations=READ)(self.list_volume_history)
        self.mcp.tool(name="list_persistent_volumes", annotations=READ)(
            self.list_persistent_volumes
        )

        if self.allow_write:
            self.mcp.tool(name="create_volume", annotations=WRITE)(self.create_volume)
            self.mcp.tool(name="resize_volume", annotations=WRITE)(self.resize_volume)
            self.mcp.tool(name="update_volume_type", annotations=WRITE)(self.update_volume_type)
            self.mcp.tool(name="delete_persistent_volume", annotations=DESTRUCTIVE)(
                self.delete_persistent_volume
            )
            self.mcp.tool(name="rename_volume", annotations=WRITE)(self.rename_volume)
            self.mcp.tool(name="attach_volume", annotations=WRITE)(self.attach_volume)
            self.mcp.tool(name="detach_volume", annotations=DESTRUCTIVE)(self.detach_volume)
            self.mcp.tool(name="delete_volume", annotations=DESTRUCTIVE)(self.delete_volume)

    async def list_volumes(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the volume name."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeListData:
        """List the block-storage volumes in the project.

        Returns {region, volumes[{id, name, size_gb, status, volume_type_id,
        zone_id, server_id, bootable, multiattach, created_at}]}.

        `status` is IN-USE when the volume is attached and AVAILABLE when it is
        free. A volume with `bootable=true` is a server's root disk — detaching
        or deleting it breaks that server.

        ## Workflow
        - A volume with no `server_id` is unattached and still billed; point
          that out when the user is reviewing costs.
        """
        pid = await require_project_id(self.config, self.client, region)
        params = {"name": name_filter} if name_filter else None
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/volumes", region=region, params=params
        )
        return VolumeListData(
            region=region or self.config.default_region,
            volumes=[VolumeItem.from_api(v) for v in raw],
        )

    async def get_volume(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Get one volume by id.

        Read `volume_type_id` here before resize_volume, which requires the
        target volume type even when only the size changes.
        """
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/volumes/{volume_id}", region=region)
        return VolumeItem.from_api(unwrap(data) or {})

    async def list_server_volumes(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeListData:
        """List the volumes attached to one server.

        ## Workflow
        - Call this before delete_server so the user can see exactly which
          disks `delete_all_volumes=true` would destroy.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/volumes/servers/{server_id}", region=region)
        return VolumeListData(
            region=region or self.config.default_region,
            volumes=[VolumeItem.from_api(v) for v in as_list(data)],
        )

    async def get_server_boot_volume(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Get the root (boot) volume of a server.

        list_server_volumes returns every disk; this returns only the one the
        operating system boots from — the disk that must not be detached and
        whose size decides how much room the OS itself has.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/volumes/servers/{server_id}/boot", region=region)
        volumes = as_list(data)
        return VolumeItem.from_api(volumes[0] if volumes else unwrap(data) or {})

    async def list_volume_history(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeHistoryListData:
        """List the size and IOPS changes a volume has been through.

        Returns {region, volume_id, history[{type, size_gb, iops, started_at}]},
        one entry per CREATE or RESIZE.

        ## Workflow
        - Use it to answer "when did this disk grow and what did it cost
          before?" — resize_volume alone leaves no trace in get_volume.
        """
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/volumes/{volume_id}/history", region=region)
        return VolumeHistoryListData(
            region=region or self.config.default_region,
            volume_id=volume_id,
            history=[VolumeHistoryItem.from_api(h) for h in as_list(data)],
        )

    async def list_persistent_volumes(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PersistentVolumeListData:
        """List the Kubernetes persistent volumes backed by vServer storage.

        Returns {region, persistent_volumes[{id, name, status, size_gb,
        cluster_id, server_id, created_at}]}. These are volumes a VKS cluster
        provisioned, not volumes created here.

        ## Workflow
        - The usual reason to look: a deleted cluster left PVs behind, and they
          are still billed. Cross-check `cluster_id` against the clusters that
          still exist before deleting anything.
        """
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(self.client, f"/v2/{pid}/persistent-volumes", region=region)
        return PersistentVolumeListData(
            region=region or self.config.default_region,
            persistent_volumes=[PersistentVolumeItem.from_api(p) for p in raw],
        )

    async def create_volume(
        self,
        body: CreateVolumeDto = Field(..., description="Volume to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Create a block-storage volume.

        ## Requirements
        - Requires `--allow-write`. A volume is **billable** from creation, even
          while unattached.
        - `volumeTypeId` comes from list_volume_types for the target zone, and
          `size` must fall within that type's `min_size_gb`/`max_size_gb`.
        - `zoneId` must be the zone of the server you intend to attach it to —
          a volume can never cross zones.

        ## Workflow
        - Ask the user for the size and IOPS tier; do not pick either silently.
        - The volume starts unattached; use attach_volume to connect it, then
          partition and mount it inside the guest OS.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/volumes", region=region, json=body.model_dump(exclude_none=True)
        )
        return VolumeItem.from_api(unwrap(data) or {})

    async def resize_volume(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        body: ResizeVolumeDto = Field(..., description="New size and volume type."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Grow a volume or change its IOPS tier.

        ## Requirements
        - Requires `--allow-write`. Growing a volume **raises its cost**.
        - A volume can only grow: `newSize` must be greater than or equal to the
          current size. Shrinking is impossible.
        - `newVolumeTypeId` is required on every call. Read the volume's current
          `volume_type_id` via get_volume and pass it back when only the size
          should change.

        ## Workflow
        - Show the user the before and after size and tier, and confirm.
        - Growing the volume does not grow the filesystem inside the guest OS —
          tell the user they still have to extend the partition themselves.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/resize",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return VolumeItem.from_api(unwrap(data) or {})

    async def update_volume_type(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        body: ChangeVolumeTypeDto = Field(..., description="Target volume type."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Move a volume to a different IOPS tier (volume type).

        ## Requirements
        - Requires `--allow-write`. A higher tier **costs more per GiB**.
        - `volumeTypeId` must come from list_volume_types **for the volume's own
          zone**; a type from another zone is rejected.
        - Changing tier can mean migrating the data to different hardware. When
          the API asks for it, `confirmMigrate=true` acknowledges that — the
          volume's performance is degraded while it runs, and on a large disk
          that is hours, not seconds.
        - resize_volume also accepts a new type when the size changes; use this
          tool when **only** the tier changes.

        ## Workflow
        - Show the user the current tier (get_volume) and the target tier with
          its IOPS and throughput, and confirm before calling.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/change-device-type",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return VolumeItem.from_api(unwrap(data) or {})

    async def delete_persistent_volume(
        self,
        persistent_volume_id: str = Field(
            ..., description="Volume ID from list_persistent_volumes."
        ),
        body: DeletePersistentVolumeDto = Field(
            default_factory=DeletePersistentVolumeDto, description="Deletion options."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a Kubernetes persistent volume. This destroys its data.

        ## Requirements
        - Requires `--allow-write`.
        - The right way to remove a PV is normally through Kubernetes, so the
          cluster's own state stays consistent. Deleting it here goes behind the
          cluster's back.
        - `forceDelete=true` deletes it even while the cluster still references
          the volume, which leaves a dangling PV object and pods that can never
          bind. Leave it off unless the user asked for it.

        ## Workflow
        - Confirm the owning `cluster_id` is really gone before deleting, and
          get explicit confirmation — this is irreversible.
        """
        require_write(self.allow_write)
        validate_id(persistent_volume_id, "persistent_volume_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["persistentVolumeId"] = persistent_volume_id
        await self.client.delete_with_body(
            f"/v2/{pid}/persistent-volumes/{persistent_volume_id}", region=region, json=payload
        )
        return f"Persistent volume {persistent_volume_id} deleted."

    async def rename_volume(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        body: RenameVolumeDto = Field(..., description="New name."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Rename a volume.

        ## Requirements
        - Requires `--allow-write`.
        - Cosmetic only: nothing inside the guest OS changes.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/rename",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return VolumeItem.from_api(unwrap(data) or {})

    async def attach_volume(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Attach a volume to a server.

        ## Requirements
        - Requires `--allow-write`.
        - The volume and the server must be in the **same availability zone**.
        - The volume must be AVAILABLE, unless it was created with
          `multiAttach=true`.

        ## Workflow
        - After attaching, the disk still has to be partitioned, formatted and
          mounted inside the guest OS — say so, the user will not see it
          otherwise.
        - The volume reports ATTACHING first; verify with get_volume that
          `status` became IN-USE.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/servers/{server_id}/attach", region=region, json={}
        )
        return VolumeItem.from_api(unwrap(data) or {})

    async def detach_volume(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        server_id: str = Field(..., description="Server the volume is attached to."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeItem:
        """Detach a volume from a server.

        ## Requirements
        - Requires `--allow-write`.
        - Unmount the filesystem inside the guest OS **first**; pulling a
          mounted disk risks data corruption.
        - A bootable root volume cannot be detached from its server.

        ## Workflow
        - Confirm with the user that the volume is unmounted, then detach.
        - The volume returns to AVAILABLE and keeps costing money until it is
          deleted.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/volumes/{volume_id}/servers/{server_id}/detach", region=region, json={}
        )
        return VolumeItem.from_api(unwrap(data) or {})

    async def delete_volume(
        self,
        volume_id: str = Field(..., description="Volume ID from list_volumes."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a volume. This is irreversible and destroys its data.

        ## Requirements
        - Requires `--allow-write`.
        - The volume must be detached first; an IN-USE volume is rejected.

        ## Workflow
        - Show the user the volume's id, name, size and last attachment, and get
          explicit confirmation. There is no undo and no recycle bin.
        """
        require_write(self.allow_write)
        validate_id(volume_id, "volume_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/volumes/{volume_id}", region=region)
        return f"Volume {volume_id} deleted."
