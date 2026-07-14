"""Resource discovery handler for GreenNode MCP Server (vServer reads)."""

from __future__ import annotations

from greennode.mcp_core.http import current_identity
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
from greennode.vks_mcp_server.tool_annotations import READ
from greennode.vks_mcp_server.validators import validate_id
from pydantic import Field


async def _require_project_id(
    config: VksConfig, client: VksClient, region: str | None = None
) -> str:
    """Return the project_id, auto-discovering it from vServer when not configured.

    project_id is **region-scoped**: each region exposes a different project via
    its own vServer endpoint. Resolution: for the default region, use the
    configured value (GRN_PROJECT_ID / credentials file) if present; otherwise
    fetch ``GET /v1/projects`` at that region's endpoint. Results are cached per
    region so later calls don't refetch.
    """
    resolved_region = region or config.default_region
    identity = current_identity()

    # The configured project_id (GRN_PROJECT_ID / credentials file) belongs to
    # the SERVICE ACCOUNT and its DEFAULT region only — a passthrough user must
    # never silently inherit it (their token resolves to their own project).
    if identity == "service" and resolved_region == config.default_region and config.project_id:
        return config.project_id
    cache_key = (identity, resolved_region)
    if cache_key in config.project_id_by_region:
        return config.project_id_by_region[cache_key]

    data = await client.vserver_get("/v1/projects", region=region)
    projects = _as_list(data, "projects")
    if not projects or not isinstance(projects[0], dict):
        raise ValueError(
            f"Could not determine project_id for region '{resolved_region}': vServer "
            "returned no project. Set GRN_PROJECT_ID or run 'grn configure'."
        )
    pid = projects[0].get("projectId")
    if not pid:
        raise ValueError("Could not determine project_id from the vServer response.")

    config.project_id_by_region[cache_key] = pid  # cache per (identity, region)
    if identity == "service" and resolved_region == config.default_region:
        config.project_id = pid  # populate the service default-region slot too
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


async def _fetch_all_items(
    client: VksClient,
    path: str,
    region: str | None = None,
    params: dict | None = None,
    page_size: int = 500,
) -> list:
    """Fetch every item from a vServer list endpoint, never truncating.

    vServer currently returns the full list in a single response — paging params
    are ignored and the envelope reports ``page=0 / pageSize=0 / totalPage=0``,
    with ``len(listData) == totalItem``. We rely on that fast path (one call).

    Defensive net: if a response ever reports more items than it returned
    (``totalItem > len(listData)`` — i.e. the backend starts enforcing paging),
    we re-fetch from page 1 with an explicit ``page``/``size`` and collect every
    page, so results are never silently cut off as an account grows.
    """
    first = await client.vserver_get(path, region=region, params=params or None)
    items = _as_list(first, "listData", "data")
    if not isinstance(first, dict):
        return items
    total = first.get("totalItem")
    if not isinstance(total, int) or len(items) >= total:
        return items

    # Backend truncated — page through explicitly (discard the partial first read).
    collected: list = []
    page = 1
    while True:
        paged = {**(params or {}), "page": page, "size": page_size}
        data = await client.vserver_get(path, region=region, params=paged)
        batch = _as_list(data, "listData", "data")
        collected.extend(batch)
        page_total = data.get("totalItem", total) if isinstance(data, dict) else total
        if not batch or len(collected) >= page_total:
            return collected
        page += 1


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
        raw = await _fetch_all_items(client, f"/v2/{pid}/networks", region=region)
        # Only ACTIVE VPCs can host new clusters/node groups.
        items = [v for v in raw if v.get("status") == "ACTIVE"]
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
        raw = await _fetch_all_items(client, f"/v2/{pid}/networks/{vpc_id}/subnets", region=region)
        # Only ACTIVE subnets can receive new nodes.
        items = [s for s in raw if s.get("status") == "ACTIVE"]
        return SubnetListData(
            vpc_id=vpc_id,
            subnets=[SubnetItem.from_api(s) for s in items],
        )

    key = ("list_subnets", resolved_region, pid, vpc_id)
    return await cache.get_or_fetch("list_subnets", key, fetch, refresh)


