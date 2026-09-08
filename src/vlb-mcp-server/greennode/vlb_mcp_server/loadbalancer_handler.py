"""Load balancer lifecycle: CRUD plus the RPC-style resize/scale/bandwidth/clone actions.

Two things shape every tool here:

- **Writes are asynchronous.** Deletes and rebalancing answer ``202`` with no
  body; the rest answer ``200`` with an untyped map. Nothing here indexes into
  a write response, and every write docstring routes the caller to
  ``get_load_balancer_status``.
- **``/loadBalancers/`` has static children** (``packages``, ``all_packages``,
  ``headers``, ``subnet``) that collide with ``{loadBalancerId}``, so every id
  goes through ``validate_lb_id`` rather than the generic ``validate_id``.
"""

from __future__ import annotations

import asyncio
from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import Region, VlbConfig
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.guards import require_write
from greennode.vlb_mcp_server.models import (
    BandwidthData,
    CloneLoadBalancerDto,
    CloneMetadataData,
    CreatedResourceData,
    CreateLoadBalancerDto,
    LoadBalancerItem,
    LoadBalancerListData,
    LoadBalancerStatusData,
    LoadBalancerSubnetListData,
    ResizeLoadBalancerDto,
    ScaleConfigDto,
    ScaleHistoryItem,
    ScaleHistoryListData,
    UpdateBandwidthPackageDto,
    ValidationResult,
)
from greennode.vlb_mcp_server.paging import (
    DEFAULT_PAGE_SIZE,
    as_list,
    fetch_list,
    lb_list_params,
    total_items,
    unwrap_one,
)
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vlb_mcp_server.validators import validate_id, validate_lb_id
from pydantic import Field


# Which listener protocols each load balancer type can terminate. The API
# rejects a mismatch with a single unlabelled Java validation message, so
# validate_load_balancer_create catches it locally instead.
_PROTOCOLS_BY_TYPE = {
    "Layer 4": {"TCP", "UDP"},
    "Layer 7": {"HTTP", "HTTPS"},
}


def _validate_create_body(body: CreateLoadBalancerDto) -> ValidationResult:
    """Check a create body against the rules the API enforces but does not explain."""
    errors: list[str] = []
    warnings: list[str] = [
        "A load balancer cannot be renamed — there is no update endpoint for its name. "
        f"Confirm '{body.name}' with the user before creating.",
    ]

    listener = body.listener
    if listener is not None:
        allowed = _PROTOCOLS_BY_TYPE[body.type]
        if listener.listenerProtocol not in allowed:
            errors.append(
                f"A '{body.type}' load balancer cannot terminate "
                f"{listener.listenerProtocol}; allowed: {sorted(allowed)}."
            )
        if listener.listenerProtocol == "HTTPS" and not listener.defaultCertificateAuthority:
            errors.append(
                "An HTTPS listener needs defaultCertificateAuthority. Import or pick one "
                "with import_certificate / list_certificates first."
            )
        if listener.listenerProtocolPort is None:
            warnings.append(
                "listenerProtocolPort is unset; the platform will pick a default rather "
                "than the port you expect."
            )

    pool = body.pool
    if pool is not None:
        if body.type == "Layer 4" and pool.poolProtocol == "HTTP":
            errors.append(
                "A 'Layer 4' load balancer cannot use an HTTP pool; use TCP, UDP or PROXY."
            )
        if pool.healthMonitor is None:
            warnings.append(
                "The pool has no healthMonitor. One is required by the standalone "
                "create_pool endpoint and cannot be added later except through update_pool."
            )

    if body.scheme == "Internal":
        warnings.append(
            "scheme='Internal' means no public address is assigned — the load balancer "
            "is reachable only from inside the VPC."
        )
    if body.autoScalable:
        warnings.append(
            "autoScalable=true only enables autoscaling; set the node bounds afterwards "
            "with configure_load_balancer_scaling."
        )

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


