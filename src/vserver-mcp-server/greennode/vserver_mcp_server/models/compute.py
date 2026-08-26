"""Instance models: servers, their interfaces, console access and audit trail.

SSH keys and placement groups live here too: neither means anything on its
own, both only exist to be attached to an instance.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.models._common import _resource_id, _zone_id
from greennode.vserver_mcp_server.models.network import (
    SecurityGroupItem,
    SecurityGroupRuleItem,
)
from pydantic import BaseModel, Field


class ServerItem(BaseModel):
    """One vServer instance."""

    id: str = Field(..., description="Server ID — pass this as serverId to other tools")
    name: str = Field("", description="Server name")
    status: str = Field("", description="Lifecycle status, e.g. ACTIVE, STOPPED, CREATING")
    private_ip: str = Field("", description="Primary private (fixed) IP address")
    public_ip: str = Field("", description="Primary public (floating) IP address, if attached")
    zone_id: str = Field("", description="Availability zone hosting the server")
    flavor_id: str = Field("", description="Flavor the server runs on")
    image_id: str = Field("", description="Image the server was created from")
    boot_volume_id: str = Field("", description="Root volume of the server")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "ServerItem":
        """Build a ServerItem from a raw vServer server object.

        The flavor and image arrive as nested objects, and the addresses come
        from the first internal interface rather than a top-level field.
        """
        flavor = data.get("flavor")
        image = data.get("image")
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
            flavor_id=(flavor or {}).get("flavorId", "") if isinstance(flavor, dict) else "",
            image_id=(image or {}).get("id", "") if isinstance(image, dict) else "",
            boot_volume_id=data.get("bootVolumeId") or "",
            created_at=data.get("createdAt") or "",
        )


class ServerListData(BaseModel):
    """Structured response for list_servers."""

    region: str = Field(..., description="Region the servers were fetched from")
    servers: list[ServerItem] = Field(default_factory=list, description="Servers in the project")


class NetworkInterfaceItem(BaseModel):
    """A network interface, attached to a server or standing alone (elastic)."""

    id: str = Field(..., description="Network interface ID")
    name: str = Field("", description="Interface name")
    fixed_ip: str = Field("", description="Private IP assigned to the interface")
    floating_ip: str = Field("", description="Public IP attached to the interface, if any")
    floating_ip_id: str = Field("", description="ID of the attached floating IP, if any")
    status: str = Field("", description="Lifecycle status")
    interface_type: str = Field("", description="PRIVATE (internal) or PUBLIC (external)")
    subnet_id: str = Field("", description="Subnet the interface lives on")
    vpc_id: str = Field("", description="VPC the interface belongs to")
    server_id: str = Field("", description="Server the interface is attached to, if any")
    zone_id: str = Field("", description="Availability zone")
    mac: str = Field("", description="MAC address")

    @classmethod
    def from_api(cls, data: dict) -> "NetworkInterfaceItem":
        """Build a NetworkInterfaceItem from a raw interface object.

        Server interfaces and elastic interfaces describe the same thing with
        different spellings. A server interface links back through
        ``serverUuid`` / ``networkUuid`` / ``subnetUuid`` and holds a private
        ``fixedIp``; an elastic interface uses ``serverId`` / ``vpcId`` and
        carries its **public** address in ``ip`` next to ``elasticIpId`` —
        reading that as a private IP would mislabel a WAN address.
        """
        elastic_ip_id = data.get("elasticIpId") or ""
        ip = data.get("ip") or ""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            fixed_ip=data.get("fixedIp") or ("" if elastic_ip_id else ip),
            floating_ip=data.get("floatingIp") or (ip if elastic_ip_id else ""),
            floating_ip_id=elastic_ip_id or data.get("floatingIpId") or "",
            status=data.get("status") or "",
            interface_type=data.get("interfaceType") or "",
            subnet_id=data.get("subnetUuid") or data.get("subnetId") or "",
            vpc_id=data.get("networkUuid") or data.get("vpcId") or "",
            server_id=data.get("serverUuid") or data.get("serverId") or "",
            zone_id=_zone_id(data),
            mac=data.get("mac") or "",
        )


class NetworkInterfaceListData(BaseModel):
    """Structured response for list_network_interfaces."""

    region: str = Field(..., description="Region the interfaces were fetched from")
    network_interfaces: list[NetworkInterfaceItem] = Field(
        default_factory=list, description="Elastic network interfaces in the project"
    )


class ServerInterfacesData(BaseModel):
    """Structured response for list_server_interfaces."""

    server_id: str = Field(..., description="Server the interfaces belong to")
    internal_interfaces: list[NetworkInterfaceItem] = Field(
        default_factory=list, description="Private interfaces attached to the server"
    )
    external_interfaces: list[NetworkInterfaceItem] = Field(
        default_factory=list, description="Public (elastic) interfaces attached to the server"
    )


class ConsoleUrlData(BaseModel):
    """Structured response for get_server_console_url."""

    server_id: str = Field(..., description="Server the console belongs to")
    url: str = Field("", description="Time-limited URL of the browser VNC console")


class ConsoleLogData(BaseModel):
    """Structured response for get_server_console_log."""

    server_id: str = Field(..., description="Server the log came from")
    log: str = Field(..., description="Raw serial-console output, oldest line first")
    truncated: bool = Field(
        False, description="True when the log was cut to the requested line count"
    )


class ServerSecurityData(BaseModel):
    """Structured response for list_server_security_groups.

    The API answers with the server's effective firewall **rules** split by
    direction rather than with a list of groups, so the owning groups are
    resolved here by name against the project's security groups.
    """

    server_id: str = Field(..., description="Server the rules apply to")
    security_groups: list[SecurityGroupItem] = Field(
        default_factory=list,
        description="Groups attached to the server, resolved from the rules' group names",
    )
    unresolved_group_names: list[str] = Field(
        default_factory=list,
        description="Group names seen on the rules that matched no security group in the project",
    )
    inbound_rules: list[SecurityGroupRuleItem] = Field(
        default_factory=list, description="Effective ingress rules"
    )
    outbound_rules: list[SecurityGroupRuleItem] = Field(
        default_factory=list, description="Effective egress rules"
    )


class ServerActionItem(BaseModel):
    """One entry of a server's action history."""

    action: str = Field("", description="What happened, e.g. create, resize, reboot")
    started_at: str = Field("", description="When the action started")
    source: str = Field("", description="Who triggered it, e.g. API or CONSOLE")

    @classmethod
    def from_api(cls, data: dict) -> "ServerActionItem":
        """Build a ServerActionItem from a raw server action entry."""
        return cls(
            action=data.get("action") or "",
            started_at=data.get("startTime") or "",
            source=data.get("userAction") or "",
        )


