"""Relational database instance tools (MySQL, MariaDB, standalone PostgreSQL).

Three things make this handler unlike a normal CRUD handler:

* The listing endpoint is **shared with the PostgreSQL Cluster family** and
  returns both, so every read here filters by id prefix and reports how many
  rows it dropped.
* Lifecycle actions take a nested envelope
  (``{databaseInstances: [{instancesId}], action, resType}``) rather than the
  instance id in the path alone -- even though the path already names the
  instance and the action.
* Create, resize and replica creation are **order flows**: they raise a
  billable order and provision asynchronously. Under the default ``IAM_USER``
  (Auto Payment) flow the response carries the new ``resourceId`` alongside the
  order URL; the resource itself still does not exist until the order
  completes. Each has a ``*_dryrun`` twin.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import VDB_RELATIONAL_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    ActionData,
    ActionResult,
    CreateRelationalInstanceDto,
    CreateRelationalReplicaDto,
    DeleteRelationalInstanceDto,
    DryRunData,
    HistoryEntry,
    HistoryListData,
    OrderData,
    OrderResult,
    PageInfo,
    RelationalInstance,
    RelationalInstanceListData,
    ReplicaListData,
    ResizeRelationalInstanceDto,
    ResizeRelationalStorageDto,
    SecurityRule,
    SecurityRuleListData,
    SettingUpdateData,
    UpdateRelationalSettingDto,
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


BASE = "/v1/database-instances"
CREATE_PATH = "/v1/payment/database-instances"

RELATIONAL_PREFIX = "db-"
"""Relational instances. `pg-` rows in the same listing are PostgreSQL Clusters."""

RESOURCE_TYPE = "dbaas"
"""The only value `resType` / `resourceType` accepts in the action envelope.

Not the path segment, not the resource name -- the literal string `dbaas`. The
spec says so in the field's ``description`` and ``example``, and nowhere else:
there is no ``enum``, so a generated summary of the schema loses it.
"""

# The `action` value each endpoint expects. Spelled out because they are NOT
# derivable from the path: `/shutdown` wants "stop", `/detach-replica` wants
# "detach_replica" with an underscore. Sending the path segment instead gets a
# 200 with an empty result array and no effect at all -- the API neither
# applies the action nor reports an error.
ACTION_START = "start"
ACTION_STOP = "stop"
ACTION_REBOOT = "reboot"
ACTION_DETACH_REPLICA = "detach_replica"
ACTION_DELETE = "delete"
ACTION_RESIZE = "resize"

DETACH_CONFIG_GROUP = ""
"""What a *caller* passes to detach the attached configuration group.

The spec says to detach with an empty string, and that is wrong. Measured on
this endpoint: ``{"configId": ""}`` is rejected with ``400 in_valid``. On the
memory endpoint -- which shares the very same ``UpdateDbConfigGroupRequest``
schema and returns a byte-identical response, field-swap bug included -- the
same empty string is rejected while ``null`` detaches, confirmed by the
instance history naming the action "Detach config <name>".

