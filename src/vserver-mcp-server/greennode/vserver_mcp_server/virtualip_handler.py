"""Virtual IP address management for the vServer MCP server.

A VIP is an IP that several instances can answer for, so a keepalived-style
pair can fail over without changing anything a client sees. Instances join a
VIP through an *address pair* — a binding between the VIP and one network
interface. Only interfaces in the VIP's own subnet may join it.

The same address-pair machinery also backs secondary subnets, which is why the
virtual-subnet pair tools live here rather than in the subnet handler.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    AddressPairDto,
    AddressPairItem,
    AddressPairListData,
    CandidateInterfaceItem,
    CandidateInterfaceListData,
    CreatePublicVirtualIpDto,
    CreateVirtualIpDto,
    UpdateVirtualIpDto,
    VirtualIpItem,
    VirtualIpListData,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_all_items, unwrap, unwrap_one
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class VirtualIpHandler:
    """Register and serve virtual-IP MCP tools."""

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

        self.mcp.tool(name="list_virtual_ips", annotations=READ)(self.list_virtual_ips)
        self.mcp.tool(name="get_virtual_ip", annotations=READ)(self.get_virtual_ip)
        self.mcp.tool(name="list_virtual_ip_address_pairs", annotations=READ)(
            self.list_virtual_ip_address_pairs
        )
        self.mcp.tool(name="get_virtual_ip_address_pair", annotations=READ)(
            self.get_virtual_ip_address_pair
        )
        self.mcp.tool(name="list_virtual_ip_candidate_interfaces", annotations=READ)(
            self.list_virtual_ip_candidate_interfaces
        )
        self.mcp.tool(name="list_public_virtual_ip_candidate_interfaces", annotations=READ)(
            self.list_public_virtual_ip_candidate_interfaces
        )
        self.mcp.tool(name="list_secondary_subnet_address_pairs", annotations=READ)(
            self.list_secondary_subnet_address_pairs
        )

        if self.allow_write:
            self.mcp.tool(name="create_virtual_ip", annotations=WRITE)(self.create_virtual_ip)
            self.mcp.tool(name="update_virtual_ip", annotations=WRITE)(self.update_virtual_ip)
            self.mcp.tool(name="create_public_virtual_ip", annotations=WRITE)(
                self.create_public_virtual_ip
            )
            self.mcp.tool(name="create_virtual_ip_address_pair", annotations=WRITE)(
                self.create_virtual_ip_address_pair
            )
            self.mcp.tool(name="create_public_virtual_ip_address_pair", annotations=WRITE)(
                self.create_public_virtual_ip_address_pair
            )
            self.mcp.tool(name="create_secondary_subnet_address_pair", annotations=WRITE)(
                self.create_secondary_subnet_address_pair
            )
            self.mcp.tool(name="delete_virtual_ip_address_pair", annotations=DESTRUCTIVE)(
                self.delete_virtual_ip_address_pair
            )
            self.mcp.tool(name="delete_public_virtual_ip_address_pair", annotations=DESTRUCTIVE)(
                self.delete_public_virtual_ip_address_pair
            )
            self.mcp.tool(name="delete_secondary_subnet_address_pair", annotations=DESTRUCTIVE)(
                self.delete_secondary_subnet_address_pair
            )
            self.mcp.tool(name="delete_virtual_ip", annotations=DESTRUCTIVE)(
                self.delete_virtual_ip
            )
            self.mcp.tool(name="delete_public_virtual_ip", annotations=DESTRUCTIVE)(
                self.delete_public_virtual_ip
            )

    async def list_virtual_ips(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> VirtualIpListData:
        """List the virtual IP addresses in the project.

        Returns {region, virtual_ips[{id, name, ip_address, type, mode, status,
        vpc_id, subnet_id, subnet_cidr, zone_id, address_pair_ips,
        created_at}]}. Both private and public VIPs come back here.

        `address_pair_ips` are the fixed IPs of the interfaces currently sharing
        the VIP — an empty list means the VIP is allocated but nothing answers
        for it yet.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[VirtualIpItem]:
            raw = await fetch_all_items(self.client, f"/v2/{pid}/virtualIpAddress", region=region)
            return [VirtualIpItem.from_api(v) for v in raw]

        key = ("list_virtual_ips", resolved_region, pid)
        vips = await self.cache.get_or_fetch("list_virtual_ips", key, fetch, refresh)
        return VirtualIpListData(region=resolved_region, virtual_ips=vips)

    async def get_virtual_ip(
        self,
        virtual_ip_id: str = Field(..., description="VIP ID from list_virtual_ips."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VirtualIpItem:
        """Get one virtual IP address by id.

        Read `mode` here before update_virtual_ip — the API requires the mode on
        every update, so it has to be sent back even when only the name changes.
        """
        validate_id(virtual_ip_id, "virtual_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/virtualIpAddress/{virtual_ip_id}", region=region)
        return VirtualIpItem.from_api(unwrap(data) or {})

    async def list_virtual_ip_address_pairs(
        self,
        virtual_ip_id: str = Field(..., description="VIP ID from list_virtual_ips."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> AddressPairListData:
        """List the network interfaces bound to one virtual IP.

        Returns {region, parent_id, address_pairs[{id, virtual_ip_id,
        network_interface_id, network_interface_ip, created_at}]}.

        The pair's own `id` — not the interface id — is what
        delete_virtual_ip_address_pair takes.
        """
        validate_id(virtual_ip_id, "virtual_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/virtualIpAddress/{virtual_ip_id}/addressPairs", region=region
        )
        return AddressPairListData(
            region=region or self.config.default_region,
            parent_id=virtual_ip_id,
            address_pairs=[AddressPairItem.from_api(p) for p in as_list(data)],
        )

    async def get_virtual_ip_address_pair(
        self,
        virtual_ip_id: str = Field(..., description="VIP ID from list_virtual_ips."),
        address_pair_id: str = Field(
            ..., description="Address pair ID from list_virtual_ip_address_pairs."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> AddressPairItem:
        """Get one address pair of a virtual IP by id.

        Use it to confirm which interface a pair actually binds before deleting
        it — the pair id alone does not say.
        """
        validate_id(virtual_ip_id, "virtual_ip_id")
        validate_id(address_pair_id, "address_pair_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/virtualIpAddress/{virtual_ip_id}/addressPairs/{address_pair_id}",
            region=region,
        )
        return AddressPairItem.from_api(unwrap_one(data))

    async def list_virtual_ip_candidate_interfaces(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CandidateInterfaceListData:
        """List the internal network interfaces that may join a private VIP.

        Returns {region, interfaces[{id, fixed_ip, subnet_id, zone_id}]} across
        the whole project.

        ## Workflow
        - Filter by the VIP's `subnet_id` yourself: only interfaces in the same
          subnet as the VIP can be bound, and the API does not filter for you.
        - Pass the chosen `id` as `networkInterfaceId` to
          create_virtual_ip_address_pair.
        """
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/virtualIpAddress/internalNetworkInterfaces", region=region
        )
        return CandidateInterfaceListData(
            region=region or self.config.default_region,
            interfaces=[CandidateInterfaceItem.from_api(i) for i in as_list(data)],
        )

    async def list_public_virtual_ip_candidate_interfaces(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CandidateInterfaceListData:
        """List the external network interfaces that may join a public VIP.

        Returns {region, interfaces[{id, fixed_ip, subnet_id, zone_id}]}.
        `subnet_id` is empty for external interfaces — they sit on the WAN side,
        not in a subnet.

        Pass the chosen `id` as `networkInterfaceId` to
        create_public_virtual_ip_address_pair.
        """
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/public-vips/externalNetworkInterfaces", region=region
        )
        return CandidateInterfaceListData(
            region=region or self.config.default_region,
            interfaces=[CandidateInterfaceItem.from_api(i) for i in as_list(data)],
        )

    async def list_secondary_subnet_address_pairs(
        self,
        secondary_subnet_id: str = Field(
            ..., description="Secondary subnet ID from get_subnet's secondary subnets."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> AddressPairListData:
        """List the interfaces bound to one secondary subnet.

        A secondary subnet is an extra CIDR on a subnet; binding an interface to
        it lets that instance answer for addresses in the extra range (the LVS /
        sub-interface case).
        """
        validate_id(secondary_subnet_id, "secondary_subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/virtual-subnets/{secondary_subnet_id}/addressPairs", region=region
        )
        return AddressPairListData(
            region=region or self.config.default_region,
            parent_id=secondary_subnet_id,
            address_pairs=[AddressPairItem.from_api(p) for p in as_list(data)],
        )

    async def create_virtual_ip(
        self,
        body: CreateVirtualIpDto = Field(..., description="Private VIP to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VirtualIpItem:
        """Create a private virtual IP address.

        ## Requirements
        - Requires `--allow-write`.
        - `subnetId` must be the subnet of **every** instance that will share
          the VIP — cross-subnet membership is impossible.
        - `mode` is mandatory: `Active/Passive` for keepalived-style failover,
          `Active/Active` to load-share.
        - `ipAddress`, if given, must be a free address inside the subnet.

        ## Workflow
        - Creating the VIP allocates the address but routes nothing. Bind each
          instance's interface with create_virtual_ip_address_pair.
        - The guest OS still has to be configured (keepalived, or a secondary
          address on the interface) — vServer will not do it. Say so.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/virtualIpAddress", region=region, json=payload)
        self.cache.invalidate("list_virtual_ips")
        return VirtualIpItem.from_api(unwrap(data) or {})

    async def update_virtual_ip(
        self,
        virtual_ip_id: str = Field(..., description="VIP ID from list_virtual_ips."),
        body: UpdateVirtualIpDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VirtualIpItem:
        """Rename a virtual IP or change its sharing mode.

        ## Requirements
        - Requires `--allow-write`.
        - `mode` is required on every call — call get_virtual_ip and send the
          current mode back when only the name or description should change,
          otherwise you silently switch the failover behaviour.
        - The VIP's address and subnet cannot be changed.

        ## Workflow
        - Switching between Active/Passive and Active/Active changes how traffic
          is distributed and usually needs the guest-OS configuration changed to
          match — confirm with the user first.
        """
        require_write(self.allow_write)
        validate_id(virtual_ip_id, "virtual_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v2/{pid}/virtualIpAddress/{virtual_ip_id}", region=region, json=payload
        )
        self.cache.invalidate("list_virtual_ips")
        return VirtualIpItem.from_api(unwrap(data) or {})

    async def create_public_virtual_ip(
        self,
        body: CreatePublicVirtualIpDto = Field(..., description="Public VIP to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VirtualIpItem:
        """Create a public virtual IP address.

        ## Requirements
        - Requires `--allow-write`. A public VIP consumes a **public IP** from
          the project's quota and is billable.
        - `type` is `public-vm` for a VIP shared by instances, `public-mkp` for
          a vMarketplace appliance.
        - Public VIPs bind **external** interfaces — use
          list_public_virtual_ip_candidate_interfaces, not the private one.

        ## Workflow
        - Confirm the type with the user; it cannot be changed afterwards.
        - Bind interfaces with create_public_virtual_ip_address_pair, then check
          the security groups on those interfaces — a public VIP is reachable
          from the internet.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/public-vips", region=region, json=payload)
        self.cache.invalidate("list_virtual_ips")
        return VirtualIpItem.from_api(unwrap(data) or {})

    async def create_virtual_ip_address_pair(
        self,
        virtual_ip_id: str = Field(..., description="VIP ID from list_virtual_ips."),
        body: AddressPairDto = Field(..., description="Network interface to bind to the VIP."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> AddressPairItem:
        """Bind a network interface to a private virtual IP.

        ## Requirements
        - Requires `--allow-write`.
        - The interface must be in the **same subnet** as the VIP; the API
          rejects anything else. Use list_virtual_ip_candidate_interfaces and
          filter on the VIP's `subnet_id`.
        - Binding alone does not make the instance answer for the VIP — the
          guest OS needs keepalived or a secondary address configured.

        ## Workflow
        - Bind every instance in the HA set, then verify with
          list_virtual_ip_address_pairs that each appears.
        """
        require_write(self.allow_write)
        validate_id(virtual_ip_id, "virtual_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/virtualIpAddress/{virtual_ip_id}/addressPairs",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return AddressPairItem.from_api(unwrap_one(data))

    async def create_public_virtual_ip_address_pair(
        self,
        virtual_ip_id: str = Field(
            ..., description="Public VIP ID from list_virtual_ips (type public-*)."
        ),
        body: AddressPairDto = Field(
            ..., description="External network interface to bind to the public VIP."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> AddressPairItem:
        """Bind an external network interface to a public virtual IP.

        ## Requirements
        - Requires `--allow-write`.
        - `networkInterfaceId` must come from
          list_public_virtual_ip_candidate_interfaces — internal interfaces are
          rejected.
        - The instance behind the interface becomes reachable on a **public**
          address; make sure its security group is not wide open first.

        ## Workflow
        - Bind, then verify with list_virtual_ip_address_pairs, then review the
          security group with list_server_security_groups.
        """
        require_write(self.allow_write)
        validate_id(virtual_ip_id, "virtual_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/public-vips/{virtual_ip_id}/addressPairs",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return AddressPairItem.from_api(unwrap_one(data))

    async def create_secondary_subnet_address_pair(
        self,
        secondary_subnet_id: str = Field(
            ..., description="Secondary subnet ID from get_subnet's secondary subnets."
        ),
        body: AddressPairDto = Field(
            ..., description="Network interface to bind to the secondary subnet."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> AddressPairItem:
        """Bind a network interface to a secondary subnet.

        ## Requirements
        - Requires `--allow-write`.
        - The interface must sit in the parent subnet the secondary CIDR was
          added to.
        - The guest OS still needs the extra addresses configured on the
          interface; binding only tells the fabric to accept them.

        ## Workflow
        - Create the secondary subnet with create_secondary_subnet first, then
          bind each interface that should use the extra range.
        """
        require_write(self.allow_write)
        validate_id(secondary_subnet_id, "secondary_subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/virtual-subnets/{secondary_subnet_id}/addressPairs",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return AddressPairItem.from_api(unwrap_one(data))

    async def delete_virtual_ip_address_pair(
        self,
        virtual_ip_id: str = Field(..., description="VIP ID from list_virtual_ips."),
        address_pair_id: str = Field(
            ..., description="Address pair ID from list_virtual_ip_address_pairs."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Unbind a network interface from a private virtual IP.

        ## Requirements
        - Requires `--allow-write`.
        - If this is the instance currently holding the VIP, traffic to the VIP
          stops until another bound instance takes over — and if it was the last
          pair, the VIP answers for nothing at all.

        ## Workflow
        - Call get_virtual_ip_address_pair first so the user sees which instance
          is about to be unbound, and confirm.
        """
        require_write(self.allow_write)
        validate_id(virtual_ip_id, "virtual_ip_id")
        validate_id(address_pair_id, "address_pair_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/virtualIpAddress/{virtual_ip_id}/addressPairs/{address_pair_id}",
            region=region,
        )
        return f"Address pair {address_pair_id} removed from VIP {virtual_ip_id}."

    async def delete_public_virtual_ip_address_pair(
        self,
        virtual_ip_id: str = Field(..., description="Public VIP ID from list_virtual_ips."),
        address_pair_id: str = Field(
            ..., description="Address pair ID from list_virtual_ip_address_pairs."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Unbind an external network interface from a public virtual IP.

        ## Requirements
        - Requires `--allow-write`.
        - Inbound internet traffic to the VIP stops reaching that instance
          immediately.

        ## Workflow
        - Show the user which interface is being unbound, and confirm.
        """
        require_write(self.allow_write)
        validate_id(virtual_ip_id, "virtual_ip_id")
        validate_id(address_pair_id, "address_pair_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/public-vips/{virtual_ip_id}/addressPairs/{address_pair_id}", region=region
        )
        return f"Address pair {address_pair_id} removed from public VIP {virtual_ip_id}."

    async def delete_secondary_subnet_address_pair(
        self,
        address_pair_id: str = Field(
            ..., description="Address pair ID from list_secondary_subnet_address_pairs."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Unbind a network interface from a secondary subnet.

        ## Requirements
        - Requires `--allow-write`.
        - The instance stops being allowed to use addresses from the secondary
          CIDR; anything still bound inside the guest OS goes dark.
        - Unlike the VIP variants this endpoint takes **only** the pair id — it
          is not scoped by subnet, so double-check the id before calling.

        ## Workflow
        - Confirm the interface with list_secondary_subnet_address_pairs first.
        """
        require_write(self.allow_write)
        validate_id(address_pair_id, "address_pair_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/virtual-subnets/addressPairs/{address_pair_id}", region=region
        )
        return f"Secondary-subnet address pair {address_pair_id} removed."

    async def delete_virtual_ip(
        self,
        virtual_ip_id: str = Field(..., description="VIP ID from list_virtual_ips."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a private virtual IP address. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Every service reachable on the VIP stops answering immediately, and
          the address goes back to the subnet pool — you may not get it back.
        - Unbind the address pairs first so the failure mode is obvious rather
          than silent.

        ## Workflow
        - Call list_virtual_ip_address_pairs, show the user which instances rely
          on the VIP, and get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(virtual_ip_id, "virtual_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/virtualIpAddress/{virtual_ip_id}", region=region)
        self.cache.invalidate("list_virtual_ips")
        return f"Virtual IP {virtual_ip_id} deleted."

    async def delete_public_virtual_ip(
        self,
        virtual_ip_id: str = Field(..., description="Public VIP ID from list_virtual_ips."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a public virtual IP address. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - The public address is released back to the pool — anything pointing at
          it (DNS records, allow-lists, partners' firewalls) breaks, and the
          same address will not be handed back.

        ## Workflow
        - Show the user the address and everything bound to it, warn that DNS
          and external allow-lists have to be updated, and confirm.
        """
        require_write(self.allow_write)
        validate_id(virtual_ip_id, "virtual_ip_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/public-vips/{virtual_ip_id}", region=region)
        self.cache.invalidate("list_virtual_ips")
        return f"Public virtual IP {virtual_ip_id} deleted."
