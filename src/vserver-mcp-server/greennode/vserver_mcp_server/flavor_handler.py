"""Flavor (instance size) discovery for the vServer MCP server.

The vServer flavor catalogue is a two-level taxonomy: a **family**
(``general-purpose``, ``gpu``) crossed with a **platform code** (``code-s``,
``code-a40``, ``code-h100`` …). ``list_flavors`` needs both, so agents call
``list_flavor_families`` and ``list_flavor_codes`` first — mirroring the
`grn vserver flavor list-families / list-codes / list` command trio.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.models import (
    FlavorCodeItem,
    FlavorCodeListData,
    FlavorFamilyItem,
    FlavorFamilyListData,
    FlavorItem,
    FlavorListData,
)
from greennode.vserver_mcp_server.paging import as_list, unwrap_one
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import READ
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


def _flavor_available(flavor: dict) -> bool:
    """A flavor is usable only if it is not sold out and has remaining capacity."""
    if flavor.get("isSoldOut"):
        return False
    remaining = flavor.get("remainingVms")
    return remaining is None or remaining > 0


class FlavorHandler:
    """Register and serve flavor-discovery MCP tools."""

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

        self.mcp.tool(name="list_flavor_families", annotations=READ)(self.list_flavor_families)
        self.mcp.tool(name="list_flavor_codes", annotations=READ)(self.list_flavor_codes)
        self.mcp.tool(name="list_flavors", annotations=READ)(self.list_flavors)
        self.mcp.tool(name="get_flavor", annotations=READ)(self.get_flavor)

    async def list_flavor_families(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> FlavorFamilyListData:
        """List the instance families available in a region.

        Returns {region, families[{key, name, types}]} — e.g. `general-purpose`
        (CPU instances) and `gpu` (GPU instances). `types` lists the sub-groups
        inside a family (standard, general, high-cpu, high-memory); they are
        informational, list_flavors takes the **family key**, not a type.

        ## Workflow
        - Step 1 of picking a flavor. Pass the chosen `key` as `family` to
          list_flavors, together with a `code` from list_flavor_codes.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> FlavorFamilyListData:
            raw = await self.client.get(f"/v1/{pid}/flavor_zones/families", region=region)
            return FlavorFamilyListData(
                region=resolved_region,
                families=[FlavorFamilyItem.from_api(f) for f in as_list(raw)],
            )

        return await self.cache.get_or_fetch(
            "list_flavor_families", ("list_flavor_families", resolved_region, pid), fetch, refresh
        )

    async def list_flavor_codes(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> FlavorCodeListData:
        """List the CPU/GPU platform codes available in a region.

        Returns {region, codes[{key, name, description}]}. A code identifies the
        hardware platform — `code-s`/`code-s2` (Intel), `code-a` (AMD EPYC),
        `code-a40`/`code-h100`/`code-rtx4090` (GPU platforms).

        ## Workflow
        - Step 2 of picking a flavor. Pass the chosen `key` as `code` to
          list_flavors. GPU codes only return flavors under the `gpu` family.
        - Not every family x code combination exists in every zone — an empty
          list_flavors result means that combination is unavailable there, not
          that the ids are wrong.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> FlavorCodeListData:
            raw = await self.client.get(f"/v1/{pid}/flavor_zones/codes", region=region)
            return FlavorCodeListData(
                region=resolved_region,
                codes=[FlavorCodeItem.from_api(c) for c in as_list(raw)],
            )

        return await self.cache.get_or_fetch(
            "list_flavor_codes", ("list_flavor_codes", resolved_region, pid), fetch, refresh
        )

    async def list_flavors(
        self,
        family: str = Field(
            ...,
            description="Instance family key from list_flavor_families, e.g. 'general-purpose'.",
        ),
        code: str = Field(
            ..., description="CPU/GPU platform code key from list_flavor_codes, e.g. 'code-s'."
        ),
        zone_id: str | None = Field(
            None,
            description=(
                "Availability zone from list_zones (e.g. 'HCM03-1C'). Strongly "
                "recommended: capacity is per-zone, so an unfiltered list can offer "
                "flavors that cannot actually be launched in the target zone."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> FlavorListData:
        """List the flavors (vCPU/RAM sizes) of one family x platform code.

        Returns {region, zone_id, family, code, flavors[{id, name, vcpu, ram_gb,
        gpu, gpu_memory_gb, bandwidth, group, remaining_vms,
        supported_image_types}]}. Sold-out flavors and those with no remaining
        capacity are excluded.

        ## Workflow
        - Call list_flavor_families and list_flavor_codes first, then this with
          the zone the user picked in list_zones.
        - Present the options by vCPU/RAM and let the user choose.
          IMPORTANT: do NOT pick a flavor silently.
        - Check `supported_image_types` against the chosen image's `image_type`
          from list_images — the API rejects an incompatible pair at create time.
        - Use the chosen `id` as `flavorId` in create_server or resize_server.
        """
        validate_id(family, "family")
        validate_id(code, "code")
        if zone_id is not None:
            validate_id(zone_id, "zone_id")

        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> FlavorListData:
            params = {"zoneId": zone_id} if zone_id else None
            raw = await self.client.get(
                f"/v1/{pid}/flavors/families/{family}/platforms/{code}",
                region=region,
                params=params,
            )
            items = [f for f in as_list(raw) if _flavor_available(f)]
            return FlavorListData(
                region=resolved_region,
                zone_id=zone_id,
                family=family,
                code=code,
                flavors=[FlavorItem.from_api(f) for f in items],
            )

        key = ("list_flavors", resolved_region, pid, family, code, zone_id)
        return await self.cache.get_or_fetch("list_flavors", key, fetch, refresh)

    async def get_flavor(
        self,
        flavor_id: str = Field(..., description="Flavor ID from list_flavors."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> FlavorItem:
        """Get one flavor by id.

        list_flavors needs a family and a platform code; this resolves a flavor
        id on its own — the case when a server already exists and you want to
        know what it is running on, or what resize_server would move it to.

        Read `supported_image_types` here before resize_server: moving a server
        to a flavor that cannot boot its image is rejected.
        """
        validate_id(flavor_id, "flavor_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v1/{pid}/flavors/{flavor_id}", region=region)
        return FlavorItem.from_api(unwrap_one(data))
