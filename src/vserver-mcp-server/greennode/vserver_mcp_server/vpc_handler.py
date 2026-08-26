"""VPC (network) management for the vServer MCP server.

A VPC is the outermost network container: every subnet, and therefore every
server, lives inside one. Mirrors the `grn vserver vpc` command group.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateVpcDto,
    UpdateVpcDto,
    VpcItem,
    VpcListData,
)
from greennode.vserver_mcp_server.paging import fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class VpcHandler:
    """Register and serve VPC MCP tools."""

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

        self.mcp.tool(name="list_vpcs", annotations=READ)(self.list_vpcs)
        self.mcp.tool(name="get_vpc", annotations=READ)(self.get_vpc)
        self.mcp.tool(name="list_active_vpcs", annotations=READ)(self.list_active_vpcs)

        if self.allow_write:
            self.mcp.tool(name="create_vpc", annotations=WRITE)(self.create_vpc)
            self.mcp.tool(name="update_vpc", annotations=WRITE)(self.update_vpc)
            self.mcp.tool(name="enable_vpc_dns", annotations=WRITE)(self.enable_vpc_dns)
            self.mcp.tool(name="delete_vpc", annotations=DESTRUCTIVE)(self.delete_vpc)

    async def list_vpcs(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the VPC name, applied by the API."
        ),
        include_inactive: bool = Field(
            False,
            description=(
                "Include VPCs that are not ACTIVE (CREATING, DELETING, ERROR). Off by "
                "default because only ACTIVE VPCs can host new subnets or servers."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> VpcListData:
        """List the VPCs (networks) in the project.

        Returns {region, vpcs[{id, name, cidr, status, zone_id, mtu,
        dhcp_option_id, route_table_id, dns_status}]}.

        ## Workflow
        - Step 1 of the create_server flow: present this list and let the user
          choose. IMPORTANT: do NOT pick a VPC silently when more than one exists.
        - Use the chosen `id` as `networkId` in create_server, and as `vpc_id`
          in list_subnets to enumerate its subnets.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[VpcItem]:
            params = {"name": name_filter} if name_filter else None
            raw = await fetch_all_items(
                self.client, f"/v2/{pid}/networks", region=region, params=params
            )
            return [VpcItem.from_api(v) for v in raw]

        key = ("list_vpcs", resolved_region, pid, name_filter)
        vpcs = await self.cache.get_or_fetch("list_vpcs", key, fetch, refresh)

        if not include_inactive:
            vpcs = [v for v in vpcs if v.status == "ACTIVE"]
        return VpcListData(region=resolved_region, vpcs=vpcs)

    async def get_vpc(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Get one VPC by id.

        Returns the same fields as list_vpcs for a single VPC. Use it to confirm
        a VPC reached ACTIVE after create_vpc, or to read its `cidr` before
        choosing a subnet CIDR inside it.
        """
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/networks/{vpc_id}", region=region)
        return VpcItem.from_api(unwrap(data) or {})

    async def create_vpc(
        self,
        body: CreateVpcDto = Field(..., description="VPC to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Create a VPC (network).

        ## Requirements
        - Requires `--allow-write`.
        - `cidr` must be a private RFC 1918 block in CIDR notation, e.g.
          `10.0.0.0/16`. Pick it large enough to hold every subnet you will add;
          a VPC CIDR cannot be changed afterwards.
        - `name` must be unique within the project.

        ## Workflow
        - Confirm name and CIDR with the user before calling — this provisions
          billable infrastructure.
        - A new VPC starts in CREATING; poll get_vpc until `status` is ACTIVE.
        - Then add a subnet with create_subnet before creating any server.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/networks", region=region, json=payload)
        return VpcItem.from_api(unwrap(data) or {})

    async def update_vpc(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        body: UpdateVpcDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Rename a VPC or replace its tags.

        ## Requirements
        - Requires `--allow-write`.
        - `name` is mandatory on every call: the API treats this as a full
          replacement of the editable fields, so pass the current name when you
          only intend to change tags.
        - The CIDR of an existing VPC cannot be changed.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.patch(f"/v2/{pid}/networks/{vpc_id}", region=region, json=payload)
        return VpcItem.from_api(unwrap(data) or {})

    async def delete_vpc(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a VPC. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - The VPC must be empty: delete its subnets first, and those subnets
          must have no servers or network interfaces attached. The API rejects
          the call otherwise.

        ## Workflow
        - Show the user the VPC's id, name and CIDR and get explicit
          confirmation before calling.
        - Call list_subnets first so the user sees what has to go first.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/networks/{vpc_id}", region=region)
        self.cache.invalidate("list_vpcs")
        return f"VPC {vpc_id} deleted."

    async def list_active_vpcs(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcListData:
        """List only the VPCs the API itself reports as usable.

        list_vpcs filters on `status == "ACTIVE"` client-side; this asks the API
        for its own answer. Use it when list_vpcs shows a VPC as ACTIVE but a
        create call still rejects it — the two disagreeing is the signal that
        the VPC is mid-transition.

        Note: this endpoint is permission-gated. A `403 IAM_PERMISSION_DENIED`
        means the caller's IAM policy lacks the right, not that no VPC is
        active; fall back to list_vpcs.
        """
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(self.client, f"/v2/{pid}/networks/active", region=region)
        return VpcListData(
            region=region or self.config.default_region,
            vpcs=[VpcItem.from_api(v) for v in raw],
        )

    async def enable_vpc_dns(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Turn on private DNS resolution inside a VPC.

        ## Requirements
        - Requires `--allow-write`.
        - Instances get internal name resolution for resources in the VPC. This
          endpoint only **enables** it — vServer exposes no matching disable, so
          treat it as one-way.
        - Check `dns_status` in get_vpc first; enabling twice is pointless.

        ## Workflow
        - Instances already running may need their resolver refreshed (a DHCP
          renew or a reboot) before they pick the change up. Say so.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.patch(f"/v2/{pid}/networks/{vpc_id}/enableDns", region=region)
        self.cache.invalidate("list_vpcs")
        return VpcItem.from_api(unwrap(data) or {})
