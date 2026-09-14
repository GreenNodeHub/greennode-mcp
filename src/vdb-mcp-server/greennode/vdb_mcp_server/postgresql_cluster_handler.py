"""PostgreSQL Cluster tools.

This family is shaped by one fact: **it has no listing and no get-by-id
endpoint.** Its 14 operations cover creating, resizing and configuring a
cluster but never enumerating one. Measured: ``GET /v1/cluster`` answers 400
and ``GET /v1/cluster/{id}`` answers 403, so the absence is real rather than
undocumented.

So `list_postgresql_clusters` and `get_postgresql_cluster` are **derived** from
the relational instance listing, which returns clusters mixed in with
relational instances. Two consequences the docstrings carry:

* the API does not apply its `name`/`status` filters to the cluster rows at
  all, so filtering happens here and a filtered count is never exact;
* reading a cluster goes through the relational detail endpoint, which accepts
  a `pg-` id happily -- and would just as happily accept a `db-` one, so this
  handler checks the prefix itself rather than trusting the endpoint.

The same is true of **five operations the family has no endpoint for at
all**: security rules, history, reboot and delete are served for a `pg-` id by
the *relational* endpoints, which is what the GreenNode Portal itself calls.
Measured on 2026-09-14: `/secrules` and `/histories` answer for a cluster,
while `/replicas/{id}` answers `403 IAM_PERMISSION_DENIED` and
`/backups/insId/{id}` answers an empty array (cluster backups live in vBackup,
not in the vDB backup store). So the wrapper is per-operation, not a blanket
"the relational API works for clusters".
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import (
    VDB_POSTGRESQL_SERVICE,
    VDB_RELATIONAL_SERVICE,
    VdbConfig,
)
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    ActionData,
    ActionResult,
    CreatePostgresqlClusterDto,
    DeleteRelationalInstanceDto,
    DryRunData,
    HistoryEntry,
    HistoryListData,
    OrderData,
    OrderResult,
    PageInfo,
    PostgresqlCluster,
    PostgresqlClusterListData,
    PostgresqlVolumeUsedData,
    ResizePostgresqlClusterDto,
    SecurityRule,
    SecurityRuleListData,
    SettingUpdateData,
    UpdatePostgresqlClusterSettingDto,
    UpdateSecurityRuleDto,
)
from greennode.vdb_mcp_server.paging import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    as_list,
    build_filter_params,
    build_page_params,
    unwrap_content_list,
    unwrap_nested_list,
    unwrap_wrapped,
)
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


CLUSTER_BASE = "/v1/cluster"

RELATIONAL_BASE = "/v1/database-instances"
"""Where the derived reads go. The cluster family owns no listing of its own."""

POSTGRESQL_PREFIX = "pg-"
"""Cluster ids. Unlike `db-`, this prefix IS distinctive -- it belongs to no
other family -- which is what makes deriving the listing possible at all."""

DETACH_CONFIG_GROUP = ""
"""What a *caller* passes to detach the attached configuration group.

Kept as this family's own constant rather than imported from the relational
handler, matching how the `ACTION_*` constants are handled: one family's
payload rule never silently becomes another's. The wire value differs from the
caller's -- see :data:`CONFIG_GROUP_DETACH_PAYLOAD`.
"""

CONFIG_GROUP_DETACH_PAYLOAD = None
"""The wire value for a detach.

The spec says ``configGroupId: ""`` detaches. On the relational endpoint that
empty string is rejected with ``400 in_valid``, and on the memory endpoint --
which shares the schema -- ``null`` is what actually detaches, confirmed by the
instance history. The same translation is applied here **by inference, not by
measurement**: this endpoint has a different field name and has not been
exercised. The docstring says so.
"""

DEFAULT_MAX_PAGES = 5
"""How many pages of the mixed listing a derived read scans by default.

