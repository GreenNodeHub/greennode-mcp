"""Backup databases — the protected vDB instances, vBackup's second product.

The vDB sibling of ``backup_server_handler``. A "backup database" joins a vDB
instance, a policy and a destination exactly as a backup server joins a vServer
instance; the path spells it ``backup-databases`` and its ids start with
``bk-db-``.

Three differences drive everything in this module:

- **No volumes.** A database is captured whole, so there is no per-disk
  inclusion flag to manage and no partial-restore trap to warn about.
- **The engine family is part of the create.** ``databaseType`` is required and
  is spelled ``PostgresCluster`` / ``RedisCluster`` — not an engine name.
- **Discovery leaves the product.** vBackup can list what is already protected
  but not what exists, so offering a choice at create time means reading the
  vDB gateway, which splits its estate across two hosts by engine family.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
from greennode.vbackup_mcp_server.guards import require_write
from greennode.vbackup_mcp_server.models import (
    BackupDatabaseItem,
    BackupDatabaseListData,
    BackupDatabasePointItem,
    BackupDatabasePointListData,
    CreateBackupDatabaseDto,
    DatabaseInstanceItem,
    DatabaseInstanceListData,
    DatabaseType,
    ProtectedDatabaseListData,
    UpdateBackupDatabasePolicyDto,
    WriteResult,
    as_dict,
    as_text,
    is_memory_type,
)
from greennode.vbackup_mcp_server.paging import as_list, fetch_all_items, unwrap
from greennode.vbackup_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field


VDB_INSTANCES_PATH = "/v1/database-instances"

VDB_PAGE_SIZE = 1000
"""What the vDB gateway accepts per page. Its ``maxSize`` reports 100, but the
larger value is honoured and returns the whole estate in one call."""


class DatabaseHandler:
    """Register and serve backup-database MCP tools."""

    def __init__(
        self,
        mcp,
        config: VbackupConfig,
        client: VbackupClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_backup_databases", annotations=READ)(self.list_backup_databases)
        self.mcp.tool(name="get_backup_database", annotations=READ)(self.get_backup_database)
        self.mcp.tool(name="list_backup_database_points", annotations=READ)(
            self.list_backup_database_points
        )
        self.mcp.tool(name="list_protected_databases", annotations=READ)(
            self.list_protected_databases
        )
        self.mcp.tool(name="list_databases", annotations=READ)(self.list_databases)

        if self.allow_write:
            self.mcp.tool(name="create_backup_database", annotations=WRITE)(
                self.create_backup_database
            )
            self.mcp.tool(name="start_database_backup", annotations=WRITE)(
                self.start_database_backup
            )
            self.mcp.tool(name="update_backup_database_policy", annotations=WRITE)(
                self.update_backup_database_policy
            )
            self.mcp.tool(name="enable_backup_database", annotations=WRITE)(
                self.enable_backup_database
            )
            self.mcp.tool(name="disable_backup_database", annotations=WRITE)(
                self.disable_backup_database
            )
            self.mcp.tool(name="delete_backup_database_point", annotations=DESTRUCTIVE)(
                self.delete_backup_database_point
            )
            self.mcp.tool(name="delete_backup_database", annotations=DESTRUCTIVE)(
                self.delete_backup_database
            )

    async def list_backup_databases(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Region to query ('HCM-3' or 'HAN'); defaults to 'HCM-3'. Backup "
                "databases are region-scoped — if the user's database isn't here, "
                "try the other region before concluding it is unprotected."
            ),
        ),
        name: str | None = Field(None, description="Filter by backup database name."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> BackupDatabaseListData:
        """List the protected databases (backup databases) in a region.

        Returns {region, total, databases[{id, name, database_id, engine,
        engine_version, status, backup_enabled, policy{...}, destination{...},
        total_backup_size_gb, free_usage_gb, ...}]}.

        `id` (`bk-db-...`) is the backup database id every other tool here
        takes; `database_id` (`pg-...` / `rd-...`) is the vDB instance it
        protects — the two are different and are not interchangeable.

        Read these together:
        - `backup_enabled=false` — the schedule is paused, existing restore
          points are untouched.
        - `database_deleted=true` — the source vDB instance is gone, yet the
          restore points remain and are still billed. Always surface this.
        - `total_backup_size_gb` against `free_usage_gb` — storage beyond the
          instance's free allowance is what actually costs money.

        `description` reading "Created by vDB." marks a backup the vDB product
        set up itself rather than one an operator created here.
        """
        params = {"name": name} if name else None
        resolved_region = region or self.config.default_region

        async def fetch() -> BackupDatabaseListData:
            raw = await fetch_all_items(
                self.client, "/v1/backup-databases", region=region, params=params
            )
            items = [BackupDatabaseItem.from_api(d) for d in raw if isinstance(d, dict)]
            return BackupDatabaseListData(
                region=resolved_region, total=len(items), databases=items
            )

        key = ("list_backup_databases", resolved_region, name or "")
        return await self.cache.get_or_fetch("list_backup_databases", key, fetch, refresh)

    async def get_backup_database(
        self,
        backup_database_id: str = Field(
            ..., description="Backup database ID (`bk-db-...`) from list_backup_databases."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupDatabaseItem:
        """Get one backup database by id.

        Returns the same shape as one entry of list_backup_databases, read
        fresh — use it to confirm state right after a write, since the list is
        cached.
        """
        validate_id(backup_database_id, "backup_database_id")
        data = await self.client.get(f"/v1/backup-databases/{backup_database_id}", region=region)
        return BackupDatabaseItem.from_api(unwrap(data))

    async def list_backup_database_points(
        self,
        backup_database_id: str = Field(
            ..., description="Backup database ID (`bk-db-...`) from list_backup_databases."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupDatabasePointListData:
        """List the restore points of one backup database.

        Returns {region, backup_database_id, total, points[{id, status, time,
        size_gb, uncompressed_size_gb, backup_type_at_run, restoring, ...}]}.

        `id` (`bk-db-pt-...`) is what delete_backup_database_point takes and
        what a restore consumes.

        Two sizes are reported and they are not interchangeable: `size_gb` is
        what is stored and billed, `uncompressed_size_gb` is how large the data
        was before compression. Quote the first when talking about cost.

        `status` matters before any action — only ACTIVE is a finished point. A
        point still uploading cannot be deleted, and the API answers a delete
        attempt with a 409 rather than a clear message.

        An empty list means no run has ever completed. That is NOT the same as
        having no schedule: check `backup_enabled` and `policy.schedule` on the
        database, and list_database_backup_history for runs that failed.
        """
        validate_id(backup_database_id, "backup_database_id")
        raw = await fetch_all_items(
            self.client,
            f"/v1/backup-databases/{backup_database_id}/backup-database-points",
            region=region,
        )
        items = [BackupDatabasePointItem.from_api(p) for p in raw if isinstance(p, dict)]
        return BackupDatabasePointListData(
            region=region or self.config.default_region,
            backup_database_id=backup_database_id,
            total=len(items),
            points=items,
        )

    async def list_protected_databases(
        self,
        database_type: DatabaseType = Field(
            ...,
            description=(
                "Engine family to check: 'PostgresCluster' or 'RedisCluster'. "
                "Required — the API answers a missing or misspelt type with an "
                "empty list rather than an error."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> ProtectedDatabaseListData:
        """List the vDB instance IDs of one engine family that are already protected.

        Returns {region, database_type, total, database_ids[]} — ids only, no
        other detail; the API answers this one as a bare `{"ids": [...]}`.

        The membership check a create depends on: an instance already in this
        list cannot be protected twice. list_databases resolves it for you and
        reports `already_protected` per instance, so call this directly only
        when the ids alone are what is wanted.

        **An empty list is ambiguous by design.** The endpoint returns
        `{"ids": []}` for a `database_type` it does not recognise, exactly as it
        does when nothing is protected. Only the two spellings above are
        meaningful — this tool constrains the value so the ambiguity cannot be
        reached by a typo.
        """
        resolved_region = region or self.config.default_region

        async def fetch() -> ProtectedDatabaseListData:
            raw = await self.client.get(
                "/v1/protected-resources/databases",
                region=region,
                params={"databaseType": database_type},
            )
            ids = [as_text(i) for i in as_list(raw, "ids") if as_text(i)]
            return ProtectedDatabaseListData(
                region=resolved_region,
                database_type=database_type,
                total=len(ids),
                database_ids=ids,
            )

        key = ("list_protected_databases", resolved_region, database_type)
        return await self.cache.get_or_fetch("list_protected_databases", key, fetch, refresh)

    async def list_databases(
        self,
        database_type: DatabaseType = Field(
            ...,
            description=(
                "Engine family to list: 'PostgresCluster' reads the vDB relational "
                "gateway, 'RedisCluster' the in-memory one. There is no combined "
                "listing — call it once per family."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        eligible_only: bool = Field(
            False,
            description=(
                "Return only the instances create_backup_database would accept. "
                "Leave false when the user asks 'why can't I back up X' — the "
                "excluded entries carry the reason."
            ),
        ),
        refresh: bool = Field(False, description="Bypass the cache and refetch."),
    ) -> DatabaseInstanceListData:
        """List the vDB instances of one engine family and whether each can be backed up.

        Returns {region, database_type, total, eligible_total, project_id,
        databases[{id, name, engine, engine_version, status, deploy_type,
        node_count, already_protected, eligible, ineligible_reason, ...}]},
        eligible instances first.

        The discovery step of a create: vBackup knows what is already protected
        but not what exists, so this reads the **vDB** gateway and joins the
        answer with list_protected_databases. Present `name` and `id` together
        — vDB names are generated and easy to confuse.

        `eligible` is false for a concrete reason, always given in
        `ineligible_reason`:
        - already has a backup database — an instance is protected once only;
        - not ACTIVE — a provisioning or stopped instance cannot be enrolled;
        - a single-node PostgreSQL — only cluster deployments can be backed up.
          Redis has no such restriction.

        Never report an ineligible instance as "not found". Say which of the
        three applies, because the fix differs for each.

        The vDB gateway is not region-scoped and resolves the project from the
        token, so `region` selects which vBackup region the protection check
        runs against, not which instances come back. An instance listed here
        may already be protected in the other region — check both before
        telling a user an instance is unprotected.
        """
        resolved_region = region or self.config.default_region

        async def fetch() -> DatabaseInstanceListData:
            protected = await self.list_protected_databases(
                database_type=database_type, region=region, refresh=False
            )
            raw = await self.client.get_vdb(
                VDB_INSTANCES_PATH,
                memory_engine=is_memory_type(database_type),
                region=region,
                params={"pageNumber": 1, "pageSize": VDB_PAGE_SIZE},
            )
            envelope = as_dict(as_dict(raw).get("data"))
            rows = envelope.get("data")
            items = [
                DatabaseInstanceItem.from_api(d, frozenset(protected.database_ids))
                for d in (rows if isinstance(rows, list) else [])
                if isinstance(d, dict)
            ]
            items.sort(key=lambda i: (not i.eligible, i.name))
            return DatabaseInstanceListData(
                region=resolved_region,
                database_type=database_type,
                total=len(items),
                eligible_total=sum(1 for i in items if i.eligible),
                databases=[i for i in items if i.eligible or not eligible_only],
                project_id=as_text(envelope.get("projectId")),
            )

        key = ("list_databases", resolved_region, database_type, eligible_only)
        return await self.cache.get_or_fetch("list_databases", key, fetch, refresh)

    async def create_backup_database(
        self,
        body: CreateBackupDatabaseDto = Field(..., description="The database to protect and how."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Protect one vDB instance by creating a backup database.

        Returns {region, resource_id, action, succeeded, detail}. The API
        answers with no body, so verify with list_backup_databases afterwards.

        ## Requirements
        - `--allow-write` must be enabled.
        - **One database per call.** Unlike create_backup_server, the body takes
          a single `databaseId`, not a list.
        - `databaseId` from list_databases and it must be `eligible` there:
          ACTIVE, not already protected, and a cluster deployment for
          PostgreSQL. Single-node PostgreSQL cannot be backed up at all.
        - `databaseType` must match the instance and is spelled
          `PostgresCluster` or `RedisCluster` — not an engine name.
        - `backupPolicyId` from list_backup_policies and `backupDestinationId`
          from list_backup_destinations. Never invent one.
        - The destination must be a **vDB** destination. A destination created
          for vServer cannot store a database, and the failure surfaces at
          create time, not before.
        - **The destination must be empty.** A vDB destination holds at most one
          backup database; reusing an occupied one answers `Bad request: The
          backup destination already contains resources.` Check with
          list_backup_destination_databases, or create a destination for this
          database.

        ## Workflow
        1. list_databases for the engine family — present name, id, engine
           version and topology, and let the user choose. Do NOT pick silently.
           If nothing is eligible, give the `ineligible_reason` rather than
           reporting an empty estate.
        2. list_backup_destinations and list_backup_policies — show the
           policy's `schedule.summary` and the destination's product, and let
           the user choose both. Offer only vDB destinations that are still
           empty; if none is, say a new destination is needed rather than
           letting the create fail.
        3. Summarise database, engine, policy schedule, destination and the
           note, then confirm.
        4. Create, then verify with list_backup_databases and report the new
           `bk-db-` id.
        """
        require_write(self.allow_write)
        validate_id(body.databaseId, "databaseId")
        validate_id(body.backupPolicyId, "backupPolicyId")
        validate_id(body.backupDestinationId, "backupDestinationId")
        await self.client.post(
            "/v1/backup-databases", region=region, json=body.model_dump(exclude_none=True)
        )
        self.cache.invalidate("list_backup_databases")
        self.cache.invalidate("list_protected_databases")
        self.cache.invalidate("list_databases")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=body.databaseId,
            action="created",
            detail=(
                "The API returns no body on create. Confirm with "
                "list_backup_databases and report the new `bk-db-` id. The first "
                "run happens at the policy's next scheduled time, not now — use "
                "start_database_backup if the user wants one immediately."
            ),
        )

    async def start_database_backup(
        self,
        backup_database_id: str = Field(
            ..., description="Backup database ID (`bk-db-...`) from list_backup_databases."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Run a backup of one database now, outside its schedule.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - The run is asynchronous: this returns as soon as it is accepted, not
          when the point exists. Never report the backup as finished here.
        - A manual run stores a FULL copy and counts against the destination's
          quota and the instance's free allowance like any other point.

        ## Workflow
        1. get_backup_database — confirm which database and that it is ACTIVE.
        2. Trigger, then poll list_backup_database_points: the new point appears
           first in a non-ACTIVE state and becomes ACTIVE when the upload ends.
        3. Report the point's `id` and `size_gb` only once it is ACTIVE.
        """
        require_write(self.allow_write)
        validate_id(backup_database_id, "backup_database_id")
        await self.client.post(
            f"/v1/backup-databases/{backup_database_id}/backup-now", region=region
        )
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_database_id,
            action="backup started",
            detail=(
                "The run was accepted, not completed. Poll list_backup_database_points "
                "until the newest point reaches ACTIVE before reporting success; it is "
                "a FULL copy and is billed as stored data."
            ),
        )

    async def update_backup_database_policy(
        self,
        backup_database_id: str = Field(
            ..., description="Backup database ID (`bk-db-...`) from list_backup_databases."
        ),
        body: UpdateBackupDatabasePolicyDto = Field(
            ..., description="The policy to attach, by id."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Attach a different backup policy to a backup database.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - The policy id comes from list_backup_policies and must live in the
          same region as the backup database.
        - Changing the policy changes the cadence AND the retention. A shorter
          retention means existing restore points beyond the new limit are
          pruned at the next run — say so before doing it.

        ## Workflow
        1. get_backup_database — the current policy and its schedule.
        2. list_backup_policies — present the candidates with their
           `schedule.summary`, and let the user choose.
        3. Compare retentions against list_backup_database_points. If the new
           policy keeps fewer points, state how many will be lost and confirm.
        4. Update, then get_backup_database to verify.
        """
        require_write(self.allow_write)
        validate_id(backup_database_id, "backup_database_id")
        validate_id(body.id, "policy id")
        await self.client.put(
            f"/v1/backup-databases/{backup_database_id}/policies",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_databases")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_database_id,
            action="policy attached",
            detail=(
                f"Policy {body.id} is now the schedule for this database. A shorter "
                "retention prunes older restore points at the next run."
            ),
        )

    async def enable_backup_database(
        self,
        backup_database_id: str = Field(
            ..., description="Backup database ID (`bk-db-...`) from list_backup_databases."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Resume the backup schedule of a backup database.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - The database must have a policy attached, otherwise there is no
          schedule to resume — check `policy.schedule` with get_backup_database.

        ## Workflow
        1. get_backup_database — confirm it is currently paused and has a policy.
        2. Enable, then verify `backup_enabled` is true.
        3. Report `next_schedule` — enabling does not trigger a run immediately.
           Use start_database_backup if the user wants one now.
        """
        require_write(self.allow_write)
        validate_id(backup_database_id, "backup_database_id")
        await self.client.put(f"/v1/backup-databases/{backup_database_id}/enabled", region=region)
        self.cache.invalidate("list_backup_databases")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_database_id,
            action="enabled",
            detail=(
                "The schedule is active again. The next run happens at the policy's "
                "next scheduled time, not immediately."
            ),
        )

    async def disable_backup_database(
        self,
        backup_database_id: str = Field(
            ..., description="Backup database ID (`bk-db-...`) from list_backup_databases."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Pause the backup schedule of a backup database.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - Pausing stops NEW runs. Existing restore points stay and keep being
          billed — this is not a way to reduce storage cost.

        ## Workflow
        1. get_backup_database — confirm which vDB instance this protects.
        2. Say plainly that the database stops being backed up from now on, and
           that existing points are kept and still charged. Confirm.
        3. Disable, then verify `backup_enabled` is false.
        """
        require_write(self.allow_write)
        validate_id(backup_database_id, "backup_database_id")
        await self.client.put(f"/v1/backup-databases/{backup_database_id}/disabled", region=region)
        self.cache.invalidate("list_backup_databases")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_database_id,
            action="disabled",
            detail=(
                "No new runs will happen. Existing restore points are kept and still "
                "billed; deleting the backup database is what removes them."
            ),
        )

    async def delete_backup_database_point(
        self,
        point_id: str = Field(
            ...,
            description=(
                "Restore point ID (`bk-db-pt-...`) from list_backup_database_points. "
                "Not the backup database id."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Delete one restore point of a backup database.

        Returns {region, resource_id, action, succeeded, detail}.

        Note the route is the point's own collection — the backup database id is
        not part of it, so the point id alone must be correct.

        ## Requirements
        - `--allow-write` must be enabled.
        - IRREVERSIBLE: that point can no longer be restored from. The backup
          database and its other points are untouched.
        - **Two different 409s, and only one is worth retrying:**
          `Your resource is being processed.` means the point is still
          uploading — wait and retry. `Your resource is being managed by Vault.`
          means the destination has a **vault lock** whose retention still
          covers this point, and retrying never succeeds. Read the
          destination's `vault_lock.min_retention_days` and tell the user the
          date the point becomes deletable, or that the lock must be lifted
          first.

        ## Workflow
        1. list_backup_database_points — state the point's `time`, `size_gb`
           and `status`, and confirm it is the one meant.
        2. If it is the only point, say plainly that the database will have no
           recoverable backup left until the next run.
        3. Delete, then re-list to verify it is gone.
        """
        require_write(self.allow_write)
        validate_id(point_id, "point_id")
        await self.client.delete(f"/v1/backup-database-points/{point_id}", region=region)
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=point_id,
            action="deleted",
            detail=(
                "The restore point is gone and the data it held cannot be recovered. "
                "The backup database and its remaining points are unaffected."
            ),
        )

    async def delete_backup_database(
        self,
        backup_database_id: str = Field(
            ..., description="Backup database ID (`bk-db-...`) from list_backup_databases."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Delete a backup database AND every restore point it holds.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - This is IRREVERSIBLE and it destroys data: the restore points go with
          the backup database. The vDB instance itself is not touched, but the
          ability to recover it from these backups is gone.
        - To stop backups without losing history, use disable_backup_database
          instead. Confirm which one the user means before proceeding.
        - A vault lock on the destination blocks this too, with the same
          `Your resource is being managed by Vault.` 409 — the backup database
          cannot go while its points are retention-locked.

        ## Workflow
        1. get_backup_database and list_backup_database_points — state the
           database name, how many restore points exist and the oldest/newest
           dates.
        2. Ask for an explicit confirmation naming that data loss. Do not accept
           a generic "yes, delete" gathered before those numbers were shown.
        3. Delete, then verify with list_backup_databases.
        """
        require_write(self.allow_write)
        validate_id(backup_database_id, "backup_database_id")
        await self.client.delete(f"/v1/backup-databases/{backup_database_id}", region=region)
        self.cache.invalidate("list_backup_databases")
        self.cache.invalidate("list_protected_databases")
        self.cache.invalidate("list_databases")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=backup_database_id,
            action="deleted",
            detail=(
                "The backup database and its restore points are gone. The vDB instance "
                "is unaffected but can no longer be recovered from these backups."
            ),
        )
