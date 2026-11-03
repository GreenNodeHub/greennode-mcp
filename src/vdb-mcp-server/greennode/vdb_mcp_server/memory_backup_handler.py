"""MemoryStore (Redis) backup tools.

The backup *resource* is shared with the relational family -- one upstream
`BackupInfo` schema, one `CreateBackupRequest`, one `DeleteBackupRequest` -- so
the models and the create DTO are shared too. What is not shared is the routing
and one payload:

* **`get` reverses the path segments**: `/backups/{id}/detail` here,
  `/backups/detail/{id}` in the relational family. Same words, different order
  -- and neither endpoint is family-scoped: each resolves the other family's
  backup ids without complaint, so `datastore_type` is the only discriminator.
* **Delete is a `POST` with no id in the path**, so its array body is the only
  thing naming what to remove -- and it therefore deletes **as many backups as
  the array holds**. The relational endpoint is a `DELETE` that repeats one id
  in the path, so it can only ever remove one. That is why this tool is named
  in the plural and takes a list.
* **Restore takes the Redis password pair instead of volume fields**, because a
  Redis flavour brings its own disk. Everything else about restore matches the
  relational family, including the two constants that no other endpoint uses
  (`restore_backup` / `dbaas-backup`).
* **There is no `list_memory_instance_backups` here** -- that endpoint hangs
  off `/database-instances/{id}/backups` and lives in the instance handler.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import VDB_MEMORY_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    BackupCreateData,
    BackupDeleteData,
    BackupDeleteResult,
    CreateBackupDto,
    DatabaseBackup,
    DryRunData,
    FreeBackupUsageData,
    MemoryBackupListData,
    OrderData,
    OrderResult,
    PageInfo,
    RestoreMemoryBackupDto,
)
from greennode.vdb_mcp_server.paging import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    as_list,
    build_page_params,
    unwrap_content_list,
    unwrap_wrapped,
)
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


BASE = "/v1/backups"

ACTION_RESTORE_BACKUP = "restore_backup"
"""What `action` must say on `POST /v1/backups/{id}/restore`.

Not `restore`, and not the path segment. The spec puts the allowed value in
the field's ``description`` ("Allowed value: restore_backup") and ``example``,
with no ``enum`` -- the same place the lifecycle actions hide theirs.
"""

BACKUP_RESOURCE_TYPE = "dbaas-backup"
"""What `resourceType` must say on a restore.

