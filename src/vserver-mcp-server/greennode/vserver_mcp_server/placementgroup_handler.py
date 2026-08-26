"""Placement group (server group) management for the vServer MCP server.

A placement group applies an affinity or anti-affinity policy to the servers
inside it, deciding whether they share physical hosts. Mirrors the
`grn vserver placement-group` command group.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreatePlacementGroupDto,
    PlacementGroupItem,
    PlacementGroupListData,
    PlacementGroupPolicyItem,
    PlacementGroupPolicyListData,
    UpdatePlacementGroupDto,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class PlacementGroupHandler:
    """Register and serve placement group MCP tools."""

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

        self.mcp.tool(name="list_placement_groups", annotations=READ)(self.list_placement_groups)
        self.mcp.tool(name="get_placement_group", annotations=READ)(self.get_placement_group)
        self.mcp.tool(name="list_placement_group_policies", annotations=READ)(
            self.list_placement_group_policies
        )

        if self.allow_write:
            self.mcp.tool(name="create_placement_group", annotations=WRITE)(
                self.create_placement_group
            )
            self.mcp.tool(name="update_placement_group", annotations=WRITE)(
                self.update_placement_group
            )
            self.mcp.tool(name="delete_placement_group", annotations=DESTRUCTIVE)(
                self.delete_placement_group
            )

    async def list_placement_groups(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the group name."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> PlacementGroupListData:
        """List the placement groups in the project.

        Returns {region, placement_groups[{id, name, description, policy_id,
        policy_name, server_ids}]}.

        ## Workflow
        - Optional step of the create_server flow: pass the chosen `id` as
          `serverGroupId` to place the new server under that policy.
        - Some groups are created by other products (marketplace apps); check
          `server_ids` before touching one.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[PlacementGroupItem]:
            params = {"name": name_filter} if name_filter else None
            raw = await fetch_all_items(
                self.client, f"/v2/{pid}/serverGroups", region=region, params=params
            )
            return [PlacementGroupItem.from_api(g) for g in raw]

        key = ("list_placement_groups", resolved_region, pid, name_filter)
        groups = await self.cache.get_or_fetch("list_placement_groups", key, fetch, refresh)
        return PlacementGroupListData(region=resolved_region, placement_groups=groups)

    async def get_placement_group(
        self,
        placement_group_id: str = Field(
            ..., description="Placement group ID from list_placement_groups."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PlacementGroupItem:
        """Get one placement group by id, including the servers inside it."""
        validate_id(placement_group_id, "placement_group_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/serverGroups/{placement_group_id}", region=region)
        return PlacementGroupItem.from_api(unwrap(data) or {})

    async def list_placement_group_policies(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> PlacementGroupPolicyListData:
        """List the placement policies a group can apply.

        Returns {policies[{id, name, description, status}]} — typically SOFT
        AFFINITY (prefer the same host), SOFT ANTI AFFINITY (prefer different
        hosts) and their strict variants.

        ## Workflow
        - Call this before create_placement_group and let the user choose: the
          policy decides whether their servers survive a single host failure.
          Anti-affinity is the usual choice for redundancy.
        - Use the chosen `id` as `policyId`.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> PlacementGroupPolicyListData:
            data = await self.client.get(f"/v2/{pid}/serverGroups/policies", region=region)
            return PlacementGroupPolicyListData(
                policies=[PlacementGroupPolicyItem.from_api(p) for p in as_list(data)]
            )

        key = ("list_placement_group_policies", resolved_region, pid)
        return await self.cache.get_or_fetch("list_placement_group_policies", key, fetch, refresh)

    async def create_placement_group(
        self,
        body: CreatePlacementGroupDto = Field(..., description="Placement group to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PlacementGroupItem:
        """Create a placement group.

        ## Requirements
        - Requires `--allow-write`.
        - `policyId` must come from list_placement_group_policies.
        - The policy is fixed at creation: it cannot be changed afterwards, only
          the name and description can.

        ## Workflow
        - Present the policies and let the user choose; explain the trade-off
          (affinity = low latency between servers, anti-affinity = survives a
          host failure). Do NOT pick a policy silently.
        - Servers join the group by passing its id as `serverGroupId` in
          create_server; an existing server cannot be moved into one.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/serverGroups", region=region, json=body.model_dump(exclude_none=True)
        )
        self.cache.invalidate("list_placement_groups")
        return PlacementGroupItem.from_api(unwrap(data) or {})

    async def update_placement_group(
        self,
        placement_group_id: str = Field(..., description="Placement group ID."),
        body: UpdatePlacementGroupDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PlacementGroupItem:
        """Rename a placement group or change its description.

        ## Requirements
        - Requires `--allow-write`.
        - `name` is mandatory on every call — pass the current name when only
          the description should change.
        - The policy cannot be changed; create a new group instead.
        """
        require_write(self.allow_write)
        validate_id(placement_group_id, "placement_group_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["serverGroupId"] = placement_group_id
        data = await self.client.put(
            f"/v2/{pid}/serverGroups/{placement_group_id}", region=region, json=payload
        )
        self.cache.invalidate("list_placement_groups")
        return PlacementGroupItem.from_api(unwrap(data) or {})

    async def delete_placement_group(
        self,
        placement_group_id: str = Field(..., description="Placement group ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a placement group. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - The group must be empty: every server in it has to be deleted first,
          because a server cannot be moved out of its group.

        ## Workflow
        - Call get_placement_group and show the user `server_ids` before asking
          for confirmation.
        """
        require_write(self.allow_write)
        validate_id(placement_group_id, "placement_group_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/serverGroups/{placement_group_id}", region=region)
        self.cache.invalidate("list_placement_groups")
        return f"Placement group {placement_group_id} deleted."