So the empty string stays the tool's own vocabulary (an agent expresses "none"
far more reliably than a JSON null) and :data:`CONFIG_ID_DETACH_PAYLOAD` is
what goes on the wire. Kept next to its memory twin in
``memory_instance_handler`` rather than shared, matching how the ``ACTION_*``
constants are handled: one family's payload rule never silently becomes
another's.
"""

CONFIG_ID_DETACH_PAYLOAD = None
"""The wire value for a detach. See :data:`DETACH_CONFIG_GROUP`."""

ORDER_NEXT_STEP = (
    "An order has been raised, not a database. Under the default IAM_USER flow the order "
    "carries a resourceId, but the instance still provisions asynchronously: expect BUILDING "
    "before ACTIVE. Confirm with get_relational_instance, or find it with "
    "list_relational_instances filtered by the name that was ordered."
)

ACTION_NEXT_STEP = (
    "The action was accepted, not completed -- vDB applies it asynchronously. Confirm with "
    "get_relational_instance rather than assuming the new state."
)

SETTING_NEXT_STEP = (
    "Applied asynchronously. Confirm with get_relational_instance -- a configuration group "
    "in particular takes longer to appear than a single follow-up read."
)

EMPTY_ACTION_WARNING = (
    "The API answered 200 but returned no result for this instance, and an action it has "
    "actually queued always reports one. Treat this as NOT applied: check "
    "get_relational_instance and list_relational_instance_histories, and tell the user the "
    "call had no effect rather than reporting success. The known cause is an `action` or "
    "`resType` value the endpoint does not recognise -- it answers 200 and silently ignores "
    "the request instead of rejecting it."
)


def _action_data(instance_id: str, results: list[ActionResult]) -> ActionData:
    """Wrap action results, refusing to present an empty response as success."""
    if not results:
        return ActionData(
            instance_id=instance_id,
            accepted=False,
            results=[],
            warning=EMPTY_ACTION_WARNING,
            next_step=ACTION_NEXT_STEP,
        )
    return ActionData(instance_id=instance_id, results=results, next_step=ACTION_NEXT_STEP)


def _instance_envelope(instance_id: str, action: str, config: dict | None = None) -> dict:
    """Build the nested envelope every lifecycle action expects.

    The instance id is already in the path; the API wants it in the body too,
    under a list, with the action repeated. Nothing here is redundant to the
    API even though all of it is redundant to the reader.
    """
    detail: dict[str, Any] = {"instancesId": instance_id}
    if config is not None:
        detail["config"] = config
    return {
        "databaseInstances": [detail],
        "action": action,
        "resType": RESOURCE_TYPE,
    }


def _order_envelope(instance_id: str, action: str, config: dict) -> dict:
    """Build the action envelope used by the resize endpoints.

    Identical to `_instance_envelope` except the key is `resourceType`, not
    `resType`. The two spellings are not interchangeable.
    """
    return {
        "databaseInstances": [{"instancesId": instance_id, "config": config}],
        "action": action,
        "resourceType": RESOURCE_TYPE,
    }


class RelationalInstanceHandler:
    """Register and serve the relational instance tools."""

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
        self.mcp.tool(name="list_relational_instances", annotations=READ)(
            self.list_relational_instances
        )
        self.mcp.tool(name="get_relational_instance", annotations=READ)(
            self.get_relational_instance
        )
        self.mcp.tool(name="list_relational_instance_histories", annotations=READ)(
            self.list_relational_instance_histories
        )
        self.mcp.tool(name="list_relational_instance_replicas", annotations=READ)(
            self.list_relational_instance_replicas
        )
        self.mcp.tool(name="list_relational_instance_secrules", annotations=READ)(
            self.list_relational_instance_secrules
        )

        # Previews are read-only whatever they preview.
        self.mcp.tool(name="create_relational_instance_dryrun", annotations=READ)(
            self.create_relational_instance_dryrun
        )
        self.mcp.tool(name="resize_relational_instance_dryrun", annotations=READ)(
            self.resize_relational_instance_dryrun
        )
        self.mcp.tool(name="resize_relational_instance_storage_dryrun", annotations=READ)(
            self.resize_relational_instance_storage_dryrun
        )
        self.mcp.tool(name="create_relational_instance_replicas_dryrun", annotations=READ)(
            self.create_relational_instance_replicas_dryrun
        )
        self.mcp.tool(name="delete_relational_instance_dryrun", annotations=READ)(
            self.delete_relational_instance_dryrun
        )

        if self.allow_write:
            self.mcp.tool(name="create_relational_instance", annotations=WRITE)(
                self.create_relational_instance
            )
            self.mcp.tool(name="start_relational_instance", annotations=WRITE)(
                self.start_relational_instance
            )
            self.mcp.tool(name="stop_relational_instance", annotations=WRITE)(
                self.stop_relational_instance
            )
            self.mcp.tool(name="reboot_relational_instance", annotations=WRITE)(
                self.reboot_relational_instance
            )
            self.mcp.tool(name="resize_relational_instance", annotations=WRITE)(
                self.resize_relational_instance
            )
            self.mcp.tool(name="resize_relational_instance_storage", annotations=WRITE)(
                self.resize_relational_instance_storage
            )
            self.mcp.tool(name="create_relational_instance_replicas", annotations=WRITE)(
                self.create_relational_instance_replicas
            )
            self.mcp.tool(name="detach_relational_instance_replica", annotations=WRITE)(
                self.detach_relational_instance_replica
            )
            self.mcp.tool(name="update_relational_instance_setting", annotations=WRITE)(
                self.update_relational_instance_setting
            )
            self.mcp.tool(name="update_relational_instance_config_group", annotations=WRITE)(
                self.update_relational_instance_config_group
            )
            self.mcp.tool(name="update_relational_instance_secrules", annotations=WRITE)(
                self.update_relational_instance_secrules
            )
            self.mcp.tool(name="delete_relational_instance", annotations=DESTRUCTIVE)(
                self.delete_relational_instance
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self.client.call(
            "GET", path, service=VDB_RELATIONAL_SERVICE, params=params or None
        )

    async def _action(self, path: str, body: dict) -> list[ActionResult]:
        raw = await self.client.call("POST", path, service=VDB_RELATIONAL_SERVICE, json=body)
        return [ActionResult.from_api(row) for row in as_list(raw)]

    async def _order(self, path: str, body: Any, user_type: str) -> list[OrderResult]:
        raw = await self.client.call(
            "POST", path, service=VDB_RELATIONAL_SERVICE, json=body, user_type=user_type
        )
        return [OrderResult.from_api(row) for row in as_list(raw)]

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_relational_instances(
        self,
        name: str | None = Field(None, description="Substring match on instance name"),
        statuses: list[str] | None = Field(
            None,
            description=(
                "Filter by status; observed values include ACTIVE, BUILDING, WAIT_BILLING. "
                "The platform declares no enum, so this is not a closed set."
            ),
        ),
        page: int = Field(1, ge=1, description="Page number (1-based, not 0-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"
        ),
    ) -> RelationalInstanceListData:
        """List relational database instances (MySQL, MariaDB, standalone PostgreSQL).

        This endpoint is a **mixed listing**: it is also the only way to
        enumerate PostgreSQL Clusters, so the raw response contains `pg-` rows
        alongside the `db-` ones. Those are filtered out here and counted in
        `postgresql_clusters_excluded`.

        Because of that mixing, the `name` and `status` filters are **not
        exact**: the API does not apply them to the `pg-` rows, so the page
        totals it reports include rows that do not match. When a filter is
        used, `filters_are_approximate` is set — do not present the count as
        definitive, and do not conclude an instance is absent from one filtered
        page.
        """
        params = {
            **build_page_params(page, page_size),
            **build_filter_params(name=name, statuses=statuses),
        }
        raw = await self._get(BASE, params)
        result = unwrap_nested_list(raw)

        mine = [
            row for row in result.items if str(row.get("id", "")).startswith(RELATIONAL_PREFIX)
        ]
        return RelationalInstanceListData(
            count=len(mine),
            page=PageInfo.from_page(result),
            postgresql_clusters_excluded=len(result.items) - len(mine),
            filters_are_approximate=bool(name or statuses),
            items=[RelationalInstance.from_api(row) for row in mine],
        )

    async def get_relational_instance(
        self,
        instance_id: str = Field(..., description="Instance ID, e.g. 'db-1234abcd-...'"),
    ) -> RelationalInstance:
        """Get one relational database instance by ID.

        **This endpoint is not family-scoped.** Measured live: it resolves a
        MemoryStore (Redis) id and a PostgreSQL Cluster `pg-` id just as
        happily as a relational one, and returns the row without complaint —
        and a Redis instance carries the same `db-` prefix, so the id gives no
        warning. Check `datastore_type` on the result before treating it as a
        relational instance: `resize_relational_instance_storage` and the other
        relational-only operations do not apply to the other families.
        (`get_memory_instance` is the strict one — it answers "not found" for
        anything that is not Redis.)

        The path also differs per family: `/database-instances/id/{id}` here,
        `/database-instances/{id}` in the memory family.
        """
        validate_id(instance_id, "instance_id")
        raw = await self._get(f"{BASE}/id/{instance_id}")
        payload = unwrap_wrapped(raw)
        return RelationalInstance.from_api(payload if isinstance(payload, dict) else {})

    async def list_relational_instance_histories(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        page: int = Field(1, ge=1, description="Page number (1-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"
        ),
    ) -> HistoryListData:
        """List the operation history of one relational instance.

        This is where an asynchronous action reports what actually happened —
        the place to look after a start, resize or restore appears to have done
        nothing.
        """
        validate_id(instance_id, "instance_id")
        raw = await self._get(
            f"{BASE}/{instance_id}/histories", build_page_params(page, page_size)
        )
        result = unwrap_content_list(raw)
        return HistoryListData(
            instance_id=instance_id,
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[HistoryEntry.from_api(row) for row in result.items],
        )

    async def list_relational_instance_replicas(
        self,
        source_instance_id: str = Field(
            ..., description="ID of the SOURCE instance whose replicas to list"
        ),
    ) -> ReplicaListData:
        """List the read replicas of one relational instance.

        The spec does not describe this response; live it is an array of
        instance rows inside the usual envelope. An empty list means the
        instance has no replicas, not that the call failed.
        """
        validate_id(source_instance_id, "source_instance_id")
        raw = await self._get(f"{BASE}/{source_instance_id}/replicas")
        rows = as_list(raw)
        return ReplicaListData(
            source_instance_id=source_instance_id,
            count=len(rows),
            items=[RelationalInstance.from_api(row) for row in rows],
        )

    async def list_relational_instance_secrules(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> SecurityRuleListData:
        """List the security-group rules guarding one relational instance."""
        validate_id(instance_id, "instance_id")
        raw = await self._get(f"{BASE}/{instance_id}/secrules")
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=instance_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    # ------------------------------------------------------------------
    # dry runs
    # ------------------------------------------------------------------

    async def create_relational_instance_dryrun(
        self,
        spec: CreateRelationalInstanceDto = Field(..., description="The instance to order"),
    ) -> DryRunData:
        """Show the exact order that create_relational_instance would place.

        Validates the whole body locally — including the password rule, which
        the API enforces but reports only as `in_valid` — and sends nothing.
        """
        return DryRunData(
            tool="create_relational_instance",
            method="POST",
            path=CREATE_PATH,
            service=VDB_RELATIONAL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order; the monthly cost depends on the flavour and storage.",
                "Returns an order, not an instance: the resourceId it carries is only provisioned "
                "once the order completes.",
                "packageId, volumeType and locateZoneId must all come from the same zone.",
            ],
        )

    async def resize_relational_instance_dryrun(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: ResizeRelationalInstanceDto = Field(..., description="Target flavour"),
    ) -> DryRunData:
        """Show the exact order that resize_relational_instance would place."""
        validate_id(instance_id, "instance_id")
        return DryRunData(
            tool="resize_relational_instance",
            method="POST",
            path=f"{BASE}/{instance_id}/resize-instance",
            service=VDB_RELATIONAL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=_order_envelope(instance_id, ACTION_RESIZE, spec.model_dump(exclude_none=True)),
            warnings=[
                "Raises a BILLABLE order and changes the monthly cost.",
                "The instance restarts, so connections drop.",
                "packageId must be a flavour offered in this instance's zone.",
            ],
        )

    async def resize_relational_instance_storage_dryrun(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: ResizeRelationalStorageDto = Field(..., description="Target storage"),
    ) -> DryRunData:
        """Show the exact order that resize_relational_instance_storage would place."""
        validate_id(instance_id, "instance_id")
        return DryRunData(
            tool="resize_relational_instance_storage",
            method="POST",
            path=f"{BASE}/{instance_id}/resize-storage",
            service=VDB_RELATIONAL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=_order_envelope(
                instance_id, "resize-storage", spec.model_dump(exclude_none=True)
            ),
            warnings=[
                "Raises a BILLABLE order and changes the monthly cost.",
                "Storage can only grow -- it cannot be shrunk afterwards.",
                "The memory family has no equivalent operation.",
            ],
        )

    async def create_relational_instance_replicas_dryrun(
        self,
        source_instance_id: str = Field(..., description="Source instance ID"),
        spec: CreateRelationalReplicaDto = Field(..., description="The replica to order"),
    ) -> DryRunData:
        """Show the exact order that create_relational_instance_replicas would place."""
        validate_id(source_instance_id, "source_instance_id")
        body = spec.model_dump(exclude_none=True)
        body["replicaSourceId"] = source_instance_id
        return DryRunData(
            tool="create_relational_instance_replicas",
            method="POST",
            path=f"{BASE}/{source_instance_id}/create-replicas",
            service=VDB_RELATIONAL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=body,
            warnings=[
                "Raises a BILLABLE order for a second instance.",
                "Returns an order, not a finished resize.",
            ],
        )

    async def delete_relational_instance_dryrun(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        options: DeleteRelationalInstanceDto | None = Field(
            None, description="Deletion options; defaults keep existing backups"
        ),
    ) -> DryRunData:
        """Show exactly what delete_relational_instance would send, without sending it.

        Read-only despite the name — nothing is deleted. Use it to confirm the
        backup options before the real call.
        """
        validate_id(instance_id, "instance_id")
        opts = options or DeleteRelationalInstanceDto()
        warnings = [
            "Destroys the instance. This cannot be undone.",
            "Replicas of this instance must be handled separately.",
        ]
        if opts.deleteAllBackup:
            warnings.insert(
                0,
                "deleteAllBackup is set: every backup of this instance is destroyed too, "
                "removing the only route to recovery.",
            )
        elif not opts.createFinalBackup:
            warnings.append(
                "No final backup will be taken. Set createFinalBackup to keep a last copy."
            )
        return DryRunData(
            tool="delete_relational_instance",
            method="POST",
            path=f"{BASE}/{instance_id}/delete",
            service=VDB_RELATIONAL_SERVICE,
            user_type=None,
            body=_instance_envelope(instance_id, ACTION_DELETE, opts.model_dump()),
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_relational_instance(
        self,
        spec: CreateRelationalInstanceDto = Field(..., description="The instance to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Order a new relational database instance. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_relational_instance_dryrun` first and have the user
          confirm the flavour, storage and zone. This is a billable order.
        - `datastoreType` + `datastoreVersion` must be a pair from
          `list_relational_datastores`; an unlisted pair yields no flavours.
        - `packageId`, `volumeType` and `locateZoneId` must all belong to the
          **same zone**. Volume types are zone-suffixed outside HCM03-1A
          (`Gen2-NVMe2-IOPS3000-HCM03-1B`), and flavour ids differ per zone.
        - `netIds` takes a **subnet** id from `list_relational_subnets`, not a
          network id.
        - The admin password must satisfy the platform rule (letters, digits
          and `$ ^ _ < >` only, start with a letter, end alphanumeric, 8-32
          characters). The API rejects anything else as a bare `in_valid`.

        ## Workflow

        1. `list_relational_datastores` → pick engine and version.
        2. `list_relational_zones` → pick the zone.
        3. `list_relational_flavors` with that engine, version **and zone** →
           pick `packageId`.
        4. `list_relational_volume_types` with the same zone → pick
           `volumeType` and respect its size range.
        5. `list_relational_subnets` → pick `netIds`.
        6. `create_relational_instance_dryrun` → confirm with the user.
        7. This tool. It returns an **order URL, not an instance**: the
           instance appears only once the order completes, then passes through
           BUILDING before ACTIVE. Poll `list_relational_instances` by name.
        """
        require_write(self.allow_write)
        orders = await self._order(CREATE_PATH, spec.model_dump(exclude_none=True), user_type)
        return OrderData(orders=orders, instance_name=spec.name, next_step=ORDER_NEXT_STEP)

    async def start_relational_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> ActionData:
        """Start a stopped relational database instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - The instance must currently be stopped; the API accepts the call
          regardless and reports the outcome asynchronously.

        ## Workflow

        Send this, then confirm with `get_relational_instance` — the response
        here only says the request was accepted. `list_relational_instance_histories`
        shows why, if it did not take effect.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/start", _instance_envelope(instance_id, ACTION_START)
        )
        return _action_data(instance_id, results)

    async def stop_relational_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> ActionData:
        """Stop a running relational database instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first: every open connection drops and the
          database becomes unreachable until it is started again.
        - Stopping does **not** stop billing for storage.

        ## Workflow

        Send this, then confirm with `get_relational_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/shutdown", _instance_envelope(instance_id, ACTION_STOP)
        )
        return _action_data(instance_id, results)

    async def reboot_relational_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> ActionData:
        """Reboot a relational database instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first: connections drop for the duration.

        ## Workflow

        Send this, then confirm with `get_relational_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/reboot", _instance_envelope(instance_id, ACTION_REBOOT)
        )
        return _action_data(instance_id, results)

    async def resize_relational_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: ResizeRelationalInstanceDto = Field(..., description="Target flavour"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Resize a relational instance to a different flavour. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `resize_relational_instance_dryrun` first and have the user
          confirm: this raises a billable order and changes the monthly cost.
        - `packageId` must be a flavour offered **in this instance's zone** —
          read `zone_id` from `get_relational_instance`, then
          `list_relational_flavors` with that zone.
        - The instance restarts, so connections drop.

        ## Workflow

        1. `get_relational_instance` → current flavour and zone.
        2. `list_relational_flavors` for that engine, version and zone.
        3. `resize_relational_instance_dryrun` → confirm with the user.
        4. This tool, then poll `get_relational_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        orders = await self._order(
            f"{BASE}/{instance_id}/resize-instance",
            _order_envelope(instance_id, ACTION_RESIZE, spec.model_dump(exclude_none=True)),
            user_type,
        )
        return OrderData(orders=orders, instance_name=None, next_step=ORDER_NEXT_STEP)

    async def resize_relational_instance_storage(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: ResizeRelationalStorageDto = Field(..., description="Target storage"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Grow the storage of a relational instance. Costs money, and cannot be undone.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `resize_relational_instance_storage_dryrun` first and have the
          user confirm: billable, and **storage cannot be shrunk afterwards**.
        - `volumeSize` must exceed the current size and stay within the range
          `list_relational_volume_types` reports for the instance's zone.
        - This operation exists only in the relational family.

        ## Workflow

        1. `get_relational_instance` → current size, type and zone.
        2. `list_relational_volume_types` for that zone → the valid range.
        3. `resize_relational_instance_storage_dryrun` → confirm with the user.
        4. This tool, then poll `get_relational_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        orders = await self._order(
            f"{BASE}/{instance_id}/resize-storage",
            _order_envelope(instance_id, ACTION_RESIZE, spec.model_dump(exclude_none=True)),
            user_type,
        )
        return OrderData(orders=orders, instance_name=None, next_step=ORDER_NEXT_STEP)

    async def create_relational_instance_replicas(
        self,
        source_instance_id: str = Field(..., description="Source instance ID"),
        spec: CreateRelationalReplicaDto = Field(..., description="The replica to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Order a read replica of a relational instance. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_relational_instance_replicas_dryrun` first and have the
          user confirm: a replica is a second billable instance.
        - `packageId` and `volumeType` must belong to the replica's zone, which
          need not be the source's.

        ## Workflow

        1. `get_relational_instance` on the source → engine, version, size.
        2. `list_relational_flavors` / `list_relational_volume_types` for the
           replica's zone.
        3. `create_relational_instance_replicas_dryrun` → confirm.
        4. This tool, then `list_relational_instance_replicas` on the source.
        """
        require_write(self.allow_write)
        validate_id(source_instance_id, "source_instance_id")
        body = spec.model_dump(exclude_none=True)
        body["replicaSourceId"] = source_instance_id
        orders = await self._order(f"{BASE}/{source_instance_id}/create-replicas", body, user_type)
        return OrderData(orders=orders, instance_name=spec.name, next_step=ORDER_NEXT_STEP)

    async def detach_relational_instance_replica(
        self,
        instance_id: str = Field(
            ..., description="ID of the REPLICA to detach, not of the source"
        ),
    ) -> ActionData:
        """Detach a read replica, promoting it to a standalone instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - `instance_id` is the **replica**, not its source.
        - Confirm with the user: replication stops permanently and the replica
          keeps billing as an independent instance.

        ## Workflow

        1. `list_relational_instance_replicas` on the source → the replica id.
        2. This tool, then `get_relational_instance` on the replica to confirm
           `replica_source_id` is now empty.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/detach-replica",
            _instance_envelope(instance_id, ACTION_DETACH_REPLICA),
        )
        return _action_data(instance_id, results)

    async def update_relational_instance_setting(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: UpdateRelationalSettingDto = Field(..., description="Settings to apply"),
    ) -> SettingUpdateData:
        """Update an instance's password, public access or automatic backup settings.

        Success is the HTTP status: the response body echoes two id fields that
        hold each other's values, and only the status is meant to be relied on,
        so the body is not surfaced.

        ## Requirements

        - `--allow-write` must be enabled.
        - The body is the **desired state, not a patch**: send the values that
          should stay as well as the ones being changed, or they may be reset.
        - A new password must satisfy the platform rule (letters, digits and
          `$ ^ _ < >` only, start with a letter, end alphanumeric, 8-32
          characters). This is validated locally because the API reports a
          violation only as `in_valid`.
        - Enabling `publicAccess` exposes the database to the internet — get
          explicit confirmation.

        ## Workflow

        1. `get_relational_instance` → the current settings.
        2. This tool with the full desired state.
        3. `get_relational_instance` again to confirm.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        body = spec.model_dump(exclude_none=True)
        body["dbInstanceId"] = instance_id
        await self.client.call(
            "PUT",
            f"{BASE}/{instance_id}/update/setting",
            service=VDB_RELATIONAL_SERVICE,
            json=body,
        )
        return SettingUpdateData(instance_id=instance_id, next_step=SETTING_NEXT_STEP)

    async def update_relational_instance_config_group(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        config_id: str = Field(
            ...,
            description=(
                "Configuration group ID from list_relational_configurations, or an empty "
                "string to detach whatever group is attached."
            ),
        ),
    ) -> SettingUpdateData:
        """Attach a configuration group to a relational instance, or detach one.

        Success is the HTTP status; the response body is not surfaced (see
        update_relational_instance_setting).

        ## Requirements

        - `--allow-write` must be enabled.
        - The configuration group's engine and version must match the
          instance's, and its `deployType` must match too (`single_node` for a
          `db-` instance).
        - Pass `""` to detach the current group. Any other value must be a
          well-formed group id. (The spec says an empty string detaches; on the
          wire it is rejected as `in_valid` and a null detaches, so this tool
          translates. Do not send `""` to the endpoint directly.)
        - **The instance must be idle.** Only one edit runs at a time: a second
          one is accepted with HTTP 200 and then fails asynchronously with
          "Cannot perform action EDIT". Wait for `status_kind` to be `settled`.
        - Applying a configuration group usually restarts the database, and a
          parameter marked `restartRequired` leaves the instance in
          `RESTART_REQUIRED` still serving the old value until it reboots.

        ## Workflow

        1. `list_relational_configurations` → a group matching the instance's
           engine, version and deploy type.
        2. `get_relational_instance` → confirm the instance is settled first.
        3. This tool.
        4. `get_relational_instance` to confirm `config_group_id`, and
           `list_relational_instance_histories` if it looks unchanged: a
           failure here is recorded **only** there, and its `description` names
           what the platform understood ("Attach config X" / "Detach config X").

        Note that a *listing* row can report the attached group's id as null
        while still carrying its name, so `config_group_id` is only trustworthy
        from `get_relational_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        detaching = config_id == DETACH_CONFIG_GROUP
        if not detaching:
            validate_id(config_id, "config_id")
        await self.client.call(
            "PUT",
            f"{BASE}/{instance_id}/update/config-group",
            service=VDB_RELATIONAL_SERVICE,
            json={
                "dbInstanceId": instance_id,
                "configId": CONFIG_ID_DETACH_PAYLOAD if detaching else config_id,
            },
        )
        return SettingUpdateData(instance_id=instance_id, next_step=SETTING_NEXT_STEP)

    async def update_relational_instance_secrules(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        rules: list[UpdateSecurityRuleDto] = Field(
            ..., description="The COMPLETE desired rule set"
        ),
    ) -> SecurityRuleListData:
        """Replace the security-group rules on a relational instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - This **replaces the whole rule set**: any existing rule missing from
          `rules` is deleted. Read `list_relational_instance_secrules` first
          and send back the rules that should survive, each with its `id`.
        - A rule opening the database port to `0.0.0.0/0` exposes it to the
          internet — get explicit confirmation.

        ## Workflow

        1. `list_relational_instance_secrules` → the current rules and ids.
        2. This tool with the full desired set.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        body = [rule.model_dump(exclude_none=True) for rule in rules]
        raw = await self.client.call(
            "PUT", f"{BASE}/{instance_id}/secrules", service=VDB_RELATIONAL_SERVICE, json=body
        )
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=instance_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    async def delete_relational_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        options: DeleteRelationalInstanceDto | None = Field(
            None, description="Deletion options; defaults keep existing backups"
        ),
    ) -> ActionData:
        """Delete a relational database instance. Irreversible.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `delete_relational_instance_dryrun` first and have the user
          confirm the instance id **and** the backup options.
        - Never set `deleteAllBackup` unless the user asked for exactly that:
          it destroys every backup, removing the only route to recovery.
        - Decide `createFinalBackup` explicitly rather than accepting the
          default — ask.
        - Replicas are not deleted with their source; handle them separately.

        ## Workflow

        1. `get_relational_instance` → confirm which instance this is.
        2. `list_relational_instance_replicas` → nothing depends on it.
        3. `delete_relational_instance_dryrun` → confirm with the user.
        4. This tool, then `list_relational_instances` to confirm it is gone.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        opts = options or DeleteRelationalInstanceDto()
        results = await self._action(
            f"{BASE}/{instance_id}/delete",
            _instance_envelope(instance_id, ACTION_DELETE, opts.model_dump()),
        )
        return _action_data(instance_id, results)