The listing is dominated by relational rows, so a project with many instances
and few clusters can push every cluster past the first page. Scanning is
capped rather than unbounded so one call cannot walk an arbitrarily large
account, and the result says when it stopped early.
"""

ORDER_NEXT_STEP = (
    "An order has been raised, not a cluster. Under the default IAM_USER flow the order "
    "carries the new resourceId, but the cluster still provisions asynchronously: measured, a "
    "two-node cluster spent about nine minutes in BUILDING before ACTIVE. Poll "
    "get_postgresql_cluster with that id, or find it with list_postgresql_clusters by name."
)

RESIZE_NEXT_STEP = (
    "A resize order has been raised. Confirm with get_postgresql_cluster rather than assuming "
    "the new shape: the change is applied asynchronously, and a poll issued seconds after the "
    "call still reports the previous state."
)

SETTING_NEXT_STEP = (
    "The request was accepted, not applied. Confirm with get_postgresql_cluster. If nothing "
    "changed, read list_relational_instance_histories for this cluster id -- an asynchronous "
    "failure is recorded there and nowhere else."
)

NO_DELETE_NOTE = (
    "Deleting a cluster goes through delete_postgresql_cluster, which calls the RELATIONAL "
    "delete endpoint -- the PostgreSQL Cluster API has none of its own."
)

REBOOT_NEXT_STEP = (
    "The reboot was accepted, not completed. Confirm with get_postgresql_cluster: the cluster "
    "passes through REBOOT before returning to ACTIVE, and a poll issued immediately still "
    "reports the previous status."
)

DELETE_NEXT_STEP = (
    "The deletion was accepted, not completed. Confirm with list_postgresql_clusters that the "
    "cluster is gone; if it is still listed after the status settles, read "
    "list_postgresql_cluster_histories for what the platform did with the request."
)

EMPTY_ACTION_WARNING = (
    "The API returned an empty result array, which means the action was NOT applied -- vDB "
    "answers an unrecognised action with HTTP 200 and no rows rather than an error. Report "
    "this as no effect and check list_postgresql_cluster_histories; do not report success."
)

RESOURCE_TYPE = "dbaas"
"""The only value `resType` accepts in the relational action envelope.

