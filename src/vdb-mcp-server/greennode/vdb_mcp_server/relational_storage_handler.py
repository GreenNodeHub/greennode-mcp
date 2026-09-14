"""Relational backup-storage tools (paid backup quota, not the backups themselves).

Backup storage is the quota backups are written into. A project starts with a
free allowance (`get_relational_free_backup_usage`) and buys quota beyond it.

Three things to know before reading this file:

* **The two operationIds in the spec are swapped relative to what the paths
  return.** `GET /backup-storages` is named `getListQuotaPackage` and returns
  the price list; `GET /backup-storages/information` is named
  `getListBackupStorage` and returns what the project actually holds. The
  paths are right, the names are not.
* **Resize and delete disagree about the envelope key.** Resize sends
  `resourceType`, delete sends `resType`, and both carry the same value
  `dbaas-backup-storage`. That is a third combination on top of the two the
  instance handler already carries, and getting it wrong is the §4.30 failure:
  HTTP 200, nothing applied, no error.
* **The envelope calls backup storage `databaseInstances`.** Both action
  bodies wrap a list of `{instancesId: "db-bk-storage-..."}`, which are backup
  storage ids, not database instance ids.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import VDB_RELATIONAL_SERVICE, VdbConfig
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    ActionResult,
    BackupStorage,
    BackupStorageActionData,
    BackupStorageListData,
    BackupStoragePackage,
    BackupStoragePackageGroup,
    BackupStoragePackageListData,
    CreateBackupStorageDto,
    DryRunData,
    OrderData,
    OrderResult,
    ResizeBackupStorageDto,
)
from greennode.vdb_mcp_server.paging import as_list
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


BASE = "/v1/backup-storages"
CREATE_PATH = "/v1/payment/backup-storages"

STORAGE_RESOURCE_TYPE = "dbaas-backup-storage"
"""The resource type both action bodies carry -- under two different keys.

