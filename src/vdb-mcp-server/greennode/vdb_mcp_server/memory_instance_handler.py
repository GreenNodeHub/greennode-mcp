"""MemoryStore (Redis) instance tools.

This is the memory family's counterpart to ``relational_instance_handler``, and
it is deliberately not a copy of it. Four differences drove the split:

* **The get-by-id paths differ, and so does how strict they are.**
  Memory reads ``GET /database-instances/{id}``, relational reads
  ``GET /database-instances/id/{id}``. Verified live: the memory path answers
  ``not_found`` for a MySQL or a cluster id, while the *relational* path
  happily returns a Redis instance. So `get_memory_instance` is the family
  check that `get_relational_instance` is not.
* **The id prefix is `db-` in both families.** It identifies nothing. The only
  discriminators are which listing returned the row and its `datastore_type`.
* **This listing is single-family and its filters are exact** -- there is no
  mixed-listing caveat and no client-side prefix filtering here.
* **There is no `resize-storage`.** A Redis flavour bundles its disk, so
  changing the flavour is the only sizing operation.

Lifecycle actions take the same nested envelope as the relational family, with
the same values that cannot be read off the path (``/shutdown`` wants
``stop``); a wrong one answers 200 and does nothing.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import VDB_MEMORY_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    ActionData,
    ActionResult,
    CreateMemoryInstanceDto,
    CreateMemoryReplicaDto,
    DatabaseBackup,
    DeleteMemoryInstanceDto,
    DryRunData,
    HistoryEntry,
    HistoryListData,
    MemoryInstance,
    MemoryInstanceBackupListData,
    MemoryInstanceListData,
    MemoryReplicaListData,
    OrderData,
    OrderResult,
    PageInfo,
    ResizeMemoryInstanceDto,
    SecurityRule,
    SecurityRuleListData,
    SettingUpdateData,
    UpdateMemorySettingDto,
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

RESOURCE_TYPE = "dbaas"
"""The only value `resType` / `resourceType` accepts in the action envelope.

The same literal as the relational family -- not `dbaas-memory`, not the path
segment. The spec says so in the field's ``description`` and ``example`` and
nowhere else: there is no ``enum`` to read it off.
"""

# The `action` value each endpoint expects. Spelled out because they are NOT
# derivable from the path: `/shutdown` wants "stop", `/detach-replica` wants
# "detach_replica" with an underscore. Sending the path segment instead gets a
# 200 with an empty result array and no effect at all.
ACTION_START = "start"
ACTION_STOP = "stop"
ACTION_REBOOT = "reboot"
ACTION_DETACH_REPLICA = "detach_replica"
ACTION_DELETE = "delete"
ACTION_RESIZE = "resize"

DETACH_CONFIG_GROUP = ""
"""What a *caller* passes to detach the attached configuration group.

The spec says to detach with an empty string, and that is wrong: measured live
on an idle instance with a group genuinely attached,
``{"configId": ""}`` is rejected with ``400 in_valid``, while ``null`` -- and
omitting the key -- detaches. The instance history names the action
"Detach config <name>" in both of the working cases, which is how the intent
was confirmed rather than inferred.

