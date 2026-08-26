"""Backup policies — the schedules that decide when a backup server runs.

A policy is shared: several backup servers can point at the same one, so an
edit here changes every server using it. That is the single most common way a
user is surprised by this product, and it shapes every docstring below.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
from greennode.vbackup_mcp_server.guards import require_write
from greennode.vbackup_mcp_server.models import (
    BackupPolicyItem,
    BackupPolicyListData,
    CreateBackupPolicyDto,
    UpdateBackupPolicyDto,
    WriteResult,
)
from greennode.vbackup_mcp_server.paging import fetch_all_items, unwrap
from greennode.vbackup_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field


class PolicyHandler:
    """Register and serve backup-policy MCP tools."""

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

        self.mcp.tool(name="list_backup_policies", annotations=READ)(self.list_backup_policies)
        self.mcp.tool(name="get_backup_policy", annotations=READ)(self.get_backup_policy)

        if self.allow_write:
            self.mcp.tool(name="create_backup_policy", annotations=WRITE)(
                self.create_backup_policy
            )
            self.mcp.tool(name="update_backup_policy", annotations=WRITE)(
                self.update_backup_policy
            )
            self.mcp.tool(name="update_default_backup_policy", annotations=WRITE)(
                self.update_default_backup_policy
            )
            self.mcp.tool(name="delete_backup_policy", annotations=DESTRUCTIVE)(
                self.delete_backup_policy
            )

    async def list_backup_policies(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        name: str | None = Field(None, description="Filter by policy name."),
        backend_id: str | None = Field(
            None, description="Filter by backend ID from list_backends."
        ),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> BackupPolicyListData:
        """List the backup policies in a region.

        Returns {region, total, policies[{id, name, is_default,
        backup_server_count, schedule{summary, run_at, hourly, daily, weekly,
        monthly}, ...}]}.

        `id` is the `backupPolicyId` create_backup_server and
        update_backup_server_policy take.

        `schedule.summary` is the line to show a user — it lists only the
        cadences that are actually enabled. An empty summary means the policy
        never runs, which is a real and easily-missed state.

        `backup_server_count` is how many servers depend on the policy: editing
        one with a non-zero count changes all of them, and deleting it is
        refused. `is_default` marks a platform-owned policy shared across the
        account — prefer creating your own over editing a default.
        """
        if backend_id:
            validate_id(backend_id, "backend_id")

        params: dict[str, str] = {}
        if name:
            params["name"] = name
        if backend_id:
            params["backendId"] = backend_id

        resolved_region = region or self.config.default_region

        async def fetch() -> BackupPolicyListData:
            raw = await fetch_all_items(
                self.client, "/v1/backup-policies", region=region, params=params or None
            )
            items = [BackupPolicyItem.from_api(p) for p in raw if isinstance(p, dict)]
            return BackupPolicyListData(region=resolved_region, total=len(items), policies=items)

        key = ("list_backup_policies", resolved_region, tuple(sorted(params.items())))
        return await self.cache.get_or_fetch("list_backup_policies", key, fetch, refresh)

    async def get_backup_policy(
        self,
        policy_id: str = Field(..., description="Policy ID from list_backup_policies."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupPolicyItem:
        """Get one backup policy by id.

        Returns the full schedule, cadence by cadence, plus
        `backup_server_count`.

        Call this before update_backup_policy: the update REPLACES the whole
        schedule, so you need the current cadences to send back the ones the
        user is not changing.
        """
        validate_id(policy_id, "policy_id")
        data = await self.client.get(f"/v1/backup-policies/{policy_id}", region=region)
        return BackupPolicyItem.from_api(unwrap(data))

    async def create_backup_policy(
        self,
        body: CreateBackupPolicyDto = Field(..., description="The policy to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupPolicyItem:
        """Create a backup policy.

        Returns the created policy, including the id to attach to a backup
        server.

        ## Requirements
        - `--allow-write` must be enabled.
        - `backendId` comes from list_backends; `projectId` from any existing
          resource in the same region (list_backup_servers or
          list_backup_policies report it).
        - Call get_configuration FIRST and validate against it: the hourly
          interval must be one it allows, each retention must be within the
          per-cadence limit, and `hour` must be one of the open
          `backup_policy_hours`.
        - At least one cadence must be enabled. A policy with all four off is
          accepted by the API and never runs — refuse to create one without
          telling the user plainly.
        - Every enabled cadence needs its matching config object
          (`dailyEnabled` without `dailyConfig` is rejected).

        ## Workflow
        1. get_configuration — read the limits.
        2. Ask the user for the cadences, retention and run hour. Do NOT pick a
           retention silently: it is the difference between a recoverable
           mistake and a lost week.
        3. Summarise the resulting schedule in plain language and confirm.
        4. Create, then attach it with update_backup_server_policy or
           create_backup_server.
        """
        require_write(self.allow_write)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post("/v1/backup-policies", region=region, json=payload)
        self.cache.invalidate("list_backup_policies")
        return BackupPolicyItem.from_api(unwrap(data))

    async def update_backup_policy(
        self,
        policy_id: str = Field(..., description="Policy ID from list_backup_policies."),
        body: UpdateBackupPolicyDto = Field(
            ..., description="The COMPLETE replacement policy, not a partial patch."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BackupPolicyItem:
        """Update a backup policy, replacing its whole schedule.

        Returns the updated policy.

        ## Requirements
        - `--allow-write` must be enabled.
        - The body REPLACES the schedule: any cadence you omit comes back
          disabled, and `name` is required on every call — sending only the
          part being changed silently turns the rest off.
        - Read the current policy with get_backup_policy first and resend every
          cadence the user is keeping.
        - Validate the new schedule against get_configuration, exactly as for a
          create.

        ## Workflow
        1. get_backup_policy — the current cadences.
        2. Check `backup_server_count`. If it is greater than 1, name the
           servers that will be affected and confirm before continuing; an edit
           here changes every one of them.
        3. Merge the user's change into the full schedule, summarise the result
           and confirm.
        4. Update, then re-read to verify the cadences that were meant to stay
           on are still on.
        """
        require_write(self.allow_write)
        validate_id(policy_id, "policy_id")
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v1/backup-policies/{policy_id}", region=region, json=payload
        )
        self.cache.invalidate("list_backup_policies")
        return BackupPolicyItem.from_api(unwrap(data))

    async def update_default_backup_policy(
        self,
        policy_id: str = Field(
            ..., description="Policy ID from list_backup_policies to make the default."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Make one backup policy the default for its product.

        Returns {region, resource_id, action, succeeded, detail}. The API
        answers without a body, so the new state has to be read back.

        The default is the policy applied when a backup server is created
        without one named — notably by create_vserver_backup_servers, which
        takes no policy at all.

        ## Requirements
        - `--allow-write` must be enabled.
        - **Exactly one policy per product is the default**, so this does two
          things at once: it promotes this policy and DEMOTES whichever one is
          default now. There is no call to clear the default without naming a
          replacement.
        - The demoted policy keeps running for every backup server already
          attached to it. Only future creates that omit a policy are affected.
        - Check the target's schedule before promoting it: a policy with no
          cadence enabled is a legal default, and every server created without
          a policy afterwards silently never runs.

        ## Workflow
        1. list_backup_policies — identify the current default (`is_default`)
           and name it to the user alongside the new one.
        2. get_backup_policy on the target — read its `schedule.summary` back
           and refuse to promote one whose summary is empty.
        3. Confirm, then switch.
        4. list_backup_policies with `refresh=true` and verify the default
           moved; the list is cached, so a stale read looks like a failed
           switch.
        """
        require_write(self.allow_write)
        validate_id(policy_id, "policy_id")
        await self.client.put(f"/v1/backup-policies/{policy_id}/switch-default", region=region)
        self.cache.invalidate("list_backup_policies")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=policy_id,
            action="switched to default",
            detail=(
                "Another policy lost the default in the same call. Re-read "
                "list_backup_policies to report which one, and note that servers "
                "already attached to it are unaffected."
            ),
        )

    async def delete_backup_policy(
        self,
        policy_id: str = Field(..., description="Policy ID from list_backup_policies."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> WriteResult:
        """Delete a backup policy.

        Returns {region, resource_id, action, succeeded, detail}. The API
        answers 204 with no body, so this reports the outcome rather than an
        echo of the deleted object.

        ## Requirements
        - `--allow-write` must be enabled.
        - The policy must have no backup servers attached. Check
          `backup_server_count` with get_backup_policy first — a policy still in
          use is refused with a 409, and moving those servers to another policy
          (update_backup_server_policy) has to happen before the delete.
        - A `is_default` policy is platform-owned; do not attempt to delete one.

        ## Workflow
        1. get_backup_policy — confirm the name and `backup_server_count`.
        2. If servers are attached, list them with
           list_backup_policies/list_backup_servers and reattach them first.
        3. State plainly that future runs on this schedule stop, then confirm.
           Existing restore points are NOT deleted by this call.
        """
        require_write(self.allow_write)
        validate_id(policy_id, "policy_id")
        await self.client.delete(f"/v1/backup-policies/{policy_id}", region=region)
        self.cache.invalidate("list_backup_policies")
        return WriteResult(
            region=region or self.config.default_region,
            resource_id=policy_id,
            action="deleted",
            detail=(
                "The schedule is gone; restore points already taken under it remain "
                "and are still billed. Remove them via their backup server if the "
                "user wanted the storage back."
            ),
        )
