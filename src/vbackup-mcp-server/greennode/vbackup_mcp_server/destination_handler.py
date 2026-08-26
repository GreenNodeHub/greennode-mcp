"""Backup destinations — the console's **Backup Location** feature group.

A destination is where backups physically land: a vault or a vStorage
container in a chosen backup region, with its own capacity ceiling, recycle bin
and retention lock. Everything the console's Backup Location screens do lives
here — the list, the detail page with its four tabs, the four independent edits
and the delete — plus the two lookups a create needs to fill `product` and
`regionId`.

The lifecycle is deliberately split into one tool per editable property, the
way the API splits it. There is no "update the destination" call: name, quota,
soft delete and lock are four separate endpoints, and a tool that pretended
otherwise would have to guess which of them the user meant to leave alone.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
from greennode.vbackup_mcp_server.guards import require_write
from greennode.vbackup_mcp_server.models import (
    BackupDatabaseItem,
    BackupDatabaseListData,
    BackupDestinationItem,
    BackupDestinationListData,
    BackupRegionItem,
    BackupRegionListData,
    BackupServerItem,
    BackupServerListData,
    CreateBackupDestinationDto,
    DestinationHistoryItem,
    DestinationHistoryListData,
    DestinationTagItem,
    DestinationTagListData,
    ProductItem,
    ProductListData,
    SoftDeleteDto,
    UpdateBackupDestinationNameDto,
    UpdateMaxQuotaDto,
    VaultLockDto,
    WriteResult,
)
from greennode.vbackup_mcp_server.paging import fetch_all_items, unwrap
from greennode.vbackup_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field
from typing import Literal


Product = Literal["vServer", "vDB"]

DEFAULT_HISTORY_LIMIT = 50


class DestinationHandler:
    """Register and serve backup-destination MCP tools."""

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

        self.mcp.tool(name="list_backup_destinations", annotations=READ)(
            self.list_backup_destinations
        )
        self.mcp.tool(name="get_backup_destination", annotations=READ)(self.get_backup_destination)
        self.mcp.tool(name="list_backup_destination_servers", annotations=READ)(
            self.list_backup_destination_servers
        )
        self.mcp.tool(name="list_backup_destination_databases", annotations=READ)(
            self.list_backup_destination_databases
        )
        self.mcp.tool(name="list_backup_destination_tags", annotations=READ)(
            self.list_backup_destination_tags
        )
        self.mcp.tool(name="list_backup_destination_history", annotations=READ)(
            self.list_backup_destination_history
        )
        self.mcp.tool(name="list_backup_products", annotations=READ)(self.list_backup_products)
        self.mcp.tool(name="list_backup_regions", annotations=READ)(self.list_backup_regions)

        if self.allow_write:
            self.mcp.tool(name="create_backup_destination", annotations=WRITE)(
                self.create_backup_destination
            )
            self.mcp.tool(name="update_backup_destination_name", annotations=WRITE)(
                self.update_backup_destination_name
            )
            self.mcp.tool(name="update_backup_destination_max_quota", annotations=WRITE)(
                self.update_backup_destination_max_quota
            )
            self.mcp.tool(name="update_backup_destination_soft_delete", annotations=WRITE)(
                self.update_backup_destination_soft_delete
            )
            self.mcp.tool(name="update_backup_destination_vault_lock", annotations=WRITE)(
                self.update_backup_destination_vault_lock
            )
            self.mcp.tool(name="delete_backup_destination", annotations=DESTRUCTIVE)(
                self.delete_backup_destination
            )

    async def list_backup_destinations(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        name: str | None = Field(None, description="Filter by destination name."),
        type: str | None = Field(
            None, description="Filter by storage backend: 'VAULT' or 'VSTORAGE'."
        ),
        backend_id: str | None = Field(
            None, description="Filter by backend ID from list_backends."
        ),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> BackupDestinationListData:
        """List the backup destinations (Backup Locations) backups can be written to.

        Returns {region, total, destinations[{id, name, status, type, product,
        is_default, backup_server_count, quota_unlimited, max_quota_gb,
        vault{used_gb, total_gb, region_name, ...}, soft_delete, vault_lock, ...}]}.

        `id` is the `backupDestinationId` create_backup_server requires. Match
        `product` to what is being protected — a vServer backup cannot be
        written to a vDB destination — and prefer the `is_default` one of that
        product unless the user names another.

        Check three things before promising anything about capacity or
        deletion: `vault.used_gb` against `max_quota_gb` (a full destination
        fails runs rather than rejecting the create), `vault_lock` (a locked
        destination refuses deletions until its retention passes), and
        `soft_delete` (deleted backups stay billed for `retain_days`).

        For the detail behind one destination — what is stored in it, its tags,
        what has been changed on it — call get_backup_destination and the
        list_backup_destination_* tools rather than re-reading this list.
        """
        if backend_id:
            validate_id(backend_id, "backend_id")

        params: dict[str, str] = {}
        if name:
            params["name"] = name
        if type:
            params["type"] = type
        if backend_id:
            params["backendId"] = backend_id

        resolved_region = region or self.config.default_region

        async def fetch() -> BackupDestinationListData:
            raw = await fetch_all_items(
                self.client, "/v1/backup-destinations", region=region, params=params or None
            )
            items = [BackupDestinationItem.from_api(d) for d in raw if isinstance(d, dict)]
            return BackupDestinationListData(
                region=resolved_region, total=len(items), destinations=items
            )

        key = ("list_backup_destinations", resolved_region, tuple(sorted(params.items())))
        return await self.cache.get_or_fetch("list_backup_destinations", key, fetch, refresh)

    async def get_backup_destination(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupDestinationItem:
        """Get one backup destination by id.

        Returns the same shape as one entry of list_backup_destinations, read
        live rather than from the cache.

        This is the tool to call before and after any destination edit: the
        four update tools each answer without a body, so this is how the new
        quota, name, soft-delete or lock is confirmed.

        `soft_delete` and `vault_lock` are null when the feature is off. Read
        `vault_lock.change_duration_days` before offering to change a lock — once
        that window has passed since the lock was enabled, the settings are
        permanent.
        """
        validate_id(destination_id, "destination_id")
        data = await self.client.get(f"/v1/backup-destinations/{destination_id}", region=region)
        return BackupDestinationItem.from_api(unwrap(data))

    async def list_backup_destination_servers(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        name: str | None = Field(None, description="Filter by backup server name."),
    ) -> BackupServerListData:
        """List the vServer backup servers stored in one destination.

        Returns {region, total, backup_servers[{id, name, server_id, status,
        backup_enabled, server_deleted, backup_policy_id, latest_record,
        next_schedule, ...}]} — the same shape as list_backup_servers.

        This is the "Backup Resources" tab of a destination, and it is the tool
        that answers **what would be lost if this destination were deleted**.
        Run it before delete_backup_destination: a non-empty result is exactly
        why the API refuses the delete with `backup_location_is_being_used`.

        It covers vServer only. A destination whose `product` is vDB stores its
        resources under list_backup_destination_databases instead, and answers
        this call with an empty list.
        """
        validate_id(destination_id, "destination_id")
        params = {"name": name} if name else None
        raw = await fetch_all_items(
            self.client,
            f"/v1/backup-destinations/{destination_id}/backup-instances",
            region=region,
            params=params,
        )
        items = [BackupServerItem.from_api(s) for s in raw if isinstance(s, dict)]
        return BackupServerListData(
            region=region or self.config.default_region, total=len(items), backup_servers=items
        )

    async def list_backup_destination_databases(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        name: str | None = Field(None, description="Filter by backup resource name."),
    ) -> BackupDatabaseListData:
        """List the backup databases stored in one destination.

        Returns {region, destination_id, total, databases[{id, name,
        database_id, engine, status, backup_enabled, ...}]}.

        The vDB counterpart of list_backup_destination_servers, and the second
        half of a destination's "Backup Resources" tab. vServer and vDB are the
        only two products vBackup covers, so between the two tools they account
        for everything stored in a destination.

        This projection nulls out `policy` and `destination` on every item — the
        destination is implied by the call. Use list_backup_databases or
        get_backup_database when the attached policy matters.

        An empty list on a destination whose `product` is vServer is expected,
        not a failure: a vServer destination cannot store a database.
        """
        validate_id(destination_id, "destination_id")
        params = {"name": name} if name else None
        raw = await fetch_all_items(
            self.client,
            f"/v1/backup-destinations/{destination_id}/backup-databases",
            region=region,
            params=params,
        )
        items = [BackupDatabaseItem.from_api(d) for d in raw if isinstance(d, dict)]
        return BackupDatabaseListData(
            region=region or self.config.default_region,
            destination_id=destination_id,
            total=len(items),
            databases=items,
        )

    async def list_backup_destination_tags(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> DestinationTagListData:
        """List the tags on one backup destination.

        Returns {region, destination_id, total, tags[{key, value, resource_id,
        resource_type, system_tag}]}.

        The "Tags" tab of a destination. Tags whose `system_tag` is true were
        set by the platform (`vng.createdBy` records who created the
        destination) and are not editable; only the rest are user tags.

        Note the endpoint is the account-wide tag service (`/v1/tags/{id}`)
        addressed by destination id, so `resource_type` comes back as
        `BACKUP_LOCATION` — the console's name for a destination.

        This server has no tool to add or remove a tag; the API exposes none.
        """
        validate_id(destination_id, "destination_id")
        resolved_region = region or self.config.default_region

        async def fetch() -> DestinationTagListData:
            raw = await fetch_all_items(self.client, f"/v1/tags/{destination_id}", region=region)
            items = [DestinationTagItem.from_api(t) for t in raw if isinstance(t, dict)]
            return DestinationTagListData(
                region=resolved_region,
                destination_id=destination_id,
                total=len(items),
                tags=items,
            )

        key = ("list_backup_destination_tags", resolved_region, destination_id)
        return await self.cache.get_or_fetch("list_backup_destination_tags", key, fetch, refresh)

    async def list_backup_destination_history(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        destination_id: str | None = Field(
            None,
            description=(
                "Destination to scope to (`bk-des-...`). Omit for every destination "
                "in the account, including ones that have since been deleted."
            ),
        ),
        limit: int = Field(
            DEFAULT_HISTORY_LIMIT,
            ge=1,
            le=500,
            description="Maximum records to return, newest first.",
        ),
    ) -> DestinationHistoryListData:
        """List what has been changed on backup destinations, newest first.

        Returns {region, destination_id, total, changes[{id, destination_id,
        destination_name, action, status, error_message, description,
        created_at}]}.

        With `destination_id` this is the "Activity" tab of one destination.
        Without it, it is the account-wide configuration log across every
        destination — including **destinations that no longer exist**, which is
        the only way to find out what happened to one that was deleted.

        Either way this is the config trail, not the run trail:
        list_backup_history covers backup RUNS, this covers edits to the place
        they land.

        Two things make it worth reading before an edit:

        - **Failed attempts are recorded too** (`status: ERROR`). An
          `error_message` of `backup_location_is_being_used` is a previous
          delete refused because resources were still stored here.
        - `description` carries the values that were used, in the API's own
          words — e.g. "Edit max-quota with {max-quota: 150GB}" — so the quota
          history is readable even though only the current value is stored on
          the destination.

        The account-wide log runs to tens of thousands of records on an active
        account, so it is capped by `limit`; say which window you are showing.
        Unlike list_backup_history this endpoint applies no default date filter
        and accepts no `from_date`.
        """
        if destination_id:
            validate_id(destination_id, "destination_id")

        path = "/v1/histories/backup-destinations"
        if destination_id:
            path = f"{path}/{destination_id}"

        raw = await fetch_all_items(self.client, path, region=region)
        items = [DestinationHistoryItem.from_api(h) for h in raw if isinstance(h, dict)][:limit]
        return DestinationHistoryListData(
            region=region or self.config.default_region,
            destination_id=destination_id or "",
            total=len(items),
            changes=items,
        )

    async def list_backup_products(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> ProductListData:
        """List the GreenNode products vBackup can protect.

        Returns {region, total, products[{id, product, enabled}]}.

        Call it before create_backup_destination or a policy create: `product`
        is the string this reports (`vServer`, `vDB`), not the `prd-...` id,
        and it is fixed for the life of a destination.

        An entry whose `enabled` is false has had backup support withdrawn —
        do not offer it, even though it is still listed.
        """
        resolved_region = region or self.config.default_region

        async def fetch() -> ProductListData:
            raw = await fetch_all_items(self.client, "/v1/products", region=region)
            items = [ProductItem.from_api(p) for p in raw if isinstance(p, dict)]
            return ProductListData(region=resolved_region, total=len(items), products=items)

        return await self.cache.get_or_fetch(
            "list_backup_products", ("list_backup_products", resolved_region), fetch, refresh
        )

    async def list_backup_regions(
        self,
        product: Product = Field(
            "vServer",
            description="Product the destination will serve. Omitting it defaults to vServer.",
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> BackupRegionListData:
        """List the backup regions a destination of a given product can store in.

        Returns {region, product, total, regions[{id, name, region_id, product}]}.

        **`region_id`, not `id`, is what create_backup_destination takes.** The
        `id` is a `vst-cf...` configuration id and is rejected there; this is the
        easiest way to get a create wrong.

        These are storage sites (HCM04, HAN02), not the two API gateways this
        server routes to (`HCM-3`, `HAN`), and they need not match: choosing a
        site away from the workload is how a cross-region backup is set up, and
        is what protects the data from losing its home region.

        Each product publishes its own list and the `region_id` values differ
        between them, so list for the product you are about to create.
        """
        resolved_region = region or self.config.default_region

        async def fetch() -> BackupRegionListData:
            raw = await fetch_all_items(
                self.client, "/v1/regions", region=region, params={"product": product}
            )
            items = [BackupRegionItem.from_api(r) for r in raw if isinstance(r, dict)]
            return BackupRegionListData(
                region=resolved_region, product=product, total=len(items), regions=items
            )

        key = ("list_backup_regions", resolved_region, product)
        return await self.cache.get_or_fetch("list_backup_regions", key, fetch, refresh)

    async def create_backup_destination(
        self,
        body: CreateBackupDestinationDto = Field(..., description="The destination to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupDestinationItem:
        """Create a backup destination (Backup Location).

        Returns the created destination, including the id
        create_backup_server takes as `backupDestinationId`.

        ## Requirements
        - `--allow-write` must be enabled.
        - `product` comes from list_backup_products and `regionId` from the
          `region_id` field of a list_backup_regions entry FOR THAT PRODUCT —
          not its `id`, and not a gateway region name.
        - `product` is permanent. A destination cannot be converted later, and
          backups of the other product cannot be written to it.
        - `maxQuota`, `softDeleteConfig` and `vaultLock` are all REQUIRED by the
          API even when the feature is off — send them with `enable: false`
          rather than omitting them, or the create fails
          `missing_required_field`. The DTO defaults already do this.
        - `isDefault` must always be sent. `true` takes default away from
          whichever destination of the same product currently holds it — name
          that destination to the user first. An ABSENT `isDefault` is read as
          true and the create is then refused, since a product may have only
          one default.
        - `vaultLock.enable: true` starts an irreversible clock: after
          `changeDuration` days the lock can never be edited or removed, by this
          server or the console. Leave it off unless the user asks for it and
          confirms the numbers.
        - A non-unlimited `maxQuota` is a hard ceiling in GB: runs FAIL once it
          is reached, they are not throttled.

        ## Workflow
        1. list_backup_products, then list_backup_regions for the chosen
           product — read `region_id` from the entry, not `id`.
        2. Ask where the data should live. Point out that a site away from the
           workload survives losing the workload's region, and that one near it
           restores faster.
        3. Ask about quota, soft delete and lock separately, and state the
           consequence of each: a quota that fails runs, a recycle bin that
           keeps billing for `retainDays`, a lock that becomes permanent.
        4. Summarise the whole configuration and confirm before creating.
        5. Create, then get_backup_destination to verify what the platform
           actually stored.
        """
        require_write(self.allow_write)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post("/v1/backup-destinations", region=region, json=payload)
        self.cache.invalidate("list_backup_destinations")
        return BackupDestinationItem.from_api(unwrap(data))

    async def update_backup_destination_name(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        body: UpdateBackupDestinationNameDto = Field(..., description="The new name."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Rename a backup destination.

        Returns {region, resource_id, action, succeeded, detail} — the API
        answers without a body.

        ## Requirements
        - `--allow-write` must be enabled.
        - Renaming changes the display name only. The id is unchanged, so
          nothing pointing at this destination breaks.

        ## Workflow
        1. get_backup_destination — confirm which destination is being renamed
           and report its current name back to the user.
        2. Rename, then re-read to confirm.
        """
        require_write(self.allow_write)
        validate_id(destination_id, "destination_id")
        await self.client.put(
            f"/v1/backup-destinations/{destination_id}/name",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_destinations")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=destination_id,
            action="renamed",
            detail=(
                "Display name only — the id is unchanged and every backup server "
                "writing here is unaffected. Re-read with get_backup_destination "
                "to confirm."
            ),
        )

    async def update_backup_destination_max_quota(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        body: UpdateMaxQuotaDto = Field(..., description="The replacement capacity ceiling."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Set the capacity ceiling of a backup destination.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - The ceiling is in **GB** and it is hard: once reached, backup runs
          FAIL. They are not queued or throttled.
        - Read `vault.used_gb` with get_backup_destination first and never set
          the ceiling below it — that stops every future run on every server
          writing here, immediately.
        - `unlimited: true` removes the ceiling and makes `maxQuota` irrelevant;
          the API reports it back as 0.

        ## Workflow
        1. get_backup_destination — read `vault.used_gb` and the current
           `max_quota_gb`.
        2. Tell the user how much headroom the new ceiling leaves, and how many
           backup servers (`backup_server_count`) stop if it runs out.
        3. Apply, then re-read to confirm.
        """
        require_write(self.allow_write)
        validate_id(destination_id, "destination_id")
        await self.client.put(
            f"/v1/backup-destinations/{destination_id}/max-quota",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_destinations")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=destination_id,
            action="max-quota updated",
            detail=(
                "Runs fail once the ceiling is reached. Verify with "
                "get_backup_destination that `vault.used_gb` still leaves headroom."
            ),
        )

    async def update_backup_destination_soft_delete(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        body: SoftDeleteDto = Field(..., description="The replacement recycle-bin setting."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Turn a destination's recycle bin on or off, or change its retention.

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - Soft delete makes a deleted backup recoverable for `retainDays` — and
          **it is billed for that whole window**. Say so before enabling it for
          a user whose stated goal is reducing cost.
        - Turning it off does not purge what is already in the recycle bin.
        - `retainDays` is ignored when `enable` is false.

        ## Workflow
        1. get_backup_destination — report the current setting; null means off.
        2. Ask what the retention should be, and state the billing consequence
           of the number they choose.
        3. Apply, then re-read to confirm.
        """
        require_write(self.allow_write)
        validate_id(destination_id, "destination_id")
        await self.client.put(
            f"/v1/backup-destinations/{destination_id}/soft-delete",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_destinations")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=destination_id,
            action="soft-delete updated",
            detail=(
                "Deleted backups remain billed for the retention window. Verify with "
                "get_backup_destination."
            ),
        )

    async def update_backup_destination_vault_lock(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        body: VaultLockDto = Field(..., description="The replacement retention lock."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Set the retention lock on a backup destination (the console's Location Lock).

        Returns {region, resource_id, action, succeeded, detail}.

        ## Requirements
        - `--allow-write` must be enabled.
        - **This can become irreversible.** `changeDuration` is a grace period
          in days from when the lock was enabled; once it elapses, the retention
          bounds cannot be changed and the lock cannot be turned off — not by
          this server, not by the console, not by support.
        - `changeDuration` must be 0-7 days, and **0 means permanent
          immediately**: the very next edit is refused with "Cannot edit vault
          lock". Never send 0 unless the user asked for exactly that.
        - `minRetention` must not exceed `maxRetention`; the API rejects the
          pair as `vault_locked_invalid` without naming either field.
        - While locked, a backup younger than `minRetention` days cannot be
          deleted, and one older than `maxRetention` days is deleted
          automatically.
        - A locked destination cannot be deleted while it still holds backups
          inside their minimum retention. An EMPTY destination can be deleted
          even under a permanent lock.
        - Do not enable a lock on a user's behalf, and never pick
          `changeDuration` for them.

        ## Workflow
        1. get_backup_destination — `vault_lock` null means unlocked. If it is
           set, check `change_duration_days` against `created_at`: past that
           window this call will be refused, and saying so beats sending it. A
           stored `change_duration_days` of 0 means it is already permanent.
        2. State the three numbers back in plain language, and say explicitly
           that after the grace period the setting is permanent.
        3. Get an explicit confirmation for THIS destination by name — not a
           general "yes, go ahead".
        4. Apply, then re-read to confirm what was stored.
        """
        require_write(self.allow_write)
        validate_id(destination_id, "destination_id")
        await self.client.put(
            f"/v1/backup-destinations/{destination_id}/vault-lock",
            region=region,
            json=body.model_dump(),
        )
        self.cache.invalidate("list_backup_destinations")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=destination_id,
            action="vault-lock updated",
            detail=(
                "The lock becomes permanent once its change-duration window elapses. "
                "Verify the stored values now with get_backup_destination."
            ),
        )

    async def delete_backup_destination(
        self,
        destination_id: str = Field(
            ..., description="Destination ID from list_backup_destinations (`bk-des-...`)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Delete a backup destination.

        Returns {region, resource_id, action, succeeded, detail}.

        **Irreversible, and it takes the stored backups with it.** Deleting a
        destination is not like disabling a backup server: nothing is kept.

        ## Requirements
        - `--allow-write` must be enabled.
        - The destination must be empty. list_backup_destination_servers and
          list_backup_destination_databases must both come back empty, or the
          API refuses with `backup_location_is_being_used`.
        - A destination under a vault lock is refused while it holds backups
          inside their minimum retention — the lock is there precisely to
          prevent this.
        - The default destination of a product should not be deleted while
          other destinations of that product exist without one; create or
          promote a replacement first.

        ## Workflow
        1. list_backup_destination_servers AND
           list_backup_destination_databases — list what is stored, by name.
           If either is non-empty, stop and move or delete those backup servers
           first; the delete cannot succeed.
        2. get_backup_destination — report `is_default`, `vault_lock` and
           `vault.used_gb` so the user knows what is being destroyed.
        3. State plainly that the backups stored here are destroyed and cannot
           be restored afterwards, then get an explicit confirmation naming this
           destination.
        4. Delete, then list_backup_destination_history to confirm the attempt
           recorded SUCCESS rather than an error.
        """
        require_write(self.allow_write)
        validate_id(destination_id, "destination_id")
        await self.client.delete(f"/v1/backup-destinations/{destination_id}", region=region)
        self.cache.invalidate("list_backup_destinations")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=destination_id,
            action="deleted",
            detail=(
                "The destination and the backups stored in it are gone and cannot be "
                "recovered. Check list_backup_destination_history if you need to "
                "confirm the platform recorded SUCCESS."
            ),
        )