class ServerActionListData(BaseModel):
    """Structured response for list_server_actions."""

    server_id: str = Field(..., description="Server the history belongs to")
    actions: list[ServerActionItem] = Field(
        default_factory=list, description="Recent actions, newest first"
    )


class SshKeyItem(BaseModel):
    """One SSH key registered in the project."""

    id: str = Field(..., description="SSH key ID — pass this as sshKeyId to create_server")
    name: str = Field("", description="SSH key name")
    public_key: str = Field("", description="The public key material")
    status: str = Field("", description="Lifecycle status")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "SshKeyItem":
        """Build an SshKeyItem from a raw vServer SSH key object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            public_key=(data.get("pubKey") or data.get("publicKey") or "").strip(),
            status=data.get("status") or "",
            created_at=data.get("createdAt") or "",
        )


class SshKeyListData(BaseModel):
    """Structured response for list_ssh_keys."""

    region: str = Field(..., description="Region the keys were fetched from")
    ssh_keys: list[SshKeyItem] = Field(default_factory=list, description="SSH keys in the project")


class CreatedSshKeyData(BaseModel):
    """Structured response for create_ssh_key.

    The private key is returned **once, at creation only** and is never
    retrievable again.
    """

    id: str = Field(..., description="SSH key ID")
    name: str = Field("", description="SSH key name")
    public_key: str = Field("", description="The public key material")
    private_key: str = Field(
        "",
        description=(
            "The generated private key. Shown only in this response and never "
            "recoverable afterwards — hand it to the user immediately and tell them "
            "to store it securely."
        ),
    )

    @classmethod
    def from_api(cls, data: dict) -> "CreatedSshKeyData":
        """Build a CreatedSshKeyData from a raw create-SSH-key response."""
        private = ""
        for key in ("privateKey", "private_key", "priKey", "privatekey"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                private = value
                break
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            public_key=(data.get("pubKey") or data.get("publicKey") or "").strip(),
            private_key=private,
        )


class PlacementGroupItem(BaseModel):
    """One placement group (server group)."""

    id: str = Field(..., description="Placement group ID — pass this as serverGroupId")
    name: str = Field("", description="Placement group name")
    description: str = Field("", description="Description")
    policy_id: str = Field("", description="Placement policy applied to the group")
    policy_name: str = Field("", description="Human-readable policy name")
    server_ids: list[str] = Field(
        default_factory=list, description="Servers currently placed in the group"
    )

    @classmethod
    def from_api(cls, data: dict) -> "PlacementGroupItem":
        """Build a PlacementGroupItem from a raw serverGroups object.

        The string ``uuid`` is the id other endpoints expect; the numeric
        ``serverGroupId`` is internal and deliberately not exposed.
        """
        servers = data.get("servers")
        return cls(
            id=data.get("uuid") or "",
            name=data.get("name") or "",
            description=data.get("description") or "",
            policy_id=data.get("policyId") or "",
            policy_name=data.get("policyName") or "",
            server_ids=[
                s.get("uuid", "")
                for s in (servers if isinstance(servers, list) else [])
                if isinstance(s, dict) and s.get("uuid")
            ],
        )


class PlacementGroupListData(BaseModel):
    """Structured response for list_placement_groups."""

    region: str = Field(..., description="Region the groups were fetched from")
    placement_groups: list[PlacementGroupItem] = Field(
        default_factory=list, description="Placement groups in the project"
    )


class PlacementGroupPolicyItem(BaseModel):
    """One placement policy that a placement group can apply."""

    id: str = Field(..., description="Policy ID — pass this as policyId")
    name: str = Field("", description="Policy name, e.g. SOFT ANTI AFFINITY")
    description: str = Field("", description="What the policy does")
    status: str = Field("", description="Whether the policy is selectable")

    @classmethod
    def from_api(cls, data: dict) -> "PlacementGroupPolicyItem":
        """Build a PlacementGroupPolicyItem from a raw serverGroups/policies entry."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            description=data.get("description") or "",
            status=data.get("status") or "",
        )


class PlacementGroupPolicyListData(BaseModel):
    """Structured response for list_placement_group_policies."""

    policies: list[PlacementGroupPolicyItem] = Field(
        default_factory=list, description="Available placement policies"
    )
