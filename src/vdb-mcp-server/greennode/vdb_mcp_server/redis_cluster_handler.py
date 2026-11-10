"""Redis Cluster tools.

The fifth vDB family, and the first to share another family's **service**: its
controller mounts at ``/v1/redis-cluster`` on the memory service, so the family
boundary is a path, not a host. Three consequences the tools carry:

* **The listing is its own.** Unlike the PostgreSQL Cluster family, whose reads
  are derived from the relational listing, Redis Cluster has a list and a
  detail endpoint of its own -- every row is a Redis Cluster, with no
  mixed-listing caveat and no prefix filtering. Pagination is mandatory there
  (``pageNumber`` and ``pageSize`` are required query parameters) and there is
  no filter of any kind: narrow a result by reading the rows.
* **The secrules read is borrowed.** This controller has a PUT for the rules
  but no GET; the memory family's instance endpoint
  (``/v1/database-instances/{id}/secrules``, same service) answers for a Redis
  Cluster id, so `list_redis_cluster_secrules` reads there. The same
  per-operation borrowing the PostgreSQL Cluster family does with the
  relational endpoints.
* **The create posts to the controller root**, not to a ``/payment`` path like
  the relational and memory creates -- but it is the same order flow
  underneath: billable, a ``user-type`` header, an asynchronous answer.

The delete takes the nested envelope the other families use, with the same
``action: "delete"`` / ``resType: "dbaas"`` values that cannot be read off the
path -- and this controller insists on exactly **one** entry whose id repeats
the path, rejecting anything else as ``Invalid request`` / ``Invalid access
resource``. A wrong ``action`` still answers 200 and does nothing.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import VDB_MEMORY_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    ActionData,
    ActionResult,
    CreateRedisClusterDto,
    DeleteRedisClusterDto,
    DryRunData,
    OrderData,
    OrderResult,
    PageInfo,
    RedisCluster,
    RedisClusterListData,
    ResizeRedisClusterDto,
    SecurityRule,
    SecurityRuleListData,
    SettingUpdateData,
    UpdateRedisClusterSecRuleDto,
    UpdateRedisClusterSettingDto,
)
from greennode.vdb_mcp_server.paging import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    as_list,
    build_page_params,
    unwrap_nested_list,
    unwrap_wrapped,
)
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


CLUSTER_BASE = "/v1/redis-cluster"

MEMORY_INSTANCE_BASE = "/v1/database-instances"
"""Where the borrowed secrules read goes.

This family's controller has a PUT for the rules but no GET; the memory
family's instance endpoint on the same service answers for a Redis Cluster id.
"""

RESOURCE_TYPE = "dbaas"
"""The only value `resType` accepts in the action envelope.

Copied deliberately rather than imported from a sibling handler, matching how
the other families handle it: these payload constants are per-family by
decision, so one family's rule can never silently become another's.
"""

ACTION_DELETE = "delete"
"""The `action` the delete endpoint expects -- not derivable from the path."""

DETACH_CONFIG_GROUP = ""
"""What a *caller* passes to detach the attached configuration group.

