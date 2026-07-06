"""Resource discovery handler for GreenNode MCP Server (vServer reads)."""

from __future__ import annotations

from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import Region, VksConfig
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.models import (
    FlavorItem,
    FlavorListData,
    PlacementGroupItem,
    PlacementGroupListData,
    QuotaData,
    SecgroupItem,
    SecgroupListData,
    SshKeyItem,
    SshKeyListData,
    SubnetItem,
    SubnetListData,
    VolumeTypeItem,
    VolumeTypeListData,
    VpcItem,
    VpcListData,
)
from greennode.vks_mcp_server.validators import validate_id
from pydantic import Field


async def _require_project_id(
    config: VksConfig, client: VksClient, region: str | None = None
) -> str:
    """Return the project_id, auto-discovering it from vServer when not configured.

    Resolution order: configured value (GRN_PROJECT_ID / credentials file) first;
    otherwise fetch it from vServer ``GET /v1/projects``. Each user has exactly one
    project, so the single returned project is used and cached on the config so
    later tool calls don't refetch.
    """
    if config.project_id:
        return config.project_id

    data = await client.vserver_get("/v1/projects", region=region)
    projects = _as_list(data, "projects")
    if not projects or not isinstance(projects[0], dict):
        raise ValueError(
            "Could not determine project_id: vServer returned no project. "
            "Set GRN_PROJECT_ID or run 'grn configure'."
        )
    pid = projects[0].get("projectId")
    if not pid:
        raise ValueError("Could not determine project_id from the vServer response.")

    config.project_id = pid  # cache for subsequent tool calls
    return pid


