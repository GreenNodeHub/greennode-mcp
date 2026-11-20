"""Package and header catalogue served by the vLB gateway itself.

These are the two lookups a creation flow needs before it can build a request:
a ``packageId`` (which is billed, and which decides the load balancer's
capacity) and the set of header names a listener is allowed to insert. Both
change rarely, so both are cached for an hour.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import Region, VlbConfig
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.models import HeaderNameListData, PackageItem, PackageListData
from greennode.vlb_mcp_server.paging import as_list
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


class CatalogueHandler:
    """Register and serve the vLB catalogue MCP tools."""

    def __init__(
        self,
        mcp,
        config: VlbConfig,
        client: VlbClient,
        cache: DiscoveryCache,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        self.mcp.tool(name="list_load_balancer_packages", annotations=READ)(
            self.list_load_balancer_packages
        )
        self.mcp.tool(name="list_listener_headers", annotations=READ)(self.list_listener_headers)

    async def list_load_balancer_packages(
        self,
        scope: Literal["active", "all"] = Field(
            "active",
            description=(
                "'active' lists the packages that can be bought right now (default); "
                "'all' also includes packages no longer sold, which is useful only for "
                "explaining an existing load balancer's package."
            ),
        ),
        zone_id: str | None = Field(
            None,
            description=(
                "The availability zone to list packages FOR (e.g. 'HCM03-1A'). Pass the "
                "same zone you will create the load balancer in: package ids are "
                "zone-specific and share nothing between zones. Omitting this does not "
                "list every package — it lists the platform's default zone. Ignored when "
                "scope='all'. Get zone ids from list_zones."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vLB."),
    ) -> PackageListData:
        """List the load balancer packages available to this project.

        Returns {region, scope, zone_id, packages[{id, name, connection_number,
        data_transfer, lb_type, ...}]}. The `id` is what `create_load_balancer`
        and `resize_load_balancer` take as `packageId`.

        ## Workflow
        - Call this before `create_load_balancer`; a package is required and
          decides both capacity and cost.
        - **Pass the `zone_id` you will actually create in.** Package ids are
          per-zone and disjoint: HCM03-1A offers 58 packages, HCM03-1B offers
          16, and the two sets share *no* ids. Using one zone's id in another
          fails the create with "Invalid package id (<id>)", and nothing in
          this listing hints at it. With `zone_id` omitted the API answers for
          its own default zone — the response's `zone_id` is then empty, which
          means "whatever the platform defaults to", not "all zones".
        - Match `lb_type` to the load balancer `type` you intend to create
          ('Layer 4' or 'Layer 7') — the two are not interchangeable.
        - Use the default scope='active'. A package listed only under
          scope='all' cannot be bought and will fail the create.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region
        resolved_zone = zone_id if scope == "active" else None

        async def fetch() -> PackageListData:
            # /all_packages takes no zoneId; the active catalogue treats it as
            # an optional filter.
            path = (
                f"/v2/{pid}/loadBalancers/packages"
                if scope == "active"
                else f"/v2/{pid}/loadBalancers/all_packages"
            )
            params = {"zoneId": resolved_zone} if resolved_zone else None
            raw = await self.client.get(path, region=region, params=params)
            return PackageListData(
                region=resolved_region,
                scope=scope,
                zone_id=resolved_zone or "",
                packages=[PackageItem.from_api(p) for p in as_list(raw) if isinstance(p, dict)],
            )

        key = ("list_load_balancer_packages", resolved_region, pid, scope, resolved_zone)
        return await self.cache.get_or_fetch("list_load_balancer_packages", key, fetch, refresh)

    async def list_listener_headers(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vLB."),
    ) -> HeaderNameListData:
        """List the header names a listener is allowed to insert.

        Returns {region, headers[str]}. Pass one of these as
        `insertHeaders[].headerName` when creating or updating a listener — the
        gateway rejects names outside this set.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> HeaderNameListData:
            raw = await self.client.get(f"/v2/{pid}/loadBalancers/headers", region=region)
            return HeaderNameListData(
                region=resolved_region,
                headers=[str(h) for h in as_list(raw) if str(h).strip()],
            )

        key = ("list_listener_headers", resolved_region, pid)
        return await self.cache.get_or_fetch("list_listener_headers", key, fetch, refresh)