The spec says an empty string detaches. In the sibling families -- which share
this schema shape -- the empty string is rejected with ``400 in_valid`` and a
``null`` is what actually detaches, confirmed by the instance history, so this
tool keeps the empty string as its own vocabulary (an agent expresses "none"
far more reliably than a JSON null) and translates. Not yet measured on this
endpoint; see :data:`CONFIG_GROUP_DETACH_PAYLOAD`.
"""

CONFIG_GROUP_DETACH_PAYLOAD = None
"""The wire value for a detach. See :data:`DETACH_CONFIG_GROUP`."""

ORDER_NEXT_STEP = (
    "An order has been raised, not a cluster. Under the default IAM_USER flow the order "
    "carries a resourceId, but the cluster still provisions asynchronously: expect BUILDING "
    "before ACTIVE. Confirm with get_redis_cluster, or find it with list_redis_clusters."
)

RESIZE_NEXT_STEP = (
    "The resize order was raised, not applied. Confirm with get_redis_cluster -- the cluster "
    "passes through RESIZE on the way back to ACTIVE, and a poll issued immediately still "
    "reports the shape from before the order."
)

SETTING_NEXT_STEP = (
    "Applied asynchronously. Confirm with get_redis_cluster -- and if a setting looks "
    "unchanged, wait: vDB runs one edit per cluster at a time, and a second edit sent while "
    "one is running is accepted with HTTP 200 and then fails asynchronously."
)

DELETE_NEXT_STEP = (
    "The deletion was accepted, not completed. Confirm with list_redis_clusters that the "
    "cluster is gone; a row still reading TRASH is the terminal soft-delete state, not a "
    "cluster still running."
)

EMPTY_ACTION_WARNING = (
    "The API answered 200 but returned no result for this cluster, and a delete it has "
    "actually queued always reports one. Treat this as NOT applied: check get_redis_cluster "
    "and tell the user the call had no effect rather than reporting success."
)

NOT_FOUND_MESSAGE = (
    "No Redis Cluster with id '{cluster_id}'. list_redis_clusters shows what exists -- note "
    "the listing has no filter, so read the rows rather than searching by name."
)


def _action_data(cluster_id: str, results: list[ActionResult]) -> ActionData:
    """Wrap action results, refusing to present an empty response as success."""
    if not results:
        return ActionData(
            instance_id=cluster_id,
            accepted=False,
            results=[],
            warning=EMPTY_ACTION_WARNING,
            next_step=DELETE_NEXT_STEP,
        )
    return ActionData(instance_id=cluster_id, results=results, next_step=DELETE_NEXT_STEP)


def _cluster_envelope(cluster_id: str, action: str, config: dict | None = None) -> dict:
    """Build the nested envelope the delete endpoint expects.

    The cluster id is already in the path; the API wants it in the body too,
    under a list, with the action repeated. This controller validates the
    shape itself: exactly one entry, whose `instancesId` repeats the path id.
    """
    detail: dict[str, Any] = {"instancesId": cluster_id}
    if config is not None:
        detail["config"] = config
    return {
        "databaseInstances": [detail],
        "action": action,
        "resType": RESOURCE_TYPE,
    }


class RedisClusterHandler:
    """Register and serve the Redis Cluster tools."""

    def __init__(
        self,
        mcp,
        config: VdbConfig,
        client: VdbClient,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_redis_clusters", annotations=READ)(self.list_redis_clusters)
        self.mcp.tool(name="get_redis_cluster", annotations=READ)(self.get_redis_cluster)
        self.mcp.tool(name="list_redis_cluster_secrules", annotations=READ)(
            self.list_redis_cluster_secrules
        )

        # Previews are read-only whatever they preview.
        self.mcp.tool(name="create_redis_cluster_dryrun", annotations=READ)(
            self.create_redis_cluster_dryrun
        )
        self.mcp.tool(name="resize_redis_cluster_dryrun", annotations=READ)(
            self.resize_redis_cluster_dryrun
        )
        self.mcp.tool(name="delete_redis_cluster_dryrun", annotations=READ)(
            self.delete_redis_cluster_dryrun
        )

        # Everything that reaches the API is registered only in write mode, so
        # a read-only server does not advertise it at all.
        if self.allow_write:
            self.mcp.tool(name="create_redis_cluster", annotations=WRITE)(
                self.create_redis_cluster
            )
            self.mcp.tool(name="resize_redis_cluster", annotations=DESTRUCTIVE)(
                self.resize_redis_cluster
            )
            self.mcp.tool(name="update_redis_cluster_setting", annotations=WRITE)(
                self.update_redis_cluster_setting
            )
            self.mcp.tool(name="update_redis_cluster_config_group", annotations=WRITE)(
                self.update_redis_cluster_config_group
            )
            self.mcp.tool(name="update_redis_cluster_secrules", annotations=WRITE)(
                self.update_redis_cluster_secrules
            )
            self.mcp.tool(name="delete_redis_cluster", annotations=DESTRUCTIVE)(
                self.delete_redis_cluster
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self.client.call(
            "GET", path, service=VDB_MEMORY_SERVICE, params=params or None
        )

    async def _order(self, path: str, body: Any, user_type: str) -> list[OrderResult]:
        raw = await self.client.call(
            "POST", path, service=VDB_MEMORY_SERVICE, json=body, user_type=user_type
        )
        return [OrderResult.from_api(row) for row in as_list(raw)]

    async def _order_put(self, path: str, body: Any, user_type: str) -> list[OrderResult]:
        """Place an order with PUT.

        The resize is a `PUT` where the create is a `POST` -- same order
        response, different method, so the helper cannot be shared with
        :meth:`_order`.
        """
        raw = await self.client.call(
            "PUT", path, service=VDB_MEMORY_SERVICE, json=body, user_type=user_type
        )
        return [OrderResult.from_api(row) for row in as_list(raw)]

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_redis_clusters(
        self,
        page: int = Field(1, ge=1, description="Page number (1-based, not 0-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"
        ),
    ) -> RedisClusterListData:
        """List the project's Redis Clusters.

        Unlike the PostgreSQL Cluster family this listing is the family's own:
        every row is a Redis Cluster, with no mixed-listing caveat. There is
        **no filter of any kind** -- no name, no status -- so narrow a result
        by reading the rows.

        Pagination is mandatory upstream: `pageNumber` and `pageSize` are
        required query parameters, so both are always sent. The listing's
        `pageObject.maxSize` is 30 (measured live on staging, 2026-11-06),
        the lowest ceiling in vDB alongside the configuration listings.
        """
        raw = await self._get(CLUSTER_BASE, build_page_params(page, page_size))
        result = unwrap_nested_list(raw)
        return RedisClusterListData(
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[RedisCluster.from_api(row) for row in result.items],
        )

    async def get_redis_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
    ) -> RedisCluster:
        """Get one Redis Cluster in full detail.

        The detail carries what the listing does plus the addresses: the
        `address` map (keyed by role as the API words it), the fixed and
        floating IPs, and the proxy nodes when the cluster was deployed with
        `enableProxies`. `config_group_id` is trustworthy here.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self._get(f"{CLUSTER_BASE}/{cluster_id}/detail")
        payload = unwrap_wrapped(raw)
        if not isinstance(payload, dict) or not payload.get("id"):
            # Guarded rather than assumed: elsewhere in this API a missing
            # resource answers 200 with an empty body, which would build a
            # model whose every field is "" and read as a real cluster.
            raise ValueError(NOT_FOUND_MESSAGE.format(cluster_id=cluster_id))
        return RedisCluster.from_api(payload)

    async def list_redis_cluster_secrules(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
    ) -> SecurityRuleListData:
        """List the security-group rules guarding one Redis Cluster.

        This family's controller has a PUT for the rules but **no GET**; this
        reads the memory family's instance endpoint on the same service,
        which answers for a Redis Cluster id.

        Worth checking on a new cluster: expect `0.0.0.0/0` on the Redis port
        (6379) by default, whatever `publicAccess` was set to -- that flag
        governs the floating IP, not the firewall.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self._get(f"{MEMORY_INSTANCE_BASE}/{cluster_id}/secrules")
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=cluster_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    # ------------------------------------------------------------------
    # dry runs
    # ------------------------------------------------------------------

    async def create_redis_cluster_dryrun(
        self,
        spec: CreateRedisClusterDto = Field(..., description="The cluster to order"),
    ) -> DryRunData:
        """Show the exact order that create_redis_cluster would place.

        Validates the whole body locally -- including the 16-128 character
        password rule and the deployment-mode shape (`numberOfNodes` for
        non-sharding, `numberOfShards` for sharding), both of which the API
        reports only as `in_valid` -- and sends nothing.
        """
        return DryRunData(
            tool="create_redis_cluster",
            method="POST",
            path=CLUSTER_BASE,
            service=VDB_MEMORY_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order; the monthly cost is per node, so it scales with "
                "numberOfNodes (or numberOfShards x (1 + numberOfReplicas)).",
                "packageId and volumeTypeId must come from the same zone as locateZoneId -- "
                "both catalogues are per-zone and omitting a zone describes HCM03-1A only.",
                "The new cluster gets a 0.0.0.0/0 rule on the Redis port regardless of "
                "publicAccess; narrow it with update_redis_cluster_secrules afterwards.",
            ],
        )

    async def resize_redis_cluster_dryrun(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        spec: ResizeRedisClusterDto = Field(..., description="The resize to order"),
    ) -> DryRunData:
        """Show the exact order that resize_redis_cluster would place."""
        validate_id(cluster_id, "cluster_id")
        return DryRunData(
            tool="resize_redis_cluster",
            method="PUT",
            path=f"{CLUSTER_BASE}/{cluster_id}/resize",
            service=VDB_MEMORY_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            # No id in the body: the controller fills it from the path.
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order and changes the monthly cost.",
                "Shrinking numberOfNodes destroys nodes. Confirm which ones the user can "
                "lose before sending it.",
                "volumeTypeId must come from list_redis_volume_types for the cluster's own "
                "zone; packageId likewise from list_redis_flavors.",
            ],
        )

    async def delete_redis_cluster_dryrun(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        options: DeleteRedisClusterDto | None = Field(
            None, description="Deletion options; defaults keep existing backups"
        ),
    ) -> DryRunData:
        """Show exactly what delete_redis_cluster would send, without sending it.

        Read-only despite the name — nothing is deleted. Use it to confirm the
        backup options before the real call.
        """
        validate_id(cluster_id, "cluster_id")
        opts = options or DeleteRedisClusterDto()
        warnings = [
            "Destroys the cluster. This cannot be undone.",
        ]
        if opts.deleteAllBackup:
            warnings.insert(
                0,
                "deleteAllBackup is set: every backup of this cluster is destroyed too, "
                "removing the only route to recovery.",
            )
        elif not opts.createFinalBackup:
            warnings.append(
                "No final backup will be taken. Set createFinalBackup to keep a last copy."
            )
        return DryRunData(
            tool="delete_redis_cluster",
            method="POST",
            path=f"{CLUSTER_BASE}/{cluster_id}/delete",
            service=VDB_MEMORY_SERVICE,
            user_type=None,
            body=_cluster_envelope(cluster_id, ACTION_DELETE, opts.model_dump()),
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_redis_cluster(
        self,
        spec: CreateRedisClusterDto = Field(..., description="The cluster to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Order a new Redis Cluster. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_redis_cluster_dryrun` first and have the user confirm
          the shape and zone. This is a billable order, priced per node.
        - `datastoreVersion` must be a version from `list_redis_datastores`.
        - `packageId` and `volumeTypeId` must come from
          `list_redis_flavors` / `list_redis_volume_types` **for the zone in
          `locateZoneId`**; `volumeSize` must sit inside the volume type's
          bounds. Mixing zones yields `in_valid`.
        - `netIds` takes **subnet** ids, not network ids.
        - The AUTH password must be **16-128** characters — letters, digits
          and `$ ^ _ < >` only, starting with a letter and ending
          alphanumeric. Validated locally; the API reports a violation only
          as `in_valid`.
        - `deploymentMode` decides the shape: `non-sharding` takes
          `numberOfNodes`, `sharding` takes `numberOfShards`; the DTO rejects
          a body sized on both.
        - `poolMaxConnections` rides with `enableProxies`,
          `slowlogLogSlowerThan` with `enableSlowlog`, and the backup
          schedule fields with `backupAuto` — set the flag alongside the
          field it configures.
        - Backup is **create-time-only**: `backupPolicyId` +
          `backupLocationId` (+ the schedule) must ride in this create. A
          cluster created without them can never gain a backup configuration
          later — `update_redis_cluster_setting` refuses `backupAuto` with
          `400` on a config-less cluster (measured on staging), and the
          settings body has no policy or location field to attach one.
        - Restoring is this same tool carrying `backupPointId` from
          `list_redis_restore_points`; the restore builds a **new** cluster
          and never touches the source.

        ## Workflow

        1. `list_redis_datastores` → the Redis version.
        2. `list_relational_zones` → the zone (this family has no zones
           catalogue of its own).
        3. `list_redis_flavors` + `list_redis_volume_types` with that zone →
           `packageId`, `volumeTypeId` and the `volumeSize` bounds.
        4. `list_memory_subnets` → `netIds` (same service; the networks are
           account-wide).
        5. `create_redis_cluster_dryrun` → confirm with the user.
        6. This tool. Under the default IAM_USER flow the order carries a
           `resourceId`, but provisioning is asynchronous: poll
           `get_redis_cluster` through BUILDING to ACTIVE.
        7. `list_redis_cluster_secrules` → the new cluster is open to
           `0.0.0.0/0`; narrow it.
        """
        require_write(self.allow_write)
        orders = await self._order(CLUSTER_BASE, spec.model_dump(exclude_none=True), user_type)
        return OrderData(orders=orders, instance_name=spec.name, next_step=ORDER_NEXT_STEP)

    async def resize_redis_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        spec: ResizeRedisClusterDto = Field(..., description="The resize to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Resize a Redis Cluster along one axis. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `resize_redis_cluster_dryrun` first and have the user confirm.
          This raises a billable order and changes a running cluster.
        - **One axis per call.** `type` is `VOLUME-SIZE`, `VOLUME-TYPE`,
          `NUMBER-OF-NODES` or `FLAVOR` — `FLAVOR` is an axis this family has
          that the PostgreSQL Cluster resize lacks — and only the field
          belonging to that axis may be set. `numberOfReplicas` rides only
          with `NUMBER-OF-NODES`. The DTO rejects any other combination
          locally.
        - `NUMBER-OF-NODES` also requires `deploymentMode` (measured: the
          axis is refused `400` without it, and the published schema does not
          list the field). Read it from `get_redis_cluster`'s
          `deployment_mode` — the row's `deploy_type` reads empty — and send
          `numberOfReplicas` as the Portal does: one less than the new
          `numberOfNodes`.
        - The nodes axis moves **one node per resize** (measured: +1 was
          accepted, +2 with the same correct shape was refused `400`), within
          the absolute 3-10 the Portal enforces. Scaling further means
          repeating the resize once the previous one has landed.
        - Shrinking `numberOfNodes` (or `numberOfReplicas`) destroys nodes.
          Confirm which ones the user can lose before sending it.
        - `volumeTypeId` / `packageId` must come from this cluster's **own
          zone** — read `zone_id` from `get_redis_cluster`, then the matching
          catalogue.
        - The cluster must be idle, with no resize order in flight: the PUT
          answers 200 **before** the RESIZE state surfaces, so a cluster that
          reads ACTIVE/settled may still owe an order — a second edit in that
          window is refused `400` outright (measured on staging between two
          back-to-back resizes), and later edits fail asynchronously instead.

        ## Workflow

        1. `get_redis_cluster` → confirm `status_kind` is `settled` and read
           the current shape.
        2. `resize_redis_cluster_dryrun` → confirm with the user.
        3. This tool.
        4. `get_redis_cluster` to confirm — but the order is **raised, not
           applied**: the cluster keeps reading ACTIVE/settled with the old
           shape until the RESIZE state surfaces, so poll for the resized
           **field** to change, not for settled. If the field never changes,
           the order failed asynchronously — there is no history endpoint in
           this family, so re-read and report rather than retrying blind.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        orders = await self._order_put(
            f"{CLUSTER_BASE}/{cluster_id}/resize",
            spec.model_dump(exclude_none=True),
            user_type,
        )
        return OrderData(orders=orders, instance_name=cluster_id, next_step=RESIZE_NEXT_STEP)

    async def update_redis_cluster_setting(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        spec: UpdateRedisClusterSettingDto = Field(..., description="The settings to change"),
    ) -> SettingUpdateData:
        """Change a cluster's password, public access, backup schedule or slowlog.

        ## Requirements

        - `--allow-write` must be enabled.
        - The body is a **patch**: omit a field to leave it unchanged. The
          DTO rejects an empty update rather than sending a no-op.
        - A new password must be **16-128** characters — letters, digits and
          `$ ^ _ < >` only, starting with a letter and ending alphanumeric.
          Every client using the old one breaks the moment this applies;
          confirm with the user first and never echo the password back.
        - Turning `publicAccess` on gives the cluster a floating IP; it does
          **not** open the firewall — that is `update_redis_cluster_secrules`
          — and an exposed Redis needs its password enabled.
        - `backupDuration` is retention in **days** (2-14), not a window
          length; `backupTime` is HH:mm (UTC+7).
        - The cluster must be idle: one edit runs at a time.

        ## Workflow

        1. `get_redis_cluster` → confirm `status_kind` is `settled` and read
           the current settings.
        2. This tool with only the fields to change.
        3. `get_redis_cluster` to confirm — there is no history endpoint in
           this family, so the re-read is the only confirmation.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        # No id in the body: the controller fills it from the path.
        await self.client.call(
            "PUT",
            f"{CLUSTER_BASE}/{cluster_id}/settings",
            service=VDB_MEMORY_SERVICE,
            json=spec.model_dump(exclude_none=True),
        )
        return SettingUpdateData(instance_id=cluster_id, next_step=SETTING_NEXT_STEP)

    async def update_redis_cluster_config_group(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        config_group_id: str = Field(
            ...,
            description=(
                "Configuration group ID from list_memory_configurations, or an empty string "
                "to detach whatever group is attached."
            ),
        ),
    ) -> SettingUpdateData:
        """Attach a configuration group to a Redis Cluster, or detach one.

        Success is the HTTP status; the response body is not surfaced — the
        sibling families' equivalents answer with two id fields holding each
        other's values, and this endpoint shares their response class.

        Note the field is `configGroupId` here, where this family's **create**
        calls the same thing `configId` — the inverse naming the PostgreSQL
        Cluster family is known for, inside one family this time.

        ## Requirements

        - `--allow-write` must be enabled.
        - The group must be a Redis group from `list_memory_configurations`
          whose `datastore_version` matches the cluster's.
        - Pass `""` to detach the current group. (The spec says an empty
          string detaches; in the sibling families the empty string is
          rejected as `in_valid` and a null detaches, so this tool
          translates. Do not send `""` to the endpoint directly.)
        - **The cluster must be idle.** Only one edit runs at a time: a
          second is accepted with HTTP 200 and then fails asynchronously.
          Wait for `status_kind` to be `settled`.

        ## Workflow

        1. `list_memory_configurations` → a Redis group matching the
           cluster's version.
        2. `get_redis_cluster` → confirm the cluster is settled first.
        3. This tool.
        4. `get_redis_cluster` to confirm `config_group_id` — the change
           takes longer to appear than a single follow-up read can catch, so
           read again before concluding it failed.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        detaching = config_group_id == DETACH_CONFIG_GROUP
        if not detaching:
            validate_id(config_group_id, "config_group_id")
        await self.client.call(
            "PUT",
            f"{CLUSTER_BASE}/{cluster_id}/config-group",
            service=VDB_MEMORY_SERVICE,
            json={"configGroupId": CONFIG_GROUP_DETACH_PAYLOAD if detaching else config_group_id},
        )
        return SettingUpdateData(instance_id=cluster_id, next_step=SETTING_NEXT_STEP)

    async def update_redis_cluster_secrules(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        rules: list[UpdateRedisClusterSecRuleDto] = Field(
            ..., description="The COMPLETE desired rule set"
        ),
    ) -> SecurityRuleListData:
        """Replace the security-group rules on a Redis Cluster.

        ## Requirements

        - `--allow-write` must be enabled.
        - This **replaces the whole rule set**: any existing rule missing
          from `rules` is deleted. Read `list_redis_cluster_secrules` first
          and send back the rules that should survive, each with its `id` —
          omit the `id` only on rules being added.
        - Each rule is the **full entity** the endpoint binds: carry the
          `direction`/`etherType`/`protocol` read back from the listing —
          a row without them is rejected with `400 bad_request`, whatever
          the published schema says.
        - The Redis port is 6379.
        - A new cluster arrives with `0.0.0.0/0` on that port. Narrowing it is
          what this tool is for; leaving it open exposes an in-memory
          database to the internet.

        ## Workflow

        1. `list_redis_cluster_secrules` → the current rules and ids.
        2. This tool with the full desired set.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        body = [rule.model_dump(exclude_none=True) for rule in rules]
        raw = await self.client.call(
            "PUT",
            f"{CLUSTER_BASE}/{cluster_id}/secrules",
            service=VDB_MEMORY_SERVICE,
            json=body,
        )
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=cluster_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    async def delete_redis_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        options: DeleteRedisClusterDto | None = Field(
            None, description="Deletion options; defaults keep existing backups"
        ),
    ) -> ActionData:
        """Delete a Redis Cluster. Irreversible.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `delete_redis_cluster_dryrun` first and have the user confirm
          the cluster id **and** the backup options.
        - Never set `deleteAllBackup` unless the user asked for exactly that:
          it destroys every backup, removing the only route to recovery.
        - Decide `createFinalBackup` explicitly rather than accepting the
          default — ask.
        - `403 IAM_PERMISSION_DENIED` is a policy answer, not a payload one:
          the gateway itself adds no permission check on this path -- the
          backend authorizes the delete action and refuses when the principal
          lacks it (measured on staging: the delete was denied while every
          other write passed on the same credentials). Ask the user to grant
          the action or delete from the console; re-shaping the request
          cannot help.

        ## Workflow

        1. `get_redis_cluster` → confirm which cluster this is.
        2. `delete_redis_cluster_dryrun` → confirm with the user.
        3. This tool, then `list_redis_clusters` to confirm it is gone.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        opts = options or DeleteRedisClusterDto()
        raw = await self.client.call(
            "POST",
            f"{CLUSTER_BASE}/{cluster_id}/delete",
            service=VDB_MEMORY_SERVICE,
            json=_cluster_envelope(cluster_id, ACTION_DELETE, opts.model_dump()),
        )
        results = [ActionResult.from_api(row) for row in as_list(raw)]
        return _action_data(cluster_id, results)