So the empty string stays the tool's own vocabulary (an agent expresses "none"
far more reliably than a JSON null) and :data:`CONFIG_ID_DETACH_PAYLOAD` is
what actually goes on the wire.
"""

CONFIG_ID_DETACH_PAYLOAD = None
"""The wire value for a detach. See :data:`DETACH_CONFIG_GROUP`."""

ORDER_NEXT_STEP = (
    "An order has been raised, not a database. Under the default IAM_USER flow the order "
    "carries a resourceId, but the instance still provisions asynchronously: expect BUILDING "
    "before ACTIVE. Confirm with get_memory_instance, or find it with list_memory_instances "
    "filtered by the name that was ordered."
)

ACTION_NEXT_STEP = (
    "The action was accepted, not completed -- vDB applies it asynchronously. Confirm with "
    "get_memory_instance rather than assuming the new state, and note that a poll issued "
    "seconds after an action still reports the status from before it."
)

SETTING_NEXT_STEP = (
    "Applied asynchronously. Confirm with get_memory_instance -- a configuration group in "
    "particular takes longer to appear than a single follow-up read. "
    "list_memory_instance_histories records what actually happened."
)

EMPTY_ACTION_WARNING = (
    "The API answered 200 but returned no result for this instance, and an action it has "
    "actually queued always reports one. Treat this as NOT applied: check get_memory_instance "
    "and list_memory_instance_histories, and tell the user the call had no effect rather than "
    "reporting success. The known cause is an `action` or `resType` value the endpoint does "
    "not recognise -- it answers 200 and silently ignores the request instead of rejecting it."
)

NOT_FOUND_MESSAGE = (
    "No MemoryStore instance with id '{instance_id}'. Note that relational instances share "
    "the 'db-' prefix, so this may be a relational instance -- try get_relational_instance. "
    "Otherwise list_memory_instances shows what exists."
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
    under a list, with the action repeated.
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
    """Build the action envelope the resize endpoint uses.

    Identical to `_instance_envelope` except the key is `resourceType`, not
    `resType`. The two spellings are not interchangeable, and nothing in the
    path says which one an endpoint wants.
    """
    return {
        "databaseInstances": [{"instancesId": instance_id, "config": config}],
        "action": action,
        "resourceType": RESOURCE_TYPE,
    }


class MemoryInstanceHandler:
    """Register and serve the MemoryStore instance tools."""

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
        self.mcp.tool(name="list_memory_instances", annotations=READ)(self.list_memory_instances)
        self.mcp.tool(name="get_memory_instance", annotations=READ)(self.get_memory_instance)
        self.mcp.tool(name="list_memory_instance_histories", annotations=READ)(
            self.list_memory_instance_histories
        )
        self.mcp.tool(name="list_memory_instance_replicas", annotations=READ)(
            self.list_memory_instance_replicas
        )
        self.mcp.tool(name="list_memory_instance_secrules", annotations=READ)(
            self.list_memory_instance_secrules
        )
        self.mcp.tool(name="list_memory_instance_backups", annotations=READ)(
            self.list_memory_instance_backups
        )

        # Previews are read-only whatever they preview.
        self.mcp.tool(name="create_memory_instance_dryrun", annotations=READ)(
            self.create_memory_instance_dryrun
        )
        self.mcp.tool(name="resize_memory_instance_dryrun", annotations=READ)(
            self.resize_memory_instance_dryrun
        )
        self.mcp.tool(name="create_memory_instance_replicas_dryrun", annotations=READ)(
            self.create_memory_instance_replicas_dryrun
        )
        self.mcp.tool(name="delete_memory_instance_dryrun", annotations=READ)(
            self.delete_memory_instance_dryrun
        )

        if self.allow_write:
            self.mcp.tool(name="create_memory_instance", annotations=WRITE)(
                self.create_memory_instance
            )
            self.mcp.tool(name="start_memory_instance", annotations=WRITE)(
                self.start_memory_instance
            )
            self.mcp.tool(name="stop_memory_instance", annotations=WRITE)(
                self.stop_memory_instance
            )
            self.mcp.tool(name="reboot_memory_instance", annotations=WRITE)(
                self.reboot_memory_instance
            )
            self.mcp.tool(name="resize_memory_instance", annotations=WRITE)(
                self.resize_memory_instance
            )
            self.mcp.tool(name="create_memory_instance_replicas", annotations=WRITE)(
                self.create_memory_instance_replicas
            )
            self.mcp.tool(name="detach_memory_instance_replica", annotations=WRITE)(
                self.detach_memory_instance_replica
            )
            self.mcp.tool(name="update_memory_instance_setting", annotations=WRITE)(
                self.update_memory_instance_setting
            )
            self.mcp.tool(name="update_memory_instance_config_group", annotations=WRITE)(
                self.update_memory_instance_config_group
            )
            self.mcp.tool(name="update_memory_instance_secrules", annotations=WRITE)(
                self.update_memory_instance_secrules
            )
            self.mcp.tool(name="delete_memory_instance", annotations=DESTRUCTIVE)(
                self.delete_memory_instance
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self.client.call(
            "GET", path, service=VDB_MEMORY_SERVICE, params=params or None
        )

    async def _action(self, path: str, body: dict) -> list[ActionResult]:
        raw = await self.client.call("POST", path, service=VDB_MEMORY_SERVICE, json=body)
        return [ActionResult.from_api(row) for row in as_list(raw)]

    async def _order(self, path: str, body: Any, user_type: str) -> list[OrderResult]:
        raw = await self.client.call(
            "POST", path, service=VDB_MEMORY_SERVICE, json=body, user_type=user_type
        )
        return [OrderResult.from_api(row) for row in as_list(raw)]

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_memory_instances(
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
    ) -> MemoryInstanceListData:
        """List MemoryStore (Redis) instances.

        Unlike `list_relational_instances`, this listing holds one family only
        and its filters are exact — a name matching nothing returns zero rows,
        so an empty result really does mean "no such instance".

        The ids look identical to relational ids (`db-...`): the prefix says
        nothing about the family. If an id is not found here, it may well be a
        relational instance.
        """
        params = {
            **build_page_params(page, page_size),
            **build_filter_params(name=name, statuses=statuses),
        }
        raw = await self._get(BASE, params)
        result = unwrap_nested_list(raw)
        return MemoryInstanceListData(
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[MemoryInstance.from_api(row) for row in result.items],
        )

    async def get_memory_instance(
        self,
        instance_id: str = Field(..., description="Instance ID, e.g. 'db-5a4d26f1-...'"),
    ) -> MemoryInstance:
        """Get one MemoryStore (Redis) instance by ID.

        This is the family-strict lookup: the memory path answers `not_found`
        for a relational instance or a PostgreSQL Cluster id, so a successful
        result here is proof the instance really is a Redis one. The relational
        get-by-id does **not** offer that guarantee — it resolves ids from
        every family.
        """
        validate_id(instance_id, "instance_id")
        raw = await self._get(f"{BASE}/{instance_id}")
        payload = unwrap_wrapped(raw)
        if not isinstance(payload, dict) or not payload.get("id"):
            # Guarded rather than assumed: elsewhere in this API a missing
            # resource comes back as 200 with an empty body, which would build
            # a model whose every field is "" and read as a real instance.
            raise ValueError(NOT_FOUND_MESSAGE.format(instance_id=instance_id))
        return MemoryInstance.from_api(payload)

    async def list_memory_instance_histories(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        page: int = Field(1, ge=1, description="Page number (1-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"
        ),
    ) -> HistoryListData:
        """List the operation history of one MemoryStore instance.

        This is where an asynchronous action reports what actually happened —
        the place to look after a start, resize or backup appeared to do
        nothing. Some failures are recorded here and nowhere else.
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

    async def list_memory_instance_replicas(
        self,
        source_instance_id: str = Field(
            ..., description="ID of the SOURCE instance whose replicas to list"
        ),
    ) -> MemoryReplicaListData:
        """List the read replicas of one MemoryStore instance.

        The spec does not describe this response; live it is an array of
        instance rows inside the usual envelope. An empty list means the
        instance has no replicas, not that the call failed — though the link
        can take a moment to appear after a replica is ordered.

        The rows are **thinner than a listing row**: `zone_id`, `subnet_id`,
        `port` and `replica_source_id` all come back empty here. Call
        `get_memory_instance` on a replica id for its real detail.
        """
        validate_id(source_instance_id, "source_instance_id")
        raw = await self._get(f"{BASE}/{source_instance_id}/replicas")
        rows = as_list(raw)
        return MemoryReplicaListData(
            source_instance_id=source_instance_id,
            count=len(rows),
            items=[MemoryInstance.from_api(row) for row in rows],
        )

    async def list_memory_instance_secrules(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> SecurityRuleListData:
        """List the security-group rules guarding one MemoryStore instance.

        Worth checking on a new instance: vDB opens the Redis port to
        `0.0.0.0/0` by default, whatever `publicAccess` was set to.
        """
        validate_id(instance_id, "instance_id")
        raw = await self._get(f"{BASE}/{instance_id}/secrules")
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=instance_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    async def list_memory_instance_backups(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> MemoryInstanceBackupListData:
        """List every backup of one MemoryStore instance.

        Unpaginated — the whole array comes back at once. The rows are index
        entries, not descriptions: this endpoint returns `instance_id`,
        `instance_name` and the sizing, flavour and network fields as null, and
        lowercases the engine name. Read `get_memory_backup` before planning a
        restore from one.

        Note the path differs from the relational family, which reads
        `GET /backups/insId/{instanceId}` instead.
        """
        validate_id(instance_id, "instance_id")
        raw = await self._get(f"{BASE}/{instance_id}/backups")
        rows = as_list(raw)
        return MemoryInstanceBackupListData(
            instance_id=instance_id,
            count=len(rows),
            items=[DatabaseBackup.from_api(row) for row in rows],
        )

    # ------------------------------------------------------------------
    # dry runs
    # ------------------------------------------------------------------

    async def create_memory_instance_dryrun(
        self,
        spec: CreateMemoryInstanceDto = Field(..., description="The instance to order"),
    ) -> DryRunData:
        """Show the exact order that create_memory_instance would place.

        Validates the whole body locally — including the 16-128 character
        password rule and the "public access requires a password" rule, both of
        which the API enforces but reports only as `in_valid` — and sends
        nothing.
        """
        return DryRunData(
            tool="create_memory_instance",
            method="POST",
            path=CREATE_PATH,
            service=VDB_MEMORY_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order; the monthly cost depends on the flavour.",
                "packageId must come from the same zone as locateZoneId -- flavour ids differ "
                "per zone, and a flavour listed without a zone describes HCM03-1A only.",
                "The new instance gets a 0.0.0.0/0 rule on port 6379 regardless of "
                "publicAccess; narrow it with update_memory_instance_secrules afterwards.",
            ],
        )

    async def resize_memory_instance_dryrun(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: ResizeMemoryInstanceDto = Field(..., description="Target flavour"),
    ) -> DryRunData:
        """Show the exact order that resize_memory_instance would place."""
        validate_id(instance_id, "instance_id")
        return DryRunData(
            tool="resize_memory_instance",
            method="POST",
            path=f"{BASE}/{instance_id}/resize-instance",
            service=VDB_MEMORY_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=_order_envelope(instance_id, ACTION_RESIZE, spec.model_dump(exclude_none=True)),
            warnings=[
                "Raises a BILLABLE order and changes the monthly cost.",
                "The instance restarts, so connections drop and an in-memory dataset without "
                "persistence is lost.",
                "packageId must be a flavour offered in this instance's zone.",
                "A smaller flavour means less RAM: confirm the dataset still fits.",
            ],
        )

    async def create_memory_instance_replicas_dryrun(
        self,
        source_instance_id: str = Field(..., description="Source instance ID"),
        spec: CreateMemoryReplicaDto = Field(..., description="The replica to order"),
    ) -> DryRunData:
        """Show the exact order that create_memory_instance_replicas would place."""
        validate_id(source_instance_id, "source_instance_id")
        body = spec.model_dump(exclude_none=True)
        body["replicaSourceId"] = source_instance_id
        return DryRunData(
            tool="create_memory_instance_replicas",
            method="POST",
            path=f"{BASE}/{source_instance_id}/create-replicas",
            service=VDB_MEMORY_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=body,
            warnings=[
                "Raises a BILLABLE order for a second instance.",
                "packageId must belong to the replica's zone, which need not be the source's.",
            ],
        )

    async def delete_memory_instance_dryrun(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        options: DeleteMemoryInstanceDto | None = Field(
            None, description="Deletion options; defaults keep existing backups"
        ),
    ) -> DryRunData:
        """Show exactly what delete_memory_instance would send, without sending it.

        Read-only despite the name — nothing is deleted. Use it to confirm the
        backup options before the real call.
        """
        validate_id(instance_id, "instance_id")
        opts = options or DeleteMemoryInstanceDto()
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
            tool="delete_memory_instance",
            method="POST",
            path=f"{BASE}/{instance_id}/delete",
            service=VDB_MEMORY_SERVICE,
            user_type=None,
            body=_instance_envelope(instance_id, ACTION_DELETE, opts.model_dump()),
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_memory_instance(
        self,
        spec: CreateMemoryInstanceDto = Field(..., description="The instance to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Order a new MemoryStore (Redis) instance. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_memory_instance_dryrun` first and have the user confirm
          the flavour and zone. This is a billable order.
        - `datastoreType` + `datastoreVersion` must be a pair from
          `list_memory_datastores`; an unlisted pair yields no flavours.
        - `packageId` must come from `list_memory_flavors` **for the zone in
          `locateZoneId`**. Flavour ids differ per zone, and a flavour list
          fetched without a zone describes HCM03-1A only.
        - `netIds` takes a **subnet** id from `list_memory_subnets`, not a
          network id.
        - The master password must be **16-128** characters — four times the
          relational minimum — using only letters, digits and `$ ^ _ < >`,
          starting with a letter and ending alphanumeric. A password that is
          valid for a MySQL instance is usually too short here.
        - `publicAccess` requires `redisPasswordEnabled`. Get explicit
          confirmation before exposing Redis publicly at all.
        - There is nothing to size but the flavour: this family has no volume
          fields and no `resize-storage`.

        ## Workflow

        1. `list_memory_datastores` → pick the Redis version.
        2. `list_relational_zones` → pick the zone. The memory family has no
           zones endpoint of its own; the zones are account-wide.
        3. `list_memory_flavors` with that version **and zone** → `packageId`.
        4. `list_memory_subnets` → `netIds`.
        5. `create_memory_instance_dryrun` → confirm with the user.
        6. This tool. Under the default IAM_USER flow the order carries a
           `resourceId`, but provisioning is asynchronous: poll
           `get_memory_instance` through BUILDING to ACTIVE.
        7. `list_memory_instance_secrules` → the new instance is open to
           `0.0.0.0/0`; narrow it.
        """
        require_write(self.allow_write)
        orders = await self._order(CREATE_PATH, spec.model_dump(exclude_none=True), user_type)
        return OrderData(orders=orders, instance_name=spec.name, next_step=ORDER_NEXT_STEP)

    async def start_memory_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> ActionData:
        """Start a stopped MemoryStore instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - The instance must currently be stopped; the API accepts the call
          regardless and reports the outcome asynchronously.

        ## Workflow

        Send this, then confirm with `get_memory_instance` — the response here
        only says the request was accepted, and a poll issued immediately still
        returns the previous status. `list_memory_instance_histories` shows why
        if it did not take effect.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/start", _instance_envelope(instance_id, ACTION_START)
        )
        return _action_data(instance_id, results)

    async def stop_memory_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> ActionData:
        """Stop a running MemoryStore instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first: every open connection drops and the
          cache becomes unreachable until it is started again.
        - Redis is in-memory. Unless persistence is configured, stopping
          discards the dataset — say so before doing it.
        - Stopping does **not** stop billing for storage.

        ## Workflow

        Send this, then confirm with `get_memory_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/shutdown", _instance_envelope(instance_id, ACTION_STOP)
        )
        return _action_data(instance_id, results)

    async def reboot_memory_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
    ) -> ActionData:
        """Reboot a MemoryStore instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first: connections drop for the duration, and
          an in-memory dataset without persistence does not survive.

        ## Workflow

        Send this, then confirm with `get_memory_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/reboot", _instance_envelope(instance_id, ACTION_REBOOT)
        )
        return _action_data(instance_id, results)

    async def resize_memory_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: ResizeMemoryInstanceDto = Field(..., description="Target flavour"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Resize a MemoryStore instance to a different flavour. Costs money.

        The only sizing operation in this family: a Redis flavour bundles its
        disk, so there is no storage resize to reach for.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `resize_memory_instance_dryrun` first and have the user confirm:
          this raises a billable order and changes the monthly cost.
        - `packageId` must be a flavour offered **in this instance's zone** —
          read `zone_id` from `get_memory_instance`, then `list_memory_flavors`
          with that zone.
        - The instance restarts, so connections drop and an unpersisted dataset
          is lost.
        - Check the target flavour's RAM against the current dataset before
          shrinking.

        ## Workflow

        1. `get_memory_instance` → current flavour, RAM and zone.
        2. `list_memory_flavors` for that version and zone.
        3. `resize_memory_instance_dryrun` → confirm with the user.
        4. This tool, then poll `get_memory_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        orders = await self._order(
            f"{BASE}/{instance_id}/resize-instance",
            _order_envelope(instance_id, ACTION_RESIZE, spec.model_dump(exclude_none=True)),
            user_type,
        )
        return OrderData(orders=orders, instance_name=None, next_step=ORDER_NEXT_STEP)

    async def create_memory_instance_replicas(
        self,
        source_instance_id: str = Field(..., description="Source instance ID"),
        spec: CreateMemoryReplicaDto = Field(..., description="The replica to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Order a read replica of a MemoryStore instance. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_memory_instance_replicas_dryrun` first and have the user
          confirm: a replica is a second billable instance.
        - `packageId` must belong to the replica's zone, which need not be the
          source's.
        - The replica inherits the source's authentication; there are no
          password fields here.

        ## Workflow

        1. `get_memory_instance` on the source → version and flavour.
        2. `list_memory_flavors` for the replica's zone.
        3. `create_memory_instance_replicas_dryrun` → confirm.
        4. This tool, then `list_memory_instance_replicas` on the source.
        """
        require_write(self.allow_write)
        validate_id(source_instance_id, "source_instance_id")
        body = spec.model_dump(exclude_none=True)
        body["replicaSourceId"] = source_instance_id
        orders = await self._order(f"{BASE}/{source_instance_id}/create-replicas", body, user_type)
        return OrderData(orders=orders, instance_name=spec.name, next_step=ORDER_NEXT_STEP)

    async def detach_memory_instance_replica(
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

        1. `list_memory_instance_replicas` on the source → the replica id.
        2. This tool, then `get_memory_instance` on the replica to confirm
           `replica_source_id` is now empty.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        results = await self._action(
            f"{BASE}/{instance_id}/detach-replica",
            _instance_envelope(instance_id, ACTION_DETACH_REPLICA),
        )
        return _action_data(instance_id, results)

    async def update_memory_instance_setting(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        spec: UpdateMemorySettingDto = Field(..., description="Settings to apply"),
    ) -> SettingUpdateData:
        """Update a MemoryStore instance's password, public access or backup settings.

        Success is the HTTP status. The spec does not describe this response at
        all, so nothing is read out of the body — see
        `update_memory_instance_config_group` for the same reasoning.

        ## Requirements

        - `--allow-write` must be enabled.
        - The body is the **desired state, not a patch**: send the values that
          should stay as well as the ones being changed.
        - `editRedisPassword` must be `true` to change `redisPasswordEnabled`
          or `redisPassword`. Without it the platform accepts the request and
          leaves the password untouched, reporting nothing — so this tool
          rejects that combination locally instead.
        - A new password must be **16-128** characters using only letters,
          digits and `$ ^ _ < >`, starting with a letter and ending
          alphanumeric. Validated locally because the API reports a violation
          only as `in_valid`.
        - Enabling `publicAccess` exposes Redis to the internet and requires
          the password to be enabled — get explicit confirmation.

        ## Workflow

        1. `get_memory_instance` → the current settings.
        2. This tool with the full desired state.
        3. `get_memory_instance` again to confirm, and
           `list_memory_instance_histories` if it looks unchanged.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        body = spec.model_dump(exclude_none=True)
        body["dbInstanceId"] = instance_id
        await self.client.call(
            "PUT",
            f"{BASE}/{instance_id}/update-setting",
            service=VDB_MEMORY_SERVICE,
            json=body,
        )
        return SettingUpdateData(instance_id=instance_id, next_step=SETTING_NEXT_STEP)

    async def update_memory_instance_config_group(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        config_id: str = Field(
            ...,
            description=(
                "Configuration group ID from list_memory_configurations, or an empty string "
                "to detach whatever group is attached."
            ),
        ),
    ) -> SettingUpdateData:
        """Attach a configuration group to a MemoryStore instance, or detach one.

        Success is the HTTP status; the response body is not surfaced. Measured
        live, it answers `{status: 202, dbInstanceId: <the PROJECT id>,
        projectId: <the INSTANCE id>}` — the two id fields hold each other's
        values, exactly as the relational pair does — so reading an id out of
        it would hand the caller a project id labelled as an instance id.

        ## Requirements

        - `--allow-write` must be enabled.
        - The group's Redis version should match the instance's, and its
          `deployType` must be `single_node`.
        - Pass `""` to detach the current group. Any other value must be a
          well-formed group id. (The spec says an empty string detaches; on the
          wire it is rejected as `in_valid` and a null detaches, so this tool
          translates. Do not send `""` to the endpoint directly.)
        - **The instance must be idle.** Only one edit runs at a time: a second
          one is accepted with HTTP 200 and then fails asynchronously with
          "Cannot perform action EDIT". Wait for `status_kind` to be `settled`.
        - Applying a configuration group restarts the database, and a parameter
          marked `restartRequired` leaves the instance in `RESTART_REQUIRED`
          still serving the old value until it reboots.

        ## Workflow

        1. `list_memory_configurations` → a group matching the instance's Redis
           version.
        2. `get_memory_instance` → confirm the instance is settled first.
        3. This tool.
        4. `get_memory_instance` to confirm `config_group_id`, and
           `list_memory_instance_histories` if it looks unchanged: a failure
           here is recorded **only** there, and its `description` names what
           the platform understood ("Attach config redis72" / "Detach config
           redis72").

        Note that a *listing* row reports the attached group's id as null while
        still carrying its name, so `config_group_id` is only trustworthy from
        `get_memory_instance`.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        detaching = config_id == DETACH_CONFIG_GROUP
        if not detaching:
            validate_id(config_id, "config_id")
        await self.client.call(
            "PUT",
            f"{BASE}/{instance_id}/update-config-group",
            service=VDB_MEMORY_SERVICE,
            json={
                "dbInstanceId": instance_id,
                "configId": CONFIG_ID_DETACH_PAYLOAD if detaching else config_id,
            },
        )
        return SettingUpdateData(instance_id=instance_id, next_step=SETTING_NEXT_STEP)

    async def update_memory_instance_secrules(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        rules: list[UpdateSecurityRuleDto] = Field(
            ..., description="The COMPLETE desired rule set"
        ),
    ) -> SecurityRuleListData:
        """Replace the security-group rules on a MemoryStore instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - This **replaces the whole rule set**: any existing rule missing from
          `rules` is deleted. Read `list_memory_instance_secrules` first and
          send back the rules that should survive, each with its `id`.
        - The Redis port is 6379.
        - A new instance arrives with `0.0.0.0/0` on that port. Narrowing it is
          what this tool is for; leaving it open exposes an in-memory database
          to the internet.

        ## Workflow

        1. `list_memory_instance_secrules` → the current rules and ids.
        2. This tool with the full desired set.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        body = [rule.model_dump(exclude_none=True) for rule in rules]
        raw = await self.client.call(
            "PUT", f"{BASE}/{instance_id}/secrules", service=VDB_MEMORY_SERVICE, json=body
        )
        rows = as_list(raw)
        return SecurityRuleListData(
            instance_id=instance_id,
            count=len(rows),
            items=[SecurityRule.from_api(row) for row in rows],
        )

    async def delete_memory_instance(
        self,
        instance_id: str = Field(..., description="Instance ID"),
        options: DeleteMemoryInstanceDto | None = Field(
            None, description="Deletion options; defaults keep existing backups"
        ),
    ) -> ActionData:
        """Delete a MemoryStore (Redis) instance. Irreversible.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `delete_memory_instance_dryrun` first and have the user confirm
          the instance id **and** the backup options.
        - Confirm the id belongs to this family: relational instances share the
          `db-` prefix, so read it back with `get_memory_instance` first.
        - Never set `deleteAllBackup` unless the user asked for exactly that:
          it destroys every backup, removing the only route to recovery.
        - Decide `createFinalBackup` explicitly rather than accepting the
          default — ask.
        - Replicas are not deleted with their source; handle them separately.

        ## Workflow

        1. `get_memory_instance` → confirm which instance this is.
        2. `list_memory_instance_replicas` → nothing depends on it.
        3. `delete_memory_instance_dryrun` → confirm with the user.
        4. This tool, then `list_memory_instances` to confirm it is gone.
        """
        require_write(self.allow_write)
        validate_id(instance_id, "instance_id")
        opts = options or DeleteMemoryInstanceDto()
        results = await self._action(
            f"{BASE}/{instance_id}/delete",
            _instance_envelope(instance_id, ACTION_DELETE, opts.model_dump()),
        )
        return _action_data(instance_id, results)
