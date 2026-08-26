"""Optional networking models: routing, ACLs, peering, interconnect, virtual IPs.

None of these is needed to run an instance; each solves a specific problem —
steering traffic, filtering a whole subnet, reaching another VPC or another
site, or sharing one address across an HA pair.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.models._common import _resource_id, _zone_id
from pydantic import BaseModel, Field


class RouteItem(BaseModel):
    """One static route inside a route table."""

    destination_cidr: str = Field(
        "", description="Destination network in CIDR notation, e.g. 10.21.0.0/24"
    )
    target: str = Field("", description="Next hop the matching traffic is forwarded to")

    @classmethod
    def from_api(cls, data: dict) -> "RouteItem":
        """Build a RouteItem from a raw vServer route object."""
        return cls(
            destination_cidr=data.get("destinationCidrBlock") or data.get("cidr") or "",
            target=data.get("target") or data.get("nextHop") or "",
        )


class RouteTableItem(BaseModel):
    """One route table."""

    id: str = Field(..., description="Route table ID (uuid), prefix 'rt-'")
    name: str = Field("", description="Route table name")
    status: str = Field("", description="ACTIVE once usable")
    vpc_id: str = Field("", description="VPC the route table belongs to")
    routes: list[RouteItem] = Field(
        default_factory=list, description="Static routes currently in the table"
    )
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "RouteTableItem":
        """Build a RouteTableItem from a raw vServer route-table object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            vpc_id=data.get("networkId") or data.get("networkUuid") or "",
            routes=[
                RouteItem.from_api(r) for r in (data.get("routes") or []) if isinstance(r, dict)
            ],
            created_at=data.get("createdAt") or "",
        )


class RouteTableListData(BaseModel):
    """Structured response for list_route_tables."""

    region: str = Field(..., description="Region the route tables were fetched from")
    route_tables: list[RouteTableItem] = Field(
        default_factory=list, description="Route tables in the project"
    )


class RouteListData(BaseModel):
    """Structured response for list_route_table_routes."""

    region: str = Field(..., description="Region the routes were fetched from")
    route_table_id: str = Field(..., description="Route table the routes belong to")
    routes: list[RouteItem] = Field(default_factory=list, description="Static routes")


class NetworkAclRuleItem(BaseModel):
    """One inbound or outbound network-ACL rule."""

    id: str = Field("", description="Rule ID (uuid), prefix 'aclr-'")
    direction: str = Field("", description="'inbound' or 'outbound'")
    seq_number: int = Field(
        0,
        description=(
            "Evaluation order, 0-32766 — the FIRST matching rule wins, so a low "
            "number outranks a high one"
        ),
    )
    protocol: str = Field("", description="ANY, TCP, UDP or ICMP")
    port: str = Field("", description="Port or port range, e.g. '443' or '0-65535'")
    source: str = Field(
        "", description="Source CIDR for inbound rules, destination CIDR for outbound"
    )
    action: str = Field("", description="'pass' to allow the traffic, 'drop' to deny it")
    system: bool = Field(
        False, description="True for the platform's own bookend rules (seq 0 and seq 2000)"
    )

    @classmethod
    def from_api(cls, data: dict) -> "NetworkAclRuleItem":
        """Build a NetworkAclRuleItem from a raw vServer ACL rule object.

        The API marks nothing as system-owned, so the two rules every ACL is
        created with — allow at seq 0, deny at seq 2000, per direction — are
        recognised by their sequence numbers instead. They are still editable
        through the rules endpoint, which is exactly why they are worth
        flagging.
        """
        seq_number = data.get("seqNumber") or 0
        return cls(
            id=_resource_id(data),
            direction=data.get("type") or "",
            seq_number=seq_number,
            protocol=data.get("protocol") or "",
            port=str(data.get("port") or ""),
            source=data.get("source") or "",
            action=data.get("action") or "",
            system=bool(data.get("system", seq_number in (0, 2000))),
        )


