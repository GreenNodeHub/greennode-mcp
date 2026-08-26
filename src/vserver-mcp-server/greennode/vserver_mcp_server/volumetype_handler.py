"""Volume-type (disk IOPS tier) discovery for the vServer MCP server.

Volume types are zone-scoped and reached through a two-step call, both internal
to this handler: ``GET /v1/{pid}/volume_type_zones?zoneId=`` yields the disk
kinds offered in the zone (SSD, NVMe), and ``GET /v1/{pid}/{id}/volume_types``
yields that kind's IOPS tiers — which is what the user actually picks.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.models import VolumeTypeItem, VolumeTypeListData
from greennode.vserver_mcp_server.paging import as_list, unwrap, unwrap_one
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import READ
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field
from typing import Literal


class VolumeTypeHandler:
    """Register and serve volume-type discovery MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        self.mcp.tool(name="list_volume_types", annotations=READ)(self.list_volume_types)
        self.mcp.tool(name="get_volume_type", annotations=READ)(self.get_volume_type)
        self.mcp.tool(name="get_default_volume_type", annotations=READ)(
            self.get_default_volume_type
        )

    async def list_volume_types(
        self,
        zone_id: str = Field(
            ...,
            description="Availability zone from list_zones, e.g. 'HCM03-1C'. Disk kinds "
            "and their IOPS tiers differ per zone.",
        ),
        disk_type: Literal["AUTO", "NVMe", "SSD"] = Field(
            "AUTO",
            description=(
                "Disk kind: AUTO (default) prefers NVMe and falls back to SSD when the "
                "zone offers no NVMe. Pass SSD or NVMe only when the user asks for one "
                "explicitly. The response echoes what was resolved."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> VolumeTypeListData:
        """List the volume types (disk IOPS tiers) available in an availability zone.

        Returns {region, zone_id, disk_type, available_disk_types,
        volume_types[{id, name, iops, throughput_mbps, min_size_gb,
        max_size_gb}]}. `disk_type` echoes the resolved kind and
        `available_disk_types` shows every kind the zone offers, so a user who
        wanted NVMe can see when only SSD exists. Users pick by **IOPS**.

        ## Workflow
        - Needed by create_server (as `rootDiskTypeId`, and `dataDiskTypeId` for
          a data disk) and by create_volume / resize_volume (as `volumeTypeId`).
        - Present the IOPS tiers, mention the resolved disk kind, and let the
          user choose. IMPORTANT: do NOT pick an IOPS tier silently.
        - Respect `min_size_gb` / `max_size_gb` when asking for a disk size; a
          root disk additionally has a 20 GiB minimum.
        """
        validate_id(zone_id, "zone_id")
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> VolumeTypeListData:
            zones_raw = await self.client.get(
                f"/v1/{pid}/volume_type_zones", region=region, params={"zoneId": zone_id}
            )
            entries = [z for z in as_list(zones_raw, "volumeTypeZones") if z.get("id")]
            by_name = {str(z.get("name", "")).upper(): z for z in entries}
            available = [str(z.get("name", "")) for z in entries]

            if disk_type == "AUTO":
                chosen = by_name.get("NVME") or by_name.get("SSD")
            else:
                chosen = by_name.get(disk_type.upper())

            if chosen is None:
                return VolumeTypeListData(
                    region=resolved_region,
                    zone_id=zone_id,
                    disk_type="",
                    available_disk_types=available,
                    volume_types=[],
                )

            tiers_raw = await self.client.get(
                f"/v1/{pid}/{chosen['id']}/volume_types", region=region
            )
            tiers = [VolumeTypeItem.from_api(v) for v in as_list(tiers_raw, "volumeTypes")]
            return VolumeTypeListData(
                region=resolved_region,
                zone_id=zone_id,
                disk_type=str(chosen.get("name") or ""),
                available_disk_types=available,
                volume_types=sorted(tiers, key=lambda t: t.iops),
            )

        key = ("list_volume_types", resolved_region, pid, zone_id, disk_type)
        return await self.cache.get_or_fetch("list_volume_types", key, fetch, refresh)

    async def get_volume_type(
        self,
        volume_type_id: str = Field(..., description="Volume type ID from list_volume_types."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeTypeItem:
        """Get one volume type (IOPS tier) by id.

        Use it to resolve the `volume_type_id` a volume reports into a readable
        tier with its IOPS, throughput and size limits — get_volume returns only
        the id.
        """
        validate_id(volume_type_id, "volume_type_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v1/{pid}/volume_types/{volume_type_id}", region=region)
        return VolumeTypeItem.from_api(unwrap_one(data))

    async def get_default_volume_type(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VolumeTypeItem:
        """Get the project's default volume type.

        This is the tier vServer falls back to when none is given. Knowing it
        matters because it is **not** necessarily the cheapest or the fastest —
        present it as a starting point and still let the user choose from
        list_volume_types.

        The default is project-wide, not per zone, so the tier it names may not
        exist in the zone being built in. Check it against
        list_volume_types(zone_id) and pick from that list when it is absent —
        creating with a foreign type id fails.
        """
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v1/{pid}/volume_default_id", region=region)
        payload = unwrap(data) or {}
        type_id = payload.get("volumeTypeId") or ""
        if not type_id:
            return VolumeTypeItem.from_api({})
        detail = await self.client.get(f"/v1/{pid}/volume_types/{type_id}", region=region)
        return VolumeTypeItem.from_api(unwrap_one(detail) or {"id": type_id})
