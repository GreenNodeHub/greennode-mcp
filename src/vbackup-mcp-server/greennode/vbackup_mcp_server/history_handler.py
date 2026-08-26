"""Backup and restore history — the audit trail that explains what actually happened.

These reads are never cached: their whole purpose is answering "did last
night's backup run?", and a cached answer to that question is worse than no
answer.

There is one trail per product and they are separate endpoints with different
field names — vServer under ``backup-instances`` / ``restoration``, vDB under
``backup-databases`` / ``restoration/databases``. Asking the vServer tool about
a database returns an empty list, not an error, so pick the tool by product.
"""

from __future__ import annotations

from datetime import datetime, timezone
from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.models import (
    BackupHistoryItem,
    BackupHistoryListData,
    DatabaseBackupHistoryItem,
    DatabaseBackupHistoryListData,
    DatabaseRestoreHistoryItem,
    DatabaseRestoreHistoryListData,
    RestoreHistoryItem,
    RestoreHistoryListData,
    ServerMigrationHistoryItem,
    ServerMigrationHistoryListData,
    newest_first,
)
from greennode.vbackup_mcp_server.paging import as_list, fetch_all_items
from greennode.vbackup_mcp_server.tool_annotations import READ
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field
from typing import Literal


DEFAULT_HISTORY_LIMIT = 50

DEFAULT_HISTORY_WINDOW_DAYS = 180
"""How far back the API looks when ``from_date`` is omitted.

Measured against the live gateway: unfiltered, ``/v1/histories/backup-instances``
reported 1717 records whose oldest was exactly 180 days old, while the same call
with ``from_date=0`` reported 4370 going back another year. The cut is silent —
nothing in the response says a window was applied.
"""

EPOCH_FOR_EVERYTHING = "1970-01-01"

MIGRATION_PATH = "/v1/{project_id}/histories/server-migration"
"""Server-migration trail, on the **vServer** gateway rather than vBackup.

It lives here rather than with the vServer instance lookup because it is an
audit trail and reads like the other four: the same question ("what happened to
this server, and when"), the same shape of answer.
"""

MIGRATION_PAGE_SIZE = 100
"""Records per request when walking the migration trail.

The endpoint **requires** ``page`` and ``size``: omitting either answers 500,
and ``page`` is 1-based (0 is a 400). There is no unpaged fast path, so this
trail is always walked page by page.
"""

MIGRATION_MAX_PAGES = 50
"""Safety stop for the paging loop, so a mis-reported page count cannot spin."""


MigrationAction = Literal["START-MIGRATING", "COMPLETE-MIGRATING", "ROLLBACK"]


def to_epoch_millis(value: str, label: str) -> int:
    """Parse an ISO-8601 date or datetime into the epoch milliseconds the API wants.

    The gateway takes ``from_date`` as a raw millisecond timestamp, which is not
    something to make a caller compute. A bare date is read as midnight UTC.
    """
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"{label} must be an ISO-8601 date or datetime such as '2026-03-01' or "
            f"'2026-03-01T00:00:00Z'; got {value!r}"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