class NetworkAclItem(BaseModel):
    """One network ACL (subnet-level firewall)."""

    id: str = Field(..., description="Network ACL ID (uuid), prefix 'netPolicy-'")
    name: str = Field("", description="ACL name")
    status: str = Field("", description="ACTIVE once usable")
    vpc_id: str = Field("", description="VPC the ACL belongs to")
    is_default: bool = Field(
        False, description="True for the VPC's default ACL, which cannot be deleted"
    )
    subnet_ids: list[str] = Field(
        default_factory=list, description="Subnets currently associated with this ACL"
    )
    rules: list[NetworkAclRuleItem] = Field(
        default_factory=list, description="Rules (detail endpoint only; empty in the list view)"
    )
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "NetworkAclItem":
        """Build a NetworkAclItem from a raw vServer network-ACL object."""
        associations = data.get("subnetAssociationList") or []
        subnet_ids = []
        for assoc in associations:
            if isinstance(assoc, dict):
                found = assoc.get("subnetUuid") or assoc.get("uuid") or assoc.get("subnetId")
                if found:
                    subnet_ids.append(found)
            elif isinstance(assoc, str):
                subnet_ids.append(assoc)
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            vpc_id=data.get("interfaceNetworkUuid") or data.get("networkId") or "",
            is_default=bool(data.get("defaultAcl", False)),
            subnet_ids=subnet_ids,
            rules=[
                NetworkAclRuleItem.from_api(r)
                for r in (data.get("aclPolicyRules") or [])
                if isinstance(r, dict)
            ],
            created_at=data.get("createdAt") or "",
        )


class NetworkAclListData(BaseModel):
    """Structured response for list_network_acls."""

    region: str = Field(..., description="Region the ACLs were fetched from")
    network_acls: list[NetworkAclItem] = Field(
        default_factory=list, description="Network ACLs in the project"
    )


class NetworkAclRuleListData(BaseModel):
    """Structured response for list_network_acl_rules."""

    region: str = Field(..., description="Region the rules were fetched from")
    network_acl_id: str = Field(..., description="ACL the rules belong to")
    inbound: list[NetworkAclRuleItem] = Field(
        default_factory=list, description="Inbound rules, lowest seq_number first"
    )
    outbound: list[NetworkAclRuleItem] = Field(
        default_factory=list, description="Outbound rules, lowest seq_number first"
    )


class PeeringItem(BaseModel):
    """One VPC peering connection."""

    id: str = Field(..., description="Peering ID (uuid)")
    name: str = Field("", description="Peering name")
    status: str = Field("", description="ACTIVE once traffic flows")
    from_vpc_id: str = Field("", description="VPC on the requesting side")
    from_cidr: str = Field("", description="CIDR of the requesting VPC")
    to_vpc_id: str = Field("", description="VPC on the accepting side")
    to_cidr: str = Field("", description="CIDR of the accepting VPC")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "PeeringItem":
        """Build a PeeringItem from a raw vServer peering object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            from_vpc_id=data.get("fromVpcUuid") or "",
            from_cidr=data.get("fromCidr") or "",
            to_vpc_id=data.get("endVpcUuid") or "",
            to_cidr=data.get("endCidr") or "",
            created_at=data.get("createdAt") or "",
        )


class PeeringListData(BaseModel):
    """Structured response for list_peerings."""

    region: str = Field(..., description="Region the peerings were fetched from")
    peerings: list[PeeringItem] = Field(
        default_factory=list, description="VPC peering connections"
    )


class InterconnectItem(BaseModel):
    """One interconnect circuit."""

    id: str = Field(..., description="Interconnect ID (uuid)")
    name: str = Field("", description="Interconnect name")
    status: str = Field("", description="Provisioning status")
    description: str = Field("", description="Free-text description")
    type_id: str = Field("", description="Circuit type id")
    type_name: str = Field("", description="Circuit type name")
    package_id: str = Field("", description="Bandwidth package id, e.g. itp-1Gbps")
    circuit_id: int | None = Field(None, description="Physical circuit number")
    enable_gw2: bool = Field(False, description="True when the redundant second gateway is on")
    gw1_ip: str = Field("", description="GreenNode-side gateway 1 IP")
    gw2_ip: str = Field("", description="GreenNode-side gateway 2 IP (empty unless enable_gw2)")
    gw_vip: str = Field("", description="Virtual IP shared by the gateways")
    remote_gw1_ip: str = Field("", description="Customer-side gateway 1 IP")
    remote_gw2_ip: str = Field("", description="Customer-side gateway 2 IP")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "InterconnectItem":
        """Build an InterconnectItem from a raw vServer interconnect object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            description=data.get("description") or "",
            type_id=data.get("typeId") or "",
            type_name=data.get("typeName") or "",
            package_id=data.get("packageId") or "",
            circuit_id=data.get("circuitId"),
            enable_gw2=bool(data.get("enableGw2", False)),
            gw1_ip=data.get("gw01Ip") or "",
            gw2_ip=data.get("gw02Ip") or "",
            gw_vip=data.get("gwVip") or "",
            remote_gw1_ip=data.get("remoteGw01Ip") or "",
            remote_gw2_ip=data.get("remoteGw02Ip") or "",
            created_at=data.get("createdAt") or "",
        )


