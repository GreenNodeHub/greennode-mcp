"""PostgreSQL Cluster backup tools.

Backups here are **not** the vDB backups of the other two families. They are
vBackup resources with their own vocabulary, and mixing the two up is the
easiest mistake to make in this family:

* A **backup database** (`bk-db-...`) is one cluster's standing backup
  configuration -- which policy runs, where it writes, when it last ran. It is
  not a copy of anything.
* A **restore point** (`bk-db-pt-...`) is the actual copy, and the thing a
  create consumes as `backupPointId`.
* A **location** (`bk-des-...`) is where copies are written, and a **policy**
  (`bk-pol-...`) is the schedule. Both are account-level and shared with the
  rest of vBackup.

None of these tools takes a backup id. Every per-cluster endpoint is keyed by
the **cluster** id, including the one called `/detail` -- which returns the
cluster's backup database, not a backup.

There is no delete, no restore and no "create backup configuration" endpoint in
this family: backups are attached when the cluster is created (`backupPolicyId`
/ `backupLocationId`), and restoring is a `create_postgresql_cluster` carrying
a `backupPointId`.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import VDB_POSTGRESQL_SERVICE, VdbConfig
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    BackupLocationListData,
    BackupLocationOption,
    BackupPolicyListData,
    BackupPolicyOption,
    PostgresqlBackup,
    PostgresqlBackupListData,
    PostgresqlBackupNowData,
    PostgresqlRestorePoint,
    PostgresqlRestorePointListData,
)
from greennode.vdb_mcp_server.paging import as_list, unwrap_wrapped
from greennode.vdb_mcp_server.tool_annotations import READ, WRITE
from pydantic import Field


BACKUP_BASE = "/v1/backup"

# Locations and policies are cached; their TTLs live in `discovery_cache`'s
# TTL_CONFIG with every other cached tool, so that one table stays the single
# place to read what this server caches and for how long. The per-cluster
# backup reads are in UNCACHED_TOOLS there: a stale backup listing would hide
# a backup that just ran.

BACKUP_NOW_NEXT_STEP = (
    "The backup was requested, not taken. This endpoint answers with a bare string and no id "
    "to poll, so confirm with list_postgresql_restore_points: a new point appears there once "
    "vBackup finishes writing it. If none appears, read get_postgresql_cluster_backup for the "
    "cluster's backup status."
)


class PostgresqlBackupHandler:
    """Register and serve the PostgreSQL Cluster backup tools."""

    def __init__(
        self,
        mcp,
        config: VdbConfig,
        client: VdbClient,
        cache: DiscoveryCache,
        allow_write: bool,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_postgresql_backups", annotations=READ)(
            self.list_postgresql_backups
        )
        self.mcp.tool(name="get_postgresql_cluster_backup", annotations=READ)(
            self.get_postgresql_cluster_backup
        )
        self.mcp.tool(name="list_postgresql_restore_points", annotations=READ)(
            self.list_postgresql_restore_points
        )
        self.mcp.tool(name="list_postgresql_backup_locations", annotations=READ)(
            self.list_postgresql_backup_locations
        )
        self.mcp.tool(name="list_postgresql_backup_policies", annotations=READ)(
            self.list_postgresql_backup_policies
        )
        if self.allow_write:
            self.mcp.tool(name="create_postgresql_cluster_backup", annotations=WRITE)(
                self.create_postgresql_cluster_backup
            )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_postgresql_backups(self) -> PostgresqlBackupListData:
        """List every backup-enabled cluster in the project.

        Each row is a cluster's **backup configuration**, not a copy of data:
        the policy it follows, the location it writes to, and when it last ran.
        To see the copies themselves, call `list_postgresql_restore_points`
        with the cluster id.

        Unpaginated and project-wide -- there is no filter of any kind, so
        narrow it by reading `cluster_id` off the rows.

        A row whose `cluster_deleted` is true belongs to a cluster that no
        longer exists. Its restore points survive, which is what makes such a
        row worth keeping, but nothing can be backed up to it again.
        """
        raw = await self.client.call(
            "GET", f"{BACKUP_BASE}/backup-vdb", service=VDB_POSTGRESQL_SERVICE
        )
        items = [PostgresqlBackup.from_api(row) for row in as_list(raw)]
        return PostgresqlBackupListData(count=len(items), items=items)

    async def get_postgresql_cluster_backup(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...'), NOT a backup id"),
    ) -> PostgresqlBackup:
        """Get one cluster's backup configuration.

        The endpoint is `/backup-vdb/{clusterId}/detail`, and despite the name
        it is keyed by the **cluster** id and returns the backup database
        (`bk-db-...`) belonging to it -- not a backup, and not something a
        backup id resolves.

        Use it to answer "is this cluster being backed up, on what schedule,
        and when did it last succeed": `backup_enabled`, `policy_name` and
        `latest_record`. A cluster that was created without a policy has no
        row here at all.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self.client.call(
            "GET",
            f"{BACKUP_BASE}/backup-vdb/{cluster_id}/detail",
            service=VDB_POSTGRESQL_SERVICE,
        )
        return PostgresqlBackup.from_api(unwrap_wrapped(raw) or {})

    async def list_postgresql_restore_points(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
    ) -> PostgresqlRestorePointListData:
        """List the points in time one cluster can be restored from.

        These are the real copies. A point's `id` ('bk-db-pt-...') is what
        `create_postgresql_cluster` takes as `backupPointId` to build a new
        cluster from the data.

        Two things to check before planning a restore:

        - `status` must be settled. A point still being written is not a point
          to restore from.
        - `engine_version` is the version the data was written by. The new
          cluster's `datastoreVersion` has to match it -- restoring across
          versions is not something this API offers.

        Restoring never touches the source: it creates a **second** cluster,
        priced as a new one.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self.client.call(
            "GET",
            f"{BACKUP_BASE}/backup-vdb/{cluster_id}/restore-point",
            service=VDB_POSTGRESQL_SERVICE,
        )
        items = [PostgresqlRestorePoint.from_api(row) for row in as_list(raw)]
        return PostgresqlRestorePointListData(cluster_id=cluster_id, count=len(items), items=items)

    async def list_postgresql_backup_locations(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> BackupLocationListData:
        """List the vBackup destinations a cluster's backups can be written to.

        A row's `id` ('bk-des-...') is a create's `backupLocationId`. These are
        **vBackup** resources shared with the rest of the account, not vDB
        ones: the `region_name` they report is a vBackup region and need not
        match the cluster's zone.

        Unlike the other two families, cluster backups do not consume a vDB
        backup-storage quota -- so `get_relational_backup_storage` and its
        memory twin say nothing about them. What bounds them is the location's
        own quota: `unlimited_quota`, or `max_quota` against `used_bytes`.
        """

        async def fetch():
            return await self.client.call(
                "GET", f"{BACKUP_BASE}/location", service=VDB_POSTGRESQL_SERVICE
            )

        raw = await self.cache.get_or_fetch(
            "list_postgresql_backup_locations", "all", fetch, refresh
        )
        items = [BackupLocationOption.from_api(row) for row in as_list(raw)]
        return BackupLocationListData(count=len(items), items=items)

    async def list_postgresql_backup_policies(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> BackupPolicyListData:
        """List the vBackup schedules a cluster's backups can follow.

        A row's `id` ('bk-pol-...') is a create's `backupPolicyId`.

        Read the schedule off the `*_enabled` flags, not off the config
        objects: a disabled frequency still carries an (empty) config, so the
        presence of `weekly_config` would say nothing. `daily_retention` is how
        many daily copies are kept.

        These policies are account-wide vBackup settings. This API can select
        one for a cluster; it cannot create or change one -- that is done in
        vBackup.
        """

        async def fetch():
            return await self.client.call(
                "GET", f"{BACKUP_BASE}/policy", service=VDB_POSTGRESQL_SERVICE
            )

        raw = await self.cache.get_or_fetch(
            "list_postgresql_backup_policies", "all", fetch, refresh
        )
        items = [BackupPolicyOption.from_api(row) for row in as_list(raw)]
        return BackupPolicyListData(count=len(items), items=items)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_postgresql_cluster_backup(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('pg-...')"),
    ) -> PostgresqlBackupNowData:
        """Take an immediate backup of a cluster, outside its schedule.

        ## Requirements

        - `--allow-write` must be enabled.
        - The cluster must already have a backup configuration: this triggers
          the existing policy and location, it does not create them. Check with
          `get_postgresql_cluster_backup` first -- a cluster created without
          `backupPolicyId` has nothing to trigger.
        - The cluster must be idle. vDB runs one action per resource at a time;
          a second is accepted with HTTP 200 and then fails asynchronously.
        - It consumes space in the backup location, which is billed by
          vBackup.

        ## Workflow

        1. `get_postgresql_cluster_backup` → confirm the cluster is backed up
           at all, and read `policy_name` / `location_name`.
        2. `get_postgresql_cluster` → confirm `status_kind` is `settled`.
        3. This tool.
        4. `list_postgresql_restore_points` to confirm. **The response carries
           no id to poll** -- it is a bare string -- so a new restore point
           appearing is the only evidence the backup landed.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        raw = await self.client.call(
            "POST",
            f"{BACKUP_BASE}/backup-vdb/{cluster_id}/backup-now",
            service=VDB_POSTGRESQL_SERVICE,
        )
        payload = unwrap_wrapped(raw)
        return PostgresqlBackupNowData(
            cluster_id=cluster_id,
            message="" if payload is None else str(payload),
            accepted=True,
            next_step=BACKUP_NOW_NEXT_STEP,
        )