Neither `dbaas` (lifecycle actions, instance resize) nor `dbaas-backup`
(restore) works here. Three resource-type values now exist across this API and
none is derivable from the path.
"""

ACTION_RESIZE = "resize"
ACTION_DELETE = "delete"

RESIZE_NEXT_STEP = (
    "An order has been raised to change the quota. It settles asynchronously -- confirm the "
    "new quota with get_relational_backup_storage rather than assuming it applied."
)

CREATE_NEXT_STEP = (
    "An order has been raised for a recurring backup-storage quota. The quota appears in "
    "get_relational_backup_storage once the order completes."
)

DELETE_NEXT_STEP = (
    "Deletion is asynchronous. Confirm with get_relational_backup_storage, and re-check "
    "get_relational_free_backup_usage: backups now have only the free allowance to live in."
)

EMPTY_ACTION_WARNING = (
    "The API answered 200 but returned no result, and an action it has actually queued "
    "always reports one. Treat this as NOT applied and tell the user the call had no effect "
    "rather than reporting success. The known cause is an `action` or resource-type value "
    "the endpoint does not recognise -- note that resize spells the key `resourceType` while "
    "delete spells it `resType`."
)

FAILED_ACTION_WARNING = (
    "The API answered HTTP 200 but the result says the action did NOT succeed. The HTTP "
    "status describes the call, not the outcome -- report the per-result error."
)


def _resize_envelope(storage_id: str, config: dict) -> dict:
    """Build the resize body: `resourceType`, and the id under `databaseInstances`."""
    return {
        "databaseInstances": [{"instancesId": storage_id, "config": config}],
        "action": ACTION_RESIZE,
        "resourceType": STORAGE_RESOURCE_TYPE,
    }


def _delete_envelope(storage_ids: list[str]) -> dict:
    """Build the delete body: `resType`, not `resourceType`.

    The one-character difference from :func:`_resize_envelope` is the whole
    point of keeping these as two functions rather than one with a flag.
    """
    return {
        "databaseInstances": [{"instancesId": sid} for sid in storage_ids],
        "action": ACTION_DELETE,
        "resType": STORAGE_RESOURCE_TYPE,
    }


class RelationalStorageHandler:
    """Register and serve the relational backup-storage tools."""

    def __init__(
        self,
        mcp,
        config: VdbConfig,
        client: VdbClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_relational_backup_storage_packages", annotations=READ)(
            self.list_relational_backup_storage_packages
        )
        self.mcp.tool(name="get_relational_backup_storage", annotations=READ)(
            self.get_relational_backup_storage
        )

        # Previews are read-only whatever they preview.
        self.mcp.tool(name="create_relational_backup_storage_dryrun", annotations=READ)(
            self.create_relational_backup_storage_dryrun
        )
        self.mcp.tool(name="resize_relational_backup_storage_dryrun", annotations=READ)(
            self.resize_relational_backup_storage_dryrun
        )

        if self.allow_write:
            self.mcp.tool(name="create_relational_backup_storage", annotations=WRITE)(
                self.create_relational_backup_storage
            )
            self.mcp.tool(name="resize_relational_backup_storage", annotations=WRITE)(
                self.resize_relational_backup_storage
            )
            self.mcp.tool(name="delete_relational_backup_storage", annotations=DESTRUCTIVE)(
                self.delete_relational_backup_storage
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> Any:
        return await self.client.call("GET", path, service=VDB_RELATIONAL_SERVICE)

    def _action_data(self, storage_ids: list[str], results: list[ActionResult], next_step: str):
        """Wrap action results, refusing to present a 200 as success on its own."""
        if not results:
            return BackupStorageActionData(
                storage_ids=list(storage_ids),
                accepted=False,
                results=[],
                warning=EMPTY_ACTION_WARNING,
                next_step=next_step,
            )
        failures = [r for r in results if r.success is False]
        if failures:
            detail = "; ".join(
                f"{r.error_message or 'no message'} (code {r.code})" for r in failures
            )
            return BackupStorageActionData(
                storage_ids=list(storage_ids),
                accepted=False,
                results=results,
                warning=f"{FAILED_ACTION_WARNING} Reported: {detail}.",
                next_step=next_step,
            )
        return BackupStorageActionData(
            storage_ids=list(storage_ids), results=results, next_step=next_step
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_relational_backup_storage_packages(
        self,
        refresh: bool = Field(
            False, description="Bypass the cache and re-read the price list from the API"
        ),
    ) -> BackupStoragePackageListData:
        """List the backup-storage quota packages available to buy.

        This is a **price list**, not what the project owns — use
        `get_relational_backup_storage` for that. The spec's operationId for
        this path (`getListQuotaPackage`) and for the other one
        (`getListBackupStorage`) read as though they were swapped; trust the
        paths.

        The API repeats the catalogue once per engine group. Measured live the
        repetitions are **identical** — groups 1 and 2 offer the same eight
        package ids, group 3 offers none — so a `package_id` is unambiguous and
        there is nothing to match against the group when choosing one. Pick
        from `packages`; `groups` preserves the raw answer, and
        `groups_are_identical` flags the day that stops being true.
        """

        async def fetch():
            return as_list(await self._get(BASE))

        rows = await self.cache.get_or_fetch(
            "list_relational_backup_storage_packages", "all", fetch, refresh=refresh
        )
        groups = [
            BackupStoragePackageGroup(
                engine_group=row.get("engineGroup"),
                count=len(row.get("packages") or []),
                packages=[BackupStoragePackage.from_api(p) for p in (row.get("packages") or [])],
            )
            for row in rows
        ]
        # Deduplicate by id: the catalogue is repeated per engine group and the
        # repetitions are identical, so a flat list is what a caller picks from.
        # Empty groups are ignored when judging equality -- group 3 offers
        # nothing, which is not a disagreement with the groups that do.
        seen: dict[str, BackupStoragePackage] = {}
        for group in groups:
            for package in group.packages:
                seen.setdefault(package.package_id, package)
        id_sets = {frozenset(p.package_id for p in g.packages) for g in groups if g.packages}
        return BackupStoragePackageListData(
            count=len(seen),
            packages=list(seen.values()),
            groups=groups,
            groups_are_identical=len(id_sets) <= 1,
        )

    async def get_relational_backup_storage(self) -> BackupStorageListData:
        """Show the paid backup storage this project holds, and how full it is.

        An empty result is the normal state for a project that has never bought
        any: backups then run against the free allowance from
        `get_relational_free_backup_usage`. The result sets `none_purchased` so
        that is not mistaken for a failed lookup.

        Despite the name, the endpoint answers with a list — a project can hold
        one quota per engine group.
        """
        rows = as_list(await self._get(f"{BASE}/information"))
        return BackupStorageListData(
            count=len(rows),
            none_purchased=not rows,
            items=[BackupStorage.from_api(row) for row in rows],
        )

    # ------------------------------------------------------------------
    # dry runs
    # ------------------------------------------------------------------

    async def create_relational_backup_storage_dryrun(
        self,
        spec: CreateBackupStorageDto = Field(..., description="The package to buy"),
    ) -> DryRunData:
        """Show the exact order that create_relational_backup_storage would place.

        Read-only — nothing is bought.
        """
        return DryRunData(
            tool="create_relational_backup_storage",
            method="POST",
            path=CREATE_PATH,
            service=VDB_RELATIONAL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order for a RECURRING quota, not a one-off purchase.",
                "Check get_relational_free_backup_usage first -- the free allowance may "
                "already be enough.",
            ],
        )

    async def resize_relational_backup_storage_dryrun(
        self,
        storage_id: str = Field(..., description="Backup storage to resize"),
        spec: ResizeBackupStorageDto = Field(..., description="Target package"),
    ) -> DryRunData:
        """Show the exact order that resize_relational_backup_storage would place.

        Read-only — nothing is ordered and no quota changes.
        """
        validate_id(storage_id, "storage_id")
        return DryRunData(
            tool="resize_relational_backup_storage",
            method="POST",
            path=f"{BASE}/actions/resize",
            service=VDB_RELATIONAL_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=_resize_envelope(storage_id, spec.model_dump(exclude_none=True)),
            warnings=[
                "Raises a BILLABLE order and changes the recurring monthly cost.",
                "Shrinking below current usage is refused by the platform; check usage_gb "
                "with get_relational_backup_storage first.",
            ],
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_relational_backup_storage(
        self,
        spec: CreateBackupStorageDto = Field(..., description="The package to buy"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Buy backup-storage quota. Costs money, every month.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_relational_backup_storage_dryrun` first and have the user
          confirm the package. This is a **recurring** charge, not a one-off.
        - Check `get_relational_free_backup_usage` first. A project that is
          still inside its free allowance does not need to buy anything.
        - `backupPackageId` must come from
          `list_relational_backup_storage_packages`. The catalogue is repeated
          per engine group but the repetitions are identical, so the id is
          unambiguous — take it from `packages`.

        ## Workflow

        1. `get_relational_free_backup_usage` → is paid storage needed at all?
        2. `get_relational_backup_storage` → is some already held?
        3. `list_relational_backup_storage_packages` → pick the package.
        4. `create_relational_backup_storage_dryrun` → confirm with the user.
        5. This tool, then poll `get_relational_backup_storage`.
        """
        require_write(self.allow_write)
        raw = await self.client.call(
            "POST",
            CREATE_PATH,
            service=VDB_RELATIONAL_SERVICE,
            json=spec.model_dump(exclude_none=True),
            user_type=user_type,
        )
        return OrderData(
            orders=[OrderResult.from_api(row) for row in as_list(raw)],
            next_step=CREATE_NEXT_STEP,
        )

    async def resize_relational_backup_storage(
        self,
        storage_id: str = Field(..., description="Backup storage to resize"),
        spec: ResizeBackupStorageDto = Field(..., description="Target package"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE, description="Billing flow; IAM_USER (Auto Payment) by default"
        ),
    ) -> OrderData:
        """Change a backup-storage quota to a different package. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `resize_relational_backup_storage_dryrun` first and have the user
          confirm. This changes a **recurring** charge.
        - Read `get_relational_backup_storage` for the current `usage_gb`:
          a package smaller than what is already stored is refused.
        - Package ids are shared across engine groups, so an id from
          `list_relational_backup_storage_packages` applies as-is.

        ## Workflow

        1. `get_relational_backup_storage` → current quota, usage and engine group.
        2. `list_relational_backup_storage_packages` → pick the target package.
        3. `resize_relational_backup_storage_dryrun` → confirm with the user.
        4. This tool, then confirm the new quota with
           `get_relational_backup_storage`.

        The body spells the resource-type key `resourceType`; the delete
        endpoint spells the same thing `resType`. Both are handled here.
        """
        require_write(self.allow_write)
        validate_id(storage_id, "storage_id")
        raw = await self.client.call(
            "POST",
            f"{BASE}/actions/resize",
            service=VDB_RELATIONAL_SERVICE,
            json=_resize_envelope(storage_id, spec.model_dump(exclude_none=True)),
            user_type=user_type,
        )
        return OrderData(
            orders=[OrderResult.from_api(row) for row in as_list(raw)],
            next_step=RESIZE_NEXT_STEP,
        )

    async def delete_relational_backup_storage(
        self,
        storage_ids: list[str] = Field(
            ..., min_length=1, description="Backup storage quotas to release"
        ),
    ) -> BackupStorageActionData:
        """Release backup-storage quota. Cannot be undone.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first. Releasing the quota removes the room the
          project's backups live in; check `get_relational_backup_storage` for
          `usage_gb` and be explicit about what is stored there.
        - After this, backups have only the free allowance
          (`get_relational_free_backup_usage`) to work with, so the next backup
          can fail for lack of room.

        ## Workflow

        1. `get_relational_backup_storage` → id, quota and current usage.
        2. `list_relational_backups` → what is actually stored.
        3. This tool, then confirm with `get_relational_backup_storage`.

        Unlike resize, this body spells the resource-type key `resType`. It is
        also not an order flow: no billing header, and the response is the
        per-item action shape rather than an order.
        """
        require_write(self.allow_write)
        for storage_id in storage_ids:
            validate_id(storage_id, "storage_ids")
        raw = await self.client.call(
            "POST",
            f"{BASE}/actions/deletions",
            service=VDB_RELATIONAL_SERVICE,
            json=_delete_envelope(list(storage_ids)),
        )
        results = [ActionResult.from_api(row) for row in as_list(raw)]
        return self._action_data(list(storage_ids), results, DELETE_NEXT_STEP)
