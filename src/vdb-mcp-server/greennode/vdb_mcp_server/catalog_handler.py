"""Catalogue tools: what the platform offers, for both the relational and memory families.

Every tool here is read-only and cached. They answer the questions a create has
to resolve first -- which engine, which version, which size, which storage,
which subnet -- and none of them reflects anything the account owns, so a
stale answer is harmless in a way that a stale instance list would not be.

The two families are separate tools rather than one tool with a `family`
argument: they do not share a single path, and the memory family has no zones
endpoint at all.
"""

from __future__ import annotations

from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import (
    VDB_KAFKA_SERVICE,
    VDB_MEMORY_SERVICE,
    VDB_POSTGRESQL_SERVICE,
    VDB_RELATIONAL_SERVICE,
    VdbConfig,
)
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.models import (
    DatastoreListData,
    DatastoreOption,
    EngineListData,
    EngineOption,
    FlavorCodeListData,
    FlavorCodeOption,
    FlavorListData,
    FlavorOption,
    InstanceFamilyListData,
    InstanceFamilyOption,
    KafkaLimits,
    NetworkListData,
    NetworkOption,
    SubnetListData,
    SubnetOption,
    VolumeTypeListData,
    VolumeTypeOption,
    ZoneListData,
    ZoneOption,
)
from greennode.vdb_mcp_server.paging import as_list, unwrap_nested_list, unwrap_wrapped
from greennode.vdb_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Any


# Paths differ between the families for the same catalogue, so they are listed
# per family rather than derived from a shared suffix.
RELATIONAL_PATHS = {
    "datastores": "/v1/database-instances/datastore",
    "engines": "/v1/database-instances/engine",
    "instance_families": "/v1/database-instances/families",
    "flavors": "/v1/database-instances/flavors",
    "flavor_codes": "/v1/database-instances/flavor_zones/codes",
    "volume_types": "/v1/database-instances/volume/types",
    "zones": "/v1/database-instances/zones",
    "networks": "/v1/database-instances/networks",
    "subnets": "/v1/database-instances/networks/subnets",
}

POSTGRESQL_PATHS = {
    "datastores": "/v1/cluster/datastore",
    "flavors": "/v1/cluster/flavors",
    "volume_types": "/v1/cluster/volume-types",
}
"""The PostgreSQL Cluster catalogue -- three endpoints, not nine.

This family has no engines, families, flavour-codes, networks or zones
catalogue of its own: the engine is always PostgreSQL, and the zones and
subnets are account-wide, so `list_relational_zones` and
`list_relational_subnets` serve it.
"""

KAFKA_PATHS = {
    "codes": "/database/codes",
    "configs": "/database/configs",
    "instance_families": "/database/families",
    "flavors": "/database/flavors",
    "volume_types": "/database/volume-types",
}
"""The Kafka catalogue. Note the paths carry **no** ``/v1``.

There is no datastore, engine, zones, networks or subnets catalogue here: Kafka
publishes its versions through ``/database/configs`` instead, and it has no
zone at all -- a cluster is placed by network and subnet, which come from the
relational catalogue.
"""

MEMORY_PATHS = {
    "datastores": "/v1/database/datastore",
    "engines": "/v1/database/engine",
    "instance_families": "/v1/database/families",
    "flavors": "/v1/database/flavors",
    "flavor_codes": "/v1/database/codes",
    "volume_types": "/v1/database/volume-types",
    "networks": "/v1/database/networks",
    "subnets": "/v1/database/networks/subnets",
}

EMPTY_FLAVORS_NOTE = (
    "No flavours matched. The API answers an unrecognised engine/version pair with an "
    "empty list rather than an error, so this almost always means the pair is wrong "
    "rather than that the engine has no sizes. Check the exact `type` and `version` "
    "values with {datastore_tool}."
)


def _postgresql_catalogue_params(zone_id: str | None, multi_zone: bool) -> dict[str, Any] | None:
    """Query parameters of the two zoned PostgreSQL Cluster catalogues.

    `multiZone=true` asks for the catalogue that suits a Multi-AZ cluster, and
    the API then **replaces** `zoneId` with the Multi-AZ default zone
    (HCM03-1A) -- so the two are refused together here instead of sending a
    zone the answer would not describe. `multiZone` is sent only when asked
    for, so a default call stays the request it was before the flag existed.
    """
    if multi_zone:
        if zone_id:
            raise ValueError(
                "zone_id cannot be combined with multi_zone: the API replaces the zone with the "
                "Multi-AZ default zone. Call with multi_zone alone (each row's zone_id names the "
                "zone it describes), or with zone_id alone for a single-zone cluster."
            )
        return {"multiZone": "true"}
    return {"zoneId": zone_id} if zone_id else None