class LoadBalancerHandler:
    """Register and serve the load balancer MCP tools."""

    def __init__(
        self,
        mcp,
        config: VlbConfig,
        client: VlbClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_load_balancers", annotations=READ)(self.list_load_balancers)
        self.mcp.tool(name="get_load_balancer", annotations=READ)(self.get_load_balancer)
        self.mcp.tool(name="list_load_balancers_by_subnet", annotations=READ)(
            self.list_load_balancers_by_subnet
        )
        self.mcp.tool(name="list_load_balancer_scale_history", annotations=READ)(
            self.list_load_balancer_scale_history
        )
        self.mcp.tool(name="get_load_balancer_clone_metadata", annotations=READ)(
            self.get_load_balancer_clone_metadata
        )
        self.mcp.tool(name="get_load_balancer_status", annotations=READ)(
            self.get_load_balancer_status
        )
        self.mcp.tool(name="validate_load_balancer_create", annotations=READ)(
            self.validate_load_balancer_create
        )

        if self.allow_write:
            self.mcp.tool(name="create_load_balancer", annotations=WRITE)(
                self.create_load_balancer
            )
            self.mcp.tool(name="delete_load_balancer", annotations=DESTRUCTIVE)(
                self.delete_load_balancer
            )
            self.mcp.tool(name="resize_load_balancer", annotations=WRITE)(
                self.resize_load_balancer
            )
            self.mcp.tool(name="configure_load_balancer_scaling", annotations=WRITE)(
                self.configure_load_balancer_scaling
            )
            self.mcp.tool(name="update_load_balancer_bandwidth", annotations=WRITE)(
                self.update_load_balancer_bandwidth
            )
            self.mcp.tool(name="create_load_balancer_clone", annotations=WRITE)(
                self.create_load_balancer_clone
            )

    # --- reads ----------------------------------------------------------------

    async def list_load_balancers(
        self,
        name: str | None = Field(None, description="Filter by name (server-side, best effort)."),
        status: str | None = Field(None, description="Filter by status, e.g. 'ACTIVE'."),
        subnet_id: str | None = Field(None, description="Filter by subnet id."),
        vpc_id: str | None = Field(None, description="Filter by VPC id."),
        zone_id: str | None = Field(None, description="Filter by availability zone."),
        page: int = Field(1, ge=1, description="Page number (1-based)."),
        size: int = Field(DEFAULT_PAGE_SIZE, ge=1, description="Page size."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vLB."),
    ) -> LoadBalancerListData:
        """List the load balancers in the project.

        Returns {region, load_balancers[{id, name, display_status, scheme, type,
        address, ...}], total_item, page, page_size, filter_applied}.

        ## Workflow
        - Check `filter_applied`: when it is false the gateway ignored the
          `name` filter, so filter the returned list yourself before answering —
          do not claim a match count the server did not produce.
        - `progress_status` on an entry means a write is still in flight there.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region
        path = f"/v2/{pid}/loadBalancers"
        params = lb_list_params(
            name=name,
            status=status,
            subnet_id=subnet_id,
            vpc_id=vpc_id,
            zone_id=zone_id,
            page=page,
            size=size,
        )

        async def fetch() -> LoadBalancerListData:
            # fetch_list walks the remaining pages when the gateway reports more
            # than it returned, and hands back the first envelope so page /
            # pageSize / totalItem cost no extra request.
            items, envelope = await fetch_list(
                self.client, path, region=region, params=params, page_size=size
            )
            total = total_items(envelope)
            load_balancers = [
                LoadBalancerItem.from_api(lb) for lb in items if isinstance(lb, dict)
            ]
            applied = True
            if name:
                applied = all(name.lower() in lb.name.lower() for lb in load_balancers)
            return LoadBalancerListData(
                region=resolved_region,
                load_balancers=load_balancers,
                total_item=total,
                page=envelope.get("page"),
                page_size=envelope.get("pageSize"),
                filter_applied=applied,
            )

        key = ("list_load_balancers", resolved_region, pid, tuple(sorted(params.items())))
        return await self.cache.get_or_fetch("list_load_balancers", key, fetch, refresh)

    async def get_load_balancer(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> LoadBalancerItem:
        """Get one load balancer by ID.

        Returns {id, name, display_status, progress_status, scheme, type,
        address, subnet_id, package, nodes, ...}. Not cached — this is what a
        poll after a write reads.
        """
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/loadBalancers/{load_balancer_id}", region=region)
        return LoadBalancerItem.from_api(unwrap_one(data))

    async def list_load_balancers_by_subnet(
        self,
        subnet_id: str = Field(..., description="Subnet ID from list_subnets."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vLB."),
    ) -> LoadBalancerSubnetListData:
        """List the load balancers placed on one subnet.

        Returns {region, subnet_id, load_balancers[...]}. Useful before deleting
        a subnet, or to find the load balancer fronting a known network.
        """
        validate_id(subnet_id, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> LoadBalancerSubnetListData:
            raw = await self.client.get(
                f"/v2/{pid}/loadBalancers/subnet/{subnet_id}", region=region
            )
            return LoadBalancerSubnetListData(
                region=resolved_region,
                subnet_id=subnet_id,
                load_balancers=[
                    LoadBalancerItem.from_api(lb) for lb in as_list(raw) if isinstance(lb, dict)
                ],
            )

        key = ("list_load_balancers_by_subnet", resolved_region, pid, subnet_id)
        return await self.cache.get_or_fetch("list_load_balancers_by_subnet", key, fetch, refresh)

    async def list_load_balancer_scale_history(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ScaleHistoryListData:
        """List the autoscaling events of a load balancer.

        Returns {load_balancer_id, events[{event_type, adjustment_number, total,
        scaled_at}]}. Empty for a load balancer that never autoscaled.
        """
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        raw = await self.client.get(
            f"/v2/{pid}/loadBalancers/{load_balancer_id}/scale-history", region=region
        )
        return ScaleHistoryListData(
            load_balancer_id=load_balancer_id,
            events=[ScaleHistoryItem.from_api(e) for e in as_list(raw) if isinstance(e, dict)],
        )

    async def get_load_balancer_clone_metadata(
        self,
        load_balancer_id: str = Field(..., description="Load balancer that would be cloned."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CloneMetadataData:
        """Get what a clone of this load balancer needs supplied.

        Returns {load_balancer_id, load_balancer_name, certificates[{listener_id,
        default_certificate_id, sni_certificate_ids}]}. Call this before
        `create_load_balancer_clone`: every listener with a certificate needs
        one entry, otherwise the clone's TLS configuration is incomplete.
        """
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        raw = await self.client.get(
            f"/v2/{pid}/loadBalancers/{load_balancer_id}/cloneMetadata", region=region
        )
        return CloneMetadataData.from_api(load_balancer_id, unwrap_one(raw))

    async def get_load_balancer_status(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> LoadBalancerStatusData:
        """Roll up the status of a load balancer and every listener and pool under it.

        Returns {load_balancer_id, name, display_status, progress_status,
        listeners[...], pools[...], settled}.

        This is the polling tool for vLB's asynchronous writes: nearly every
        mutation returns before the work is done, and the gateway rejects a
        second change while one is in flight. After ANY write, call this until
        `settled` is true.

        `settled` fails open — an unrecognised status counts as settled and the
        raw value is returned, because the API documents no status vocabulary.
        """
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        base = f"/v2/{pid}/loadBalancers/{load_balancer_id}"
        lb_raw, listeners_raw, pools_raw = await asyncio.gather(
            self.client.get(base, region=region),
            self.client.get(f"{base}/listeners", region=region),
            self.client.get(f"{base}/pools", region=region),
        )
        return LoadBalancerStatusData.build(
            load_balancer_id,
            unwrap_one(lb_raw),
            [x for x in as_list(listeners_raw) if isinstance(x, dict)],
            [x for x in as_list(pools_raw) if isinstance(x, dict)],
        )

    async def validate_load_balancer_create(
        self,
        body: CreateLoadBalancerDto = Field(
            ..., description="The exact body you intend to pass to create_load_balancer."
        ),
    ) -> ValidationResult:
        """Check a create body locally, before spending a create.

        Returns {ok, errors[], warnings[]}. Makes no API call.

        A rejected `create_load_balancer` comes back as a 400 with a single
        unlabelled validation message and no field name, so this catches the
        combinations that fail — Layer 4 with an HTTP listener, HTTPS without a
        default certificate — and flags the consequences worth confirming, such
        as the name being permanent.

        It cannot check that `packageId` and `subnetId` exist: verify those with
        `list_load_balancer_packages` and `list_subnets`.
        """
        return _validate_create_body(body)

    # --- writes ---------------------------------------------------------------

    async def create_load_balancer(
        self,
        body: CreateLoadBalancerDto = Field(..., description="Load balancer definition."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CreatedResourceData:
        """Create a load balancer, optionally with its first listener and pool.

        ## Requirements
        - Requires `--allow-write`.
        - `packageId` must come from `list_load_balancer_packages` and its
          `lb_type` must match `type`.
        - `subnetId` must come from `list_subnets`; the subnet pins the zone.
        - The name is **permanent** — there is no rename endpoint. Confirm it
          with the user first.
        - Run `validate_load_balancer_create` on the same body first.

        ## Workflow
        - Creating the `listener` and `pool` inline is the only way to get a
          working load balancer in one call; otherwise add them afterwards with
          `create_pool` then `create_listener`.
        - Poll `get_load_balancer_status` until `settled` before the next write.
        - If the returned `id` is empty the create still SUCCEEDED — read the
          `hint`, confirm with `list_load_balancers`, and never retry.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/loadBalancers", region=region, json=payload)
        self.cache.invalidate("list_load_balancers")
        self.cache.invalidate("list_load_balancers_by_subnet")
        return CreatedResourceData.from_api(data, "list_load_balancers")

    async def delete_load_balancer(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a load balancer and everything under it.

        ## Requirements
        - Requires `--allow-write`.
        - Irreversible: the listeners, pools and members go with it, and traffic
          to its address stops. Confirm the id and name with the user first.

        ## Workflow
        - The API answers 202 and deletes asynchronously; poll
          `list_load_balancers` until it disappears.
        """
        require_write(self.allow_write)
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/loadBalancers/{load_balancer_id}", region=region)
        self.cache.invalidate("list_load_balancers")
        self.cache.invalidate("list_load_balancers_by_subnet")
        return (
            f"Load balancer {load_balancer_id} deletion accepted. It is removed "
            "asynchronously — poll list_load_balancers to confirm."
        )

    async def resize_load_balancer(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        package_id: str = Field(
            ..., description="Target package id from list_load_balancer_packages."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Move a load balancer to a different package (capacity and price change).

        ## Requirements
        - Requires `--allow-write`.
        - `package_id` must exist in `list_load_balancer_packages` with an
          `lb_type` matching the load balancer's `type`.
        - Billing changes immediately; confirm the target package with the user.

        ## Workflow
        - Poll `get_load_balancer_status` until `settled`.
        """
        require_write(self.allow_write)
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        payload = ResizeLoadBalancerDto(packageId=package_id).model_dump()
        await self.client.put(
            f"/v2/{pid}/loadBalancers/{load_balancer_id}/resize", region=region, json=payload
        )
        self.cache.invalidate("list_load_balancers")
        return (
            f"Resize of {load_balancer_id} to package {package_id} accepted. "
            "Poll get_load_balancer_status until settled."
        )

    async def configure_load_balancer_scaling(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        max_size: int = Field(..., ge=1, description="Maximum number of nodes."),
        subnets: list[str] = Field(
            ...,
            description=(
                "ALL active subnet ids the load balancer should span. Must include its "
                "current primary subnet — omitting it removes a node from that zone."
            ),
        ),
        min_size: int | None = Field(
            None, ge=1, description="Minimum nodes; defaults to the number of subnets."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Configure Multi-AZ autoscaling (node bounds and the subnets to span).

        ## Requirements
        - Requires `--allow-write`.
        - `subnets` is a **full replacement**: read the load balancer's current
          `subnet_id` with `get_load_balancer` and include it, or that zone
          loses its node.
        - The load balancer must have been created with `autoScalable=true`.

        ## Workflow
        - Poll `get_load_balancer_status` until `settled`, then check
          `list_load_balancer_scale_history` for what actually happened.
        """
        require_write(self.allow_write)
        validate_lb_id(load_balancer_id)
        if not subnets:
            raise ValueError(
                "configure_load_balancer_scaling needs at least one subnet, and the list "
                "must include the load balancer's current primary subnet "
                "(read it from get_load_balancer)."
            )
        pid = await require_project_id(self.config, self.client, region)
        payload = ScaleConfigDto(
            scaling={"maxSize": max_size, "minSize": min_size},
            networking={"subnets": subnets},
        ).model_dump(exclude_none=True)
        await self.client.put(
            f"/v2/{pid}/loadBalancers/{load_balancer_id}/rebalancing",
            region=region,
            json=payload,
        )
        self.cache.invalidate("list_load_balancers")
        return (
            f"Scaling configuration for {load_balancer_id} accepted. "
            "Poll get_load_balancer_status until settled."
        )

    async def update_load_balancer_bandwidth(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        bandwidth_package_id: str = Field(
            ..., description="Bandwidth package id, formatted 'bandwidth-pack-<uuid>'."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> BandwidthData:
        """Attach or change the bandwidth package of an internet-facing load balancer.

        ## Requirements
        - Requires `--allow-write`.
        - Only meaningful for `scheme='Internet'`; an internal load balancer has
          no public bandwidth to meter.
        - Billing changes immediately; confirm the package with the user.

        ## Workflow
        - Returns the package now in effect. Poll `get_load_balancer_status`
          until `settled` before the next write.
        """
        require_write(self.allow_write)
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        payload = UpdateBandwidthPackageDto(bandWidthPackageId=bandwidth_package_id).model_dump()
        data = await self.client.put(
            f"/v2/{pid}/loadBalancers/{load_balancer_id}/bandwidth", region=region, json=payload
        )
        self.cache.invalidate("list_load_balancers")
        return BandwidthData.from_api(unwrap_one(data))

    async def create_load_balancer_clone(
        self,
        load_balancer_id: str = Field(..., description="Load balancer to clone FROM."),
        name: str = Field(..., description="Name for the new load balancer (5-50 chars)."),
        package_id: str = Field(..., description="Package id for the new load balancer."),
        certificates: list[dict] | None = Field(
            None,
            description=(
                "Per-listener certificate overrides, each "
                "{listenerId, defaultCertificateId, sniCertificateIds?}. Take the listener "
                "ids from get_load_balancer_clone_metadata."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CreatedResourceData:
        """Create a new load balancer copied from an existing one.

        ## Requirements
        - Requires `--allow-write`.
        - This CREATES a second billed load balancer; it does not modify the
          source. Confirm with the user.
        - Call `get_load_balancer_clone_metadata` first — every listener that
          serves TLS needs a `certificates` entry, or the clone's listener has
          no certificate.
        - The new name is permanent.

        ## Workflow
        - Poll `get_load_balancer_status` on the NEW id until `settled`.
        - If the returned `id` is empty the clone still SUCCEEDED — confirm with
          `list_load_balancers` and never retry.
        """
        require_write(self.allow_write)
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        payload = CloneLoadBalancerDto(
            name=name, packageId=package_id, certificates=certificates
        ).model_dump(exclude_none=True)
        data = await self.client.post(
            f"/v2/{pid}/loadBalancers/{load_balancer_id}/clone", region=region, json=payload
        )
        self.cache.invalidate("list_load_balancers")
        return CreatedResourceData.from_api(data, "list_load_balancers")