async def _locate_cluster(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    cluster_id: str,
    refresh: bool = False,
) -> tuple[str, str]:
    """Find which region hosts *cluster_id*; return ``(region, vpc_id)``.

    The VKS API is region-scoped, so the cluster is looked up in the default
    region first, then the remaining ones. Cached: a cluster never moves.
    """
    regions = [config.default_region] + [r for r in config.regions if r != config.default_region]

    async def fetch() -> tuple[str, str]:
        errors: list[str] = []
        for region in regions:
            try:
                data = await client.get(f"/v1/clusters/{cluster_id}", region=region)
            except RuntimeError as exc:
                errors.append(f"{region}: {exc}")
                continue
            vpc_id = data.get("vpcId", "") if isinstance(data, dict) else ""
            return (region, vpc_id)
        raise ValueError(
            f"Cluster '{cluster_id}' was not found in any region "
            f"({', '.join(regions)}). Check the id via list_clusters. "
            f"Details: {'; '.join(errors)}"
        )

    key = ("cluster_locate", cluster_id)
    return await cache.get_or_fetch("cluster_locate", key, fetch, refresh)


async def _resolve_zone_context(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    cluster_id: str,
    subnet_id: str,
    refresh: bool = False,
) -> tuple[str, str]:
    """Derive ``(region, zone_uuid)`` for zone-scoped discovery.

    The cluster pins the region and VPC; the chosen subnet (which must belong
    to that VPC) pins the availability zone. Doing this server-side means
    agents never juggle region/zone values across calls — they pass the two
    ids they already hold, and a mismatch is impossible.
    """
    validate_id(cluster_id, "cluster_id")
    validate_id(subnet_id, "subnet_id")
    region, vpc_id = await _locate_cluster(config, client, cache, cluster_id, refresh)
    if not vpc_id:
        raise ValueError(f"Cluster '{cluster_id}' reports no VPC — cannot derive a zone.")

    subnets = await _subnet_list(config, client, cache, vpc_id, region, refresh)
    subnet = next((s for s in subnets.subnets if s.id == subnet_id), None)
    if subnet is None:
        available = ", ".join(s.id for s in subnets.subnets) or "none"
        raise ValueError(
            f"Subnet '{subnet_id}' is not an ACTIVE subnet of cluster '{cluster_id}' "
            f"(VPC {vpc_id}). Pick one via list_subnets(vpc_id='{vpc_id}'); "
            f"available: {available}."
        )
    if subnet.zone is None or not subnet.zone.uuid:
        raise ValueError(
            f"Subnet '{subnet_id}' has no availability zone — it cannot host node-group "
            "resources; pick a different subnet via list_subnets."
        )
    return (region, subnet.zone.uuid)


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


def _flavor_available(f: dict) -> bool:
    """A flavor is usable only if not sold out and has remaining capacity."""
    if f.get("isSoldOut"):
        return False
    remaining = f.get("remainingVms")
    return remaining is None or remaining > 0