class CatalogHandler:
    """Register and serve the vDB catalogue tools."""

    def __init__(
        self,
        mcp,
        config: VdbConfig,
        client: VdbClient,
        cache: DiscoveryCache,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        # Registered one literal name at a time, not in a loop: the monorepo
        # Conventions job reads these names statically out of the source, and a
        # loop over a list would make every tool here invisible to that check.
        self.mcp.tool(name="list_relational_datastores", annotations=READ)(
            self.list_relational_datastores
        )
        self.mcp.tool(name="list_relational_engines", annotations=READ)(
            self.list_relational_engines
        )
        self.mcp.tool(name="list_relational_instance_families", annotations=READ)(
            self.list_relational_instance_families
        )
        self.mcp.tool(name="list_relational_flavors", annotations=READ)(
            self.list_relational_flavors
        )
        self.mcp.tool(name="list_relational_flavor_codes", annotations=READ)(
            self.list_relational_flavor_codes
        )
        self.mcp.tool(name="list_relational_volume_types", annotations=READ)(
            self.list_relational_volume_types
        )
        self.mcp.tool(name="list_relational_zones", annotations=READ)(self.list_relational_zones)
        self.mcp.tool(name="list_relational_networks", annotations=READ)(
            self.list_relational_networks
        )
        self.mcp.tool(name="list_relational_subnets", annotations=READ)(
            self.list_relational_subnets
        )
        self.mcp.tool(name="list_memory_datastores", annotations=READ)(self.list_memory_datastores)
        self.mcp.tool(name="list_memory_engines", annotations=READ)(self.list_memory_engines)
        self.mcp.tool(name="list_memory_instance_families", annotations=READ)(
            self.list_memory_instance_families
        )
        self.mcp.tool(name="list_memory_flavors", annotations=READ)(self.list_memory_flavors)
        self.mcp.tool(name="list_memory_flavor_codes", annotations=READ)(
            self.list_memory_flavor_codes
        )
        self.mcp.tool(name="list_memory_volume_types", annotations=READ)(
            self.list_memory_volume_types
        )
        self.mcp.tool(name="list_memory_networks", annotations=READ)(self.list_memory_networks)
        self.mcp.tool(name="list_memory_subnets", annotations=READ)(self.list_memory_subnets)
        self.mcp.tool(name="list_postgresql_datastores", annotations=READ)(
            self.list_postgresql_datastores
        )
        self.mcp.tool(name="list_postgresql_flavors", annotations=READ)(
            self.list_postgresql_flavors
        )
        self.mcp.tool(name="list_postgresql_volume_types", annotations=READ)(
            self.list_postgresql_volume_types
        )
        self.mcp.tool(name="get_kafka_limits", annotations=READ)(self.get_kafka_limits)
        self.mcp.tool(name="list_kafka_flavors", annotations=READ)(self.list_kafka_flavors)
        self.mcp.tool(name="list_kafka_volume_types", annotations=READ)(
            self.list_kafka_volume_types
        )
        self.mcp.tool(name="list_kafka_flavor_codes", annotations=READ)(
            self.list_kafka_flavor_codes
        )
        self.mcp.tool(name="list_kafka_instance_families", annotations=READ)(
            self.list_kafka_instance_families
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _fetch(
        self,
        tool: str,
        service: str,
        path: str,
        refresh: bool,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Fetch a catalogue endpoint through the discovery cache.

        The cache key carries the query parameters, not just the tool name: the
        flavour and volume-type catalogues change with the engine, version and
        zone asked for, and keying on the tool alone would serve one engine's
        sizes as another's.
        """
        key = (path, tuple(sorted((params or {}).items())))

        async def fetch():
            return await self.client.call("GET", path, service=service, params=params or None)

        return await self.cache.get_or_fetch(tool, key, fetch, refresh)

    async def _datastores(self, tool: str, service: str, path: str, refresh: bool):
        raw = await self._fetch(tool, service, path, refresh)
        items = [DatastoreOption.from_api(row) for row in as_list(raw)]
        return DatastoreListData(count=len(items), items=items)

    async def _engines(self, tool: str, service: str, path: str, refresh: bool):
        raw = await self._fetch(tool, service, path, refresh)
        items = [EngineOption.from_api(row) for row in as_list(raw)]
        return EngineListData(count=len(items), items=items)

    async def _instance_families(self, tool: str, service: str, path: str, refresh: bool):
        raw = await self._fetch(tool, service, path, refresh)
        items = [InstanceFamilyOption.from_api(row) for row in as_list(raw)]
        return InstanceFamilyListData(count=len(items), items=items)

    async def _flavors(
        self,
        tool: str,
        service: str,
        path: str,
        datastore_type: str,
        version: str,
        zone_id: str | None,
        refresh: bool,
        datastore_tool: str,
    ):
        # Both are declared required by the API. A blank `version` is answered
        # with a 500, so it is rejected here rather than sent.
        if not (datastore_type or "").strip():
            raise ValueError(
                "datastore_type is required (e.g. 'mysql', 'postgresql', 'redis'); "
                f"list the valid values with {datastore_tool}"
            )
        if not (version or "").strip():
            raise ValueError(
                "version is required (e.g. '8.0', '15', '7.2'); "
                f"list the valid engine/version pairs with {datastore_tool}"
            )

        params: dict[str, Any] = {
            "type": datastore_type.strip(),
            "version": version.strip(),
        }
        if zone_id:
            params["zoneId"] = zone_id

        raw = await self._fetch(tool, service, path, refresh, params)
        items = [FlavorOption.from_api(row) for row in as_list(raw)]
        return FlavorListData(
            count=len(items),
            items=items,
            datastore_type=params["type"],
            version=params["version"],
            zone_id=zone_id,
            note=None if items else EMPTY_FLAVORS_NOTE.format(datastore_tool=datastore_tool),
        )

    async def _flavor_codes(self, tool: str, service: str, path: str, refresh: bool):
        raw = await self._fetch(tool, service, path, refresh)
        items = [FlavorCodeOption.from_api(row) for row in as_list(raw)]
        return FlavorCodeListData(count=len(items), items=items)

    async def _volume_types(
        self, tool: str, service: str, path: str, zone_id: str | None, refresh: bool
    ):
        params = {"zoneId": zone_id} if zone_id else None
        raw = await self._fetch(tool, service, path, refresh, params)
        # `data` is an object here (`{projectId, data[]}`), so `as_list` would
        # report an empty catalogue instead of failing.
        page = unwrap_nested_list(raw)
        items = [VolumeTypeOption.from_api(row) for row in page.items]
        return VolumeTypeListData(count=len(items), items=items, zone_id=zone_id)

    async def _networks(self, tool: str, service: str, path: str, refresh: bool):
        raw = await self._fetch(tool, service, path, refresh)
        items = [NetworkOption.from_api(row) for row in as_list(raw)]
        return NetworkListData(count=len(items), items=items)

    async def _subnets(self, tool: str, service: str, path: str, refresh: bool):
        raw = await self._fetch(tool, service, path, refresh)
        items = [
            SubnetOption.from_network(network, subnet)
            for network in as_list(raw)
            for subnet in (network.get("subnets") or [])
        ]
        return SubnetListData(count=len(items), items=items)

    # ------------------------------------------------------------------
    # relational family
    # ------------------------------------------------------------------

    async def list_relational_datastores(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> DatastoreListData:
        """List the relational engine/version pairs the platform can provision.

        Start here when creating a MySQL, MariaDB or PostgreSQL instance: the
        `type` and `version` values returned are exactly what
        `list_relational_flavors` and the create tools expect. `type` is
        reported lowercase (`mysql`) and matched case-insensitively, but the
        pair must be one that appears here -- `postgresql` with a MySQL version
        is accepted by the API and silently matches nothing.
        """
        return await self._datastores(
            "list_relational_datastores",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["datastores"],
            refresh,
        )

    async def list_relational_engines(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> EngineListData:
        """List the relational engine families and their licences.

        Coarser than `list_relational_datastores`, which is the one a create
        needs: this omits versions.
        """
        return await self._engines(
            "list_relational_engines",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["engines"],
            refresh,
        )

    async def list_relational_instance_families(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> InstanceFamilyListData:
        """List the relational instance families (general purpose, memory optimised, ...).

        The `key` of a family is the `family_type` carried by each flavour, so
        this is how to group the output of `list_relational_flavors`.

        The endpoint mixes two kinds of row: rows whose `group` is
        `family_custom` describe a **zone group** (their `id` is what a flavour
        reports as `zone_group_id`) and carry no `key`. Filter on `group`
        before presenting these as families.
        """
        return await self._instance_families(
            "list_relational_instance_families",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["instance_families"],
            refresh,
        )

    async def list_relational_flavors(
        self,
        datastore_type: str = Field(
            ..., description="Engine type from list_relational_datastores, e.g. 'mysql'"
        ),
        version: str = Field(
            ..., description="Engine version from list_relational_datastores, e.g. '8.0'"
        ),
        zone_id: str | None = Field(
            None,
            description="Availability zone from list_relational_zones; narrows the result",
        ),
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> FlavorListData:
        """List the instance sizes available for one relational engine and version.

        Unlike the rest of the catalogue this is **not** a bare lookup: the API
        requires both `datastore_type` and `version` and answers 400 without
        them. An unrecognised pair is not an error either -- it returns an
        empty list -- so take both values from `list_relational_datastores`
        rather than composing them by hand.

        `zone_id` changes the answer, and **omitting it does not mean "all
        zones"** -- the API falls back to the platform default zone, so the
        result silently describes one zone only. Decide the zone first (see
        `list_relational_zones`) and pass it, or treat an unzoned result as
        "the default zone" rather than as the whole catalogue.
        """
        return await self._flavors(
            "list_relational_flavors",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["flavors"],
            datastore_type,
            version,
            zone_id,
            refresh,
            "list_relational_datastores",
        )

    async def list_relational_flavor_codes(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> FlavorCodeListData:
        """List the relational flavour codes (short keys behind the families)."""
        return await self._flavor_codes(
            "list_relational_flavor_codes",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["flavor_codes"],
            refresh,
        )

    async def list_relational_volume_types(
        self,
        zone_id: str | None = Field(None, description="Availability zone; narrows the result"),
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> VolumeTypeListData:
        """List the storage types available to relational instances.

        Each row carries the size range it accepts, which is what bounds the
        `volumeSize` of a create and of `resize_relational_instance_storage`.

        As with flavours, omitting `zone_id` describes the platform **default
        zone** rather than every zone -- storage types differ between zones.
        """
        return await self._volume_types(
            "list_relational_volume_types",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["volume_types"],
            zone_id,
            refresh,
        )

    async def list_relational_zones(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> ZoneListData:
        """List the availability zones relational instances can be placed in.

        The memory family has no equivalent endpoint; its zones come from the
        `zone_id` of each flavour instead.
        """
        raw = await self._fetch(
            "list_relational_zones",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["zones"],
            refresh,
        )
        items = [ZoneOption.from_api(row) for row in as_list(raw)]
        return ZoneListData(count=len(items), items=items)

    async def list_relational_networks(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> NetworkListData:
        """List the networks (VPCs) available to relational instances.

        A create needs a **subnet**, not a network -- use
        `list_relational_subnets` for that. This is the coarser view.
        """
        return await self._networks(
            "list_relational_networks",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["networks"],
            refresh,
        )

    async def list_relational_subnets(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> SubnetListData:
        """List the subnets a relational instance can be created in.

        The API answers with networks that *contain* subnets; this flattens
        them so each row carries the `subnet_id` a create expects plus the
        network that owns it. A network with no subnets yet contributes no
        rows, so an empty result means "no usable subnet", not "no network".
        """
        return await self._subnets(
            "list_relational_subnets",
            VDB_RELATIONAL_SERVICE,
            RELATIONAL_PATHS["subnets"],
            refresh,
        )

    # ------------------------------------------------------------------
    # memory family
    # ------------------------------------------------------------------

    async def list_memory_datastores(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> DatastoreListData:
        """List the MemoryStore (Redis) engine/version pairs the platform can provision.

        Start here when creating a Redis instance. The `type` and `version`
        values are what `list_memory_flavors` and the create tools expect, and
        the versions offered are not the ones a Redis user might assume -- take
        them from here rather than guessing.
        """
        return await self._datastores(
            "list_memory_datastores",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["datastores"],
            refresh,
        )

    async def list_memory_engines(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> EngineListData:
        """List the MemoryStore engine families and their licences."""
        return await self._engines(
            "list_memory_engines",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["engines"],
            refresh,
        )

    async def list_memory_instance_families(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> InstanceFamilyListData:
        """List the MemoryStore instance families."""
        return await self._instance_families(
            "list_memory_instance_families",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["instance_families"],
            refresh,
        )

    async def list_memory_flavors(
        self,
        datastore_type: str = Field(
            ..., description="Engine type from list_memory_datastores, e.g. 'redis'"
        ),
        version: str = Field(
            ..., description="Engine version from list_memory_datastores, e.g. '7.2'"
        ),
        zone_id: str | None = Field(None, description="Availability zone; narrows the result"),
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> FlavorListData:
        """List the instance sizes available for one MemoryStore engine and version.

        The API requires both `datastore_type` and `version` (400 without them)
        and returns an **empty list** for an unrecognised pair rather than an
        error, so take both from `list_memory_datastores`.

        Omitting `zone_id` describes the platform **default zone**, not every
        zone. The memory family has no zones endpoint; use
        `list_relational_zones` or the `zone_id` of a flavour row.
        """
        return await self._flavors(
            "list_memory_flavors",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["flavors"],
            datastore_type,
            version,
            zone_id,
            refresh,
            "list_memory_datastores",
        )

    async def list_memory_flavor_codes(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> FlavorCodeListData:
        """List the MemoryStore flavour codes."""
        return await self._flavor_codes(
            "list_memory_flavor_codes",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["flavor_codes"],
            refresh,
        )

    async def list_memory_volume_types(
        self,
        zone_id: str | None = Field(None, description="Availability zone; narrows the result"),
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> VolumeTypeListData:
        """List the storage types available to MemoryStore instances.

        Omitting `zone_id` describes the platform **default zone**, not every
        zone.
        """
        return await self._volume_types(
            "list_memory_volume_types",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["volume_types"],
            zone_id,
            refresh,
        )

    async def list_memory_networks(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> NetworkListData:
        """List the networks (VPCs) available to MemoryStore instances."""
        return await self._networks(
            "list_memory_networks",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["networks"],
            refresh,
        )

    async def list_memory_subnets(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> SubnetListData:
        """List the subnets a MemoryStore instance can be created in.

        Like the relational equivalent, the API answers with networks that
        contain subnets and this flattens them.
        """
        return await self._subnets(
            "list_memory_subnets",
            VDB_MEMORY_SERVICE,
            MEMORY_PATHS["subnets"],
            refresh,
        )

    # ------------------------------------------------------------------
    # PostgreSQL Cluster
    # ------------------------------------------------------------------

    async def list_postgresql_datastores(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> DatastoreListData:
        """List the PostgreSQL versions a cluster can be created with.

        Start here: `version` from a row is what a create's
        `datastoreVersion` takes, and it is also what bounds a restore -- a
        restore point records the version it was written by and cannot be
        restored onto another.

        The versions here are **not** the same set as
        `list_relational_datastores` reports for PostgreSQL: the two products
        track versions separately.
        """
        return await self._datastores(
            "list_postgresql_datastores",
            VDB_POSTGRESQL_SERVICE,
            POSTGRESQL_PATHS["datastores"],
            refresh,
        )

    async def list_postgresql_flavors(
        self,
        zone_id: str | None = Field(
            None,
            description=(
                "Availability zone from list_relational_zones. Omitting it describes the "
                "platform default zone (HCM03-1A), not every zone."
            ),
        ),
        multi_zone: bool = Field(
            False,
            description=(
                "List what suits a Multi-AZ cluster (the API's `multiZone=true`), answered for "
                "the Multi-AZ default zone, currently HCM03-1A. Cannot be combined with zone_id"
            ),
        ),
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> FlavorListData:
        """List the cluster flavours (node sizes) available in a zone.

        Unlike the relational and memory flavour catalogues this one needs **no**
        engine or version -- the family has one engine -- so it can be called
        bare. What it cannot do is describe more than one zone at a time, and
        the zones do not offer the same thing: measured on 2026-09-14,
        HCM03-1A and HCM03-1B each list 28 sizes while **HCM03-1C lists only
        15**, and the ids differ per zone even where the size is the same. So
        neither the ids nor the *availability* of a flavour carries across
        zones -- always pass the zone the cluster will live in, and re-list
        rather than reusing a row from another zone.

        A row's `id` ('pgp-...') is a create's `packageId`. The `ram_gb` and
        `vcpus` are **per node**, so the cluster's total is that times
        `numberOfNodes`.

        For a **Multi-AZ** cluster call with `multi_zone=True` and no
        `zone_id`: the API answers for the Multi-AZ default zone (HCM03-1A
        today; each row's `zone_id` says so), and that zone is the create's
        `locateZoneId`. The cluster builds each zone's nodes from that zone's
        own copy of the flavour, so the chosen size must also be listed --
        same `name` -- in every other zone the cluster will span; check them
        with `zone_id`.
        """
        params = _postgresql_catalogue_params(zone_id, multi_zone)
        raw = await self._fetch(
            "list_postgresql_flavors",
            VDB_POSTGRESQL_SERVICE,
            POSTGRESQL_PATHS["flavors"],
            refresh,
            params,
        )
        items = [FlavorOption.from_api(row) for row in as_list(raw)]
        return FlavorListData(
            count=len(items), items=items, zone_id=zone_id, multi_zone=multi_zone
        )

    async def list_postgresql_volume_types(
        self,
        zone_id: str | None = Field(
            None,
            description=(
                "Availability zone from list_relational_zones. Omitting it describes the "
                "platform default zone (HCM03-1A), not every zone."
            ),
        ),
        multi_zone: bool = Field(
            False,
            description=(
                "List what suits a Multi-AZ cluster (the API's `multiZone=true`), answered for "
                "the Multi-AZ default zone, currently HCM03-1A. Cannot be combined with zone_id"
            ),
        ),
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> VolumeTypeListData:
        """List the storage types available to PostgreSQL Clusters in a zone.

        A row's `id` ('pgst-...') is what a create's `volumeTypeId` and a
        `VOLUME-TYPE` resize take -- note that the relational create asks for
        the volume type's **name** instead, so the two are not interchangeable.
        `min_volume_size` / `max_volume_size` bound `volumeSize`.

        Like the flavours, both the ids and the selection differ per zone
        (measured: 5 types in HCM03-1A and HCM03-1B, 4 in HCM03-1C), and the
        three must agree: `packageId`, `volumeTypeId` and `locateZoneId` all
        from the same zone.

        For a **Multi-AZ** cluster call with `multi_zone=True` and no
        `zone_id`, exactly as for the flavours. Each zone's nodes are built
        from that zone's own copy of the volume type, so the chosen `type`
        must exist in every zone the cluster spans. As of 2026-10-01
        **HCM03-1C has no NVMe volume type**, which keeps it out of Multi-AZ
        clusters; check each zone with `zone_id` rather than assuming.
        """
        params = _postgresql_catalogue_params(zone_id, multi_zone)
        raw = await self._fetch(
            "list_postgresql_volume_types",
            VDB_POSTGRESQL_SERVICE,
            POSTGRESQL_PATHS["volume_types"],
            refresh,
            params,
        )
        # A bare array inside the envelope -- where the relational and memory
        # volume-type endpoints wrap theirs in `{projectId, data[]}`. Same
        # catalogue, three families, two shapes: measured, not assumed.
        items = [VolumeTypeOption.from_api(row) for row in as_list(raw)]
        return VolumeTypeListData(
            count=len(items), items=items, zone_id=zone_id, multi_zone=multi_zone
        )

    # ------------------------------------------------------------------
    # Kafka
    # ------------------------------------------------------------------

    async def get_kafka_limits(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> KafkaLimits:
        """Get the bounds and name rules the Kafka platform enforces.

        **Start here for anything Kafka.** This is the one endpoint in vDB
        where the platform publishes its own validation rules instead of
        answering a violation with an opaque error, and it carries things no
        other catalogue does:

        - `kafka_versions` — the versions a create may ask for. There is no
          datastore catalogue in this family; this is it.
        - `min_brokers` / `max_brokers` — **3 to 10**. A Kafka cluster needs a
          quorum, so 3 is the floor, unlike a PostgreSQL Cluster's 2.
        - `security_rule_ports` — the **only** ports a firewall rule may open.
        - the name regexes, which differ per resource: a configuration group
          may contain spaces, a topic may contain dots, a cluster may not.
        - `configs_forced_values` — broker settings the platform overrides
          whatever a configuration group says.

        Upstream every value is a **string**, and six of them are JSON encoded
        inside that string; both are decoded here.

        One bound is **not** in here: a topic's replication factor may not
        exceed the cluster's broker count. Read that from `get_kafka_cluster`.
        """
        raw = await self._fetch(
            "get_kafka_limits", VDB_KAFKA_SERVICE, KAFKA_PATHS["configs"], refresh
        )
        # This one IS enveloped -- it is among Kafka's nine wrapped operations --
        # so the map lives in `data`, not at the top level.
        payload = unwrap_wrapped(raw)
        return KafkaLimits.from_api(payload if isinstance(payload, dict) else {})

    async def list_kafka_flavors(
        self,
        datastore_type: str | None = Field(
            None,
            description="Optional engine filter; this family has one engine, so rarely needed",
        ),
        version: str | None = Field(None, description="Optional version filter"),
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> FlavorListData:
        """List the broker flavours a Kafka cluster can be built from.

        Unlike the relational and memory flavour catalogues, `type` and
        `version` are **optional** here — measured: calling bare returns the
        same 24 rows as passing them — so it can be called without a discovery
        step. There is no `zone_id` either: Kafka has no zones.

        A row's `id` is an **integer** and is not what a create takes. The
        create's `serverFlavorId` is the row's `flavor_id` (`flav-…`), which
        this projection reports separately. Sending the integer is the single
        easiest mistake to make in this family.

        `ram_gb` and `vcpus` are **per broker**, so a cluster's total is that
        times the broker count — and so is the bill.
        """
        params: dict[str, Any] = {}
        if datastore_type:
            params["type"] = datastore_type
        if version:
            params["version"] = version
        raw = await self._fetch(
            "list_kafka_flavors",
            VDB_KAFKA_SERVICE,
            KAFKA_PATHS["flavors"],
            refresh,
            params or None,
        )
        items = [FlavorOption.from_api(row) for row in as_list(raw)]
        return FlavorListData(count=len(items), items=items, zone_id=None)

    async def list_kafka_volume_types(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> VolumeTypeListData:
        """List the storage types a Kafka cluster can use.

        Wrapped in `{projectId, data[]}` like the relational and memory
        catalogues — the PostgreSQL Cluster one is the odd bare array.

        As with the flavours, the id a create takes is **not** the row's `id`:
        `kafkaStorageType` wants the row's `kafka_uuid` (`vtype-…`). The
        `type` name (`kafka.Gen2-NVMe2-IOPS3000`) is for display only.

        `min_volume_size` / `max_volume_size` bound `kafkaStorageSize`, and
        that size applies **per broker**.
        """
        raw = await self._fetch(
            "list_kafka_volume_types", VDB_KAFKA_SERVICE, KAFKA_PATHS["volume_types"], refresh
        )
        page = unwrap_nested_list(raw)
        items = [VolumeTypeOption.from_api(row) for row in page.items]
        return VolumeTypeListData(count=len(items), items=items, zone_id=None)

    async def list_kafka_flavor_codes(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> FlavorCodeListData:
        """List the platform codes behind Kafka's instance families."""
        return await self._flavor_codes(
            "list_kafka_flavor_codes", VDB_KAFKA_SERVICE, KAFKA_PATHS["codes"], refresh
        )

    async def list_kafka_instance_families(
        self,
        refresh: bool = Field(False, description="Bypass the cache and re-fetch"),
    ) -> InstanceFamilyListData:
        """List Kafka's instance families.

        Same two-kinds-of-row shape as the other families: a `group` of
        `family_custom` describes a zone group rather than a family, and
        carries no `key`.
        """
        return await self._instance_families(
            "list_kafka_instance_families",
            VDB_KAFKA_SERVICE,
            KAFKA_PATHS["instance_families"],
            refresh,
        )
