"""Network ACL management for the vServer MCP server.

A network ACL is the **subnet-level** firewall, one layer outside the
instance-level security group: inbound traffic clears the ACL before it reaches
a security group, outbound traffic clears the security group first. Unlike
security groups, an ACL is stateless and can deny as well as allow.
"""

from __future__ import annotations

import asyncio
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateNetworkAclDto,
    NetworkAclItem,
    NetworkAclListData,
    NetworkAclRuleItem,
    NetworkAclRuleListData,
    UpdateNetworkAclRulesDto,
    UpdateNetworkAclSubnetsDto,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_paged_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


SYSTEM_SEQ_NUMBERS = (0, 2000)

_RULE_FIELDS = ("type", "seqNumber", "protocol", "port", "source", "action")


def _rule_key(rule: dict) -> tuple:
    """Identity of a rule for comparing what was asked for with what landed."""
    return (
        str(rule.get("type", "")).lower(),
        int(rule.get("seqNumber") or 0),
        str(rule.get("protocol", "")).lower(),
        str(rule.get("port", "")),
        str(rule.get("source", "")),
        str(rule.get("action", "")).lower(),
    )


def _merge_with_platform_defaults(requested: list[dict], current: list[dict]) -> list[dict]:
    """Return the rule list to send, with the platform's bookend rules kept.

    The rules endpoint replaces the whole set, and the allow-at-0 / deny-at-2000
    pair each direction ships with is part of that set — leaving it out of the
    body deletes it, which silently turns an ACL into deny-all inbound. Those
    rules carry no marker of their own, so they are recognised by their sequence
    numbers and re-appended whenever the caller did not resend them.
    """
    keys = {_rule_key(rule) for rule in requested}
    merged = list(requested)
    for rule in current:
        if int(rule.get("seqNumber") or 0) not in SYSTEM_SEQ_NUMBERS:
            continue
        if _rule_key(rule) in keys:
            continue
        merged.append({field: rule.get(field) for field in _RULE_FIELDS})
    return merged


def _split_rules(rules: list[NetworkAclRuleItem]) -> tuple[list, list]:
    """Split ACL rules into inbound and outbound, each ordered by evaluation."""
    inbound = sorted(
        (r for r in rules if r.direction.lower() == "inbound"), key=lambda r: r.seq_number
    )
    outbound = sorted(
        (r for r in rules if r.direction.lower() == "outbound"), key=lambda r: r.seq_number
    )
    return inbound, outbound


