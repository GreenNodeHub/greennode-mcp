"""Catalogue discovery: backends, the platform configuration, protected servers.

These are the account-wide lookups every other flow starts from — a create
needs a backend and the limits a policy must respect — so they are cached and
all expose a ``refresh`` parameter.

Backup destinations have their own handler: they grew from a single lookup into
a full lifecycle (create, four edits, delete, four detail views) and no longer
belong among read-only catalogue reads. See ``destination_handler``.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
from greennode.vbackup_mcp_server.models import (
    BackendItem,
    BackendListData,
    ConfigurationData,
    ProtectedServerListData,
    as_text,
)
from greennode.vbackup_mcp_server.paging import as_list, fetch_all_items
from greennode.vbackup_mcp_server.tool_annotations import READ
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field


class CatalogueHandler:
    """Register and serve vBackup catalogue-discovery MCP tools."""

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

        self.mcp.tool(name="list_backends", annotations=READ)(self.list_backends)
        self.mcp.tool(name="get_configuration", annotations=READ)(self.get_configuration)
        self.mcp.tool(name="list_protected_servers", annotations=READ)(self.list_protected_servers)

    async def list_backends(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Region gateway to query ('HCM-3' or 'HAN'). The two gateways do not "
                "return the same set of backends — query the region the user means."
            ),
        ),
        refresh: bool = Field(
            False, description="Bypass the short-lived cache and refetch from vBackup."
        ),
    ) -> BackendListData:
        """List the vBackup backends visible to the caller.

        Returns {region, backends[{id, name}]}.

        `id` is the `backendId` that backup servers, policies, destinations and
        history records carry, and that every create body requires. Do NOT infer
        a region from it: one region's gateway can return backends belonging to
        another, so a backend named for one region may appear in both listings.
        """
        resolved_region = region or self.config.default_region

        async def fetch() -> BackendListData:
            raw = await fetch_all_items(self.client, "/v1/backends", region=region)
            return BackendListData(
                region=resolved_region,
                backends=[BackendItem.from_api(b) for b in raw if isinstance(b, dict)],
            )

        return await self.cache.get_or_fetch(
            "list_backends", ("list_backends", resolved_region), fetch, refresh
        )

    async def get_configuration(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> ConfigurationData:
        """Get the platform limits a backup policy must respect.

        Returns {region, backup_policy_hourly_intervals, backup_policy_retention_limits,
        backup_policy_hours, allowed_backup_server_status,
        snapshot_policy_hourly_intervals, snapshot_policy_retention_limits}.

        Call this BEFORE create_backup_policy or update_backup_policy. It is the
        authority for what a policy may contain — the hourly intervals allowed,
        the retention ceiling per cadence, and which clock hours the platform
        has left open. Hardcoded bounds drift; these do not.

        The `snapshot_*` values belong to vServer SNAPSHOT policies, a different
        product with different limits. They are returned for contrast only —
        never validate a backup policy against them.

        `allowed_backup_server_status` lists the vServer instance states that can
        be added as a backup server; an instance in any other state is rejected
        by create_backup_server.
        """
        resolved_region = region or self.config.default_region

        async def fetch() -> ConfigurationData:
            raw = await self.client.get("/v1/configurations", region=region)
            return ConfigurationData.from_api(resolved_region, raw)

        return await self.cache.get_or_fetch(
            "get_configuration", ("get_configuration", resolved_region), fetch, refresh
        )

    async def list_protected_servers(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        backend_id: str | None = Field(
            None, description="Filter by backend ID from list_backends."
        ),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vBackup."),
    ) -> ProtectedServerListData:
        """List the vServer instance IDs that already have a backup server.

        Returns {region, total, server_ids[]} — ids only, no other detail; the
        API answers this one as a bare `{"ids": [...]}` object.

        Use it as a cheap membership check before offering to protect an
        instance, so the same server is not registered twice. For anything
        beyond "is it protected", call list_backup_servers instead.

        An empty list is normal on an account whose policies do not mark their
        servers as protected (`isProtectedServer`), so it is NOT proof that
        nothing is backed up — check list_backup_servers before telling a user
        they have no backups.
        """
        if backend_id:
            validate_id(backend_id, "backend_id")

        params = {"backendId": backend_id} if backend_id else None
        resolved_region = region or self.config.default_region

        async def fetch() -> ProtectedServerListData:
            raw = await self.client.get(
                "/v1/backup-instances/protected-servers", region=region, params=params
            )
            ids = [as_text(i) for i in as_list(raw, "ids") if as_text(i)]
            return ProtectedServerListData(region=resolved_region, total=len(ids), server_ids=ids)

        key = ("list_protected_servers", resolved_region, backend_id or "")
        return await self.cache.get_or_fetch("list_protected_servers", key, fetch, refresh)