async def _flavor_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    zone: str,
    region: str | None = None,
    need: str | None = None,
    refresh: bool = False,
) -> FlavorListData:
    """Fetch available worker flavors for an availability zone (cached).

    Uses the same chain as the VKS portal's node-group flavor picker:
    (1) ``GET /flavor_zones/families`` -> family keys + their platform codes;
    (2) per family x code,
    ``GET /flavors/families/{family}/platforms/{code}/clusters/master/false?zoneId=``
    -> the cluster-worker flavors of that platform in the zone. (The old
    ``flavor_zones/customs/clusters`` chain returned [] for whole regions,
    e.g. HAN.) Sold-out flavors are excluded; each item's **id** is the
    ``flavorId``.
    """
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> FlavorListData:
        families = await client.vserver_get(f"/v1/{pid}/flavor_zones/families", region=region)
        by_id: dict[str, dict] = {}
        for family in families if isinstance(families, list) else []:
            family_key = family.get("key")
            codes = (family.get("condition") or {}).get("codes") or []
            if not family_key:
                continue
            for code in codes:
                try:
                    items = _as_list(
                        await client.vserver_get(
                            f"/v1/{pid}/flavors/families/{family_key}/platforms/{code}"
                            "/clusters/master/false",
                            region=region,
                            params={"zoneId": zone},
                        ),
                        "listData",
                        "data",
                        "flavors",
                    )
                except RuntimeError:
                    continue  # a platform code may not exist in this region
                for f in items:
                    fid = f.get("flavorId")
                    if fid:
                        by_id[fid] = f
        flavors = []
        for f in by_id.values():
            if not _flavor_available(f):
                continue
            group = _suggest_group(f)
            if need and group.lower() != need.lower():
                continue
            flavors.append(FlavorItem.from_api(f, group))
        return FlavorListData(region=resolved_region, zone=zone, need=need, flavors=flavors)

    # Normalize need for the key: the filter compares case-insensitively, so
    # "Dev/test" and "dev/test" must share a cache slot.
    key = ("list_flavors", resolved_region, pid, zone, need.lower() if need else None)
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
        items = await _fetch_all_items(
            client, f"/v2/{pid}/sshKeys", region=region, params={"page": 1, "size": 500}
        )
        return SshKeyListData(
            region=resolved_region, ssh_keys=[SshKeyItem.from_api(k) for k in items]
        )

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
        raw = await _fetch_all_items(client, f"/v2/{pid}/secgroups", region=region)
        # Only ACTIVE security groups can be attached to new nodes.
        items = [g for g in raw if g.get("status") == "ACTIVE"]
        return SecgroupListData(
            region=resolved_region, secgroups=[SecgroupItem.from_api(g) for g in items]
        )

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
        items = await _fetch_all_items(client, f"/v2/{pid}/serverGroups", region=region)
        return PlacementGroupListData(
            placement_groups=[PlacementGroupItem.from_api(g) for g in items]
        )

    key = ("list_placement_groups", resolved_region, pid)
    return await cache.get_or_fetch("list_placement_groups", key, fetch, refresh)


# Node groups use NVME disks; SSD volume types are not offered via this tool.
_VOLUME_TYPE_NAME = "NVME"


async def _volumetype_list(
    config: VksConfig,
    client: VksClient,
    cache: DiscoveryCache,
    zone: str,
    region: str | None = None,
    refresh: bool = False,
) -> VolumeTypeListData:
    """Fetch the NVME volume types available in an availability zone (cached).

    Two-step vServer flow, both internal: (1) ``GET /volume_type_zones?zoneId=<zone>``
    → pick the entry named NVME to get its volume-type-zone id; (2)
    ``GET /{that_id}/volume_types`` → the IOPS tiers. *zone* is the subnet's
    ``zone.uuid`` (from list_subnets). Each item's **id** is the ``diskType``.
    """
    pid = await _require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> VolumeTypeListData:
        zones_data = await client.vserver_get(
            f"/v1/{pid}/volume_type_zones", region=region, params={"zoneId": zone}
        )
        zones = _as_list(zones_data, "volumeTypeZones", "data", "listData")
        nvme = next((z for z in zones if z.get("name", "").upper() == _VOLUME_TYPE_NAME), None)
        volume_types: list[VolumeTypeItem] = []
        if nvme and nvme.get("id"):
            vt_data = await client.vserver_get(
                f"/v1/{pid}/{nvme['id']}/volume_types", region=region
            )
            volume_types = [
                VolumeTypeItem.from_api(vt)
                for vt in _as_list(vt_data, "volumeTypes", "data", "listData")
            ]
        return VolumeTypeListData(region=resolved_region, zone=zone, volume_types=volume_types)

    key = ("list_volume_types", resolved_region, pid, zone)
    return await cache.get_or_fetch("list_volume_types", key, fetch, refresh)