class NetworkAclHandler:
    """Register and serve network-ACL MCP tools."""

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

        self.mcp.tool(name="list_network_acls", annotations=READ)(self.list_network_acls)
        self.mcp.tool(name="get_network_acl", annotations=READ)(self.get_network_acl)
        self.mcp.tool(name="list_network_acl_rules", annotations=READ)(self.list_network_acl_rules)

        if self.allow_write:
            self.mcp.tool(name="create_network_acl", annotations=WRITE)(self.create_network_acl)
            self.mcp.tool(name="update_network_acl_rules", annotations=WRITE)(
                self.update_network_acl_rules
            )
            self.mcp.tool(name="update_network_acl_subnets", annotations=WRITE)(
                self.update_network_acl_subnets
            )
            self.mcp.tool(name="delete_network_acl", annotations=DESTRUCTIVE)(
                self.delete_network_acl
            )

    async def list_network_acls(
        self,
        name_filter: str = Field("", description="Optional substring match on the ACL name."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> NetworkAclListData:
        """List the network ACLs in the project.

        Returns {region, network_acls[{id, name, status, vpc_id, is_default,
        subnet_ids, rules, created_at}]}. The list view carries no rules or
        subnet associations — call get_network_acl for those.

        ## Workflow
        - `is_default=true` marks the ACL a subnet falls back to when it is not
          explicitly associated with another; it cannot be deleted.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[NetworkAclItem]:
            raw = await fetch_paged_items(
                self.client, f"/v2/{pid}/network-acl/list", region=region, name=name_filter or ""
            )
            return [NetworkAclItem.from_api(a) for a in raw]

        key = ("list_network_acls", resolved_region, pid, name_filter)
        acls = await self.cache.get_or_fetch("list_network_acls", key, fetch, refresh)
        return NetworkAclListData(region=resolved_region, network_acls=acls)

    async def get_network_acl(
        self,
        network_acl_id: str = Field(..., description="ACL ID from list_network_acls."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkAclItem:
        """Get one network ACL by id, with its rules and associated subnets.

        Returns the ACL plus `rules` (both directions, unsorted) and
        `subnet_ids`. Read both before calling update_network_acl_rules or
        update_network_acl_subnets — each replaces the whole set it manages.
        """
        validate_id(network_acl_id, "network_acl_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/network-acl/{network_acl_id}", region=region)
        return NetworkAclItem.from_api(unwrap(data) or {})

    async def list_network_acl_rules(
        self,
        network_acl_id: str = Field(..., description="ACL ID from list_network_acls."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkAclRuleListData:
        """List the rules of one network ACL, split by direction.

        Returns {region, network_acl_id, inbound[], outbound[]} with each list
        sorted by `seq_number` — which is also **evaluation order**: the first
        rule that matches decides, and later rules never run.

        Every ACL ships four immutable default rules (`system=true`): allow-all
        at seq 0 and deny-all at seq 2000, in each direction. A custom rule only
        has an effect if its `seq_number` sits between them.
        """
        validate_id(network_acl_id, "network_acl_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/network-acl/{network_acl_id}/rules", region=region
        )
        rules = [NetworkAclRuleItem.from_api(r) for r in as_list(data)]
        inbound, outbound = _split_rules(rules)
        return NetworkAclRuleListData(
            region=region or self.config.default_region,
            network_acl_id=network_acl_id,
            inbound=inbound,
            outbound=outbound,
        )

    async def create_network_acl(
        self,
        body: CreateNetworkAclDto = Field(..., description="Network ACL to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkAclItem:
        """Create a network ACL in a VPC.

        ## Requirements
        - Requires `--allow-write`.
        - `vpc` is a VPC id from list_vpcs. An ACL cannot span VPCs.
        - The new ACL starts with the four default rules only and is associated
          with no subnets, so it has no effect until
          update_network_acl_subnets attaches one.

        ## Workflow
        - Create the ACL, add rules with update_network_acl_rules, and only
          then associate subnets — attaching first means a window where the
          subnet is governed by allow-all defaults.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/network-acl", region=region, json=payload)
        self.cache.invalidate("list_network_acls")
        return NetworkAclItem.from_api(unwrap(data) or {})

    async def update_network_acl_rules(
        self,
        network_acl_id: str = Field(..., description="ACL ID from list_network_acls."),
        body: UpdateNetworkAclRulesDto = Field(
            ..., description="The complete rule set, both directions together."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkAclItem:
        """Replace the rules of a network ACL.

        ## Requirements
        - Requires `--allow-write`.
        - This is a **full replacement** covering both directions: any rule
          missing from `detailAclRuleList` is removed. Call
          list_network_acl_rules first and resend the rules you want to keep.
        - The platform's allow-at-0 and deny-at-2000 rules are part of that set.
          This tool re-appends any of them the caller left out, so an edit
          cannot silently turn the ACL into deny-all.
        - `protocol` is sent lowercase; the API rejects the uppercase spelling
          it returns on reads.
        - An ACL is **stateless**: allowing inbound traffic does NOT allow the
          reply. Every request/response pair needs a rule in each direction.
        - `seqNumber` decides everything — the first match wins. Keep custom
          rules between 1 and 1999 or they never run.

        ## Workflow
        - Show the user the resulting ordered rule list, both directions, and
          get explicit confirmation. A wrong ACL can cut off every instance in
          the associated subnets at once.
        - The change is applied asynchronously; this tool waits for the ACL to
          leave UPDATING and then re-reads the rules, so what it returns is what
          the platform actually kept.
        """
        require_write(self.allow_write)
        validate_id(network_acl_id, "network_acl_id")
        pid = await require_project_id(self.config, self.client, region)
        current = as_list(
            await self.client.get(f"/v2/{pid}/network-acl/{network_acl_id}/rules", region=region)
        )
        requested = [
            {**rule, "protocol": str(rule.get("protocol", "")).lower()}
            for rule in body.model_dump(exclude_none=True)["detailAclRuleList"]
        ]
        payload = {
            "aclId": network_acl_id,
            "detailAclRuleList": _merge_with_platform_defaults(requested, current),
        }
        await self.client.put(
            f"/v2/{pid}/network-acl/{network_acl_id}/rules", region=region, json=payload
        )
        self.cache.invalidate("list_network_acls")
        acl = await self._wait_for_acl(pid, network_acl_id, region)
        landed = {_rule_key(r) for r in as_list(acl.get("aclPolicyRules"))}
        missing = [r for r in requested if _rule_key(r) not in landed]
        if missing:
            raise RuntimeError(
                f"The platform accepted the request but did not keep "
                f"{len(missing)} of the {len(requested)} rules sent "
                f"(first missing: {missing[0]}). The ACL now holds only the rules "
                f"list_network_acl_rules reports — re-check it before relying on this ACL."
            )
        return NetworkAclItem.from_api(acl)

    async def _wait_for_acl(
        self, pid: str, network_acl_id: str, region: str | None, attempts: int = 15
    ) -> dict:
        """Poll an ACL until it leaves UPDATING, then return its detail object.

        Rule and subnet changes are applied asynchronously: the write answers
        immediately with `status: UPDATING` and empty rule lists, and a second
        write against a busy ACL fails with "is busy doing something". Waiting
        here means callers see the settled state instead of an empty shell.
        """
        detail: dict = {}
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(2)
            detail = unwrap(
                await self.client.get(f"/v2/{pid}/network-acl/{network_acl_id}", region=region)
            )
            detail = detail if isinstance(detail, dict) else {}
            if str(detail.get("status", "")).upper() != "UPDATING":
                return detail
        return detail

    async def update_network_acl_subnets(
        self,
        network_acl_id: str = Field(..., description="ACL ID from list_network_acls."),
        body: UpdateNetworkAclSubnetsDto = Field(
            ..., description="The complete set of subnets this ACL should govern."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkAclItem:
        """Set which subnets a network ACL governs.

        ## Requirements
        - Requires `--allow-write`.
        - This is a **full replacement**: subnets missing from `subnetUuids`
          are detached and revert to the VPC's default ACL.
        - A subnet can be governed by only one ACL at a time, so attaching a
          subnet here silently detaches it from whichever ACL held it before.
        - The subnets must belong to the same VPC as the ACL.

        ## Workflow
        - Call get_network_acl for the current associations and
          list_network_acl_rules for what is about to apply, show the user both,
          then confirm. Every instance in the subnet is affected at once.
        """
        require_write(self.allow_write)
        validate_id(network_acl_id, "network_acl_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["aclId"] = network_acl_id
        data = await self.client.put(
            f"/v2/{pid}/network-acl/{network_acl_id}/subnets", region=region, json=payload
        )
        self.cache.invalidate("list_network_acls")
        return NetworkAclItem.from_api(unwrap(data) or {})

    async def delete_network_acl(
        self,
        network_acl_id: str = Field(..., description="ACL ID from list_network_acls."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a network ACL. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - A VPC's default ACL (`is_default=true`) cannot be deleted.
        - Subnets still associated with the ACL fall back to the VPC's default
          ACL, which allows all traffic — deleting a restrictive ACL **opens**
          those subnets up.

        ## Workflow
        - Call get_network_acl, show the user the rules and the subnets that
          would lose them, and get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(network_acl_id, "network_acl_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/network-acl/{network_acl_id}", region=region)
        self.cache.invalidate("list_network_acls")
        return f"Network ACL {network_acl_id} deleted."
