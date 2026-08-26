"""Short-lived TTL cache for read-only vBackup discovery results."""

from __future__ import annotations

from greennode.mcp_core.cache import DEFAULT_MAXSIZE
from greennode.mcp_core.cache import DiscoveryCache as _CoreDiscoveryCache
from greennode.mcp_core.http import current_identity


TTL_CONFIG: dict[str, int] = {
    "list_backends": 3600,
    "get_configuration": 3600,
    "list_backup_destinations": 600,
    "list_backup_destination_tags": 600,
    "list_backup_products": 3600,
    "list_backup_regions": 3600,
    "list_backup_policies": 120,
    "list_backup_servers": 60,
    "list_backup_databases": 60,
    "list_protected_servers": 60,
    "list_protected_databases": 60,
    "list_databases": 300,
}

UNCACHED_TOOLS = (
    "get_backup_destination",
    "get_backup_destination_metrics",
    "get_backup_metrics",
    "get_backup_server_point_download_urls",
    "get_backup_statistics",
    "get_vserver_instance",
    "list_backup_destination_databases",
    "list_backup_destination_history",
    "list_backup_destination_servers",
    "list_backup_history",
    "list_restore_history",
    "list_server_migration_history",
    "list_backup_server_points",
    "list_backup_server_volumes",
    "get_backup_server",
    "get_backup_database",
    "list_backup_database_points",
    "get_backup_policy",
    "list_volume_usage",
)
"""Tools deliberately absent from TTL_CONFIG, and therefore never cached.

They answer "what happened just now" or "what is the state right after my
write". A cached answer to either is worse than no answer, so they are listed
here to make the omission a decision rather than an oversight.
"""


class DiscoveryCache(_CoreDiscoveryCache):
    """vBackup discovery cache: core cache preconfigured with this package's TTLs.

    TTLs are tiered by how fast the resource changes: the backend list and the
    platform configuration are effectively static, destinations and policies
    change when an operator edits them, and the backup-server list moves
    whenever a scheduled run finishes. A tool with no entry here is never
    cached — history and restore-point reads must always hit the API, because
    their whole purpose is telling the user what just happened.

    Every key is prefixed with the caller identity (hash of the passthrough
    user token, or 'service'), so under token passthrough one user's cached
    results can never be served to another.
    """

    def __init__(
        self, ttl_config: dict[str, int] | None = None, maxsize: int = DEFAULT_MAXSIZE, timer=None
    ):
        super().__init__(
            ttl_config if ttl_config is not None else TTL_CONFIG, maxsize=maxsize, timer=timer
        )

    async def get_or_fetch(self, tool, key, fetch, refresh=False):
        """Cache lookup with the caller identity baked into the key."""
        scoped_key = (current_identity(), key)
        return await super().get_or_fetch(tool, scoped_key, fetch, refresh)
