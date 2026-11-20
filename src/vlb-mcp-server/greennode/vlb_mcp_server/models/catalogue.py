"""Catalogue and discovery models.

Two sources feed these: the vLB gateway's own package/header catalogue, and the
vServer gateway projections a placement decision needs (zones, VPCs, subnets,
servers). The vServer projections are deliberately thin — just enough to pick a
subnet or a backend IP. Anything richer belongs to ``vserver-mcp-server``.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.models._common import _resource_id, _zone_id
from pydantic import BaseModel, Field


class PackageItem(BaseModel):
    """One load balancer package (the billed size/SKU)."""

    id: str = Field(..., description="Package ID — pass this as packageId when creating/resizing")
    name: str = Field("", description="Package name, e.g. Small")
    type: str = Field("", description="Package type")
    connection_number: int | None = Field(None, description="Concurrent connections included")
    data_transfer: int | None = Field(None, description="Included data transfer")
    mode: str = Field("", description="Package mode; only active packages can be bought")
    lb_type: str = Field(
        "", description="Load balancer type the package applies to ('Layer 4' / 'Layer 7')"
    )
    display_lb_type: str = Field("", description="Human-readable load balancer type")

    @classmethod
    def from_api(cls, data: dict) -> "PackageItem":
        """Build a PackageItem from a raw LoadBalancerPackageDto."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            type=data.get("type") or "",
            connection_number=data.get("connectionNumber"),
            data_transfer=data.get("dataTransfer"),
            mode=data.get("mode") or "",
            lb_type=data.get("lbType") or "",
            display_lb_type=data.get("displayLbType") or "",
        )


class PackageListData(BaseModel):
    """Structured response for list_load_balancer_packages."""

    region: str = Field(..., description="Region the packages were fetched from")
    scope: str = Field(
        ...,
        description="'active' (buyable now) or 'all' (includes packages no longer sold)",
    )
    zone_id: str = Field("", description="Zone the active packages were filtered to, if any")
    packages: list[PackageItem] = Field(default_factory=list, description="Available packages")


class HeaderNameListData(BaseModel):
    """Structured response for list_listener_headers."""

    region: str = Field(..., description="Region the header names were fetched from")
    headers: list[str] = Field(
        default_factory=list,
        description="Header names a listener may insert (use as insertHeaders[].headerName)",
    )


class ZoneItem(BaseModel):
    """One availability zone (projected from vServer)."""

    id: str = Field(..., description="Zone ID — pass this as zoneId to other tools")
    name: str = Field("", description="Zone name, e.g. HCM03-1A")
    description: str = Field("", description="Human-readable description")
    enabled: bool = Field(True, description="Whether the zone accepts new resources")

    @classmethod
    def from_api(cls, data: dict) -> "ZoneItem":
        """Build a ZoneItem from a raw vServer zone object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            description=data.get("description") or "",
            enabled=bool(data.get("isEnabled", True)),
        )


class ZoneListData(BaseModel):
    """Structured response for list_zones."""

    region: str = Field(..., description="Region the zones were fetched from")
    zones: list[ZoneItem] = Field(default_factory=list, description="Availability zones")


class VpcItem(BaseModel):
    """One VPC (projected from vServer)."""

    id: str = Field(..., description="VPC ID — pass this as vpc_id to list_subnets")
    name: str = Field("", description="VPC name")
    cidr: str = Field("", description="CIDR block of the VPC")
    status: str = Field("", description="Lifecycle status; only ACTIVE VPCs accept new resources")

    @classmethod
    def from_api(cls, data: dict) -> "VpcItem":
        """Build a VpcItem from a raw vServer network object.

        The VPC list calls the display name ``displayName``, not ``name``.
        """
        return cls(
            id=_resource_id(data),
            name=data.get("displayName") or data.get("name") or "",
            cidr=data.get("cidr") or "",
            status=data.get("status") or "",
        )


class VpcListData(BaseModel):
    """Structured response for list_vpcs."""

    region: str = Field(..., description="Region the VPCs were fetched from")
    vpcs: list[VpcItem] = Field(default_factory=list, description="VPCs in the project")


class SubnetItem(BaseModel):
    """One subnet (projected from vServer) — where a load balancer is placed."""

    id: str = Field(..., description="Subnet ID — pass this as subnetId to create_load_balancer")
    name: str = Field("", description="Subnet name")
    cidr: str = Field("", description="CIDR block of the subnet")
    status: str = Field(
        "", description="Lifecycle status; only ACTIVE subnets can host a load balancer"
    )
    zone_id: str = Field("", description="Availability zone — this pins the load balancer's zone")
    vpc_id: str = Field("", description="VPC the subnet belongs to")

    @classmethod
    def from_api(cls, data: dict) -> "SubnetItem":
        """Build a SubnetItem from a raw vServer subnet object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            cidr=data.get("cidr") or "",
            status=data.get("status") or "",
            zone_id=_zone_id(data),
            vpc_id=data.get("networkUuid") or data.get("networkId") or "",
        )


class SubnetListData(BaseModel):
    """Structured response for list_subnets."""

    region: str = Field(..., description="Region the subnets were fetched from")
    vpc_id: str = Field(..., description="VPC the subnets belong to")
    subnets: list[SubnetItem] = Field(default_factory=list, description="Subnets of the VPC")


class ServerItem(BaseModel):
    """One vServer instance — a candidate pool member."""

    id: str = Field(..., description="Server ID")
    name: str = Field("", description="Server name")
    status: str = Field("", description="Lifecycle status, e.g. ACTIVE, STOPPED")
    private_ip: str = Field(
        "", description="Primary private IP — this is what a pool member's ipAddress must be"
    )
    public_ip: str = Field("", description="Primary public IP, if attached")
    zone_id: str = Field("", description="Availability zone hosting the server")

    @classmethod
    def from_api(cls, data: dict) -> "ServerItem":
        """Build a ServerItem from a raw vServer server object.

        The addresses come from the first internal interface rather than a
        top-level field.
        """
        interfaces = data.get("internalInterfaces")
        first = interfaces[0] if isinstance(interfaces, list) and interfaces else {}
        if not isinstance(first, dict):
            first = {}
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            private_ip=first.get("fixedIp") or "",
            public_ip=first.get("floatingIp") or "",
            zone_id=_zone_id(data),
        )


class ServerListData(BaseModel):
    """Structured response for list_servers."""

    region: str = Field(..., description="Region the servers were fetched from")
    servers: list[ServerItem] = Field(default_factory=list, description="Servers in the project")