class HistoryHandler:
    """Register and serve history MCP tools."""

    def __init__(
        self,
        mcp,
        config: VbackupConfig,
        client: VbackupClient,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_backup_history", annotations=READ)(self.list_backup_history)
        self.mcp.tool(name="list_restore_history", annotations=READ)(self.list_restore_history)
        self.mcp.tool(name="list_database_backup_history", annotations=READ)(
            self.list_database_backup_history
        )
        self.mcp.tool(name="list_database_restore_history", annotations=READ)(
            self.list_database_restore_history
        )
        self.mcp.tool(name="list_server_migration_history", annotations=READ)(
            self.list_server_migration_history
        )

    async def list_backup_history(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        backup_server_id: str | None = Field(
            None, description="Filter to one backup server (`bk-ins-...`)."
        ),
        server_id: str | None = Field(
            None, description="Filter to one protected vServer instance (`ins-...`)."
        ),
        from_date: str | None = Field(
            None,
            description=(
                "Only runs at or after this moment, as an ISO-8601 date or datetime "
                "('2026-03-01' or '2026-03-01T00:00:00Z'; a bare date means midnight "
                "UTC). OMITTING THIS IS NOT 'everything' — the API then looks back "
                "only 180 days. Pass '1970-01-01' to search the whole history."
            ),
        ),
        limit: int = Field(
            DEFAULT_HISTORY_LIMIT,
            ge=1,
            le=500,
            description=(
                "Maximum runs to return, newest first. The full history of an "
                "account runs to thousands of records — raise this only when the "
                "user asks for a longer window."
            ),
        ),
    ) -> BackupHistoryListData:
        """List backup runs, newest first.

        Returns {region, total, runs[{id, backup_server_id, server_id, status,
        error_message, snapshot_time, finish_time, size_gb, used_gb,
        policy_name_at_run, destination_name_at_run, deletion_status}]}.

        This is the tool that answers "did the backup run last night?" and "why
        did it fail?". Filter by `server_id` when the user names a machine and
        by `backup_server_id` when they name a backup.

        `error_message` is non-empty exactly on the runs that failed — read it
        rather than guessing from `status`. `policy_name_at_run` and
        `destination_name_at_run` are captured at run time and stay accurate
        after the policy or destination is renamed or deleted, so use them to
        explain an old run instead of re-reading the live objects.

        `deletion_status` tells you the restore point a run produced has since
        been removed, which is why a successful run may have no point behind it.

        **The default window is 180 days, silently.** With no `from_date` the
        API returns only the last 180 days and says nothing about the cut, so an
        empty result never proves a backup never ran — re-ask with
        `from_date='1970-01-01'` before telling a user they have no history.

        This is the vServer trail. vDB backups live in
        list_database_backup_history and do not appear here.

        `backendId` and `projectId` are accepted by the endpoint and **ignored**
        — the results are identical with and without them — so they are
        deliberately not exposed. Filter by `server_id` or `backup_server_id`
        instead.

        Always report the count returned alongside the `limit` used, so a user
        knows whether they are seeing the whole window.
        """
        for value, label in (
            (backup_server_id, "backup_server_id"),
            (server_id, "server_id"),
        ):
            if value:
                validate_id(value, label)

        params: dict[str, str | int] = {}
        if backup_server_id:
            params["backupInstanceId"] = backup_server_id
        if server_id:
            params["serverId"] = server_id
        if from_date:
            params["from_date"] = to_epoch_millis(from_date, "from_date")

        raw = await fetch_all_items(
            self.client, "/v1/histories/backup-instances", region=region, params=params or None
        )
        items = newest_first(
            [BackupHistoryItem.from_api(h) for h in raw if isinstance(h, dict)],
            "snapshot_time",
            "finish_time",
            "created_at",
        )[:limit]
        return BackupHistoryListData(
            region=region or self.config.default_region, total=len(items), runs=items
        )

    async def list_restore_history(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        backup_server_id: str | None = Field(
            None, description="Filter to one backup server (`bk-ins-...`)."
        ),
        server_id: str | None = Field(
            None, description="Filter to one vServer instance (`ins-...`)."
        ),
        limit: int = Field(
            DEFAULT_HISTORY_LIMIT, ge=1, le=500, description="Maximum restores to return."
        ),
    ) -> RestoreHistoryListData:
        """List restore operations, newest first.

        Returns {region, total, restores[{id, type, status, backup_server_id,
        backup_server_point_id, destination_server_id, destination_volume_id,
        created_at, finish_at}]}.

        Use it to answer "was this machine restored, and from which point?" —
        `backup_server_point_id` names the restore point the data came from and
        `destination_server_id` where it was written.

        An empty list means no restore has ever been run in this region; it is
        the normal state for an account that has never had an incident.

        **This server cannot start a restore.** The gateway publishes this
        read-only history and no endpoint to trigger a restore — that is done in
        the GreenNode console. Tell the user that plainly instead of looking for
        another tool.

        This is the vServer trail; vDB restores are in
        list_database_restore_history. Unlike list_backup_history this endpoint
        applies no default date window, and it takes no `from_date`.
        """
        for value, label in (
            (backup_server_id, "backup_server_id"),
            (server_id, "server_id"),
        ):
            if value:
                validate_id(value, label)

        params: dict[str, str] = {}
        if backup_server_id:
            params["backupInstanceId"] = backup_server_id
        if server_id:
            params["serverId"] = server_id

        raw = await fetch_all_items(
            self.client, "/v1/histories/restoration", region=region, params=params or None
        )
        items = newest_first(
            [RestoreHistoryItem.from_api(r) for r in raw if isinstance(r, dict)],
            "created_at",
            "finish_at",
        )[:limit]
        return RestoreHistoryListData(
            region=region or self.config.default_region, total=len(items), restores=items
        )

    async def list_database_backup_history(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        database_id: str | None = Field(
            None, description="Filter to one vDB instance (e.g. `pg-...`)."
        ),
        backup_database_id: str | None = Field(
            None, description="Filter to one vDB backup resource (`bk-db-...`)."
        ),
        from_date: str | None = Field(
            None,
            description=(
                "Only runs at or after this moment, as an ISO-8601 date or datetime. "
                "Omitting it applies the API's silent 180-day window; pass "
                "'1970-01-01' for the whole history."
            ),
        ),
        limit: int = Field(
            DEFAULT_HISTORY_LIMIT,
            ge=1,
            le=500,
            description="Maximum runs to return, newest first.",
        ),
    ) -> DatabaseBackupHistoryListData:
        """List vDB backup runs, newest first.

        Returns {region, total, runs[{id, backup_database_id,
        backup_database_name, database_id, status, error_message,
        compressed_gb, uncompressed_gb, policy_name_at_run,
        destination_name_at_run, deletion_status, created_at}]}.

        The vDB counterpart of list_backup_history. A vDB backup never appears
        in the vServer trail and vice versa, so pick the tool by what the user
        is asking about — a database or a server.

        Sizes differ from the vServer trail: `compressed_gb` is what is actually
        stored and billed, `uncompressed_gb` is the source size before
        compression. Quote the compressed number for cost questions; the ratio
        between the two is what makes database backups cheap.

        The same silent 180-day default window applies as on
        list_backup_history — an empty result is not proof that nothing ran.
        """
        for value, label in (
            (database_id, "database_id"),
            (backup_database_id, "backup_database_id"),
        ):
            if value:
                validate_id(value, label)

        params: dict[str, str | int] = {}
        if database_id:
            params["databaseId"] = database_id
        if backup_database_id:
            params["backupDatabaseId"] = backup_database_id
        if from_date:
            params["from_date"] = to_epoch_millis(from_date, "from_date")

        raw = await fetch_all_items(
            self.client, "/v1/histories/backup-databases", region=region, params=params or None
        )
        items = newest_first(
            [DatabaseBackupHistoryItem.from_api(h) for h in raw if isinstance(h, dict)],
            "created_at",
        )[:limit]
        return DatabaseBackupHistoryListData(
            region=region or self.config.default_region, total=len(items), runs=items
        )

    async def list_database_restore_history(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        backup_database_id: str | None = Field(
            None, description="Filter to one vDB backup resource (`bk-db-...`)."
        ),
        limit: int = Field(
            DEFAULT_HISTORY_LIMIT, ge=1, le=500, description="Maximum restores to return."
        ),
    ) -> DatabaseRestoreHistoryListData:
        """List vDB restore operations, newest first.

        Returns {region, total, restores[{id, status, backup_database_id,
        backup_database_name, backup_database_point_id,
        destination_database_id, finish_at, created_at, updated_at}]}.

        The vDB counterpart of list_restore_history. `backup_database_point_id`
        names the backup the data came from and `destination_database_id` the
        vDB instance it was written INTO — which is often a different instance
        from the one that was backed up.

        **This server cannot start a restore.** As with vServer, the gateway
        publishes the history and no endpoint to trigger one; that happens in
        the GreenNode console.

        An empty list is the normal state for an account that has never had to
        restore a database.
        """
        if backup_database_id:
            validate_id(backup_database_id, "backup_database_id")

        params: dict[str, str] = {}
        if backup_database_id:
            params["backupDatabaseId"] = backup_database_id

        raw = await fetch_all_items(
            self.client,
            "/v1/histories/restoration/databases",
            region=region,
            params=params or None,
        )
        items = newest_first(
            [DatabaseRestoreHistoryItem.from_api(r) for r in raw if isinstance(r, dict)],
            "created_at",
            "finish_at",
        )[:limit]
        return DatabaseRestoreHistoryListData(
            region=region or self.config.default_region, total=len(items), restores=items
        )

    async def _fetch_migration_pages(
        self, project_id: str, region: str | None, params: dict
    ) -> tuple[list[dict], int]:
        """Walk the migration trail page by page, returning the rows and the reported total.

        The endpoint has neither the unpaged fast path nor the envelope the rest
        of this package uses, so ``fetch_all_items`` cannot serve it: the rows
        arrive under ``listData`` and the counters are singular (``totalItem`` /
        ``totalPage``) in the vServer spelling.
        """
        path = MIGRATION_PATH.format(project_id=project_id)
        collected: list[dict] = []
        reported = 0
        page = 1
        while page <= MIGRATION_MAX_PAGES:
            data = await self.client.get_vserver(
                path,
                region=region,
                params={**params, "page": page, "size": MIGRATION_PAGE_SIZE},
            )
            envelope = data if isinstance(data, dict) else {}
            rows = [r for r in as_list(envelope, "listData") if isinstance(r, dict)]
            collected.extend(rows)
            if isinstance(envelope.get("totalItem"), int):
                reported = envelope["totalItem"]
            total_pages = envelope.get("totalPage")
            if not rows or not isinstance(total_pages, int) or page >= total_pages:
                break
            page += 1
        return collected, reported or len(collected)

    async def list_server_migration_history(
        self,
        project_id: str = Field(
            ...,
            description=(
                "Project whose migrations to read — REQUIRED, it is a path segment. "
                "Read it off any resource from list_backup_servers; each region has "
                "its own project."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        server_id: str | None = Field(
            None, description="Filter to one instance (`ins-...`), applied by the API."
        ),
        status: str | None = Field(
            None,
            description=(
                "Filter by outcome status, applied by the API. Observed values are "
                "'START-MIGRATING-SUCCESS' and 'COMPLETE-MIGRATING-SUCCESS'; a "
                "failed step reports its own status. Prefer filtering by `action` "
                "when you mean 'what was attempted'."
            ),
        ),
        action: MigrationAction | None = Field(
            None,
            description=(
                "Keep only steps of this kind. Applied by THIS SERVER after "
                "fetching, because the API accepts an `action` parameter and "
                "silently ignores it."
            ),
        ),
        limit: int = Field(
            DEFAULT_HISTORY_LIMIT,
            ge=1,
            le=500,
            description="Maximum steps to return, newest first.",
        ),
    ) -> ServerMigrationHistoryListData:
        """List server migration steps, newest first.

        Returns {region, project_id, total, total_available, migrations[{id,
        server_id, server_name, action, status, created_at, updated_at}]}.

        A migration moves a running instance to different infrastructure by
        replicating it, cutting over, and then either confirming or abandoning
        the move. Each step is one record here, so a single server's migration
        normally appears as two rows: the cutover and whatever ended it.

        **`action` is what happened; `status` only reports the phase reached.**
        A rolled-back migration finishes with `COMPLETE-MIGRATING-SUCCESS` —
        the same status as one that was confirmed. Reading `status` alone will
        tell you a migration succeeded when it was actually abandoned. Always
        report the pair, and read them per server:

        | action | Meaning |
        |---|---|
        | `START-MIGRATING` | The cutover ran; the instance is up at the new site but the move is not final |
        | `COMPLETE-MIGRATING` | The move was confirmed and the source released — irreversible |
        | `ROLLBACK` | The move was abandoned and the instance returned to its origin |

        A server whose newest record is `START-MIGRATING` is **mid-migration**:
        it has been cut over but neither confirmed nor rolled back, so it is
        still waiting on a decision. That is the state worth surfacing.

        Why this lives in a backup server: a migration relocates the machine a
        backup server protects, which is exactly when restore points matter and
        exactly when their history gets confusing. Cross-check with
        list_backup_history around the same timestamps — a run that failed
        during a cutover usually explains itself here.

        `server_name` is the name recorded at the time of the step, so it stays
        readable after a rename and may differ from the instance's name today.

        This trail is read from the vServer gateway and covers ALL instances in
        the project, not only the backed-up ones. An empty result means no
        migration has ever been run for this project in this region — migrations
        are region-scoped, so check both before concluding there were none.
        """
        validate_id(project_id, "project_id")
        if server_id:
            validate_id(server_id, "server_id")

        params: dict[str, str] = {}
        if server_id:
            params["serverId"] = server_id
        if status:
            params["status"] = status

        rows, reported = await self._fetch_migration_pages(project_id, region, params)
        items = newest_first(
            [ServerMigrationHistoryItem.from_api(r) for r in rows],
            "created_at",
            "updated_at",
        )
        if action:
            items = [i for i in items if i.action == action]
            reported = len(items)
        return ServerMigrationHistoryListData(
            region=region or self.config.default_region,
            project_id=project_id,
            total=len(items[:limit]),
            total_available=reported,
            migrations=items[:limit],
        )