Copied deliberately rather than imported from the relational handler: these
payload constants are per-family by decision, so one family's rule can never
silently become another's if the platform changes one of them.
"""

ACTION_REBOOT = "reboot"
ACTION_DELETE = "delete"
"""The `action` each endpoint expects, which is NOT derivable from the path."""


def _is_cluster(row: dict) -> bool:
    return str(row.get("id") or "").startswith(POSTGRESQL_PREFIX)


def _cluster_envelope(cluster_id: str, action: str, config: dict | None = None) -> dict:
    """Build the nested envelope the relational lifecycle endpoints expect.

    The id is already in the path; the API wants it in the body too, under a
    list, with the action repeated. Sending the path segment as the `action`
    instead gets HTTP 200 with an empty result array and no effect at all.
    """
    detail: dict[str, Any] = {"instancesId": cluster_id}
    if config is not None:
        detail["config"] = config
    return {
        "databaseInstances": [detail],
        "action": action,
        "resType": RESOURCE_TYPE,
    }


class PostgresqlClusterHandler:
    """Register and serve the PostgreSQL Cluster tools."""

    def __init__(self, mcp, config: VdbConfig, client: VdbClient, allow_write: bool):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_postgresql_clusters", annotations=READ)(
            self.list_postgresql_clusters
        )
        self.mcp.tool(name="get_postgresql_cluster", annotations=READ)(self.get_postgresql_cluster)
        self.mcp.tool(name="get_postgresql_cluster_volume_used", annotations=READ)(
            self.get_postgresql_cluster_volume_used
        )
        self.mcp.tool(name="list_postgresql_cluster_secrules", annotations=READ)(
            self.list_postgresql_cluster_secrules
        )
        self.mcp.tool(name="list_postgresql_cluster_histories", annotations=READ)(
            self.list_postgresql_cluster_histories
        )
        self.mcp.tool(name="create_postgresql_cluster_dryrun", annotations=READ)(
            self.create_postgresql_cluster_dryrun
        )
        self.mcp.tool(name="resize_postgresql_cluster_dryrun", annotations=READ)(
            self.resize_postgresql_cluster_dryrun
        )
        # The dry runs above are read-only and stay available without
        # --allow-write: they are how a caller plans an order it cannot yet
        # place. Everything that reaches the API is registered only in write
        # mode, so a read-only server does not advertise it at all.
        if self.allow_write:
            self.mcp.tool(name="create_postgresql_cluster", annotations=WRITE)(
                self.create_postgresql_cluster
            )
            self.mcp.tool(name="resize_postgresql_cluster", annotations=DESTRUCTIVE)(
                self.resize_postgresql_cluster
            )
            self.mcp.tool(name="update_postgresql_cluster_setting", annotations=WRITE)(
                self.update_postgresql_cluster_setting
            )
            self.mcp.tool(name="update_postgresql_cluster_config_group", annotations=WRITE)(
                self.update_postgresql_cluster_config_group
            )
            self.mcp.tool(name="update_postgresql_cluster_secrules", annotations=WRITE)(
                self.update_postgresql_cluster_secrules
            )
            self.mcp.tool(name="reboot_postgresql_cluster", annotations=WRITE)(
                self.reboot_postgresql_cluster
            )
            self.mcp.tool(name="delete_postgresql_cluster", annotations=DESTRUCTIVE)(
                self.delete_postgresql_cluster
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _scan(
        self,
        name: str | None,
        statuses: list[str] | None,
        page_size: int,
        max_pages: int,
    ) -> tuple[list[dict], int, int, bool]:
        """Walk the mixed relational listing collecting `pg-` rows.

        Returns the cluster rows, how many relational rows were dropped, how
        many pages were read, and whether more pages remained.

        The `name`/`status` filters are still sent: they shrink the relational
        half of each page, which means more clusters fit. They are then applied
        again here, because the API does not apply them to cluster rows -- a
        cluster comes back whatever is asked for.
        """
        params: dict[str, Any] = build_filter_params(name=name, statuses=statuses)
        clusters: list[dict] = []
        dropped = 0
        pages = 0
        more = False

        for page_number in range(1, max_pages + 1):
            raw = await self.client.call(
                "GET",
                RELATIONAL_BASE,
                service=VDB_RELATIONAL_SERVICE,
                params={**params, **build_page_params(page_number, page_size)},
            )
            page = unwrap_nested_list(raw)
            pages += 1
            for row in page.items:
                if _is_cluster(row):
                    clusters.append(row)
                else:
                    dropped += 1
            total_pages = page.total_pages or 0
            if page_number >= total_pages:
                break
            if page_number == max_pages:
                more = True

        return clusters, dropped, pages, more

    async def _order(self, path: str, body: Any, user_type: str) -> list[OrderResult]:
        raw = await self.client.call(
            "POST", path, service=VDB_POSTGRESQL_SERVICE, json=body, user_type=user_type
        )
        return [OrderResult.from_api(row) for row in as_list(raw)]

    async def _order_put(self, path: str, body: Any, user_type: str) -> list[OrderResult]:
        """Place an order with PUT.

        The cluster resize is a `PUT` where every other order flow in vDB is a
        `POST` -- same order response, different method, so the helper cannot
        be shared with :meth:`_order`.
        """
        raw = await self.client.call(
            "PUT", path, service=VDB_POSTGRESQL_SERVICE, json=body, user_type=user_type
        )
        return [OrderResult.from_api(row) for row in as_list(raw)]

    async def _action(self, path: str, body: dict) -> list[ActionResult]:
        """Send a lifecycle action to the RELATIONAL endpoint that serves clusters."""
        raw = await self.client.call("POST", path, service=VDB_RELATIONAL_SERVICE, json=body)
        return [ActionResult.from_api(row) for row in as_list(raw)]

    def _action_data(self, cluster_id: str, results: list[ActionResult], next_step: str):
        """Wrap action results, refusing to present an empty response as success."""
        if not results:
            return ActionData(
                instance_id=cluster_id,
                accepted=False,
                results=[],
                warning=EMPTY_ACTION_WARNING,
                next_step=next_step,
            )
        return ActionData(instance_id=cluster_id, results=results, next_step=next_step)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_postgresql_clusters(
        self,
        name: str | None = Field(None, description="Filter by cluster name"),
        statuses: list[str] | None = Field(
            None,
            description=(
                "Filter by status; observed values include ACTIVE, BUILDING, WAIT_BILLING. "
                "The platform declares no enum, so this is not a closed set."
            ),
        ),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE,
            ge=1,
            le=MAX_PAGE_SIZE,
            description="Rows fetched per page of the underlying mixed listing",
        ),
        max_pages: int = Field(
            DEFAULT_MAX_PAGES, ge=1, le=50, description="How many pages to scan before stopping"
        ),
    ) -> PostgresqlClusterListData:
        """List the PostgreSQL Clusters in this project.

        **Derived, not fetched.** This family has no listing endpoint, so the
        clusters are read out of the *relational* instance listing, which
        returns both kinds of row, and filtered here by the `pg-` id prefix.

        Two things follow, and both matter when reporting a result:

        - **A filtered count is never exact.** The API ignores `name` and
          `statuses` for cluster rows -- measured: a name that matches nothing
          still returns every cluster -- so the filtering is done locally,
          across the pages that were scanned. `filters_are_approximate` says
          so.
        - **Paging counts relational rows too.** A project with many instances
          can push clusters onto later pages; `more_pages_exist` means the scan
          stopped before the listing did, and `max_pages` raises the limit.
        """
        clusters, dropped, pages, more = await self._scan(name, statuses, page_size, max_pages)

        wanted = clusters
        if name:
            needle = name.lower()
            wanted = [row for row in wanted if needle in str(row.get("name") or "").lower()]
        if statuses:
            wanted_statuses = {s.upper() for s in statuses}
            wanted = [
                row for row in wanted if str(row.get("status") or "").upper() in wanted_statuses
            ]

        items = [PostgresqlCluster.from_api(row) for row in wanted]
        return PostgresqlClusterListData(
            count=len(items),
            relational_instances_excluded=dropped,
            filters_are_approximate=bool(name or statuses),
            pages_scanned=pages,
            more_pages_exist=more,
            items=items,
        )

    async def get_postgresql_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
    ) -> PostgresqlCluster:
        """Get one PostgreSQL Cluster in full.

        **Derived**, like the listing: it reads the relational detail endpoint,
        which resolves `pg-` ids. That endpoint is not family-scoped in either
        direction -- it answers for relational and Redis instances too -- so
        the `pg-` prefix is checked here before the call rather than trusting a
        200 to mean "this is a cluster".

        Prefer this over a listing row for anything that matters: the detail
        response carries fields the row leaves out, and `config_group_id` in
        particular is only trustworthy from here.
        """
        validate_id(cluster_id, "cluster_id")
        if not cluster_id.startswith(POSTGRESQL_PREFIX):
            raise ValueError(
                f"'{cluster_id}' is not a PostgreSQL Cluster id (those start with "
                f"'{POSTGRESQL_PREFIX}'). A 'db-' id is a relational instance or a Redis one: "
                "use get_relational_instance or get_memory_instance."
            )
        raw = await self.client.call(
            "GET",
            f"{RELATIONAL_BASE}/id/{cluster_id}",
            service=VDB_RELATIONAL_SERVICE,
        )
        return PostgresqlCluster.from_api(unwrap_wrapped(raw) or {})

    async def get_postgresql_cluster_volume_used(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
    ) -> PostgresqlVolumeUsedData:
        """Get the disk actually used by a cluster.

        This is the **only** source: `get_postgresql_cluster` reports
        `volume_used` as null on a cluster, however much data it holds.

        The values come back as strings carrying their own unit -- `"219M"`,
        `"1.2G"` -- and are passed through verbatim. Compare them against the
        cluster's `volume_size_gb` by reading the unit; do not assume GB.

        The array's length does not track the node count: a three-node cluster
        answered with a single value. Report what is there rather than calling
        an element a node.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self.client.call(
            "GET",
            f"{CLUSTER_BASE}/{cluster_id}/volume-used",
            service=VDB_POSTGRESQL_SERVICE,
        )
        return PostgresqlVolumeUsedData(
            cluster_id=cluster_id,
            values=[str(value) for value in as_list(raw)],
        )

    async def list_postgresql_cluster_secrules(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
    ) -> SecurityRuleListData:
        """List the security-group rules guarding a cluster.

        The PostgreSQL Cluster API has no security-rule endpoint; this reads
        the **relational** one, which serves a `pg-` id. Measured 2026-09-14.

        Read this before anything else on a new cluster: a freshly created one
        carries a single ingress rule opening its port to `0.0.0.0/0`,
        regardless of what `publicAccess` was set to -- `publicAccess` governs
        the floating IP, not the firewall.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self.client.call(
            "GET",
            f"{RELATIONAL_BASE}/{cluster_id}/secrules",
            service=VDB_RELATIONAL_SERVICE,
        )
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=cluster_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    async def list_postgresql_cluster_histories(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
        page: int = Field(1, ge=1, description="Page number (1-based, not 0-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"
        ),
    ) -> HistoryListData:
        """List what has been done to a cluster, and what the platform made of it.

        Served by the **relational** history endpoint, which answers for a
        `pg-` id.

        **This is the only record of an asynchronous failure.** vDB accepts a
        second concurrent edit with HTTP 200 and then fails it out of band; it
        appears in no listing and no detail response. Each entry's
        `description` states what the platform understood the request to be,
        which is how to tell an attach from a detach, or a real failure from a
        concurrency clash.

        Read it whenever a write looks like it did nothing -- before retrying,
        and before reporting success.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self.client.call(
            "GET",
            f"{RELATIONAL_BASE}/{cluster_id}/histories",
            service=VDB_RELATIONAL_SERVICE,
            params=build_page_params(page, page_size),
        )
        result = unwrap_content_list(raw)
        return HistoryListData(
            instance_id=cluster_id,
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[HistoryEntry.from_api(row) for row in result.items],
        )

    # ------------------------------------------------------------------
    # dry runs
    # ------------------------------------------------------------------

    async def create_postgresql_cluster_dryrun(
        self,
        spec: CreatePostgresqlClusterDto = Field(..., description="The cluster to order"),
    ) -> DryRunData:
        """Show the exact order that create_postgresql_cluster would place.

        Validates the whole body locally -- including the node range and the
        password rule -- and sends nothing.
        """
        return DryRunData(
            tool="create_postgresql_cluster",
            method="POST",
            path=CLUSTER_BASE,
            service=VDB_POSTGRESQL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order, and a cluster bills per node: the cost is roughly "
                f"{spec.numberOfNodes}x a single instance of the same flavour.",
                "Returns an order, not a cluster: the resourceId it carries is only provisioned once "
                "the order completes -- around nine minutes for two nodes.",
                "packageId, volumeTypeId and locateZoneId must all come from the same zone.",
                NO_DELETE_NOTE,
            ],
        )

    async def resize_postgresql_cluster_dryrun(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
        spec: ResizePostgresqlClusterDto = Field(..., description="The resize to order"),
    ) -> DryRunData:
        """Show the exact order that resize_postgresql_cluster would place.

        Checks locally that exactly one axis is being changed and that the
        field matching `type` is the one supplied, and sends nothing.
        """
        validate_id(cluster_id, "cluster_id")
        return DryRunData(
            tool="resize_postgresql_cluster",
            method="PUT",
            path=f"{CLUSTER_BASE}/{cluster_id}/resize",
            service=VDB_POSTGRESQL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order and changes a running cluster.",
                "Storage grows only: a smaller volumeSize is rejected.",
                "Reducing numberOfNodes removes nodes from a live cluster -- confirm with the "
                "user which replicas they can afford to lose.",
            ],
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_postgresql_cluster(
        self,
        spec: CreatePostgresqlClusterDto = Field(..., description="The cluster to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Order a new PostgreSQL Cluster. Costs money, per node.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_postgresql_cluster_dryrun` first and have the user
          confirm the flavour, node count, storage and zone. A cluster bills
          per node, so this is the most expensive create in vDB.
        - `packageId`, `volumeTypeId` and `locateZoneId` must all belong to the
          **same zone**: both catalogues return different ids per zone.
        - `netIds` takes a **subnet** id ('sub-...') from
          `list_relational_subnets`, not a network id.
        - `configId`, if given, must be a configuration group whose deployType
          is `cluster` -- its id is prefixed `pg-cfg-`. A `single_node` group
          cannot be attached to a cluster.
        - To build the cluster **from a backup**, pass `backupPointId` from
          `list_postgresql_restore_points`, and match `datastoreVersion` to
          that point's `engine_version`.
        - There is no delete endpoint in this family: what this creates cannot
          be removed with these tools.

        ## Workflow

        1. `list_postgresql_datastores` → pick `datastoreVersion`.
        2. `list_relational_zones` → pick the zone.
        3. `list_postgresql_flavors` with that zone → pick `packageId`.
        4. `list_postgresql_volume_types` with the same zone → pick
           `volumeTypeId` and respect its size range.
        5. `list_relational_subnets` → pick `netIds`.
        6. Optional: `list_postgresql_backup_locations` and
           `list_postgresql_backup_policies` to schedule backups from the
           start.
        7. `create_postgresql_cluster_dryrun` → confirm with the user.
        8. This tool. It returns an **order**, whose `resourceId` is the
           cluster id under the default IAM_USER flow; poll
           `get_postgresql_cluster` with it and expect BUILDING before ACTIVE
           (about nine minutes for two nodes).
        """
        require_write(self.allow_write)
        orders = await self._order(CLUSTER_BASE, spec.model_dump(exclude_none=True), user_type)
        return OrderData(orders=orders, instance_name=spec.name, next_step=ORDER_NEXT_STEP)

    async def resize_postgresql_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
        spec: ResizePostgresqlClusterDto = Field(..., description="The resize to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE, description="Billing flow; leave at IAM_USER (Auto Payment)"
        ),
    ) -> OrderData:
        """Resize a PostgreSQL Cluster along one axis. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `resize_postgresql_cluster_dryrun` first and have the user
          confirm. This raises a billable order and changes a running cluster.
        - **One axis per call.** `type` is `VOLUME-SIZE`, `VOLUME-TYPE` or
          `NUMBER-OF-NODES` -- upper case and hyphenated, unlike every other
          enumerated value in this API -- and only the field belonging to that
          axis may be set. The DTO rejects any other combination locally.
        - Storage grows only.
        - `volumeTypeId` must come from `list_postgresql_volume_types` for the
          cluster's **own zone**.
        - Shrinking `numberOfNodes` destroys nodes. Confirm which replicas the
          user can lose before sending it.
        - The cluster must be idle. vDB runs one edit per resource at a time: a
          second is accepted with HTTP 200 and then fails asynchronously.

        ## Workflow

        1. `get_postgresql_cluster` → confirm `status_kind` is `settled` and
           read the current shape.
        2. `resize_postgresql_cluster_dryrun` → confirm with the user.
        3. This tool.
        4. `get_postgresql_cluster` to confirm. If nothing changed, read
           `list_relational_instance_histories` for this id -- an asynchronous
           failure is recorded only there.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        orders = await self._order_put(
            f"{CLUSTER_BASE}/{cluster_id}/resize",
            spec.model_dump(exclude_none=True),
            user_type,
        )
        return OrderData(orders=orders, instance_name=cluster_id, next_step=RESIZE_NEXT_STEP)

    async def update_postgresql_cluster_secrules(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
        rules: list[UpdateSecurityRuleDto] = Field(
            ..., description="The COMPLETE desired rule set"
        ),
    ) -> SecurityRuleListData:
        """Replace the security-group rules on a cluster.

        Served by the **relational** endpoint, which answers for a `pg-` id.

        ## Requirements

        - `--allow-write` must be enabled.
        - This **replaces the whole rule set**: any existing rule missing from
          `rules` is deleted. Read `list_postgresql_cluster_secrules` first and
          send back every rule that should survive, each with its `id`.
        - A cluster listens on **two** ports -- the read/write port and the
          read-only one (`port` and `port_ro` from `get_postgresql_cluster`,
          typically 5432 and 15432). A rule set written for the primary alone
          silently cuts off every read-only client.
        - A rule opening either port to `0.0.0.0/0` exposes the database to the
          internet. Get explicit confirmation.

        ## Workflow

        1. `list_postgresql_cluster_secrules` → the current rules and ids.
        2. `get_postgresql_cluster` → both ports.
        3. This tool with the full desired set.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        body = [rule.model_dump(exclude_none=True) for rule in rules]
        raw = await self.client.call(
            "PUT",
            f"{RELATIONAL_BASE}/{cluster_id}/secrules",
            service=VDB_RELATIONAL_SERVICE,
            json=body,
        )
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=cluster_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    async def reboot_postgresql_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
    ) -> ActionData:
        """Reboot a cluster. Every connection drops.

        Served by the **relational** lifecycle endpoint, which answers for a
        `pg-` id. Note there is no start and no stop for a cluster: reboot is
        the only lifecycle action this family has.

        ## Requirements

        - `--allow-write` must be enabled.
        - **Never reboot on your own initiative**, including to clear a
          `RESTART_REQUIRED` left by a configuration change. Say a reboot is
          needed, say that every open connection drops, and let the user pick
          the moment.
        - The cluster must be idle: one action runs at a time.

        ## Workflow

        1. `get_postgresql_cluster` → confirm which cluster, and that
           `status_kind` is `settled`.
        2. Ask the user, in one clear question.
        3. This tool, then poll `get_postgresql_cluster` through REBOOT back to
           ACTIVE. If the result's `accepted` is false, the action was ignored
           -- read `list_postgresql_cluster_histories`.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        results = await self._action(
            f"{RELATIONAL_BASE}/{cluster_id}/reboot",
            _cluster_envelope(cluster_id, ACTION_REBOOT),
        )
        return self._action_data(cluster_id, results, REBOOT_NEXT_STEP)

    async def delete_postgresql_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
        options: DeleteRelationalInstanceDto | None = Field(
            None, description="Deletion options; the defaults keep existing backups"
        ),
    ) -> ActionData:
        """Delete a PostgreSQL Cluster. Irreversible, and it destroys every node.

        Served by the **relational** delete endpoint: the PostgreSQL Cluster
        API has no delete of its own, and this is the call the Portal makes.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm the cluster **by name and id** with the user first. There is
          no dry run and no undo.
        - Never set `deleteAllBackup` unless the user asked for exactly that.
          A cluster's restore points are vBackup resources and they are the
          only route back to the data.
        - Decide `createFinalBackup` explicitly rather than accepting the
          default -- ask.

        ## Workflow

        1. `get_postgresql_cluster` → confirm which cluster this is.
        2. `list_postgresql_restore_points` → show what backups exist, and say
           what happens to them.
        3. Confirm with the user, naming the cluster.
        4. This tool, then `list_postgresql_clusters` to confirm it is gone.
           An `accepted` of false means the request was ignored, not that the
           cluster survived deletion -- check the history.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        opts = options or DeleteRelationalInstanceDto()
        results = await self._action(
            f"{RELATIONAL_BASE}/{cluster_id}/delete",
            _cluster_envelope(cluster_id, ACTION_DELETE, opts.model_dump()),
        )
        return self._action_data(cluster_id, results, DELETE_NEXT_STEP)

    async def update_postgresql_cluster_setting(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
        spec: UpdatePostgresqlClusterSettingDto = Field(..., description="The settings to change"),
    ) -> SettingUpdateData:
        """Change a cluster's master password or public access.

        ## Requirements

        - `--allow-write` must be enabled.
        - At least one of `password` / `publicAccess` must be set; the DTO
          rejects an empty update rather than sending a no-op.
        - A new password must satisfy the platform rule (letters, digits and
          `$ ^ _ < >` only, start with a letter, end alphanumeric). Every
          client using the old one breaks the moment this applies -- confirm
          with the user first, and never echo the password back into the chat.
        - Turning `publicAccess` on gives the cluster a public address; it does
          **not** narrow the firewall. Review the security rules separately.
        - The cluster must be idle: one edit runs at a time.

        ## Workflow

        1. `get_postgresql_cluster` → confirm `status_kind` is `settled`.
        2. This tool.
        3. `get_postgresql_cluster` to confirm. If `public_access` looks
           unchanged, read `list_relational_instance_histories` for this id.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        await self.client.call(
            "PUT",
            f"{CLUSTER_BASE}/{cluster_id}/settings",
            service=VDB_POSTGRESQL_SERVICE,
            json=spec.model_dump(exclude_none=True),
        )
        return SettingUpdateData(instance_id=cluster_id, next_step=SETTING_NEXT_STEP)

    async def update_postgresql_cluster_config_group(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
        config_group_id: str = Field(
            ...,
            description=(
                "Configuration group ID ('pg-cfg-...') from list_relational_configurations, "
                "or an empty string to detach whatever group is attached."
            ),
        ),
    ) -> SettingUpdateData:
        """Attach a configuration group to a cluster, or detach one.

        ## Requirements

        - `--allow-write` must be enabled.
        - The group's engine and version must match the cluster's, **and its
          deployType must be `cluster`** -- such a group's id is prefixed
          `pg-cfg-`. A `single_node` group belongs to a relational instance.
        - Pass `""` to detach. The spec says an empty string detaches; on the
          two endpoints where that was measured it is rejected as `in_valid`
          and a null detaches instead, so this tool sends null and keeps `""`
          as its own vocabulary. That translation has **not** been verified on
          this endpoint specifically -- if a detach appears to do nothing, read
          the history before concluding anything.
        - The field on the wire is `configGroupId` here, where the relational
          and memory endpoints call the same thing `configId`. Go through the
          tool rather than hand-building the body.
        - The cluster must be idle: one edit runs at a time.
        - Applying a group usually restarts the database, and a parameter
          marked `restartRequired` leaves it in `RESTART_REQUIRED` still
          serving the old value until it reboots. Never reboot on your own
          initiative.

        ## Workflow

        1. `list_relational_configurations` → a group with deployType
           `cluster` matching the engine and version.
        2. `get_postgresql_cluster` → confirm it is settled.
        3. This tool.
        4. `get_postgresql_cluster` to confirm `config_group_id`, and
           `list_relational_instance_histories` if it looks unchanged: the
           history's `description` names what the platform understood
           ("Attach config X" / "Detach config X").
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        detaching = config_group_id == DETACH_CONFIG_GROUP
        if not detaching:
            validate_id(config_group_id, "config_group_id")
        await self.client.call(
            "PUT",
            f"{CLUSTER_BASE}/{cluster_id}/config-group",
            service=VDB_POSTGRESQL_SERVICE,
            json={"configGroupId": CONFIG_GROUP_DETACH_PAYLOAD if detaching else config_group_id},
        )
        return SettingUpdateData(instance_id=cluster_id, next_step=SETTING_NEXT_STEP)
