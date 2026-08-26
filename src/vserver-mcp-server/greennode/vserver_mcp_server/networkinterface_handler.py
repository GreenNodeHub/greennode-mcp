"""Floating IP, elastic network interface and DHCP option management.

These three families all shape how an instance reaches, and is reached from,
the network. Mirrors the `grn vserver floating-ip`, `network-interface` and
`dhcp` command groups.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateDhcpOptionDto,
    CreateNetworkInterfaceDto,
    DhcpOptionItem,
    DhcpOptionListData,
    FloatingIpItem,
    FloatingIpListData,
    NetworkInterfaceItem,
    NetworkInterfaceListData,
    RenameNetworkInterfaceDto,
    UpdateResourceTagsDto,
    VpcItem,
    VpcListData,
)
from greennode.vserver_mcp_server.paging import fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


DEFAULT_DNS_SERVERS = ("10.166.12.196", "10.166.12.197")
MAX_ADDITIONAL_DNS_SERVERS = 2
NETWORK_INTERFACE_RESOURCE_TYPE = "NETWORK-INTERFACE"


class NetworkInterfaceHandler:
    """Register and serve floating IP, elastic interface and DHCP MCP tools."""

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

        self.mcp.tool(name="list_floating_ips", annotations=READ)(self.list_floating_ips)
        self.mcp.tool(name="list_elastic_ips", annotations=READ)(self.list_elastic_ips)
        self.mcp.tool(name="list_network_interfaces", annotations=READ)(
            self.list_network_interfaces
        )
        self.mcp.tool(name="get_network_interface", annotations=READ)(self.get_network_interface)
        self.mcp.tool(name="list_dhcp_options", annotations=READ)(self.list_dhcp_options)
        self.mcp.tool(name="get_dhcp_option", annotations=READ)(self.get_dhcp_option)
        self.mcp.tool(name="list_dhcp_option_vpcs", annotations=READ)(self.list_dhcp_option_vpcs)

        if self.allow_write:
            self.mcp.tool(name="create_network_interface", annotations=WRITE)(
                self.create_network_interface
            )
            self.mcp.tool(name="rename_network_interface", annotations=WRITE)(
                self.rename_network_interface
            )
            self.mcp.tool(name="update_network_interface_tags", annotations=WRITE)(
                self.update_network_interface_tags
            )
            self.mcp.tool(name="delete_network_interface", annotations=DESTRUCTIVE)(
                self.delete_network_interface
            )
            self.mcp.tool(name="delete_floating_ip", annotations=DESTRUCTIVE)(
                self.delete_floating_ip
            )
            self.mcp.tool(name="create_dhcp_option", annotations=WRITE)(self.create_dhcp_option)
            self.mcp.tool(name="update_vpc_dhcp_option", annotations=WRITE)(
                self.update_vpc_dhcp_option
            )
            self.mcp.tool(name="delete_dhcp_option", annotations=DESTRUCTIVE)(
                self.delete_dhcp_option
            )

    async def list_floating_ips(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> FloatingIpListData:
        """List the floating (public WAN) IPs in the project.

        Returns {region, floating_ips[{id, ip, status, fixed_ip,
        network_interface_id, zone_id, created_at}]}. `status` is ATTACHED when
        the address is in use and AVAILABLE when it is free.

        ## Workflow
        - attach_server_floating_ip needs an AVAILABLE address; pick one here.
        - An AVAILABLE floating IP is still billable — flag unused ones when the
          user is reviewing cost.
        """
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(self.client, f"/v2/{pid}/wanIps", region=region)
        return FloatingIpListData(
            region=region or self.config.default_region,
            floating_ips=[FloatingIpItem.from_api(f) for f in raw],
        )

    async def delete_floating_ip(
        self,
        floating_ip_id: str = Field(..., description="Floating IP ID from list_floating_ips."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Release a floating IP. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Detach it from any server first with detach_server_floating_ip.
        - The address goes back to the public pool: the same one will very
          likely not be obtainable again, so anything pointing at it (DNS
          records, firewall allow-lists) breaks permanently.

        ## Workflow
        - Show the user the actual IP address, not just the id, and get
          explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(floating_ip_id, "floating_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/wanIps/{floating_ip_id}", region=region)
        return f"Floating IP {floating_ip_id} released."

    async def list_network_interfaces(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the interface name."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkInterfaceListData:
        """List the elastic network interfaces in the project.

        Returns {region, network_interfaces[{id, name, fixed_ip, floating_ip,
        floating_ip_id, status, interface_type, subnet_id, vpc_id, server_id,
        zone_id, mac}]}.

        An elastic interface exists independently of any server: it can be
        detached from one instance and attached to another, carrying its IP
        with it.

        ## Workflow
        - An interface with an empty `server_id` is free and can be passed to
          attach_server_external_interface.
        """
        pid = await require_project_id(self.config, self.client, region)
        params = {"name": name_filter} if name_filter else None
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/network-interfaces-elastic", region=region, params=params
        )
        return NetworkInterfaceListData(
            region=region or self.config.default_region,
            network_interfaces=[NetworkInterfaceItem.from_api(n) for n in raw],
        )

    async def get_network_interface(
        self,
        network_interface_id: str = Field(
            ..., description="Interface ID from list_network_interfaces."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkInterfaceItem:
        """Get one elastic network interface by id."""
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/network-interfaces-elastic/{network_interface_id}", region=region
        )
        return NetworkInterfaceItem.from_api(unwrap(data) or {})

    async def create_network_interface(
        self,
        body: CreateNetworkInterfaceDto = Field(..., description="Interface to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkInterfaceItem:
        """Create an elastic network interface.

        ## Requirements
        - Requires `--allow-write`. An elastic interface is **billable**.
        - `zoneId` must come from list_zones and fixes which servers the
          interface can later attach to.
        - `floatingIpId`, when given, must be an AVAILABLE address from
          list_floating_ips.

        ## Workflow
        - Attach it to a server afterwards with
          attach_server_external_interface.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/network-interfaces-elastic",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return NetworkInterfaceItem.from_api(unwrap(data) or {})

    async def rename_network_interface(
        self,
        network_interface_id: str = Field(..., description="Interface ID."),
        body: RenameNetworkInterfaceDto = Field(..., description="New name."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkInterfaceItem:
        """Rename an elastic network interface.

        ## Requirements
        - Requires `--allow-write`.
        - The name is the only editable field; use update_network_interface_tags
          for tags.
        """
        require_write(self.allow_write)
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/network-interfaces-elastic/{network_interface_id}/rename",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return NetworkInterfaceItem.from_api(unwrap(data) or {})

    async def update_network_interface_tags(
        self,
        network_interface_id: str = Field(..., description="Interface ID."),
        body: UpdateResourceTagsDto = Field(..., description="Complete replacement tag list."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkInterfaceItem:
        """Replace the tags of an elastic network interface.

        ## Requirements
        - Requires `--allow-write`.
        - `tagRequestList` is a **full replacement**: any tag left out is
          removed. Read the current set with list_resource_tags first.
        - `resourceType` must be `NETWORK-INTERFACE`.
        """
        require_write(self.allow_write)
        validate_id(network_interface_id, "network_interface_id")
        if body.resourceType != NETWORK_INTERFACE_RESOURCE_TYPE:
            raise ValueError(
                f"resourceType must be '{NETWORK_INTERFACE_RESOURCE_TYPE}' for a network "
                f"interface, got '{body.resourceType}'."
            )
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["resourceId"] = network_interface_id
        await self.client.put(
            f"/v2/{pid}/tag/resource/{network_interface_id}", region=region, json=payload
        )
        return await self.get_network_interface(
            network_interface_id=network_interface_id, region=region
        )

    async def delete_network_interface(
        self,
        network_interface_id: str = Field(..., description="Interface ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete an elastic network interface. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Detach it from any server first with
          detach_server_external_interface.

        ## Workflow
        - Show the user the interface's id, name and IP and get explicit
          confirmation.
        """
        require_write(self.allow_write)
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/network-interfaces-elastic/{network_interface_id}", region=region
        )
        return f"Network interface {network_interface_id} deleted."

    async def list_dhcp_options(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> DhcpOptionListData:
        """List the DHCP option sets in the project.

        Returns {region, dhcp_options[{id, name, status, dns_servers, mtu,
        associated_vpc_ids}]}. A DHCP option set decides which DNS servers and
        MTU the instances of a VPC receive.
        """
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(self.client, f"/v2/{pid}/dhcp_option", region=region)
        return DhcpOptionListData(
            region=region or self.config.default_region,
            dhcp_options=[DhcpOptionItem.from_api(d) for d in raw],
        )

    async def get_dhcp_option(
        self,
        dhcp_option_id: str = Field(..., description="DHCP option set ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> DhcpOptionItem:
        """Get one DHCP option set by id, including the VPCs using it."""
        validate_id(dhcp_option_id, "dhcp_option_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/dhcp_option/{dhcp_option_id}", region=region)
        return DhcpOptionItem.from_api(unwrap(data) or {})

    async def list_dhcp_option_vpcs(
        self,
        dhcp_option_id: str = Field(..., description="DHCP option set ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcListData:
        """List the VPCs currently associated with a DHCP option set.

        ## Workflow
        - Call this before delete_dhcp_option: a set still bound to a VPC
          cannot be removed cleanly.
        """
        validate_id(dhcp_option_id, "dhcp_option_id")
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(
            self.client,
            f"/v2/{pid}/networks",
            region=region,
            params={"dhcpOptionIds": dhcp_option_id},
        )
        return VpcListData(
            region=region or self.config.default_region,
            vpcs=[VpcItem.from_api(v) for v in raw],
        )

    async def create_dhcp_option(
        self,
        body: CreateDhcpOptionDto = Field(..., description="DHCP option set to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> DhcpOptionItem:
        """Create a DHCP option set.

        ## Requirements
        - Requires `--allow-write`.
        - The two GreenNode default DNS servers (10.166.12.196, 10.166.12.197)
          are always included and are added automatically — list only the extra
          servers in `dnsServers`.
        - At most **two** extra servers may be added, four in total.

        ## Workflow
        - The set does nothing until a VPC is pointed at it with
          update_vpc_dhcp_option.
        - Instances pick the new DNS settings up on their next DHCP lease
          renewal, not immediately.
        """
        require_write(self.allow_write)
        payload = body.model_dump(exclude_none=True)
        payload["dnsServers"] = _build_dns_servers(body.dnsServers)
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(f"/v2/{pid}/dhcp_option", region=region, json=payload)
        return DhcpOptionItem.from_api(unwrap(data) or {})

    async def update_vpc_dhcp_option(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        dhcp_option_id: str | None = Field(
            None,
            description=(
                "DHCP option set to associate with the VPC. Leave unset together "
                "with detach=true to remove the current association."
            ),
        ),
        detach: bool = Field(
            False, description="Remove the VPC's DHCP option association instead of setting one."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Associate a VPC with a DHCP option set, or detach it.

        ## Requirements
        - Requires `--allow-write`.
        - A VPC belongs to **at most one** DHCP option set: associating a new
          one silently replaces the previous association.
        - Pass either `dhcp_option_id` or `detach=true`, never both.

        ## Workflow
        - Changing DNS servers affects every instance in the VPC once their
          leases renew; say so before applying it to a production network.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        if detach and dhcp_option_id:
            raise ValueError("Pass either dhcp_option_id or detach=true, not both.")
        if not detach and not dhcp_option_id:
            raise ValueError(
                "Provide dhcp_option_id to associate a DHCP option set, or detach=true to remove "
                "the current association."
            )

        payload: dict = {}
        if not detach:
            validate_id(dhcp_option_id, "dhcp_option_id")
            payload["dhcpOptionId"] = dhcp_option_id

        pid = await require_project_id(self.config, self.client, region)
        await self.client.patch(
            f"/v2/{pid}/networks/{vpc_id}/updateDhcpOption", region=region, json=payload
        )
        self.cache.invalidate("list_vpcs")
        if detach:
            return f"VPC {vpc_id} detached from its DHCP option set."
        return f"VPC {vpc_id} associated with DHCP option set {dhcp_option_id}."

    async def delete_dhcp_option(
        self,
        dhcp_option_id: str = Field(..., description="DHCP option set ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a DHCP option set. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Detach every VPC first with update_vpc_dhcp_option(detach=true);
          check list_dhcp_option_vpcs to see which are still bound.

        ## Workflow
        - Show the user the set's name, DNS servers and associated VPCs, and get
          explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(dhcp_option_id, "dhcp_option_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/dhcp_option/{dhcp_option_id}", region=region)
        return f"DHCP option set {dhcp_option_id} deleted."

    async def list_elastic_ips(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match applied by the API."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> FloatingIpListData:
        """List the project's elastic IPs.

        Same shape as list_floating_ips. vServer keeps two views of its public
        addresses: `wanIps`, which list_floating_ips reads and which is what
        attach_server_floating_ip works against, and `elastic-ips`, the newer
        console-side view. Prefer list_floating_ips for anything you intend to
        attach; use this one to cross-check when the two disagree.

        Note: this endpoint is permission-gated. A `403 IAM_PERMISSION_DENIED`
        means the caller's IAM policy lacks the right, not that the project has
        no public addresses — fall back to list_floating_ips.
        """
        pid = await require_project_id(self.config, self.client, region)
        params = {"name": name_filter} if name_filter else None
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/elastic-ips", region=region, params=params
        )
        return FloatingIpListData(
            region=region or self.config.default_region,
            floating_ips=[FloatingIpItem.from_api(ip) for ip in raw],
        )


def _build_dns_servers(extra: list[str] | None) -> list[str]:
    """Prepend the mandatory default DNS servers to the caller's list.

    The platform always serves its own two resolvers; callers may add at most
    two more. Defaults passed in again are ignored rather than counted.
    """
    servers = list(DEFAULT_DNS_SERVERS)
    added = 0
    for raw in extra or []:
        ip = raw.strip()
        if not ip or ip in DEFAULT_DNS_SERVERS:
            continue
        added += 1
        if added > MAX_ADDITIONAL_DNS_SERVERS:
            raise ValueError(
                f"At most {MAX_ADDITIONAL_DNS_SERVERS} DNS servers may be added beyond the "
                f"{len(DEFAULT_DNS_SERVERS)} GreenNode defaults."
            )
        servers.append(ip)
    return servers