class InterconnectListData(BaseModel):
    """Structured response for list_interconnects."""

    region: str = Field(..., description="Region the interconnects were fetched from")
    interconnects: list[InterconnectItem] = Field(
        default_factory=list, description="Interconnect circuits"
    )


class InterconnectConnectionItem(BaseModel):
    """One VPC attachment on an interconnect."""

    id: str = Field(..., description="Connection ID (uuid)")
    name: str = Field("", description="Connection name")
    status: str = Field("", description="Provisioning status")
    description: str = Field("", description="Free-text description")
    interconnect_id: str = Field("", description="Interconnect the connection belongs to")
    vpc_id: str = Field("", description="VPC reachable through this connection")
    remote_subnets: list[str] = Field(
        default_factory=list,
        description="Customer-side CIDRs routed over the circuit into this VPC",
    )
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "InterconnectConnectionItem":
        """Build an InterconnectConnectionItem from a raw connection object.

        ``remoteSubnets`` arrives as a comma-separated string, not a list.
        """
        remote = data.get("remoteSubnets")
        if isinstance(remote, str):
            subnets = [s.strip() for s in remote.split(",") if s.strip()]
        elif isinstance(remote, list):
            subnets = [str(s) for s in remote]
        else:
            subnets = []
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            description=data.get("description") or "",
            interconnect_id=data.get("interconnectId") or "",
            vpc_id=data.get("networkId") or "",
            remote_subnets=subnets,
            created_at=data.get("createdAt") or "",
        )


class InterconnectConnectionListData(BaseModel):
    """Structured response for list_interconnect_connections."""

    region: str = Field(..., description="Region the connections were fetched from")
    interconnect_id: str = Field(..., description="Interconnect the connections belong to")
    connections: list[InterconnectConnectionItem] = Field(
        default_factory=list, description="VPC attachments on the circuit"
    )


class InterconnectCatalogueItem(BaseModel):
    """One entry of the interconnect package or circuit-type catalogue."""

    id: str = Field(..., description="Catalogue entry ID — pass as packageId or typeId")
    name: str = Field("", description="Display name, e.g. '1Gbps'")
    status: str = Field("", description="ACTIVE when the entry can be ordered")
    description: str = Field("", description="What the entry provides")

    @classmethod
    def from_api(cls, data: dict) -> "InterconnectCatalogueItem":
        """Build an InterconnectCatalogueItem from a raw catalogue entry."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            description=data.get("description") or "",
        )


class InterconnectCatalogueListData(BaseModel):
    """Structured response for list_interconnect_packages / list_interconnect_circuit_types."""

    region: str = Field(..., description="Region the catalogue was fetched from")
    items: list[InterconnectCatalogueItem] = Field(
        default_factory=list, description="Catalogue entries"
    )


class PingResultData(BaseModel):
    """Structured response for ping_interconnect."""

    interconnect_id: str = Field(..., description="Interconnect that was tested")
    gateway_number: int = Field(..., description="Which gateway was tested (1 or 2)")
    reachable: bool | None = Field(
        None, description="Whether the remote gateway answered (None if the API did not say)"
    )
    detail: str = Field("", description="Raw result the API reported")


class VirtualIpItem(BaseModel):
    """One virtual IP address (VIP)."""

    id: str = Field(..., description="VIP ID (uuid), prefix 'vip-'")
    name: str = Field("", description="VIP name")
    description: str = Field("", description="Free-text description")
    ip_address: str = Field("", description="The shared IP itself")
    type: str = Field("", description="'private', 'public-vm' or 'public-mkp'")
    mode: str = Field("", description="'Active/Active' or 'Active/Passive'")
    status: str = Field("", description="ACTIVE once usable")
    vpc_id: str = Field("", description="VPC the VIP lives in")
    subnet_id: str = Field("", description="Subnet the VIP lives in (private VIPs)")
    subnet_cidr: str = Field("", description="CIDR of that subnet")
    zone_id: str = Field("", description="Availability zone")
    address_pair_ips: list[str] = Field(
        default_factory=list, description="IPs of the interfaces currently sharing this VIP"
    )
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "VirtualIpItem":
        """Build a VirtualIpItem from a raw vServer VIP object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            description=data.get("description") or "",
            ip_address=data.get("ipAddress") or "",
            type=data.get("type") or "",
            mode=data.get("mode") or "",
            status=data.get("status") or "",
            vpc_id=data.get("networkId") or "",
            subnet_id=data.get("subnetId") or "",
            subnet_cidr=data.get("subnetCIDR") or "",
            zone_id=_zone_id(data),
            address_pair_ips=[str(ip) for ip in (data.get("addressPairIps") or [])],
            created_at=data.get("createdAt") or "",
        )


