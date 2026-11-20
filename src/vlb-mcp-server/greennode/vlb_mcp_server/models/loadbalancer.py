"""Load balancer response models.

The response spellings differ from the request spellings for the same concepts
(``loadBalancerSchema`` vs ``scheme``, ``loadBalanceMethod`` vs ``algorithm``,
``address`` vs ``ipAddress``). Every mapping is confined to a ``from_api``
classmethod, and the model field always takes the **request** name so a caller
reads back the same vocabulary it has to write.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.models._common import _resource_id, _zone_id
from greennode.vlb_mcp_server.status import is_settled
from pydantic import BaseModel, Field


class PackageInfoItem(BaseModel):
    """The package a load balancer currently runs on."""

    package_id: str = Field("", description="Package ID")
    name: str = Field("", description="Package name")
    connection_number: int | None = Field(None, description="Concurrent connections included")
    data_transfer: int | None = Field(None, description="Included data transfer")

    @classmethod
    def from_api(cls, data: dict) -> "PackageInfoItem":
        """Build a PackageInfoItem from a raw PackageInfo object."""
        return cls(
            package_id=data.get("packageId") or "",
            name=data.get("name") or "",
            connection_number=data.get("connectionNumber"),
            data_transfer=data.get("dataTransfer"),
        )


class NodeInfoItem(BaseModel):
    """One node of a Multi-AZ load balancer."""

    status: str = Field("", description="Node status")
    zone_id: str = Field("", description="Zone the node runs in")
    zone_name: str = Field("", description="Zone display name")
    subnet_id: str = Field("", description="Subnet the node is attached to")

    @classmethod
    def from_api(cls, data: dict) -> "NodeInfoItem":
        """Build a NodeInfoItem from a raw NodeInfo object."""
        return cls(
            status=data.get("status") or "",
            zone_id=data.get("zoneId") or "",
            zone_name=data.get("zoneName") or "",
            subnet_id=data.get("subnetId") or "",
        )


class LoadBalancerItem(BaseModel):
    """One load balancer."""

    id: str = Field(..., description="Load balancer ID — pass this as load_balancer_id")
    name: str = Field("", description="Load balancer name (permanent; there is no rename API)")
    display_status: str = Field("", description="Lifecycle status, e.g. ACTIVE")
    progress_status: str = Field(
        "", description="In-flight operation, if any; writes are rejected while one is running"
    )
    scheme: str = Field(
        "", description="'Internet' or 'Internal' (the API returns this as loadBalancerSchema)"
    )
    type: str = Field("", description="'Layer 4' or 'Layer 7'")
    display_type: str = Field("", description="Human-readable type")
    address: str = Field("", description="IP address clients connect to")
    subnet_id: str = Field("", description="Primary subnet the load balancer sits on")
    subnet_cidr: str = Field("", description="CIDR of the primary subnet")
    backend_subnet_id: str = Field("", description="Subnet used to reach backends, if separate")
    network_id: str = Field("", description="VPC the load balancer belongs to")
    package_id: str = Field("", description="Current package ID")
    package: PackageInfoItem | None = Field(None, description="Current package details")
    zone_id: str = Field("", description="Availability zone")
    location: str = Field("", description="Location reported by the API")
    description: str = Field("", description="Description")
    auto_scalable: bool = Field(False, description="Whether autoscaling is enabled")
    min_size: int | None = Field(None, description="Minimum node count when autoscaling")
    max_size: int | None = Field(None, description="Maximum node count when autoscaling")
    total_nodes: int | None = Field(None, description="Number of nodes currently running")
    nodes: list[NodeInfoItem] = Field(default_factory=list, description="Per-node detail")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "LoadBalancerItem":
        """Build a LoadBalancerItem from a raw LoadBalancerDto."""
        package = data.get("packageInfo")
        nodes = data.get("nodes")
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            display_status=data.get("displayStatus") or "",
            progress_status=data.get("progressStatus") or "",
            scheme=data.get("loadBalancerSchema") or "",
            type=data.get("type") or "",
            display_type=data.get("displayType") or "",
            address=data.get("address") or "",
            subnet_id=data.get("privateSubnetId") or "",
            subnet_cidr=data.get("privateSubnetCidr") or "",
            backend_subnet_id=data.get("backendSubnetId") or "",
            network_id=data.get("networkId") or "",
            package_id=data.get("packageId") or "",
            package=PackageInfoItem.from_api(package) if isinstance(package, dict) else None,
            zone_id=_zone_id(data),
            location=data.get("location") or "",
            description=data.get("description") or "",
            auto_scalable=bool(data.get("autoScalable")),
            min_size=data.get("minSize"),
            max_size=data.get("maxSize"),
            total_nodes=data.get("totalNodes"),
            nodes=[
                NodeInfoItem.from_api(n)
                for n in (nodes if isinstance(nodes, list) else [])
                if isinstance(n, dict)
            ],
            created_at=str(data.get("createdAt") or ""),
            updated_at=str(data.get("updatedAt") or ""),
        )


class LoadBalancerListData(BaseModel):
    """Structured response for list_load_balancers."""

    region: str = Field(..., description="Region the load balancers were fetched from")
    load_balancers: list[LoadBalancerItem] = Field(
        default_factory=list, description="Load balancers in the project"
    )
    total_item: int | None = Field(
        None,
        description=(
            "Number of load balancers this response describes. The gateway's own total "
            "when no local filter ran, otherwise the length of the filtered list."
        ),
    )
    page: int | None = Field(
        None,
        description=(
            "Page returned. 0 when paging did not take effect — the gateway only pages "
            "this endpoint when a name filter is set, and returns the whole project "
            "otherwise."
        ),
    )
    page_size: int | None = Field(None, description="Page size returned; 0 when unpaged")
    filter_applied: bool = Field(
        True,
        description=(
            "False when a name filter was passed but the gateway returned "
            "non-matching entries — i.e. the server-side filter did not take effect. "
            "Results are still complete; filter locally before presenting them."
        ),
    )
    filtered_locally: list[str] = Field(
        default_factory=list,
        description=(
            "Filters this server applied to the results itself because the gateway "
            "accepts them and then ignores them (status, subnet_id, vpc_id). The list "
            "is already filtered — this only records where the work happened."
        ),
    )


class LoadBalancerSubnetListData(BaseModel):
    """Structured response for list_load_balancers_by_subnet."""

    region: str = Field(..., description="Region the load balancers were fetched from")
    subnet_id: str = Field(..., description="Subnet the load balancers sit on")
    load_balancers: list[LoadBalancerItem] = Field(
        default_factory=list, description="Load balancers on the subnet"
    )


class ScaleHistoryItem(BaseModel):
    """One autoscaling event."""

    id: str = Field("", description="Event ID")
    event_type: str = Field("", description="What happened, e.g. SCALE_OUT")
    adjustment_number: int | None = Field(None, description="Nodes added or removed")
    total: int | None = Field(None, description="Node count after the event")
    scaled_at: str = Field("", description="When the event happened")

    @classmethod
    def from_api(cls, data: dict) -> "ScaleHistoryItem":
        """Build a ScaleHistoryItem from a raw ScaleHistoryDto."""
        return cls(
            id=str(data.get("id") or ""),
            event_type=data.get("eventType") or "",
            adjustment_number=data.get("adjustmentNumber"),
            total=data.get("total"),
            scaled_at=str(data.get("scaledAt") or ""),
        )


class ScaleHistoryListData(BaseModel):
    """Structured response for list_load_balancer_scale_history."""

    load_balancer_id: str = Field(..., description="Load balancer the events belong to")
    events: list[ScaleHistoryItem] = Field(
        default_factory=list, description="Autoscaling events, newest first as returned"
    )


class CloneCertificateItem(BaseModel):
    """Certificates a listener of the source load balancer uses."""

    listener_id: str = Field("", description="Listener ID in the source load balancer")
    default_certificate_id: str = Field("", description="Certificate the listener serves")
    sni_certificate_ids: list[str] = Field(
        default_factory=list, description="Additional certificates offered for SNI"
    )

    @classmethod
    def from_api(cls, data: dict) -> "CloneCertificateItem":
        """Build a CloneCertificateItem from a raw Certificates object."""
        sni = data.get("sniCertificateIds")
        return cls(
            listener_id=data.get("listenerId") or "",
            default_certificate_id=data.get("defaultCertificateId") or "",
            sni_certificate_ids=[str(s) for s in (sni if isinstance(sni, list) else [])],
        )


class CloneMetadataData(BaseModel):
    """Structured response for get_load_balancer_clone_metadata."""

    load_balancer_id: str = Field(..., description="Load balancer that would be cloned")
    load_balancer_name: str = Field("", description="Name of the source load balancer")
    certificates: list[CloneCertificateItem] = Field(
        default_factory=list,
        description="Per-listener certificates the clone must be given, one entry per listener",
    )

    @classmethod
    def from_api(cls, load_balancer_id: str, data: dict) -> "CloneMetadataData":
        """Build CloneMetadataData from a raw LBCloneMetadataDto."""
        certs = data.get("certificates")
        return cls(
            load_balancer_id=load_balancer_id,
            load_balancer_name=data.get("loadBalancerName") or "",
            certificates=[
                CloneCertificateItem.from_api(c)
                for c in (certs if isinstance(certs, list) else [])
                if isinstance(c, dict)
            ],
        )


class BandwidthData(BaseModel):
    """Structured response for update_load_balancer_bandwidth."""

    load_balancer_id: str = Field("", description="Load balancer the package is attached to")
    bandwidth_package_id: str = Field("", description="Bandwidth package now in effect")
    status: str = Field("", description="Status of the attachment")
    last_synced_at: str = Field("", description="When the platform last synced the package")

    @classmethod
    def from_api(cls, data: dict) -> "BandwidthData":
        """Build BandwidthData from a raw LoadBalancerBandwidthDto."""
        return cls(
            load_balancer_id=data.get("lbUuid") or "",
            bandwidth_package_id=data.get("packageId") or "",
            status=data.get("status") or "",
            last_synced_at=str(data.get("lastSyncedAt") or ""),
        )


class ResourceStatusItem(BaseModel):
    """Status of one child resource in the status roll-up."""

    id: str = Field("", description="Resource ID")
    name: str = Field("", description="Resource name")
    display_status: str = Field("", description="Lifecycle status")
    progress_status: str = Field("", description="In-flight operation, if any")

    @classmethod
    def from_api(cls, data: dict) -> "ResourceStatusItem":
        """Build a ResourceStatusItem from any vLB DTO carrying the status pair."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            display_status=data.get("displayStatus") or "",
            progress_status=data.get("progressStatus") or "",
        )


