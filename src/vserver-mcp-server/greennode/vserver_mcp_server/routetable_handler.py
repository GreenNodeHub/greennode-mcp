"""Route table management for the vServer MCP server.

A route table holds the static routes that steer traffic leaving a VPC towards
somewhere other than the default gateway — a peering, an interconnect, or an
appliance instance. Routes are attached to a VPC, not to a subnet, so one table
governs every subnet inside it.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateRouteTableDto,
    RouteItem,
    RouteListData,
    RouteTableItem,
    RouteTableListData,
    UpdateRouteTableRoutesDto,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_paged_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class RouteTableHandler:
    """Register and serve route-table MCP tools."""

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

        self.mcp.tool(name="list_route_tables", annotations=READ)(self.list_route_tables)
        self.mcp.tool(name="get_route_table", annotations=READ)(self.get_route_table)
        self.mcp.tool(name="list_route_table_routes", annotations=READ)(
            self.list_route_table_routes
        )

        if self.allow_write:
            self.mcp.tool(name="create_route_table", annotations=WRITE)(self.create_route_table)
            self.mcp.tool(name="update_route_table_routes", annotations=WRITE)(
                self.update_route_table_routes
            )
            self.mcp.tool(name="delete_route_table", annotations=DESTRUCTIVE)(
                self.delete_route_table
            )

    async def list_route_tables(
        self,
        name_filter: str = Field(
            "", description="Optional substring match on the route table name."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> RouteTableListData:
        """List the route tables in the project.

        Returns {region, route_tables[{id, name, status, vpc_id, routes[],
        created_at}]}.

        Every VPC gets a route table when it is created, so an untouched project
        still shows one table per VPC with an empty `routes` list — that means
        "default routing only", not "misconfigured".

        ## Workflow
        - Match `vpc_id` against list_vpcs to see which VPC a table steers.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[RouteTableItem]:
            raw = await fetch_paged_items(
                self.client, f"/v2/{pid}/route-table", region=region, name=name_filter or ""
            )
            return [RouteTableItem.from_api(r) for r in raw]

        key = ("list_route_tables", resolved_region, pid, name_filter)
        tables = await self.cache.get_or_fetch("list_route_tables", key, fetch, refresh)
        return RouteTableListData(region=resolved_region, route_tables=tables)

    async def get_route_table(
        self,
        route_table_id: str = Field(..., description="Route table ID from list_route_tables."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> RouteTableItem:
        """Get one route table by id, including its routes.

        Use it to read the current route set before update_route_table_routes,
        which replaces the whole set rather than appending to it.
        """
        validate_id(route_table_id, "route_table_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/route-table/{route_table_id}", region=region)
        return RouteTableItem.from_api(unwrap(data) or {})

    async def list_route_table_routes(
        self,
        route_table_id: str = Field(..., description="Route table ID from list_route_tables."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> RouteListData:
        """List just the static routes of one route table.

        Returns {region, route_table_id, routes[{destination_cidr, target}]}.
        get_route_table returns the same routes alongside the table's metadata;
        this tool is the cheaper call when only the routes matter.
        """
        validate_id(route_table_id, "route_table_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/route-table/route/{route_table_id}", region=region
        )
        return RouteListData(
            region=region or self.config.default_region,
            route_table_id=route_table_id,
            routes=[RouteItem.from_api(r) for r in as_list(data)],
        )

    async def create_route_table(
        self,
        body: CreateRouteTableDto = Field(..., description="Route table to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> RouteTableItem:
        """Create a route table in a VPC.

        ## Requirements
        - Requires `--allow-write`.
        - `name` must be 5-50 characters of letters, digits, `_` and `-` — no
          leading or trailing spaces. The API rejects anything else.
        - `networkId` is a VPC id from list_vpcs; a route table cannot be moved
          to another VPC afterwards.
        - Each route's `destinationCidrBlock` must not overlap the VPC's own
          CIDR — traffic inside the VPC is routed locally and cannot be
          overridden.

        ## Workflow
        - Ask the user which VPC and which destinations to route; do not invent
          a next hop.
        - Routes can be left empty here and added later with
          update_route_table_routes.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/route-table", region=region, json=payload)
        self.cache.invalidate("list_route_tables")
        return RouteTableItem.from_api(unwrap(data) or {})

    async def update_route_table_routes(
        self,
        route_table_id: str = Field(..., description="Route table ID from list_route_tables."),
        body: UpdateRouteTableRoutesDto = Field(
            ..., description="The complete set of routes the table should end up with."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> RouteTableItem:
        """Replace the static routes of a route table.

        ## Requirements
        - Requires `--allow-write`.
        - This is a **full replacement**, not an append: any route missing from
          `routes` is removed. Call get_route_table first and send back the
          routes you want to keep alongside the new ones.
        - Removing a route can black-hole live traffic immediately.

        ## Workflow
        - Show the user the current routes and the proposed routes side by side,
          and get explicit confirmation before calling.
        """
        require_write(self.allow_write)
        validate_id(route_table_id, "route_table_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v2/{pid}/route-table/{route_table_id}/routes", region=region, json=payload
        )
        self.cache.invalidate("list_route_tables")
        return RouteTableItem.from_api(unwrap(data) or {})

    async def delete_route_table(
        self,
        route_table_id: str = Field(..., description="Route table ID from list_route_tables."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a route table. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - A VPC's default route table cannot be deleted.
        - Traffic that relied on the table's routes falls back to default
          routing the moment it is gone.

        ## Workflow
        - Call get_route_table and show the user the routes that will disappear,
          then get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(route_table_id, "route_table_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/route-table/{route_table_id}", region=region)
        self.cache.invalidate("list_route_tables")
        return f"Route table {route_table_id} deleted."
