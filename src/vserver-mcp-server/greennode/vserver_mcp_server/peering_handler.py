"""VPC peering for the vServer MCP server.

A peering joins two VPCs so instances reach each other over private addresses.
vServer does **not** expose a create endpoint — peerings are provisioned by
GreenNode support on request — so this handler is read plus delete only.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import PeeringItem, PeeringListData
from greennode.vserver_mcp_server.paging import fetch_paged_items
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class PeeringHandler:
    """Register and serve VPC-peering MCP tools."""

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

        self.mcp.tool(name="list_peerings", annotations=READ)(self.list_peerings)

        if self.allow_write:
            self.mcp.tool(name="delete_peering", annotations=DESTRUCTIVE)(self.delete_peering)

    async def list_peerings(
        self,
        name_filter: str = Field("", description="Optional substring match on the peering name."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> PeeringListData:
        """List the VPC peering connections in the project.

        Returns {region, peerings[{id, name, status, from_vpc_id, from_cidr,
        to_vpc_id, to_cidr, created_at}]}.

        ## Workflow
        - A peering only carries traffic once both sides have a route for the
          other's CIDR — check list_route_tables when a peering is ACTIVE but
          instances still cannot reach each other.
        - vServer offers **no API to create a peering**: the user has to request
          one from GreenNode support (support@greennode.ai). Say so rather than
          looking for a create tool.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[PeeringItem]:
            raw = await fetch_paged_items(
                self.client, f"/v2/{pid}/peering", region=region, name=name_filter or ""
            )
            return [PeeringItem.from_api(p) for p in raw]

        key = ("list_peerings", resolved_region, pid, name_filter)
        peerings = await self.cache.get_or_fetch("list_peerings", key, fetch, refresh)
        return PeeringListData(region=resolved_region, peerings=peerings)

    async def delete_peering(
        self,
        peering_id: str = Field(..., description="Peering ID from list_peerings."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a VPC peering connection. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Private traffic between the two VPCs stops the moment the peering is
          gone, and re-creating it needs a **support request** — there is no
          create API.

        ## Workflow
        - Show the user both VPCs and their CIDRs from list_peerings, warn that
          re-creation goes through GreenNode support, and get explicit
          confirmation.
        """
        require_write(self.allow_write)
        validate_id(peering_id, "peering_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/peering/{peering_id}", region=region)
        self.cache.invalidate("list_peerings")
        return f"Peering {peering_id} deleted."
