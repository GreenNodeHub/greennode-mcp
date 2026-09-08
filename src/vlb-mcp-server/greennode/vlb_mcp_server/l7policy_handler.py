"""Layer 7 routing policies — per-listener rules that override the default pool.

One spec artefact matters here: the OpenAPI document declares **two** path
templates for the same URL, ``.../l7policies/{l7PolicyId}`` for the GET and
``.../l7policies/{policyId}`` for the PUT and DELETE. They are one endpoint.
All four tools take a single ``policy_id`` and build the identical path; a test
asserts that. Do not "fix" this by adding a second tool.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import Region, VlbConfig
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.guards import require_write
from greennode.vlb_mcp_server.models import (
    CreatedResourceData,
    CreateL7PolicyDto,
    L7PolicyItem,
    L7PolicyListData,
    ReorderPoliciesDto,
    ReorderPolicyDto,
    UpdateL7PolicyDto,
)
from greennode.vlb_mcp_server.paging import as_list, unwrap_one
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vlb_mcp_server.validators import validate_id, validate_lb_id
from pydantic import Field


class L7PolicyHandler:
    """Register and serve the L7 policy MCP tools."""

    def __init__(
        self,
        mcp,
        config: VlbConfig,
        client: VlbClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_l7_policies", annotations=READ)(self.list_l7_policies)
        self.mcp.tool(name="get_l7_policy", annotations=READ)(self.get_l7_policy)

        if self.allow_write:
            self.mcp.tool(name="create_l7_policy", annotations=WRITE)(self.create_l7_policy)
            self.mcp.tool(name="update_l7_policy", annotations=WRITE)(self.update_l7_policy)
            self.mcp.tool(name="delete_l7_policy", annotations=DESTRUCTIVE)(self.delete_l7_policy)
            self.mcp.tool(name="update_l7_policy_order", annotations=WRITE)(
                self.update_l7_policy_order
            )

    async def _listener_path(
        self, load_balancer_id: str, listener_id: str, region: str | None
    ) -> str:
        """Validate both ids and build the listener path the policies hang off."""
        validate_lb_id(load_balancer_id)
        validate_id(listener_id, "listener_id")
        pid = await require_project_id(self.config, self.client, region)
        return f"/v2/{pid}/loadBalancers/{load_balancer_id}/listeners/{listener_id}"

    async def list_l7_policies(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> L7PolicyListData:
        """List the L7 routing policies of a listener.

        Returns {load_balancer_id, listener_id, policies[{id, name, action,
        redirect_pool_id, redirect_url, position, rules[...]}]}.

        `position` decides evaluation order — the first policy whose rules all
        match wins, and traffic matching none goes to the listener's
        `default_pool_id`. Read positions from here before calling
        `update_l7_policy_order`.
        """
        path = await self._listener_path(load_balancer_id, listener_id, region)
        raw = await self.client.get(f"{path}/l7policies", region=region)
        return L7PolicyListData(
            load_balancer_id=load_balancer_id,
            listener_id=listener_id,
            policies=[L7PolicyItem.from_api(p) for p in as_list(raw) if isinstance(p, dict)],
        )

    async def get_l7_policy(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        policy_id: str = Field(..., description="Policy ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> L7PolicyItem:
        """Get one L7 policy with its rules.

        Returns {id, name, action, redirect_pool_id, redirect_url,
        redirect_http_code, keep_query_string, position, rules[...]}.

        Read this before `update_l7_policy`: that call replaces `rules`, and a
        policy left with no rules matches everything.
        """
        path = await self._listener_path(load_balancer_id, listener_id, region)
        validate_id(policy_id, "policy_id")
        data = await self.client.get(f"{path}/l7policies/{policy_id}", region=region)
        return L7PolicyItem.from_api(unwrap_one(data))

    async def create_l7_policy(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        body: CreateL7PolicyDto = Field(..., description="Policy definition."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CreatedResourceData:
        """Create an L7 routing policy on a listener.

        ## Requirements
        - Requires `--allow-write`.
        - Only meaningful on an HTTP/HTTPS listener of a 'Layer 7' load
          balancer.
        - `REDIRECT_TO_POOL` needs `redirectPoolId` (create the pool first);
          `REDIRECT_TO_URL` needs `redirectUrl`.
        - ALL of `rules` must match for the policy to fire. A policy with no
          rules matches every request and will capture traffic meant for the
          default pool.
        - The name is permanent — `update_l7_policy` cannot change it.

        ## Workflow
        - New policies land at the end of the evaluation order; use
          `update_l7_policy_order` to put a more specific rule ahead of a
          broader one.
        - Poll `get_load_balancer_status` until `settled`.
        - If the returned `id` is empty the create still SUCCEEDED — confirm
          with `list_l7_policies` and never retry.
        """
        require_write(self.allow_write)
        path = await self._listener_path(load_balancer_id, listener_id, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"{path}/l7policies", region=region, json=payload)
        return CreatedResourceData.from_api(data, "list_l7_policies")

    async def update_l7_policy(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        policy_id: str = Field(..., description="Policy ID."),
        body: UpdateL7PolicyDto = Field(
            ...,
            description=(
                "The COMPLETE desired policy. `rules` is replaced wholesale — read the "
                "current ones with get_l7_policy and resend those you want to keep."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Replace an L7 policy's action and rules.

        ## Requirements
        - Requires `--allow-write.`
        - `rules` is a full replacement. Omitting it can leave the policy with
          no conditions, which makes it match EVERY request on the listener —
          read `get_l7_policy` first and resend the rules you want to keep.
        - The name cannot be changed; recreate the policy for that.
        - Changing the action changes where live traffic goes. Confirm with the
          user.

        ## Workflow
        - Poll `get_load_balancer_status` until `settled`, then confirm with
          `get_l7_policy`.
        """
        require_write(self.allow_write)
        path = await self._listener_path(load_balancer_id, listener_id, region)
        validate_id(policy_id, "policy_id")
        payload = body.model_dump(exclude_none=True)
        await self.client.put(f"{path}/l7policies/{policy_id}", region=region, json=payload)
        return (
            f"L7 policy {policy_id} updated. Poll get_load_balancer_status until settled, "
            "then confirm with get_l7_policy."
        )

    async def delete_l7_policy(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        policy_id: str = Field(..., description="Policy ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete an L7 routing policy.

        ## Requirements
        - Requires `--allow-write`.
        - Traffic this policy used to route falls through to the listener's
          default pool — or to `defaultAction` if there is none. Check where it
          will land before deleting.
        - The pool it redirected to is not deleted.

        ## Workflow
        - The API answers 202; poll `list_l7_policies` to confirm, and check the
          remaining policies' `position` values.
        """
        require_write(self.allow_write)
        path = await self._listener_path(load_balancer_id, listener_id, region)
        validate_id(policy_id, "policy_id")
        await self.client.delete(f"{path}/l7policies/{policy_id}", region=region)
        return (
            f"L7 policy {policy_id} deletion accepted. It is removed asynchronously — "
            "poll list_l7_policies to confirm."
        )

    async def update_l7_policy_order(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        policies: list[ReorderPolicyDto] = Field(
            ..., description="Each policy and its new position."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Change the evaluation order of a listener's L7 policies.

        ## Requirements
        - Requires `--allow-write`.
        - Order decides routing: the first policy whose rules all match wins, so
          a broad policy placed ahead of a specific one makes the specific one
          unreachable.
        - Read the current `position` values from `list_l7_policies` and keep
          the same numbering. The API contradicts itself about whether positions
          are 0- or 1-based — mirroring what it already reports avoids the
          question.
        - Two policies cannot share a position.

        ## Workflow
        - Poll `get_load_balancer_status` until `settled`, then confirm the new
          order with `list_l7_policies`.
        """
        require_write(self.allow_write)
        path = await self._listener_path(load_balancer_id, listener_id, region)
        positions = [p.position for p in policies]
        if len(set(positions)) != len(positions):
            raise ValueError(
                "Two policies were given the same duplicate position; evaluation order "
                "would be ambiguous. Read the current order from list_l7_policies and "
                "assign each policy a distinct position."
            )
        payload = ReorderPoliciesDto(policies=policies).model_dump()
        await self.client.put(f"{path}/reorderL7Policies", region=region, json=payload)
        return (
            f"Reordering of {len(policies)} L7 policies accepted. Poll "
            "get_load_balancer_status until settled, then confirm with list_l7_policies."
        )