class VirtualIpListData(BaseModel):
    """Structured response for list_virtual_ips."""

    region: str = Field(..., description="Region the VIPs were fetched from")
    virtual_ips: list[VirtualIpItem] = Field(
        default_factory=list, description="Virtual IP addresses"
    )


class AddressPairItem(BaseModel):
    """One network interface bound to a VIP."""

    id: str = Field(..., description="Address pair ID (uuid) — needed to remove the binding")
    virtual_ip_id: str = Field("", description="VIP the interface is bound to")
    network_interface_id: str = Field("", description="Bound network interface")
    network_interface_ip: str = Field("", description="Fixed IP of that interface")
    virtual_subnet_id: str = Field("", description="Secondary subnet, when the pair is one")
    cidr: str = Field("", description="CIDR, when the pair is a secondary-subnet pair")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "AddressPairItem":
        """Build an AddressPairItem from a raw vServer address-pair object."""
        return cls(
            id=_resource_id(data),
            virtual_ip_id=data.get("virtualIpAddressId") or "",
            network_interface_id=data.get("networkInterfaceId") or "",
            network_interface_ip=data.get("networkInterfaceIp") or "",
            virtual_subnet_id=data.get("virtualSubnetId") or "",
            cidr=data.get("cidr") or "",
            created_at=data.get("createdAt") or "",
        )


class AddressPairListData(BaseModel):
    """Structured response for the address-pair listings."""

    region: str = Field(..., description="Region the address pairs were fetched from")
    parent_id: str = Field(..., description="VIP or secondary subnet the pairs belong to")
    address_pairs: list[AddressPairItem] = Field(
        default_factory=list, description="Bound network interfaces"
    )


class CandidateInterfaceItem(BaseModel):
    """One network interface that may be added to a VIP as an address pair."""

    id: str = Field(..., description="Network interface ID — pass as networkInterfaceId")
    fixed_ip: str = Field("", description="The interface's own IP")
    subnet_id: str = Field("", description="Subnet the interface sits in (null for external)")
    zone_id: str = Field("", description="Availability zone")

    @classmethod
    def from_api(cls, data: dict) -> "CandidateInterfaceItem":
        """Build a CandidateInterfaceItem from a raw candidate-interface object."""
        return cls(
            id=_resource_id(data),
            fixed_ip=data.get("fixedIp") or "",
            subnet_id=data.get("subnetId") or "",
            zone_id=_zone_id(data),
        )


class CandidateInterfaceListData(BaseModel):
    """Structured response for the VIP candidate-interface listings."""

    region: str = Field(..., description="Region the interfaces were fetched from")
    interfaces: list[CandidateInterfaceItem] = Field(
        default_factory=list, description="Interfaces eligible to join a VIP"
    )
