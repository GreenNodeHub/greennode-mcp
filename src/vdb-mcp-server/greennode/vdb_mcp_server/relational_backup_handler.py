"""Relational backup tools (MySQL, MariaDB, standalone PostgreSQL).

Four things here do not follow the instance handler, even though the family
and the envelope are the same:

* **Restore is not a lifecycle action, it is a create.** It raises a billable
  order and builds a *new* instance from the backup; it never writes over the
  instance the backup came from. So it needs everything a create needs --
  flavour, volume, subnet, zone -- supplied again.
* **The restore envelope uses different constants** from every other action:
  `action` is `restore_backup` and the key is `resourceType` holding
  `dbaas-backup`, not `resType` holding `dbaas`. Getting either wrong is the
  §4.30 failure mode -- HTTP 200, nothing applied, no error.
* **Delete takes a JSON array as its body**, and repeats in it the backup id
  that is already in the path.
* **Create backup is not an order flow.** It answers synchronously with the id
  of the backup it started, so unlike a create instance there is nothing to
  poll for by name.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import VDB_RELATIONAL_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    BackupCreateData,
    BackupDeleteData,
    BackupDeleteResult,
    CreateBackupDto,
    DatabaseBackup,
    DryRunData,
    FreeBackupUsageData,
    OrderData,
    OrderResult,
    PageInfo,
    RelationalBackupListData,
    RelationalInstanceBackupListData,
    RestoreRelationalBackupDto,
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
    "is untouched and so is the instance it came from. The new instance appears once the "
    "order completes; find it with list_relational_instances filtered by the name that was "
    "ordered, and expect BUILDING before ACTIVE."
)

CREATE_NEXT_STEP = (
    "The backup id exists immediately but the backup does not: it is written asynchronously "
    "and passes through NEW/BUILDING/SAVING before COMPLETED. Poll get_relational_backup "
    "until status_kind stops being transitional, and do not restore from it before then. "
    "`success: true` here describes the request being accepted, NOT the backup being made -- "
    "observed live, a backup can be accepted with HTTP 200 and then fail on the platform "
    "side, after which it never appears in any listing and get_relational_backup reports it "
    "as missing. When that happens, list_relational_instance_histories is the only place the "
    "failure is recorded; report it as a platform fault rather than retrying blindly."
)

DELETE_NEXT_STEP = (
    "Deletion is asynchronous. Confirm with list_relational_instance_backups rather than "
    "assuming the backup is gone."
)

EMPTY_DELETE_WARNING = (
    "The API answered 200 but returned no result for this backup, and a deletion it has "
    "actually queued always reports one. Treat this as NOT deleted: re-read "
    "list_relational_instance_backups and tell the user the call had no effect rather than "
    "reporting success."
)

FAILED_DELETE_WARNING = (
    "The API answered HTTP 200 but the result says the deletion did NOT happen. Observed "
    "live: deleting a backup id that does not exist returns 200 carrying "
    "`success: false, code: 404, errorMsg: 'Resource not found'`. The HTTP status describes "
    "the call, not the deletion -- report the per-result error, not success."
)

MISSING_BACKUP_MESSAGE = (
    "No backup with id {backup_id!r}. The API answered HTTP 200 with an empty payload rather "
    "than a 404, so this is 'not found', not an empty backup. It is not a family mismatch "
    "either: this endpoint resolves memory backups too, so the id does not exist at all. Two "
    "common causes: the backup was requested but the platform failed to build it -- "
    "create_relational_backup returns an id and HTTP 200 before the work happens -- or it has "
    "already been deleted. Check list_relational_instance_histories for a 'Create backup' "
    "entry with status 'Failed'."
)


def _restore_envelope(backup_id: str, config: dict) -> dict:
    """Build the body of a restore, with the backup id in both places it is wanted.

    The API asks for the backup id in the path *and* inside `config`. The
    caller supplies it once, here, so the two cannot disagree -- which is why
    `RestoreRelationalBackupDto` has no `backupId` field of its own.
    """
    return {
        "databaseInstances": [{"config": {**config, "backupId": backup_id}}],
        "action": ACTION_RESTORE_BACKUP,
        "resourceType": BACKUP_RESOURCE_TYPE,
    }


def _delete_body(backup_id: str) -> list[dict[str, str]]:
    """Build the JSON **array** body a backup deletion takes."""
    return [{"backupId": backup_id}]


class RelationalBackupHandler:
    """Register and serve the relational backup tools."""

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
        self.mcp.tool(name="list_relational_backups", annotations=READ)(
            self.list_relational_backups
        )
        self.mcp.tool(name="get_relational_backup", annotations=READ)(self.get_relational_backup)
        self.mcp.tool(name="list_relational_instance_backups", annotations=READ)(
            self.list_relational_instance_backups
        )
        self.mcp.tool(name="get_relational_free_backup_usage", annotations=READ)(
            self.get_relational_free_backup_usage
        )

        # Previews are read-only whatever they preview.
        self.mcp.tool(name="restore_relational_backup_dryrun", annotations=READ)(
            self.restore_relational_backup_dryrun
        )

        if self.allow_write:
            self.mcp.tool(name="create_relational_backup", annotations=WRITE)(
                self.create_relational_backup
            )
            self.mcp.tool(name="restore_relational_backup", annotations=WRITE)(
                self.restore_relational_backup
            )
            self.mcp.tool(name="delete_relational_backup", annotations=DESTRUCTIVE)(
                self.delete_relational_backup
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self.client.call(
            "GET", path, service=VDB_RELATIONAL_SERVICE, params=params or None
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_relational_backups(
        self,
        page: int = Field(1, ge=1, description="Page number (1-based, not 0-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"
        ),
    ) -> RelationalBackupListData:
        """List every relational backup in the project, across all instances.

        Two limits worth knowing before relying on this:

        - It takes **no filters** — not by name, not by instance, not by
          status. For one instance's backups use
          `list_relational_instance_backups`, a different endpoint that returns
          the whole set unpaginated.
        - The rows are **summaries**. This endpoint returns `null` for storage
          size, storage type, flavour, network, config group, username and
          retention, and lowercases the engine name. Call
          `get_relational_backup` before planning a restore from a row here.

        The `pageObject.max_page_size` it reports is 50, lower than the 100 the
        instance listings report; a larger page size is still honoured.
        """
        raw = await self._get(BASE, build_page_params(page, page_size))
        result = unwrap_content_list(raw)
        return RelationalBackupListData(
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[DatabaseBackup.from_api(row) for row in result.items],
        )

    async def get_relational_backup(
        self,
        backup_id: str = Field(..., description="Backup ID, e.g. 'bk-1234abcd-...'"),
    ) -> DatabaseBackup:
        """Get one relational backup by ID.

        Note the path is `/backups/detail/{id}` in this family but
        `/backups/{id}/detail` in the memory family — the segments are the same
        and the order is not.

        **Neither endpoint is family-scoped**, though: measured live, this one
        returns a Redis backup and the memory one returns a MySQL backup, both
        without complaint. So a result here is not evidence the backup belongs
        to this family — read `datastore_type`. That matters because a restore
        built from it needs the *matching* family's restore tool and the
        matching flavour catalogue.
        """
        validate_id(backup_id, "backup_id")
        raw = await self._get(f"{BASE}/detail/{backup_id}")
        payload = unwrap_wrapped(raw)
        # A backup that does not exist comes back as 200 with an empty payload,
        # not a 404. Returning a blank model would hand the caller a backup
        # whose every field is "" -- indistinguishable, to an agent, from a real
        # one it can act on.
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError(MISSING_BACKUP_MESSAGE.format(backup_id=backup_id))
        return DatabaseBackup.from_api(payload)

    async def list_relational_instance_backups(
        self,
        instance_id: str = Field(..., description="Instance ID, e.g. 'db-1234abcd-...'"),
    ) -> RelationalInstanceBackupListData:
        """List every backup of one relational instance.

        Unlike `list_relational_backups` this returns the whole set in one
        response — it does not paginate. An empty list means the instance has
        no backups, not that the call failed.

        Rows are summaries here too, and `instance_id`/`instance_name` come
        back empty because the instance is the thing being asked about —
        `instance_id` on the result is the one that was requested.
        """
        validate_id(instance_id, "instance_id")
        raw = await self._get(f"{BASE}/insId/{instance_id}")
        rows = as_list(raw)
        return RelationalInstanceBackupListData(
            instance_id=instance_id,
            count=len(rows),
            items=[DatabaseBackup.from_api(row) for row in rows],
        )

    async def get_relational_free_backup_usage(self) -> FreeBackupUsageData:
        """Show the project's free backup allowance and how much of it is used.

        Backups beyond the free allowance need paid backup storage — see
        `list_relational_backup_storage_packages`. Check this before creating a
        large backup rather than after.
        """
        raw = await self._get(f"{BASE}/free-backup")
        payload = unwrap_wrapped(raw)
        return FreeBackupUsageData.from_api(payload if isinstance(payload, dict) else {})

    # ------------------------------------------------------------------
    # dry runs
    # ------------------------------------------------------------------

    async def restore_relational_backup_dryrun(
        self,
        backup_id: str = Field(..., description="Backup to restore from"),
        spec: RestoreRelationalBackupDto = Field(
            ..., description="The NEW instance the restore would create"
        ),
    ) -> DryRunData:
        """Show the exact order that restore_relational_backup would place.

        Read-only — nothing is restored and nothing is ordered. Use it to let
        the user confirm the flavour, storage and zone of the new instance
        before paying for it.
        """
        validate_id(backup_id, "backup_id")
        return DryRunData(
            tool="restore_relational_backup",
            method="POST",
            path=f"{BASE}/{backup_id}/restore",
            service=VDB_RELATIONAL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=_restore_envelope(backup_id, spec.model_dump(exclude_none=True)),
            warnings=[
                "Raises a BILLABLE order for a NEW database instance, priced like a create.",
                "The backup and the instance it came from are left untouched -- this does "
                "not roll anything back.",
                "packageId, volumeType and locateZoneId must all come from the same zone.",
                "volumeSize must be at least the source instance's storage size.",
            ],
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_relational_backup(
        self,
        spec: CreateBackupDto = Field(..., description="The backup to take"),
    ) -> BackupCreateData:
        """Take a backup of a relational database instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - The instance must exist and be readable — check with
          `get_relational_instance` if unsure.
        - An `INCREMENTAL` backup needs `parentId`: another backup **of the
          same instance** to build on. A `FULL` backup must not have one.
        - `description` must not be empty. The spec marks it optional and the
          API accepts a request without it — then fails the backup
          asynchronously and leaves nothing behind. Verified live with the
          instance idle: no `description` field and `description: ""` both
          fail, a non-empty one succeeds. It defaults to the Portal's own
          wording, so leave it alone unless the user wants something specific.
        - **The instance must not already be running a backup.** Only one
          backup action is allowed at a time, and a second request is accepted
          with HTTP 200 and then fails with `Cannot perform action
          CREATE_BACKUP, current database action is CREATE_BACKUP`. Wait for
          the previous backup to leave its transitional status first.
        - Backups consume backup storage. Check
          `get_relational_free_backup_usage` first; past the free allowance
          this needs a paid backup storage package.

        ## Workflow

        1. `get_relational_free_backup_usage` → confirm there is room.
        2. For an incremental backup, `list_relational_instance_backups` → pick
           `parentId`.
        3. This tool. It returns a backup id straight away, but the backup is
           written asynchronously: poll `get_relational_backup` until the
           status settles on `COMPLETED`. An `INCREMENTAL` backup is only as
           good as its chain of parents — deleting a parent invalidates it.

        If the id stops resolving instead of settling, the backup failed on the
        platform side. `list_relational_instance_histories` is the only place
        that records why — read the `Create backup` entry there and report its
        error rather than retrying.
        """
        require_write(self.allow_write)
        validate_id(spec.dbInstanceId, "dbInstanceId")
        if spec.parentId:
            validate_id(spec.parentId, "parentId")
        raw = await self.client.call(
            "POST",
            f"{BASE}/create",
            service=VDB_RELATIONAL_SERVICE,
            json=spec.model_dump(exclude_none=True),
        )
        payload = unwrap_wrapped(raw)
        return BackupCreateData.from_api(
            payload if isinstance(payload, dict) else {}, next_step=CREATE_NEXT_STEP
        )

    async def restore_relational_backup(
        self,
        backup_id: str = Field(..., description="Backup to restore from"),
        spec: RestoreRelationalBackupDto = Field(
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
        """Restore a backup into a NEW relational instance. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `restore_relational_backup_dryrun` first and have the user
          confirm the flavour, storage and zone. This is a billable order,
          priced like creating an instance — it does **not** reuse the source
          instance's billing.
        - Make sure the user understands this creates a **second** instance.
          Nothing is rolled back and nothing is overwritten; the backup and its
          source instance are untouched. To replace the original, they have to
          delete it themselves afterwards.
        - The backup must be `COMPLETED`. Restoring from one still in
          `NEW`/`BUILDING`/`SAVING` is restoring from incomplete data.
        - `volumeSize` must be at least the source instance's size
          (`storage_size_gb` on the backup).
        - `packageId`, `volumeType` and `locateZoneId` must all belong to the
          **same zone**, exactly as on a create.

        ## Workflow

        1. `get_relational_backup` → confirm `COMPLETED` and read
           `storage_size_gb`, `datastore_type`, `datastore_version`. Use the
           detail tool, not a row from `list_relational_backups`: the listing
           returns those three as null.
        2. `list_relational_zones` → pick the zone for the new instance.
        3. `list_relational_flavors` with that engine, version **and zone** →
           pick `packageId`.
        4. `list_relational_volume_types` for the same zone → pick
           `volumeType`.
        5. `list_relational_subnets` → pick `netIds`. These are `sub-...`
           ids; the backup's own `network_ids` are `net-...` ids and will not
           work here, despite both being called `netIds` upstream.
        6. `restore_relational_backup_dryrun` → confirm with the user.
        7. This tool, then poll `list_relational_instances` by the new name.
        """
        require_write(self.allow_write)
        validate_id(backup_id, "backup_id")
        raw = await self.client.call(
            "POST",
            f"{BASE}/{backup_id}/restore",
            service=VDB_RELATIONAL_SERVICE,
            json=_restore_envelope(backup_id, spec.model_dump(exclude_none=True)),
            user_type=user_type,
        )
        return OrderData(
            orders=[OrderResult.from_api(row) for row in as_list(raw)],
            instance_name=spec.name,
            next_step=RESTORE_NEXT_STEP,
        )

    async def delete_relational_backup(
        self,
        backup_id: str = Field(..., description="Backup to delete"),
    ) -> BackupDeleteData:
        """Delete one relational backup. This cannot be undone.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first. A deleted backup is unrecoverable, and
          it may be the only copy of the data.
        - Check `list_relational_instance_backups` for children first: any
          `INCREMENTAL` backup whose `parent_id` is this one becomes
          unrestorable when its parent goes.

        ## Workflow

        1. `get_relational_backup` → show the user what they are about to lose
           (name, instance, size, date).
        2. `list_relational_instance_backups` → check nothing depends on it.
        3. This tool, then confirm with `list_relational_instance_backups`.

        The API takes the backup id in the URL *and* in a JSON array body; both
        are filled from this one argument, so they cannot disagree. Only the
        one named backup is deleted.
        """
        require_write(self.allow_write)
        validate_id(backup_id, "backup_id")
        raw = await self.client.call(
            "DELETE",
            f"{BASE}/{backup_id}/delete",
            service=VDB_RELATIONAL_SERVICE,
            json=_delete_body(backup_id),
        )
        results = [BackupDeleteResult.from_api(row) for row in as_list(raw)]
        if not results:
            return BackupDeleteData(
                backup_ids=[backup_id],
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
                f"{r.error_message or 'no message'} (code {r.code})" for r in failures
            )
            return BackupDeleteData(
                backup_ids=[backup_id],
                accepted=False,
                results=results,
                warning=f"{FAILED_DELETE_WARNING} Reported: {detail}.",
                next_step=DELETE_NEXT_STEP,
            )
        return BackupDeleteData(
            backup_ids=[backup_id], results=results, next_step=DELETE_NEXT_STEP
        )