def _as_list(data, *wrapper_keys):
    """Normalise a vServer response to a list.

    Accepts a bare array, or a dict wrapping the array under one of
    *wrapper_keys* (e.g. 'listData').
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in wrapper_keys:
            if isinstance(data.get(key), list):
                return data[key]
    return []


async def _vpc_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    region: str | None = None,
    refresh: bool = False,
) -> VpcListData:
    """Fetch VPCs/networks as structured data (cached)."""
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> VpcListData:
        data = await client.vserver_get(f"/v2/{pid}/networks", region=region)
        items = _as_list(data, "listData")
        return VpcListData(
            region=resolved_region,
            vpcs=[VpcItem.from_api(v) for v in items],
        )

    key = ("list_vpcs", resolved_region, pid)
    return await cache.get_or_fetch("list_vpcs", key, fetch, refresh)


async def _subnet_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    vpc_id: str,
    region: str | None = None,
    refresh: bool = False,
) -> SubnetListData:
    """Fetch subnets of a VPC as structured data (cached)."""
    validate_id(vpc_id, "vpc_id")
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> SubnetListData:
        data = await client.vserver_get(f"/v2/{pid}/networks/{vpc_id}/subnets", region=region)
        items = _as_list(data, "listData")
        return SubnetListData(
            vpc_id=vpc_id,
            subnets=[SubnetItem.from_api(s) for s in items],
        )

    key = ("list_subnets", resolved_region, pid, vpc_id)
    return await cache.get_or_fetch("list_subnets", key, fetch, refresh)


def _suggest_group(flavor: dict) -> str:
    """Classify a flavor into a deployment-need group."""
    cpu = float(flavor.get("cpu") or 0)
    memory = float(flavor.get("memory") or 0)
    gpu = float(flavor.get("gpu") or 0)
    if gpu > 0:
        return "AI/GPU"
    if cpu and memory / cpu >= 6:
        return "RAM cao"
    if cpu >= 8:
        return "Compute"
    if cpu <= 2:
        return "Dev/test"
    return "Cân bằng"


async def _flavor_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    region: str | None = None,
    need: str | None = None,
    refresh: bool = False,
) -> FlavorListData:
    """Fetch cluster flavors as structured data, optionally filtered by deployment-need group (cached)."""
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> FlavorListData:
        data = await client.vserver_get(f"/v1/{pid}/flavors/customs/clusters", region=region)
        items = _as_list(data, "listData")
        flavors = []
        for f in items:
            group = _suggest_group(f)
            if need and group.lower() != need.lower():
                continue
            flavors.append(FlavorItem.from_api(f, group))
        return FlavorListData(need=need, flavors=flavors)

    # Normalize need for the key: the filter compares case-insensitively, so
    # "Dev/test" and "dev/test" must share a cache slot.
    key = ("list_flavors", resolved_region, pid, need.lower() if need else None)
    return await cache.get_or_fetch("list_flavors", key, fetch, refresh)


async def _sshkey_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    region: str | None = None,
    refresh: bool = False,
) -> SshKeyListData:
    """Fetch SSH keys as structured data (cached)."""
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> SshKeyListData:
        data = await client.vserver_get(
            f"/v2/{pid}/sshKeys", region=region, params={"page": 1, "size": 100}
        )
        items = _as_list(data, "listData")
        return SshKeyListData(ssh_keys=[SshKeyItem.from_api(k) for k in items])

    key = ("list_ssh_keys", resolved_region, pid)
    return await cache.get_or_fetch("list_ssh_keys", key, fetch, refresh)


async def _secgroup_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    region: str | None = None,
    refresh: bool = False,
) -> SecgroupListData:
    """Fetch security groups as structured data (cached)."""
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> SecgroupListData:
        data = await client.vserver_get(f"/v2/{pid}/secgroups", region=region)
        items = _as_list(data, "listData")
        return SecgroupListData(secgroups=[SecgroupItem.from_api(g) for g in items])

    key = ("list_security_groups", resolved_region, pid)
    return await cache.get_or_fetch("list_security_groups", key, fetch, refresh)


async def _placementgroup_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    region: str | None = None,
    refresh: bool = False,
) -> PlacementGroupListData:
    """Fetch placement groups (vServer server groups) as structured data (cached).

    The group **uuid** is the ``placementGroupId`` for node-group creation with
    ``placementGroupConfigDto.type = EXISTING``.
    """
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> PlacementGroupListData:
        data = await client.vserver_get(f"/v2/{pid}/serverGroups", region=region)
        items = _as_list(data, "listData", "data")
        return PlacementGroupListData(
            placement_groups=[PlacementGroupItem.from_api(g) for g in items]
        )

    key = ("list_placement_groups", resolved_region, pid)
    return await cache.get_or_fetch("list_placement_groups", key, fetch, refresh)


async def _volumetype_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    zone_id: str | None = None,
    type_name: str | None = None,
    region: str | None = None,
    refresh: bool = False,
) -> VolumeTypeListData:
    """Fetch volume types as structured data (cached).

    Two-step vServer flow: list volume-type zones (optionally scoped to *zone_id*),
    then fetch the volume types of each zone (or only the zone matching *type_name*,
    case-insensitively). The volume type **id** is the ``diskType`` value for
    cluster/node-group creation.
    """
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> VolumeTypeListData:
        params = {"zoneId": zone_id} if zone_id else None
        zones_data = await client.vserver_get(
            f"/v1/{pid}/volume_type_zones", region=region, params=params
        )
        zones = _as_list(zones_data, "volumeTypeZones", "data", "listData")
        if type_name:
            zones = [z for z in zones if z.get("name", "").lower() == type_name.lower()]

        volume_types: list[VolumeTypeItem] = []
        for zone in zones:
            zone_uuid = zone.get("id", "")
            if not zone_uuid:
                continue
            vt_data = await client.vserver_get(
                f"/v1/{pid}/{zone_uuid}/volume_types", region=region
            )
            for vt in _as_list(vt_data, "volumeTypes", "data", "listData"):
                volume_types.append(VolumeTypeItem.from_api(vt, type_zone=zone.get("name", "")))

        return VolumeTypeListData(zone_id=zone_id, volume_types=volume_types)

    key = (
        "list_volume_types",
        resolved_region,
        pid,
        zone_id,
        type_name.lower() if type_name else None,
    )
    return await cache.get_or_fetch("list_volume_types", key, fetch, refresh)


async def _quota_get(client: VksClient, region: str | None = None) -> QuotaData:
    """Fetch the VKS quota for the current user (not cached: changes after creates)."""
    data = await client.get("/v1/quota", region=region)
    return QuotaData.from_api(data if isinstance(data, dict) else {})


class DiscoveryHandler:
    """Register and serve read-only vServer resource-discovery MCP tools."""

    def __init__(self, mcp, config: VksConfig, client: VksClient, cache: DiscoveryCache):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        self.mcp.tool(name="list_vpcs")(self.list_vpcs)
        self.mcp.tool(name="list_subnets")(self.list_subnets)
        self.mcp.tool(name="list_flavors")(self.list_flavors)
        self.mcp.tool(name="list_ssh_keys")(self.list_ssh_keys)
        self.mcp.tool(name="list_security_groups")(self.list_security_groups)
        self.mcp.tool(name="list_volume_types")(self.list_volume_types)
        self.mcp.tool(name="list_placement_groups")(self.list_placement_groups)
        self.mcp.tool(name="get_quota")(self.get_quota)

    async def list_vpcs(
        self,
        region: Region | None = Field(None, description="Region override"),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from vServer (use after creating a resource in the console).",
        ),
    ) -> VpcListData:
        """List VPCs (networks) in the project. Use the ID as `vpcId` when creating a cluster."""
        return await _vpc_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def list_subnets(
        self,
        vpc_id: str = Field(..., description="VPC/network ID (from list_vpcs)"),
        region: Region | None = Field(None, description="Region override"),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from vServer (use after creating a resource in the console).",
        ),
    ) -> SubnetListData:
        """List subnets of a VPC. Use the ID as `subnetId` when creating a cluster."""
        return await _subnet_list(
            self.config, self.client, self.cache, vpc_id=vpc_id, region=region, refresh=refresh
        )

    async def list_flavors(
        self,
        need: str | None = Field(
            None,
            description="Filter by deployment need group: Dev/test, Cân bằng, Compute, RAM cao, AI/GPU",
        ),
        region: Region | None = Field(None, description="Region override"),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from vServer (use after creating a resource in the console).",
        ),
    ) -> FlavorListData:
        """List cluster flavors, each tagged with a suggested deployment-need group (optionally filtered by `need`). Use the ID as `flavorId`."""
        return await _flavor_list(
            self.config, self.client, self.cache, region=region, need=need, refresh=refresh
        )

    async def list_ssh_keys(
        self,
        region: Region | None = Field(None, description="Region override"),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from vServer (use after creating a resource in the console).",
        ),
    ) -> SshKeyListData:
        """List SSH keys in the project. Use the ID as `sshKeyId` when creating a node group."""
        return await _sshkey_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def list_security_groups(
        self,
        region: Region | None = Field(None, description="Region override"),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from vServer (use after creating a resource in the console).",
        ),
    ) -> SecgroupListData:
        """List security groups. Use IDs in `securityGroups` when creating a node group."""
        return await _secgroup_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def list_volume_types(
        self,
        zone_id: str | None = Field(
            None, description="Availability-zone ID filter (omit to list all zones)"
        ),
        type_name: str | None = Field(
            None, description="Volume-type-zone name filter, e.g. 'SSD' or 'NVMe'"
        ),
        region: Region | None = Field(None, description="Region override"),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from vServer (use after creating a resource in the console).",
        ),
    ) -> VolumeTypeListData:
        """List volume types. Use the ID as `diskType` when creating a cluster/node group."""
        return await _volumetype_list(
            self.config,
            self.client,
            self.cache,
            zone_id=zone_id,
            type_name=type_name,
            region=region,
            refresh=refresh,
        )

    async def list_placement_groups(
        self,
        region: Region | None = Field(None, description="Region override"),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from vServer (use after creating a resource in the console).",
        ),
    ) -> PlacementGroupListData:
        """List placement groups (server groups). Use the ID as `placementGroupId` with placementGroupConfigDto type=EXISTING."""
        return await _placementgroup_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def get_quota(
        self,
        region: Region | None = Field(None, description="Region override"),
    ) -> QuotaData:
        """Get the VKS quota for the current user (max/used clusters, node groups, nodes). Check before creating."""
        return await _quota_get(self.client, region=region)
