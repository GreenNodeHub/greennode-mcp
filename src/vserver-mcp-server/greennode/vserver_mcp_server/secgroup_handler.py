"""Security group and rule management for the vServer MCP server.

A security group is a stateful firewall attached to a server's network
interfaces; its rules decide which traffic reaches the instance. Mirrors the
`grn vserver secgroup` and `grn vserver secgroup rule` command groups.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateSecurityGroupDto,
    CreateSecurityGroupRuleDto,
    SecurityGroupItem,
    SecurityGroupListData,
    SecurityGroupRuleItem,
    SecurityGroupRuleListData,
    SecurityGroupRuleSampleItem,
    SecurityGroupRuleSampleListData,
    ServerItem,
    ServerListData,
    UpdateSecurityGroupDto,
    UpdateSecurityGroupRuleDto,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_all_items, unwrap, unwrap_one
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


ICMP_TYPE_MAX = 255

PORT_MAX = 65535

_FULL_RANGE = {"icmp": (1, ICMP_TYPE_MAX)}
_DEFAULT_RANGE = (1, PORT_MAX)


def _validate_rule(body: CreateSecurityGroupRuleDto) -> None:
    """Check a rule body's port range for internal consistency.

    The two bounds go together, in order. For ``icmp`` the pair is an ICMP
    **type** range rather than ports, so it is capped at 255 — the API's own
    "All ICMP" preset is 1-255.
    """
    has_min = body.portRangeMin is not None
    has_max = body.portRangeMax is not None

    if has_min != has_max:
        raise ValueError(
            "portRangeMin and portRangeMax must be given together, or both omitted "
            "to cover the protocol's full range."
        )
    if not has_min:
        return
    if body.portRangeMin > body.portRangeMax:
        raise ValueError(
            f"portRangeMin ({body.portRangeMin}) must be <= portRangeMax ({body.portRangeMax})."
        )
    if body.protocol.lower() == "icmp" and body.portRangeMax > ICMP_TYPE_MAX:
        raise ValueError(
            "For protocol 'icmp' the port range is an ICMP type range and must stay "
            f"within 0-{ICMP_TYPE_MAX} (the API's 'All ICMP' preset is 1-255)."
        )


def _rule_payload(body: CreateSecurityGroupRuleDto) -> dict:
    """Serialise a rule body, filling the port range the API insists on.

    ``portRangeMin``/``portRangeMax`` are required by the API on every rule,
    including icmp and protocol-number rules where "ports" are meaningless.
    Omitting them here would mean a 400 for what reads like a complete request,
    so an omitted pair becomes the protocol's full range — 1-255 for icmp
    (ICMP types), 1-65535 otherwise, matching the API's own presets.
    """
    payload = body.model_dump(exclude_none=True)
    if body.portRangeMin is None:
        low, high = _FULL_RANGE.get(body.protocol.lower(), _DEFAULT_RANGE)
        payload["portRangeMin"] = low
        payload["portRangeMax"] = high
    return payload


class SecurityGroupHandler:
    """Register and serve security-group and rule MCP tools."""

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

        self.mcp.tool(name="list_security_groups", annotations=READ)(self.list_security_groups)
        self.mcp.tool(name="get_security_group", annotations=READ)(self.get_security_group)
        self.mcp.tool(name="list_security_group_rules", annotations=READ)(
            self.list_security_group_rules
        )
        self.mcp.tool(name="get_security_group_rule", annotations=READ)(
            self.get_security_group_rule
        )
        self.mcp.tool(name="list_security_group_rule_samples", annotations=READ)(
            self.list_security_group_rule_samples
        )
        self.mcp.tool(name="list_security_group_servers", annotations=READ)(
            self.list_security_group_servers
        )

        if self.allow_write:
            self.mcp.tool(name="create_security_group", annotations=WRITE)(
                self.create_security_group
            )
            self.mcp.tool(name="update_security_group", annotations=WRITE)(
                self.update_security_group
            )
            self.mcp.tool(name="delete_security_group", annotations=DESTRUCTIVE)(
                self.delete_security_group
            )
            self.mcp.tool(name="create_security_group_rule", annotations=WRITE)(
                self.create_security_group_rule
            )
            self.mcp.tool(name="update_security_group_rule", annotations=WRITE)(
                self.update_security_group_rule
            )
            self.mcp.tool(name="delete_security_group_rule", annotations=DESTRUCTIVE)(
                self.delete_security_group_rule
            )

    async def list_security_groups(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the group name, applied by the API."
        ),
        include_inactive: bool = Field(False, description="Include groups that are not ACTIVE."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> SecurityGroupListData:
        """List the security groups in the project.

        Returns {region, security_groups[{id, name, description, status,
        system}]}. `system=true` marks platform-managed groups — for example
        those VKS creates for a cluster's worker nodes, whose descriptions say
        "Please DO NOT DELETE it". Never offer those for deletion.

        ## Workflow
        - Optional step of the create_server flow: security groups are optional
          on a server. If the user wants them, present this list and let them
          choose one or more. IMPORTANT: do NOT pick silently.
        - Use the chosen `id`(s) as `securityGroup` in create_server or
          update_server_security_groups.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[SecurityGroupItem]:
            params = {"name": name_filter} if name_filter else None
            raw = await fetch_all_items(
                self.client, f"/v2/{pid}/secgroups", region=region, params=params
            )
            return [SecurityGroupItem.from_api(g) for g in raw]

        key = ("list_security_groups", resolved_region, pid, name_filter)
        groups = await self.cache.get_or_fetch("list_security_groups", key, fetch, refresh)

        if not include_inactive:
            groups = [g for g in groups if g.status == "ACTIVE"]
        return SecurityGroupListData(region=resolved_region, security_groups=groups)

    async def get_security_group(
        self,
        security_group_id: str = Field(
            ..., description="Security group ID from list_security_groups."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupItem:
        """Get one security group by id."""
        validate_id(security_group_id, "security_group_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/secgroups/{security_group_id}", region=region)
        return SecurityGroupItem.from_api(unwrap(data) or {})

    async def list_security_group_rules(
        self,
        security_group_id: str = Field(
            ..., description="Security group ID from list_security_groups."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupRuleListData:
        """List the rules of a security group.

        Returns {security_group_id, rules[{id, direction, protocol, ether_type,
        port_range_min, port_range_max, remote_ip_prefix, remote_group_id,
        description, status}]}.

        A freshly created group already carries default egress rules for IPv4
        and IPv6 that allow all outbound traffic — read this before adding
        rules so you do not duplicate them.
        """
        validate_id(security_group_id, "security_group_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/secgroups/{security_group_id}/secGroupRules", region=region
        )
        return SecurityGroupRuleListData(
            security_group_id=security_group_id,
            rules=[SecurityGroupRuleItem.from_api(r) for r in as_list(data)],
        )

    async def get_security_group_rule(
        self,
        security_group_id: str = Field(..., description="Security group ID."),
        rule_id: str = Field(..., description="Rule ID from list_security_group_rules."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupRuleItem:
        """Get one security group rule by id.

        The API answers this with a one-element array inside a ``data``
        envelope rather than a bare object; the response is normalised here.
        """
        validate_id(security_group_id, "security_group_id")
        validate_id(rule_id, "rule_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/secgroups/{security_group_id}/secgroupRules/{rule_id}", region=region
        )
        return SecurityGroupRuleItem.from_api(unwrap_one(data))

    async def list_security_group_rule_samples(
        self,
        security_group_id: str = Field(
            ..., description="Any security group ID; the presets are the same for all of them."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupRuleSampleListData:
        """List the rule presets the vServer console offers.

        Returns {samples[{name, protocol, port_range_min, port_range_max}]} —
        30 named shortcuts: 'All TCP' (tcp 1-65535), 'All ICMP' (icmp 1-255),
        the usual service ports, and the protocol-number entries ('GRE' 47,
        'ESP' 50, 'AH' 51, 'VRRP' 112) that IPsec and keepalived need.

        ## Workflow
        - Call this before create_security_group_rule when the user describes a
          rule in words ("allow SSH", "open all ICMP"): match their intent to a
          preset and reuse its protocol and port range instead of guessing.
        - **'SSH' is port 22 but 'SSH VNG' is port 234, and 'RDP' is 3389 while
          'RDP VNG' is 3490.** GreenNode images listen on the VNG ports, and the
          default security group opens those — offer the VNG preset first for a
          GreenNode image and say why.
        - The preset does not carry a direction or a remote CIDR; still ask the
          user for those.
        """
        validate_id(security_group_id, "security_group_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/secgroups/{security_group_id}/secgroupRules/samples", region=region
        )
        return SecurityGroupRuleSampleListData(
            samples=[SecurityGroupRuleSampleItem.from_api(s) for s in as_list(data)]
        )

    async def create_security_group(
        self,
        body: CreateSecurityGroupDto = Field(..., description="Security group to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupItem:
        """Create a security group.

        ## Requirements
        - Requires `--allow-write`.
        - `name` must be unique within the project.

        ## Workflow
        - A new group allows all outbound traffic and **no** inbound traffic.
          Add inbound rules with create_security_group_rule before expecting a
          server behind it to be reachable.
        - Attach it to a server at creation (`securityGroup` in create_server)
          or afterwards with update_server_security_groups.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/secgroups", region=region, json=payload)
        self.cache.invalidate("list_security_groups")
        return SecurityGroupItem.from_api(unwrap(data) or {})

    async def update_security_group(
        self,
        security_group_id: str = Field(..., description="Security group ID."),
        body: UpdateSecurityGroupDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupItem:
        """Rename a security group or change its description.

        ## Requirements
        - Requires `--allow-write`.
        - `name` is mandatory on every call — pass the current name when you
          only mean to change the description.
        - Do not rename `system=true` groups; the platform matches some of them
          by name.
        """
        require_write(self.allow_write)
        validate_id(security_group_id, "security_group_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v2/{pid}/secgroups/{security_group_id}", region=region, json=payload
        )
        self.cache.invalidate("list_security_groups")
        return SecurityGroupItem.from_api(unwrap(data) or {})

    async def delete_security_group(
        self,
        security_group_id: str = Field(..., description="Security group ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a security group. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - The group must not be attached to any server; detach it first with
          update_server_security_groups.
        - Refuse `system=true` groups: they belong to another product (VKS
          clusters, marketplace apps) and deleting one breaks it.

        ## Workflow
        - Show the user the group's id, name and rule count, and get explicit
          confirmation before calling.
        """
        require_write(self.allow_write)
        validate_id(security_group_id, "security_group_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/secgroups/{security_group_id}", region=region)
        self.cache.invalidate("list_security_groups")
        return f"Security group {security_group_id} deleted."

    async def create_security_group_rule(
        self,
        security_group_id: str = Field(..., description="Security group to add the rule to."),
        body: CreateSecurityGroupRuleDto = Field(..., description="Rule to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupRuleItem:
        """Add a rule to a security group.

        Security groups only **allow**; there is no deny rule, and they are
        stateful, so a reply to allowed traffic needs no matching rule in the
        other direction. To deny something, use a network ACL instead.

        ## Requirements
        - Requires `--allow-write`.
        - `portRangeMin`/`portRangeMax` go together, min <= max. Omit both and
          the protocol's full range is sent (1-255 for icmp, 1-65535 otherwise).
        - For `icmp` the pair is an ICMP **type** range, not ports.
        - `remoteIpPrefix` is a CIDR: `0.0.0.0/0` means anywhere.
        - `etherType` must match the address family of `remoteIpPrefix` —
          IPv6 rules need `::/0`-style prefixes.

        ## Workflow
        - Call list_security_group_rule_samples first and reuse the API's own
          preset rather than guessing a port. **GreenNode images listen for SSH
          on port 234 and RDP on 3490** ("SSH VNG" / "RDP VNG" in the presets),
          not on 22/3389 — a rule for 22 usually locks the user out.
        - Opening a port to `0.0.0.0/0` exposes it to the whole internet. Say so
          explicitly and confirm, especially for remote access and database
          ports. Prefer the narrowest CIDR that works — the VPC CIDR for
          internal-only access.
        """
        require_write(self.allow_write)
        validate_id(security_group_id, "security_group_id")
        _validate_rule(body)
        pid = await require_project_id(self.config, self.client, region)
        payload = _rule_payload(body)
        data = await self.client.post(
            f"/v2/{pid}/secgroups/{security_group_id}/secgroupRules",
            region=region,
            json=payload,
        )
        return SecurityGroupRuleItem.from_api(unwrap(data) or {})

    async def update_security_group_rule(
        self,
        security_group_id: str = Field(..., description="Security group ID."),
        rule_id: str = Field(..., description="Rule ID from list_security_group_rules."),
        body: UpdateSecurityGroupRuleDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SecurityGroupRuleItem:
        """Update a rule's description or tags.

        ## Requirements
        - Requires `--allow-write`.
        - Only the description and tags are editable. Direction, protocol, port
          range, ether type and remote CIDR are **immutable** — to change any of
          them, delete the rule and create a replacement.
        """
        require_write(self.allow_write)
        validate_id(security_group_id, "security_group_id")
        validate_id(rule_id, "rule_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v2/{pid}/secgroups/{security_group_id}/secgroupRules/{rule_id}",
            region=region,
            json=payload,
        )
        return SecurityGroupRuleItem.from_api(unwrap(data) or {})

    async def delete_security_group_rule(
        self,
        security_group_id: str = Field(..., description="Security group ID."),
        rule_id: str = Field(..., description="Rule ID from list_security_group_rules."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a rule from a security group. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Removing an egress rule can cut a running server's outbound access,
          and removing the ingress rule that carries remote access (port 234 on
          a GreenNode image, 22 elsewhere) locks the user out of the instance.

        ## Workflow
        - Show the user the rule's direction, protocol, ports and remote CIDR,
          and get explicit confirmation before calling.
        """
        require_write(self.allow_write)
        validate_id(security_group_id, "security_group_id")
        validate_id(rule_id, "rule_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/secgroups/{security_group_id}/secgroupRules/{rule_id}", region=region
        )
        return f"Rule {rule_id} deleted from security group {security_group_id}."

    async def list_security_group_servers(
        self,
        security_group_id: str = Field(
            ..., description="Security group ID from list_security_groups."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerListData:
        """List the servers a security group is attached to.

        Same shape as list_servers, narrowed to one group.

        ## Workflow
        - Call this before changing or deleting a group: it is the blast radius.
          Every server here is affected the moment a rule changes.
        - delete_security_group fails while servers are still attached — this
          shows which ones have to be moved first.
        """
        validate_id(security_group_id, "security_group_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/secgroups/{security_group_id}/servers", region=region
        )
        return ServerListData(
            region=region or self.config.default_region,
            servers=[ServerItem.from_api(s) for s in as_list(data)],
        )
