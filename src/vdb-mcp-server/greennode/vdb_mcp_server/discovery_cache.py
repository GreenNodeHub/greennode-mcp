"""Short-lived TTL cache for read-only vDB discovery results."""

from __future__ import annotations

from greennode.mcp_core.cache import DEFAULT_MAXSIZE
from greennode.mcp_core.cache import DiscoveryCache as _CoreDiscoveryCache
from greennode.mcp_core.http import current_identity


TTL_CONFIG: dict[str, int] = {
    # Catalogue: what the platform offers. Changes when GreenNode ships a new
    # engine version or flavour, i.e. rarely, and never as a result of anything
    # the caller does.
    "list_relational_datastores": 3600,
    "list_memory_datastores": 3600,
    "list_relational_engines": 3600,
    "list_memory_engines": 3600,
    "list_relational_instance_families": 3600,
    "list_memory_instance_families": 3600,
    "list_relational_flavors": 3600,
    "list_memory_flavors": 3600,
    "list_relational_flavor_codes": 3600,
    "list_memory_flavor_codes": 3600,
    "list_relational_volume_types": 3600,
    "list_memory_volume_types": 3600,
    "list_relational_zones": 3600,
    "list_postgresql_datastores": 3600,
    "list_postgresql_flavors": 3600,
    "list_postgresql_volume_types": 3600,
    # Kafka's catalogue, including the limits endpoint -- which is a catalogue
    # of the platform's own validation rules, not of anything the account owns.
    "get_kafka_limits": 3600,
    "list_kafka_flavors": 3600,
    "list_kafka_volume_types": 3600,
    "list_kafka_flavor_codes": 3600,
    "list_kafka_instance_families": 3600,
    # Engine parameters: the set of knobs an engine version exposes, plus their
    # bounds. Catalogue data in everything but name -- it does not change when
    # a caller edits a configuration group, only when GreenNode ships a new
    # engine version.
    "list_relational_configuration_params": 3600,
    "list_memory_configuration_params": 3600,
    # Account topology: edited by an operator in another product, so it moves
    # on a human timescale rather than an API one.
    "list_relational_networks": 600,
    "list_memory_networks": 600,
    "list_relational_subnets": 600,
    "list_memory_subnets": 600,
    # Backup storage quota packages: a price list, not a usage figure.
    "list_relational_backup_storage_packages": 3600,
    "list_memory_backup_storage_packages": 3600,
    # vBackup destinations and schedules: account-level settings edited in
    # another product, like the network topology above. A stale entry at worst
    # omits one somebody just created, and `refresh` fixes that -- where a
    # stale backup *listing* would hide a backup that just ran, which is why
    # the per-cluster backup reads are uncached.
    "list_postgresql_backup_locations": 600,
    "list_postgresql_backup_policies": 600,
}

UNCACHED_TOOLS = (
    # Anything that answers "what is the state right now", especially right
    # after a write. An order is asynchronous, so a cached instance list would
    # keep reporting BUILDING (or absence) after the instance went ACTIVE.
    "list_relational_instances",
    "list_memory_instances",
    "get_relational_instance",
    "get_memory_instance",
    "list_relational_instance_histories",
    "list_memory_instance_histories",
    "list_relational_instance_replicas",
    "list_memory_instance_replicas",
    "list_relational_instance_secrules",
    "list_memory_instance_secrules",
    "list_relational_backups",
    "list_memory_backups",
    "get_relational_backup",
    "get_memory_backup",
    "list_relational_instance_backups",
    "list_memory_instance_backups",
    "get_relational_free_backup_usage",
    "get_memory_free_backup_usage",
    "get_relational_backup_storage",
    "get_memory_backup_storage",
    "list_relational_configurations",
    "list_memory_configurations",
    "get_relational_configuration",
    "get_memory_configuration",
    # The PostgreSQL Cluster family: its listing is derived from the
    # (uncached) relational one, and every backup read here answers "what has
    # run", not "what is on offer".
    "list_postgresql_clusters",
    "get_postgresql_cluster",
    "get_postgresql_cluster_volume_used",
    "list_postgresql_backups",
    "get_postgresql_cluster_backup",
    "list_postgresql_restore_points",
    # Kafka: everything that answers "what exists right now". Its writes are
    # asynchronous and answer with a bare string, so a cached read would be
    # the only thing a caller had to go on -- and it would be stale.
    "list_kafka_clusters",
    "get_kafka_cluster",
    "list_kafka_cluster_histories",
    "list_kafka_topics",
    "get_kafka_topic",
    "list_kafka_users",
    "get_kafka_user",
    "get_kafka_user_credentials",
    "list_kafka_config_groups",
    "get_kafka_config_group",
    "get_kafka_config_group_version",
)
"""Tools deliberately absent from TTL_CONFIG, and therefore never cached.

Listing them makes the omission a decision rather than an oversight. The
configuration *parameters* endpoints are catalogue data and could be cached;
the configuration *groups* a user has created cannot, because a user creates
them through this very server.
"""


class DiscoveryCache(_CoreDiscoveryCache):
    """vDB discovery cache: the core cache preconfigured with this package's TTLs.

    Every key is prefixed with the caller identity (hash of the passthrough
    user token, or 'service'), so under token passthrough one user's cached
    results can never be served to another.
    """

    def __init__(
        self,
        ttl_config: dict[str, int] | None = None,
        maxsize: int = DEFAULT_MAXSIZE,
        timer=None,
    ):
        super().__init__(
            ttl_config if ttl_config is not None else TTL_CONFIG,
            maxsize=maxsize,
            timer=timer,
        )

    async def get_or_fetch(self, tool, key, fetch, refresh=False):
        """Cache lookup with the caller identity baked into the key."""
        scoped_key = (current_identity(), key)
        return await super().get_or_fetch(tool, scoped_key, fetch, refresh)