async def _quota_get(client: VksClient, region: str | None = None) -> QuotaData:
    """Fetch the VKS quota for the current user (not cached: changes after creates)."""
    data = await client.get("/v1/quota", region=region)
    resolved_region = region or client._config.default_region
    return QuotaData.from_api(data if isinstance(data, dict) else {}, region=resolved_region)


class DiscoveryHandler:
    """Register and serve read-only vServer resource-discovery MCP tools."""

    def __init__(self, mcp, config: VksConfig, client: VksClient, cache: DiscoveryCache):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        self.mcp.tool(name="list_vpcs", annotations=READ)(self.list_vpcs)
        self.mcp.tool(name="list_subnets", annotations=READ)(self.list_subnets)
        self.mcp.tool(name="list_flavors", annotations=READ)(self.list_flavors)
        self.mcp.tool(name="list_ssh_keys", annotations=READ)(self.list_ssh_keys)
        self.mcp.tool(name="list_security_groups", annotations=READ)(self.list_security_groups)
        self.mcp.tool(name="list_volume_types", annotations=READ)(self.list_volume_types)
        self.mcp.tool(name="list_placement_groups", annotations=READ)(self.list_placement_groups)
        self.mcp.tool(name="get_quota", annotations=READ)(self.get_quota)

    async def list_vpcs(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Region to list VPCs from ('HCM-3' or 'HAN'); defaults to the configured "
                "region. IMPORTANT: a cluster lives in the same region as its VPC — use "
                "the region the user wants the cluster in."
            ),
        ),
        refresh: bool = Field(
            False,
            description=(
                "Bypass the short-lived cache and refetch from vServer. Set true after "
                "the user creates or deletes a VPC in the console mid-conversation."
            ),
        ),
    ) -> VpcListData:
        """List ACTIVE VPCs (networks) in the project.

        Returns minimal items {id, name, enabled_dns}; non-ACTIVE VPCs are
        excluded because they cannot host new clusters or node groups.

        ## Workflow
        - create_cluster flow, step 1: present this list to the user and let them
          choose. IMPORTANT: do NOT pick a VPC silently when more than one exists.
        - azStrategy=MULTI clusters require a vDNS-enabled VPC: offer only items
          with `enabled_dns=true` (none qualifying -> stop and tell the user to
          enable vDNS on a VPC in the console first).
        - Use the chosen `id` as `vpcId` in create_cluster, and as `vpc_id` in
          list_subnets to enumerate its subnets.
        """
        return await _vpc_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def list_subnets(
        self,
        vpc_id: str = Field(
            ...,
            description=(
                "VPC/network ID to enumerate. For create_cluster: the id the user chose "
                "from list_vpcs. For create_nodegroup: the cluster's own VPC — get it "
                "from get_cluster(cluster_id).vpc_id, do not ask the user for it."
            ),
        ),
        region: Region = Field(
            "HCM-3",
            description=(
                "Region ('HCM-3' or 'HAN'); defaults to 'HCM-3'. "
                "IMPORTANT: when working against an existing cluster, pass that "
                "cluster's region."
            ),
        ),
        refresh: bool = Field(
            False,
            description=(
                "Bypass the short-lived cache and refetch from vServer. Set true after "
                "the user creates or deletes a subnet in the console mid-conversation."
            ),
        ),
    ) -> SubnetListData:
        """List ACTIVE subnets of a VPC.

        Returns minimal items {id, name, zone, secondary_subnets}; non-ACTIVE
        subnets are excluded because nodes cannot join them.

        ## Workflow
        - create_nodegroup flow, step 1: call get_cluster first for the cluster's
          vpc_id, then present this list to the user and let them choose.
          IMPORTANT: do NOT pick a subnet silently when more than one exists.
        - The chosen subnet's `zone.uuid` scopes the next steps: pass it to
          list_flavors and list_volume_types (flavors/disk types differ per zone).
        - Use the chosen `id` as `subnetId`; use its `secondary_subnets` as
          `secondarySubnets` when networkType is CILIUM_NATIVE_ROUTING.
        """
        return await _subnet_list(
            self.config, self.client, self.cache, vpc_id=vpc_id, region=region, refresh=refresh
        )

    async def list_flavors(
        self,
        cluster_id: str = Field(
            ...,
            description=(
                "VKS cluster the node group will belong to. Its region and VPC "
                "anchor the lookup — the tool locates the cluster itself, so no "
                "region parameter is needed."
            ),
        ),
        subnet_id: str = Field(
            ...,
            description=(
                "Subnet the user chose via list_subnets (must belong to the "
                "cluster's VPC). Its availability zone scopes the flavors — the "
                "tool derives the zone itself."
            ),
        ),
        need: str | None = Field(
            None,
            description=(
                "Optional deployment-need filter: Dev/test, Cân bằng, Compute, "
                "RAM cao, AI/GPU. Omit to list all, then help the user pick by vCPU/RAM."
            ),
        ),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and refetch from vServer.",
        ),
    ) -> FlavorListData:
        """List worker flavors (node sizes) available to a cluster's chosen subnet.

        Returns {region, zone, need, flavors[{id, name, vcpu, ram_gb, gpu, group}]};
        sold-out flavors are excluded. Each flavor is tagged with a suggested
        deployment-need `group`. Region and availability zone are derived from
        the cluster and subnet server-side — a mismatch is impossible.

        ## Workflow
        - create_nodegroup flow, after the user picks a subnet in list_subnets:
          call this with the cluster id and that subnet id, present flavors
          (by vCPU/RAM, optionally filtered by `need`), and let the user choose.
          IMPORTANT: do NOT pick a flavor silently.
        - Use the chosen flavor's `id` as `flavorId` in create_nodegroup.
        """
        resolved_region, zone = await _resolve_zone_context(
            self.config, self.client, self.cache, cluster_id, subnet_id, refresh
        )
        return await _flavor_list(
            self.config,
            self.client,
            self.cache,
            zone=zone,
            region=resolved_region,
            need=need,
            refresh=refresh,
        )

    async def list_ssh_keys(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Which region to list SSH keys from ('HCM-3' or 'HAN'). "
                "IMPORTANT: SSH keys are region-scoped. If the user names a region, "
                "pass it here. In a cluster/node-group flow, pass the cluster's region "
                "(from get_cluster). Omit only when neither is known — it then defaults "
                "to 'HCM-3', and the response's `region` field says which."
            ),
        ),
        refresh: bool = Field(
            False,
            description=(
                "Bypass the short-lived cache and refetch from vServer. Set true after "
                "the user creates an SSH key in the console mid-conversation."
            ),
        ),
    ) -> SshKeyListData:
        """List SSH keys in the project, for one region.

        Returns {region, ssh_keys[{id, name}]}. The `region` field echoes which
        region was queried — check it matches what the user wanted before using
        the keys (a wrong/empty result usually means the wrong region).

        ## Workflow
        - create_nodegroup flow: present this list and let the user choose (VKS
          uses one key per node group). IMPORTANT: do NOT pick a key silently.
        - If empty, the user has no key in this region — tell them to create one
          in the GreenNode console (vServer -> SSH Keys), then retry with
          refresh=true. Do NOT invent an id.
        - Use the chosen `id` as `sshKeyId` in create_nodegroup.
        """
        return await _sshkey_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def list_security_groups(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Which region to list security groups from ('HCM-3' or 'HAN'). "
                "IMPORTANT: security groups are region-scoped. If the user names a "
                "region, pass it; in a cluster/node-group flow, pass the cluster's "
                "region (from get_cluster). Defaults to 'HCM-3'."
            ),
        ),
        refresh: bool = Field(
            False,
            description=(
                "Bypass the short-lived cache and refetch from vServer. Set true after "
                "the user creates a security group in the console mid-conversation."
            ),
        ),
    ) -> SecgroupListData:
        """List ACTIVE security groups in the project, for one region.

        Returns {region, secgroups[{id, name}]}; non-ACTIVE groups are excluded.
        The `region` field echoes which region was queried — check it matches
        what the user wanted before using the ids.

        ## Workflow
        - create_nodegroup flow (optional): security groups are optional on a node
          group. If the user wants them, present this list and let them choose one
          or more. IMPORTANT: do NOT pick silently.
        - Use the chosen `id`(s) in the `securityGroups` array of create_nodegroup.
        """
        return await _secgroup_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def list_volume_types(
        self,
        cluster_id: str = Field(
            ...,
            description=(
                "VKS cluster the node group will belong to. Its region and VPC "
                "anchor the lookup — the tool locates the cluster itself, so no "
                "region parameter is needed."
            ),
        ),
        subnet_id: str = Field(
            ...,
            description=(
                "Subnet the user chose via list_subnets (must belong to the "
                "cluster's VPC). Its availability zone scopes the volume types — "
                "the tool derives the zone itself."
            ),
        ),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and refetch from vServer.",
        ),
    ) -> VolumeTypeListData:
        """List NVME volume types (disk types) available to a cluster's chosen subnet.

        Returns {region, zone, volume_types[{id, iops}]}. Node groups use NVME
        disks; region and availability zone are derived from the cluster and
        subnet server-side — a mismatch is impossible. The user picks by **IOPS**.

        ## Workflow
        - create_nodegroup flow, after the user picks a subnet in list_subnets:
          call this with the cluster id and that subnet id, present the IOPS
          options, and let the user choose. IMPORTANT: do NOT pick an IOPS tier
          silently.
        - Use the chosen item's `id` as `diskType` in create_nodegroup.
        """
        resolved_region, zone = await _resolve_zone_context(
            self.config, self.client, self.cache, cluster_id, subnet_id, refresh
        )
        return await _volumetype_list(
            self.config,
            self.client,
            self.cache,
            zone=zone,
            region=resolved_region,
            refresh=refresh,
        )

    async def list_placement_groups(
        self,
        region: Region = Field(
            "HCM-3",
            description="Region ('HCM-3' or 'HAN'); defaults to 'HCM-3'.",
        ),
        refresh: bool = Field(
            False,
            description=(
                "Bypass the short-lived cache and refetch from vServer. Set true after "
                "the user creates a placement group in the console mid-conversation."
            ),
        ),
    ) -> PlacementGroupListData:
        """List placement groups (vServer server groups) in the project.

        Returns minimal items {id, name}. Only needed for the advanced
        create_nodegroup option `placementGroupConfigDto` with type=EXISTING.

        ## Workflow
        - Optional step, only when the user wants to place nodes in an EXISTING
          placement group. Present this list and let them choose.
          IMPORTANT: do NOT pick a placement group silently.
        - Use the chosen `id` as `placementGroupConfigDto.placementGroupId`
          (with `type="EXISTING"`). For a brand-new group, skip this tool and use
          `type="NEW"` + a name instead.
        """
        return await _placementgroup_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def get_quota(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Which region's quota to check ('HCM-3' or 'HAN'). "
                "IMPORTANT: quota is per-region. Pass the region the cluster/node "
                "group will be created in. Defaults to 'HCM-3'."
            ),
        ),
    ) -> QuotaData:
        """Get the current user's VKS quota for one region.

        Returns {region, max_clusters, num_clusters, max_node_groups_per_cluster,
        max_nodes_per_node_group}. The `region` field echoes which region was
        queried.

        ## Workflow
        - Call before create_cluster / create_nodegroup to catch a full quota
          early (e.g. num_clusters == max_clusters) instead of failing mid-create.
        """
        return await _quota_get(self.client, region=region)