class LoadBalancerStatusData(BaseModel):
    """Structured response for get_load_balancer_status.

    Poll this after every write. ``settled`` is the signal the next write is
    allowed — the gateway rejects a second change while one is in flight.
    """

    load_balancer_id: str = Field(..., description="Load balancer inspected")
    name: str = Field("", description="Load balancer name")
    display_status: str = Field("", description="Lifecycle status of the load balancer")
    progress_status: str = Field("", description="In-flight operation on the load balancer")
    listeners: list[ResourceStatusItem] = Field(
        default_factory=list, description="Status of each listener"
    )
    pools: list[ResourceStatusItem] = Field(
        default_factory=list, description="Status of each pool"
    )
    settled: bool = Field(
        True,
        description=(
            "True when nothing in the tree is still in flight and the next write is safe. "
            "An unrecognised status counts as settled — no status enums are documented."
        ),
    )

    @classmethod
    def build(
        cls,
        load_balancer_id: str,
        lb: dict,
        listeners: list[dict],
        pools: list[dict],
    ) -> "LoadBalancerStatusData":
        """Assemble the roll-up and compute ``settled`` across the whole tree."""
        children = (
            [ResourceStatusItem.from_api(x) for x in listeners],
            [ResourceStatusItem.from_api(x) for x in pools],
        )
        listener_items, pool_items = children
        statuses = [lb.get("displayStatus"), lb.get("progressStatus")]
        for item in listener_items + pool_items:
            statuses.extend([item.display_status, item.progress_status])
        return cls(
            load_balancer_id=load_balancer_id,
            name=lb.get("name") or "",
            display_status=lb.get("displayStatus") or "",
            progress_status=lb.get("progressStatus") or "",
            listeners=listener_items,
            pools=pool_items,
            settled=is_settled(*statuses),
        )


