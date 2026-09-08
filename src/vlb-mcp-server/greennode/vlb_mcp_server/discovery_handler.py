"""Read-only vServer lookups a load balancer decision depends on.

A load balancer is placed on a vServer subnet and its pool members are vServer
private IPs, but vLB's own API exposes neither. Without these four proxies a
``create_load_balancer`` flow dead-ends at "I need a subnetId" unless the user
happens to have ``vserver-mcp-server`` loaded as well.

Strictly reads: this package must never mutate a vServer resource.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import Region, VlbConfig
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.models import (
    ServerItem,
    ServerListData,
    SubnetItem,
    SubnetListData,
    VpcItem,
    VpcListData,
    ZoneItem,
    ZoneListData,
)
from greennode.vlb_mcp_server.paging import as_list
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.tool_annotations import READ
from greennode.vlb_mcp_server.validators import validate_id
from pydantic import Field


class DiscoveryHandler:
    """Register and serve the vServer discovery MCP tools."""

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

        self.mcp.tool(name="list_zones", annotations=READ)(self.list_zones)
        self.mcp.tool(name="list_vpcs", annotations=READ)(self.list_vpcs)
        self.mcp.tool(name="list_subnets", annotations=READ)(self.list_subnets)
        self.mcp.tool(name="list_servers", annotations=READ)(self.list_servers)

    async def list_zones(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch."),
    ) -> ZoneListData:
        """List the availability zones of a region (read from vServer).

        Returns {region, zones[{id, name, enabled}]}. The `id` is what
        `create_load_balancer` takes as `zoneId` and what
        `list_load_balancer_packages` filters on.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> ZoneListData:
            raw = await self.client.get_vserver(f"/v1/{pid}/zones", region=region)
            items = [z for z in as_list(raw) if isinstance(z, dict) and z.get("isEnabled", True)]
            return ZoneListData(
                region=resolved_region, zones=[ZoneItem.from_api(z) for z in items]
            )

        key = ("list_zones", resolved_region, pid)
        return await self.cache.get_or_fetch("list_zones", key, fetch, refresh)

    async def list_vpcs(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch."),
    ) -> VpcListData:
        """List the VPCs of the project (read from vServer).

        Returns {region, vpcs[{id, name, cidr, status}]}. Pass a VPC `id` to
        `list_subnets` to find the subnet a load balancer will sit on.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> VpcListData:
            raw = await self.client.get_vserver(f"/v2/{pid}/networks", region=region)
            return VpcListData(
                region=resolved_region,
                vpcs=[VpcItem.from_api(v) for v in as_list(raw) if isinstance(v, dict)],
            )

        key = ("list_vpcs", resolved_region, pid)
        return await self.cache.get_or_fetch("list_vpcs", key, fetch, refresh)

    async def list_subnets(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch."),
    ) -> SubnetListData:
        """List the subnets of a VPC (read from vServer).

        Returns {region, vpc_id, subnets[{id, name, cidr, status, zone_id}]}.
        The `id` is what `create_load_balancer` takes as `subnetId`, and the
        subnet's `zone_id` pins the load balancer's zone — pick the subnet
        first, then read the zone off it rather than choosing a zone separately.
        """
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> SubnetListData:
            raw = await self.client.get_vserver(
                f"/v2/{pid}/networks/{vpc_id}/subnets", region=region
            )
            return SubnetListData(
                region=resolved_region,
                vpc_id=vpc_id,
                subnets=[SubnetItem.from_api(s) for s in as_list(raw) if isinstance(s, dict)],
            )

        key = ("list_subnets", resolved_region, pid, vpc_id)
        return await self.cache.get_or_fetch("list_subnets", key, fetch, refresh)

    async def list_servers(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch."),
    ) -> ServerListData:
        """List the servers of the project (read from vServer), as pool-member candidates.

        Returns {region, servers[{id, name, status, private_ip, zone_id}]}. A
        pool member's `ipAddress` is the server's **private_ip** — vLB reaches
        backends over the private network, so passing a public IP produces a
        pool that never becomes healthy.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> ServerListData:
            raw = await self.client.get_vserver(f"/v2/{pid}/servers", region=region)
            return ServerListData(
                region=resolved_region,
                servers=[ServerItem.from_api(s) for s in as_list(raw) if isinstance(s, dict)],
            )

        key = ("list_servers", resolved_region, pid)
        return await self.cache.get_or_fetch("list_servers", key, fetch, refresh)