Two differences from the instance envelope at once: the key is `resourceType`
(the lifecycle actions use `resType`) and the value is `dbaas-backup` (they use
plain `dbaas`). Neither is derivable from the other, and a wrong value here
produces a 200 with an empty result rather than a rejection.
"""

RESTORE_NEXT_STEP = (
    "An order has been raised for a NEW instance built from this backup -- the backup itself "
    "is untouched and so is the instance it came from. Under the default IAM_USER flow the "
    "order carries a resourceId, but provisioning is asynchronous: poll get_memory_instance, "
    "or find it with list_memory_instances filtered by the name that was ordered, and expect "
    "BUILDING before ACTIVE."
)

CREATE_NEXT_STEP = (
    "The backup id exists immediately but the backup does not: it is written asynchronously "
    "and passes through NEW/BUILDING/SAVING before COMPLETED. Poll get_memory_backup until "
    "status_kind stops being transitional, and do not restore from it before then. "
    "`success: true` here describes the request being accepted, NOT the backup being made -- "
    "in the relational family a backup has been observed accepted with HTTP 200 and then "
    "failing on the platform side, after which it appears in no listing and the id resolves "
    "to nothing. When that happens, list_memory_instance_histories is the only place the "
    "failure is recorded; report it as a platform fault rather than retrying blindly."
)

DELETE_NEXT_STEP = (
    "Deletion is asynchronous. Confirm with list_memory_instance_backups rather than assuming "
    "the backups are gone."
)

EMPTY_DELETE_WARNING = (
    "The API answered 200 but returned no results at all, and a deletion it has actually "
    "queued reports one row per backup. Treat this as NOT deleted: re-read "
    "list_memory_instance_backups and tell the user the call had no effect rather than "
    "reporting success."
)

FAILED_DELETE_WARNING = (
    "The API answered HTTP 200 but at least one result says the deletion did NOT happen. "
    "Measured on this endpoint: deleting a backup id that does not exist returns 200 carrying "
    "`success: false, code: 404, errorMsg: 'Resource not found'`, while a deletion that "
    "really happens reports `success: null` -- so null is normal here and only an explicit "
    "false is a failure. The HTTP status describes the call, not the deletion; report the "
    "per-result error, not success."
)

PARTIAL_DELETE_WARNING = (
    "The API returned fewer results than backups were asked for, so at least one id was not "
    "acted on and the response does not say which. Re-read list_memory_instance_backups "
    "before reporting anything as deleted."
)

MISSING_BACKUP_MESSAGE = (
    "No backup with id {backup_id!r}. The relational family answers this case with HTTP 200 "
    "and an empty payload rather than a 404, so this is 'not found', not an empty backup. "
    "Note that it is not a family mismatch either: this endpoint resolves relational backups "
    "too, so the id does not exist at all. Two common causes: the backup was requested but "
    "the platform failed to build it (create_memory_backup returns an id and HTTP 200 before "
    "the work happens), or it has already been deleted. Check "
    "list_memory_instance_histories for a 'Create backup' entry with status 'Failed'."
)


def _restore_envelope(backup_id: str, config: dict) -> dict:
    """Build the body of a restore, with the backup id in both places it is wanted.

    The API asks for the backup id in the path *and* inside `config`. The
    caller supplies it once, here, so the two cannot disagree -- which is why
    `RestoreMemoryBackupDto` has no `backupId` field of its own.
    """
    return {
        "databaseInstances": [{"config": {**config, "backupId": backup_id}}],
        "action": ACTION_RESTORE_BACKUP,
        "resourceType": BACKUP_RESOURCE_TYPE,
    }


def _delete_body(backup_ids: list[str]) -> list[dict[str, str]]:
    """Build the JSON **array** body this deletion takes.

    Unlike the relational endpoint there is no id in the path, so this array is
    the whole instruction: every entry in it is deleted.
    """
    return [{"backupId": backup_id} for backup_id in backup_ids]


class MemoryBackupHandler:
    """Register and serve the MemoryStore backup tools."""

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
        self.mcp.tool(name="list_memory_backups", annotations=READ)(self.list_memory_backups)
        self.mcp.tool(name="get_memory_backup", annotations=READ)(self.get_memory_backup)
        self.mcp.tool(name="get_memory_free_backup_usage", annotations=READ)(
            self.get_memory_free_backup_usage
        )

        # Previews are read-only whatever they preview.
        self.mcp.tool(name="restore_memory_backup_dryrun", annotations=READ)(
            self.restore_memory_backup_dryrun
        )

        if self.allow_write:
            self.mcp.tool(name="create_memory_backup", annotations=WRITE)(
                self.create_memory_backup
            )
            self.mcp.tool(name="restore_memory_backup", annotations=WRITE)(
                self.restore_memory_backup
            )
            self.mcp.tool(name="delete_memory_backups", annotations=DESTRUCTIVE)(
                self.delete_memory_backups
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self.client.call(
            "GET", path, service=VDB_MEMORY_SERVICE, params=params or None
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_memory_backups(
        self,
        page: int = Field(1, ge=1, description="Page number (1-based, not 0-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"
        ),
    ) -> MemoryBackupListData:
        """List every MemoryStore backup in the project, across all instances.

        Two limits worth knowing before relying on this:

        - It takes **no filters** — not by name, not by instance, not by
          status. For one instance's backups use
          `list_memory_instance_backups`, a different endpoint that returns the
          whole set unpaginated.
        - The rows are **summaries**. This endpoint returns `null` for storage
          size, storage type, flavour, network, config group, username and
          retention, and lowercases the engine name. Call `get_memory_backup`
          before planning a restore from a row here.
        """
        raw = await self._get(BASE, build_page_params(page, page_size))
        result = unwrap_content_list(raw)
        return MemoryBackupListData(
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[DatabaseBackup.from_api(row) for row in result.items],
        )

    async def get_memory_backup(
        self,
        backup_id: str = Field(..., description="Backup ID, e.g. 'bk-1234abcd-...'"),
    ) -> DatabaseBackup:
        """Get one MemoryStore backup by ID.

        Note the path is `/backups/{id}/detail` in this family but
        `/backups/detail/{id}` in the relational one — the same segments in the
        other order.

        **Neither endpoint is family-scoped**, though: measured live, this one
        returns a MySQL backup and the relational one returns a Redis backup,
        both without complaint. So a result here is not evidence the backup
        belongs to this family — read `datastore_type`. That matters because a
        restore built from it needs the *matching* family's restore tool and
        the matching flavour catalogue.

        Use this rather than a row from `list_memory_backups` whenever the
        sizing, flavour or network fields matter: the listing returns those as
        null and lowercases the engine name.
        """
        validate_id(backup_id, "backup_id")
        raw = await self._get(f"{BASE}/{backup_id}/detail")
        payload = unwrap_wrapped(raw)
        # Guarded rather than assumed: the relational twin answers a missing
        # backup with 200 and an empty payload instead of a 404, and a blank
        # model would hand the caller a backup whose every field is "" --
        # indistinguishable, to an agent, from a real one it can act on.
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError(MISSING_BACKUP_MESSAGE.format(backup_id=backup_id))
        return DatabaseBackup.from_api(payload)

    async def get_memory_free_backup_usage(self) -> FreeBackupUsageData:
        """Show the project's free MemoryStore backup allowance and how much is used.

        The allowance is **separate from the relational one** — a different
        figure from `get_relational_free_backup_usage`, against a different
        pool.

        **It is not a constant.** It is the sum of what the project's instances
        grant, so it moves as instances come and go: measured live, restoring
        one extra instance on a flavour whose catalogue row says
        `backupSize: 5` took the allowance from 100 GB to 105, and deleting
        that instance took it back to 100. So read it when you need it rather
        than remembering a number, and expect it to shrink when an instance is
        deleted — which can put existing backups over the line.

        Backups beyond the allowance need paid backup storage — see
        `list_memory_backup_storage_packages`. Check this before creating a
        large backup rather than after.
        """
        raw = await self._get(f"{BASE}/free-backup")
        payload = unwrap_wrapped(raw)
        return FreeBackupUsageData.from_api(payload if isinstance(payload, dict) else {})

    # ------------------------------------------------------------------
    # dry runs
    # ------------------------------------------------------------------

    async def restore_memory_backup_dryrun(
        self,
        backup_id: str = Field(..., description="Backup to restore from"),
        spec: RestoreMemoryBackupDto = Field(
            ..., description="The NEW instance the restore would create"
        ),
    ) -> DryRunData:
        """Show the exact order that restore_memory_backup would place.

        Read-only — nothing is restored and nothing is ordered. Use it to let
        the user confirm the flavour and zone of the new instance before paying
        for it, and to have the 16-128 character password rule checked locally
        rather than coming back as a bare `in_valid`.
        """
        validate_id(backup_id, "backup_id")
        return DryRunData(
            tool="restore_memory_backup",
            method="POST",
            path=f"{BASE}/{backup_id}/restore",
            service=VDB_MEMORY_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=_restore_envelope(backup_id, spec.model_dump(exclude_none=True)),
            warnings=[
                "Raises a BILLABLE order for a NEW database instance, priced like a create.",
                "The backup and the instance it came from are left untouched -- this does "
                "not roll anything back.",
                "packageId must come from the same zone as locateZoneId -- flavour ids differ "
                "per zone, and a flavour listed without a zone describes HCM03-1A only.",
                "The new instance gets a 0.0.0.0/0 rule on port 6379 regardless of "
                "publicAccess; narrow it with update_memory_instance_secrules afterwards.",
            ],
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_memory_backup(
        self,
        spec: CreateBackupDto = Field(..., description="The backup to take"),
    ) -> BackupCreateData:
        """Take a backup of a MemoryStore (Redis) instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - `dbInstanceId` must be a **MemoryStore** instance. Relational
          instances share the `db-` prefix, so confirm with
          `get_memory_instance` (which rejects anything that is not Redis)
          rather than reading the id.
        - An `INCREMENTAL` backup needs `parentId`: another backup **of the
          same instance** to build on. A `FULL` backup must not have one.
        - `description` must not be empty. The spec marks it optional and the
          API accepts a request without it — then, in the relational family,
          fails the backup asynchronously and leaves nothing behind. It
          defaults to the Portal's own wording, so leave it alone unless the
          user wants something specific.
        - **The instance must be idle.** Only one action runs at a time, and a
          second request is accepted with HTTP 200 and then fails with
          `Cannot perform action ...`. Wait for `status_kind` to be `settled`.
        - Backups consume backup storage. Check
          `get_memory_free_backup_usage` first — the free allowance is not a
          fixed number, it is the sum of what the project's instances grant, so
          read it rather than assuming. Past it, this needs a paid backup
          storage package.

        ## Workflow

        1. `get_memory_free_backup_usage` → confirm there is room.
        2. For an incremental backup, `list_memory_instance_backups` → pick
           `parentId`.
        3. This tool. It returns a backup id straight away, but the backup is
           written asynchronously: poll `get_memory_backup` until the status
           settles on `COMPLETED`. An `INCREMENTAL` backup is only as good as
           its chain of parents — deleting a parent invalidates it.

        If the id stops resolving instead of settling, the backup failed on the
        platform side. `list_memory_instance_histories` is the only place that
        records why — read the `Create backup` entry there and report its error
        rather than retrying.
        """
        require_write(self.allow_write)
        validate_id(spec.dbInstanceId, "dbInstanceId")
        if spec.parentId:
            validate_id(spec.parentId, "parentId")
        raw = await self.client.call(
            "POST",
            f"{BASE}/create",
            service=VDB_MEMORY_SERVICE,
            json=spec.model_dump(exclude_none=True),
        )
        payload = unwrap_wrapped(raw)
        return BackupCreateData.from_api(
            payload if isinstance(payload, dict) else {}, next_step=CREATE_NEXT_STEP
        )

    async def restore_memory_backup(
        self,
        backup_id: str = Field(..., description="Backup to restore from"),
        spec: RestoreMemoryBackupDto = Field(
            ..., description="The NEW instance to create from the backup"
        ),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Restore a backup into a NEW MemoryStore instance. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `restore_memory_backup_dryrun` first and have the user confirm
          the flavour and zone. This is a billable order, priced like creating
          an instance — it does **not** reuse the source instance's billing.
        - Make sure the user understands this creates a **second** instance.
          Nothing is rolled back and nothing is overwritten; the backup and its
          source instance are untouched. To replace the original, they have to
          delete it themselves afterwards.
        - The backup must be `COMPLETED`. Restoring from one still in
          `NEW`/`BUILDING`/`SAVING` is restoring from incomplete data.
        - `packageId` must come from `list_memory_flavors` **for the zone in
          `locateZoneId`**, exactly as on a create. There are no volume fields
          to get wrong here — the flavour decides the disk — so check the
          flavour's RAM against the backup's `ram_gb` instead.
        - `redisPassword` sets the **new** instance's master password; it is
          not inherited from the source. 16-128 characters, and required
          whenever `redisPasswordEnabled` is on.

        ## Workflow

        1. `get_memory_backup` → confirm `COMPLETED` and read `ram_gb`,
           `datastore_type`, `datastore_version`. Use the detail tool, not a
           row from `list_memory_backups`: the listing returns those as null.
        2. `list_relational_zones` → pick the zone for the new instance (the
           memory family has no zones endpoint; the zones are account-wide).
        3. `list_memory_flavors` with that version **and zone** → `packageId`.
        4. `list_memory_subnets` → `netIds`. These are `sub-...` ids; the
           backup's own `network_ids` are `net-...` ids and will not work here,
           despite both being called `netIds` upstream.
        5. `restore_memory_backup_dryrun` → confirm with the user.
        6. This tool, then poll `get_memory_instance` on the returned
           `resource_id` through BUILDING to ACTIVE.
        7. `list_memory_instance_secrules` → the new instance is open to
           `0.0.0.0/0`; narrow it.
        """
        require_write(self.allow_write)
        validate_id(backup_id, "backup_id")
        raw = await self.client.call(
            "POST",
            f"{BASE}/{backup_id}/restore",
            service=VDB_MEMORY_SERVICE,
            json=_restore_envelope(backup_id, spec.model_dump(exclude_none=True)),
            user_type=user_type,
        )
        return OrderData(
            orders=[OrderResult.from_api(row) for row in as_list(raw)],
            instance_name=spec.name,
            next_step=RESTORE_NEXT_STEP,
        )

    async def delete_memory_backups(
        self,
        backup_ids: list[str] = Field(
            ...,
            min_length=1,
            description=(
                "Backups to delete. EVERY id in this list is deleted -- the endpoint takes no "
                "id in the path, so the list is the whole instruction."
            ),
        ),
    ) -> BackupDeleteData:
        """Delete one or more MemoryStore backups. This cannot be undone.

        Plural on purpose: unlike the relational endpoint, which repeats a
        single id in its path, this one is a `POST` whose array body is the
        only thing naming what to remove — so it deletes **as many backups as
        are listed**. Passing several ids is one irreversible action on all of
        them.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm the list with the user, id by id. A deleted backup is
          unrecoverable and may be the only copy of the data.
        - Check `list_memory_instance_backups` for children first: any
          `INCREMENTAL` backup whose `parent_id` is in this list becomes
          unrestorable when its parent goes.
        - Do not pass ids you have not shown the user. There is no partial
          confirmation and no undo.

        ## Workflow

        1. `get_memory_backup` on each id → show the user what they are about
           to lose (name, instance, size, date).
        2. `list_memory_instance_backups` → check nothing depends on them.
        3. This tool, then confirm with `list_memory_instance_backups`.

        HTTP 200 does not mean deleted: each result row carries its own
        `success` flag and can report a 404 inside a 2xx response, so check
        `accepted` and `warning` on the result rather than the status code.
        """
        require_write(self.allow_write)
        # `min_length=1` on the Field only constrains the MCP input schema; a
        # direct call bypasses it, and an empty array would send a destructive
        # request that deletes nothing and reports nothing.
        if not backup_ids:
            raise ValueError(
                "backup_ids must name at least one backup. This endpoint takes no id in the "
                "path, so an empty list is a delete request that identifies nothing."
            )
        for backup_id in backup_ids:
            validate_id(backup_id, "backup_ids")
        raw = await self.client.call(
            "POST",
            f"{BASE}/delete",
            service=VDB_MEMORY_SERVICE,
            json=_delete_body(backup_ids),
        )
        results = [BackupDeleteResult.from_api(row) for row in as_list(raw)]
        if not results:
            return BackupDeleteData(
                backup_ids=backup_ids,
                accepted=False,
                results=[],
                warning=EMPTY_DELETE_WARNING,
                next_step=DELETE_NEXT_STEP,
            )
        # HTTP 200 does not mean deleted: the per-result row carries its own
        # success flag and can report a 404 inside a 2xx response.
        failures = [r for r in results if r.success is False]
        if failures:
            detail = "; ".join(
                f"{r.backup_id or 'unknown id'}: {r.error_message or 'no message'} (code {r.code})"
                for r in failures
            )
            return BackupDeleteData(
                backup_ids=backup_ids,
                accepted=False,
                results=results,
                warning=f"{FAILED_DELETE_WARNING} Reported: {detail}.",
                next_step=DELETE_NEXT_STEP,
            )
        if len(results) < len(backup_ids):
            return BackupDeleteData(
                backup_ids=backup_ids,
                accepted=False,
                results=results,
                warning=(
                    f"{PARTIAL_DELETE_WARNING} Asked for {len(backup_ids)}, "
                    f"got {len(results)} results."
                ),
                next_step=DELETE_NEXT_STEP,
            )
        return BackupDeleteData(backup_ids=backup_ids, results=results, next_step=DELETE_NEXT_STEP)