class CreatedResourceData(BaseModel):
    """What a vLB create returns.

    The API types all five create responses as an untyped ``Map<String,String>``
    and never documents which key holds the new id, so ``id`` may legitimately
    be empty on a **successful** create. When it is, ``hint`` says what to do —
    it never means the create failed, and retrying would duplicate a billed
    resource.
    """

    id: str = Field("", description="New resource ID, or empty if the API did not name one")
    raw: dict = Field(default_factory=dict, description="The create response verbatim")
    hint: str = Field("", description="What to do next when `id` is empty; empty otherwise")

    @classmethod
    def from_api(cls, data: object, list_tool: str) -> "CreatedResourceData":
        """Build CreatedResourceData, degrading to a hint rather than raising."""
        from greennode.vlb_mcp_server.paging import created_id

        raw = data if isinstance(data, dict) else {}
        new_id = created_id(raw)
        hint = (
            ""
            if new_id
            else (
                "The API accepted the create but returned no recognisable id. The resource "
                f"was almost certainly created — confirm with {list_tool}. Do NOT retry."
            )
        )
        return cls(id=new_id, raw=raw, hint=hint)


class ValidationResult(BaseModel):
    """Structured response for validate_load_balancer_create."""

    ok: bool = Field(..., description="True when nothing would certainly be rejected")
    errors: list[str] = Field(
        default_factory=list, description="Problems that will make the create fail"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Consequences worth confirming with the user first"
    )
