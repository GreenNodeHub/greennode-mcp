"""Pydantic BaseModel classes for GreenNode MCP Server responses."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date(dt: str | None) -> str:
    """Return the first 10 characters of a date string, or empty string."""
    if not dt:
        return ""
    return str(dt)[:10]


def _kv_table(data: list[tuple[str, str]]) -> str:
    """Render a list of (key, value) tuples as a markdown key-value table."""
    rows = ["| Property | Value |", "|---|---|"]
    for k, v in data:
        rows.append(f"| {k} | {v} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Cluster models
# ---------------------------------------------------------------------------


class ClusterSummary(BaseModel):
    """Summary of a VKS cluster, used in list responses."""

    id: str = Field(..., description="Cluster ID")
    name: str = Field("", description="Cluster name")
    status: str = Field("", description="Cluster status (ACTIVE, CREATING, DELETING, etc.)")
    version: str = Field("", description="Kubernetes version")
    num_nodes: int | str = Field(0, description="Number of nodes")
    enable_private_cluster: str = Field("", description="Whether private cluster is enabled")
    az_strategy: str = Field("", description="Availability zone strategy")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> ClusterSummary:
        """Build a ClusterSummary from a raw VKS API cluster dict."""
        return cls(
            id=data.get("uid", data.get("id", "")),
            name=data.get("name", ""),
            status=data.get("status", ""),
            version=data.get("version", data.get("kubernetesVersion", "")),
            num_nodes=data.get("nodeCount", data.get("numNodes", "")),
            enable_private_cluster=str(data.get("enablePrivateCluster", "")),
            az_strategy=str(data.get("azStrategy", "")),
            created_at=data.get("createdAt", ""),
        )


class ClusterListData(BaseModel):
    """Wrapper for cluster list response."""

    region: str = Field(..., description="Region name")
    clusters: list[ClusterSummary] = Field(default_factory=list, description="List of clusters")

    @property
    def total(self) -> int:
        """Return the number of clusters in the list."""
        return len(self.clusters)

    def to_markdown(self) -> str:
        """Render the cluster list as a markdown table."""
        if not self.clusters:
            return f"No clusters found (region: {self.region})"
        rows = [
            f"Cluster list (region: {self.region}):",
            "| # | Name | ID | Status | Version | Node count | Private | AZ Strategy | Created |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for i, c in enumerate(self.clusters, start=1):
            rows.append(
                f"| {i} | {c.name} | {c.id} | {c.status} | {c.version} | {c.num_nodes} | "
                f"{c.enable_private_cluster} | {c.az_strategy} | {_date(c.created_at)} |"
            )
        rows.append(f"\nTotal: {len(self.clusters)} cluster(s)")
        return "\n".join(rows)


class ClusterDetail(BaseModel):
    """Full detail of a VKS cluster."""

    id: str = Field(..., description="Cluster ID")
    name: str = Field("", description="Cluster name")
    description: str = Field("", description="Cluster description")
    status: str = Field("", description="Cluster status")
    version: str = Field("", description="Kubernetes version")
    release_channel: str = Field("", description="Release channel (STABLE, RAPID)")
    network_type: str = Field("", description="Network type")
    vpc_id: str = Field("", description="VPC ID")
    subnet_id: str = Field("", description="Subnet ID")
    cidr: str = Field("", description="CIDR")
    az_strategy: str = Field("", description="Availability zone strategy")
    list_subnet_ids: list[str] = Field(default_factory=list, description="List of subnet IDs")
    secondary_subnets: list[str] = Field(default_factory=list, description="Secondary subnets")
    num_nodes: int | str = Field(0, description="Number of nodes")
    enable_private_cluster: str = Field("", description="Whether private cluster is enabled")
    enabled_lb_plugin: str = Field("", description="Load balancer plugin enabled")
    enabled_csi_plugin: str = Field("", description="CSI plugin enabled")
    enabled_service_endpoint: str = Field("", description="Service endpoint enabled")
    whitelist_node_cidrs: str = Field("", description="Whitelist node CIDRs")
    poc: str = Field("", description="Whether this is a PoC cluster")
    auto_renewal: str = Field("", description="Auto renewal enabled")
    auto_upgrade_config: Optional[dict] = Field(None, description="Auto-upgrade configuration")
    fleet: str = Field("", description="Fleet info")
    location: str = Field("", description="Location")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, c: dict) -> ClusterDetail:
        """Build a ClusterDetail from a raw VKS API cluster dict."""
        network_type = c.get("networkType", c.get("network", {}).get("type", ""))
        vpc_id = c.get("vpcId", "")
        subnet_id = c.get("subnetId", "")
        cidr = c.get("cidr", "")
        return cls(
            id=c.get("uid", c.get("id", "")),
            name=c.get("name", ""),
            description=c.get("description", ""),
            status=c.get("status", ""),
            version=c.get("version", c.get("kubernetesVersion", "")),
            release_channel=c.get("releaseChannel", ""),
            network_type=network_type,
            vpc_id=vpc_id,
            subnet_id=subnet_id,
            cidr=cidr,
            az_strategy=c.get("azStrategy", ""),
            list_subnet_ids=c.get("listSubnetIds", []),
            secondary_subnets=c.get("secondarySubnets", []),
            num_nodes=c.get("nodeCount", c.get("numNodes", "")),
            enable_private_cluster=str(c.get("enablePrivateCluster", "")),
            enabled_lb_plugin=str(c.get("enabledLoadBalancerPlugin", "")),
            enabled_csi_plugin=str(c.get("enabledBlockStoreCsiPlugin", "")),
            enabled_service_endpoint=str(c.get("enabledServiceEndpoint", "")),
            whitelist_node_cidrs=str(c.get("whitelistNodeCIDRs", "")),
            poc=str(c.get("poc", "")),
            auto_renewal=str(c.get("autoRenewal", "")),
            auto_upgrade_config=c.get("autoUpgradeConfig", None),
            fleet=str(c.get("fleet", "")),
            location=str(c.get("location", "")),
            created_at=c.get("createdAt", ""),
            updated_at=c.get("updatedAt", ""),
        )

    def to_markdown(self) -> str:
        """Render the cluster details as markdown."""
        if self.auto_upgrade_config:
            auto_upgrade = (
                f"{self.auto_upgrade_config.get('weekdays', '')}"
                f" at {self.auto_upgrade_config.get('time', '')}"
            )
        else:
            auto_upgrade = "(not configured)"

        if self.fleet:
            fleet = f"{self.fleet.get('name', '')} ({self.fleet.get('id', '')})"
        else:
            fleet = "(none)"

        whitelist = self.whitelist_node_cidrs if self.whitelist_node_cidrs else ""
        secondary = ", ".join(self.secondary_subnets) if self.secondary_subnets else ""
        list_subnets = ", ".join(self.list_subnet_ids) if self.list_subnet_ids else ""

        data = [
            ("ID", self.id),
            ("Name", self.name),
            ("Description", self.description),
            ("Status", self.status),
            ("Version", self.version),
            ("Release Channel", self.release_channel),
            ("Network Type", self.network_type),
            ("VPC ID", self.vpc_id),
            ("Subnet ID", self.subnet_id),
            ("CIDR", self.cidr),
            ("AZ Strategy", self.az_strategy),
            ("List Subnet IDs", list_subnets),
            ("Secondary Subnets", secondary),
            ("Node count", str(self.num_nodes)),
            ("Private Cluster", self.enable_private_cluster),
            ("LB Plugin", self.enabled_lb_plugin),
            ("CSI Plugin", self.enabled_csi_plugin),
            ("Service Endpoint", self.enabled_service_endpoint),
            ("Whitelist Node CIDRs", whitelist),
            ("PoC", self.poc),
            ("Auto Renewal", self.auto_renewal),
            ("Auto-Upgrade", auto_upgrade),
            ("Fleet", fleet),
            ("Location", self.location),
            ("Created", _date(self.created_at)),
            ("Updated", _date(self.updated_at)),
        ]
        return f"Cluster detail **{self.name}**:\n" + _kv_table(data)


# ---------------------------------------------------------------------------
# Node group models
# ---------------------------------------------------------------------------


class NodeGroupSummary(BaseModel):
    """Summary of a VKS node group, used in list responses."""

    id: str = Field(..., description="Node group ID")
    name: str = Field("", description="Node group name")
    status: str = Field("", description="Node group status")
    num_nodes: int | str = Field(0, description="Number of nodes")
    image_id: str = Field("", description="Image ID")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> NodeGroupSummary:
        """Build a NodeGroupSummary from a raw VKS API node-group dict."""
        return cls(
            id=data.get("uid", data.get("id", "")),
            name=data.get("name", ""),
            status=data.get("status", ""),
            num_nodes=data.get("nodeCount", data.get("numNodes", "")),
            image_id=data.get("imageId", ""),
            created_at=data.get("createdAt", ""),
        )


class NodeGroupListData(BaseModel):
    """Wrapper for node group list response."""

    cluster_name: str = Field("", description="Parent cluster name")
    node_groups: list[NodeGroupSummary] = Field(
        default_factory=list, description="List of node groups"
    )

    @property
    def total(self) -> int:
        """Return the number of node groups in the list."""
        return len(self.node_groups)

    def to_markdown(self) -> str:
        """Render the node-group list as a markdown table."""
        if not self.node_groups:
            return f"No node groups found in cluster {self.cluster_name}"
        rows = [
            f"Node groups of cluster {self.cluster_name}:",
            "| # | Name | ID | Status | Node count | Image ID | Created |",
            "|---|---|---|---|---|---|---|",
        ]
        for i, ng in enumerate(self.node_groups, start=1):
            rows.append(
                f"| {i} | {ng.name} | {ng.id} | {ng.status} | {ng.num_nodes} | {ng.image_id} | {_date(ng.created_at)} |"
            )
        return "\n".join(rows)


class NodeGroupDetail(BaseModel):
    """Full detail of a VKS node group."""

    id: str = Field(..., description="Node group ID")
    cluster_id: str = Field("", description="Parent cluster ID")
    name: str = Field("", description="Node group name")
    status: str = Field("", description="Node group status")
    num_nodes: int | str = Field(0, description="Number of nodes")
    image_id: str = Field("", description="Image ID")
    flavor_id: str = Field("", description="Flavor ID")
    disk_size: str = Field("", description="Disk size")
    disk_type: str = Field("", description="Disk type")
    enable_private_nodes: str = Field("", description="Whether private nodes enabled")
    subnet_id: str = Field("", description="Private subnet ID")
    secondary_subnets: list[str] = Field(default_factory=list, description="Secondary subnets")
    enabled_encryption_volume: str = Field("", description="Whether volume encryption is enabled")
    placement_group_id: str = Field("", description="Placement group ID")
    ssh_key_id: str = Field("", description="SSH key ID")
    security_groups: list[str] = Field(default_factory=list, description="Security group IDs")
    upgrade_config: Optional[dict] = Field(None, description="Upgrade configuration")
    auto_scale_config: Optional[dict] = Field(None, description="Auto-scale configuration")
    labels: dict[str, str] = Field(default_factory=dict, description="Node labels")
    taints: list[dict] = Field(default_factory=list, description="Node taints")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, ng: dict) -> NodeGroupDetail:
        """Build a NodeGroupDetail from a raw VKS API node-group dict."""
        disk_size = str(ng.get("disk", {}).get("size", ng.get("diskSize", "")))
        disk_type = ng.get("disk", {}).get("type", ng.get("diskType", ""))
        return cls(
            id=ng.get("uid", ng.get("id", "")),
            cluster_id=ng.get("clusterId", ng.get("clusterUid", "")),
            name=ng.get("name", ""),
            status=ng.get("status", ""),
            num_nodes=ng.get("nodeCount", ng.get("numNodes", "")),
            image_id=ng.get("imageId", ""),
            flavor_id=ng.get("flavorId", ng.get("flavor", "")),
            disk_size=disk_size,
            disk_type=disk_type,
            enable_private_nodes=str(ng.get("enablePrivateNodes", ng.get("privateNodes", ""))),
            subnet_id=ng.get("subnetId", ""),
            secondary_subnets=ng.get("secondarySubnets", []),
            enabled_encryption_volume=str(ng.get("enabledEncryptionVolume", "")),
            placement_group_id=str(ng.get("placementGroupId", "")),
            ssh_key_id=ng.get("sshKeyId", ng.get("sshKey", "")),
            security_groups=ng.get("securityGroups", []),
            upgrade_config=ng.get("upgradeConfig", None),
            auto_scale_config=ng.get("autoScaleConfig", ng.get("autoscale", None)),
            labels=ng.get("labels", {}),
            taints=ng.get("taints", []),
            created_at=ng.get("createdAt", ""),
            updated_at=ng.get("updatedAt", ""),
        )

    def to_markdown(self) -> str:
        """Render the node-group details as markdown."""
        disk = (
            f"{self.disk_size} GB ({self.disk_type})" if self.disk_size and self.disk_type else ""
        )

        if self.upgrade_config:
            uc = self.upgrade_config
            upgrade = f"strategy={uc.get('strategy', '')}, maxSurge={uc.get('maxSurge', '')}, maxUnavailable={uc.get('maxUnavailable', '')}"
        else:
            upgrade = ""

        if self.auto_scale_config:
            asc = self.auto_scale_config
            autoscale = f"{asc.get('minSize', '')}-{asc.get('maxSize', '')}"
        else:
            autoscale = "not configured"

        labels = ", ".join(f"{k}={v}" for k, v in self.labels.items()) if self.labels else ""
        taints = (
            ", ".join(
                f"{t.get('key', '')}={t.get('value', '')}:{t.get('effect', '')}"
                for t in self.taints
            )
            if self.taints
            else ""
        )
        sgs = ", ".join(str(s) for s in self.security_groups) if self.security_groups else ""

        data = [
            ("ID", self.id),
            ("Cluster ID", self.cluster_id),
            ("Name", self.name),
            ("Status", self.status),
            ("Node count", str(self.num_nodes)),
            ("Image ID", self.image_id),
            ("Flavor ID", self.flavor_id),
            ("Disk", disk),
            ("Private Nodes", self.enable_private_nodes),
            ("Subnet ID", self.subnet_id),
            ("Secondary Subnets", ", ".join(self.secondary_subnets)),
            ("Volume Encryption", self.enabled_encryption_volume),
            ("Placement Group ID", self.placement_group_id),
            ("SSH Key ID", self.ssh_key_id),
            ("Security Groups", sgs),
            ("Upgrade Config", upgrade),
            ("Auto-scale", autoscale),
            ("Labels", labels),
            ("Taints", taints),
            ("Created", _date(self.created_at)),
            ("Updated", _date(self.updated_at)),
        ]
        return f"Node group detail **{self.name}**:\n" + _kv_table(data)


class NodeItem(BaseModel):
    """A single node inside a node group (VKS API NodeDto)."""

    id: str = Field("", description="Node ID")
    name: str = Field("", description="Node name")
    status: str = Field("", description="Node status")
    floating_ip: str = Field("", description="Floating (public) IP")
    fixed_ip: str = Field("", description="Fixed (private) IP")
    ready: str = Field("", description="Whether the node is Ready")
    poc: str = Field("", description="Whether this is a PoC node")

    @classmethod
    def from_api(cls, n: dict) -> NodeItem:
        """Build a NodeItem from a raw VKS API node dict."""
        return cls(
            id=n.get("id", n.get("uid", "")),
            name=n.get("name", ""),
            status=n.get("status", ""),
            floating_ip=str(n.get("floatingIp", "")),
            fixed_ip=str(n.get("fixedIp", "")),
            ready=str(n.get("ready", "")),
            poc=str(n.get("poc", "")),
        )


class NodesData(BaseModel):
    """Wrapper for list_nodes response."""

    nodegroup_id: str = Field(..., description="Parent node group ID")
    nodes: list[NodeItem] = Field(default_factory=list, description="List of nodes")

    @property
    def total(self) -> int:
        """Return the number of nodes in the list."""
        return len(self.nodes)

    def to_markdown(self) -> str:
        """Render the node list as a markdown table."""
        if not self.nodes:
            return "No nodes found in this node group."
        rows = [
            f"Nodes list (node group: {self.nodegroup_id}):",
            "| # | Name | ID | Status | Floating IP | Fixed IP | Ready | PoC |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i, n in enumerate(self.nodes, start=1):
            rows.append(
                f"| {i} | {n.name} | {n.id} | {n.status} | {n.floating_ip} | "
                f"{n.fixed_ip} | {n.ready} | {n.poc} |"
            )
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Format helpers (used by handlers)
# ---------------------------------------------------------------------------


def format_cluster_detail(c: dict) -> str:
    """Format full cluster details as a markdown key-value table.

    Args:
        c: Cluster dict from the VKS API.

    Returns:
        Markdown-formatted detail string.
    """
    return ClusterDetail.from_api(c).to_markdown()


# ---------------------------------------------------------------------------
# Discovery models (vServer)
# ---------------------------------------------------------------------------


class VpcItem(BaseModel):
    """A VPC/network from vServer."""

    id: str = Field(..., description="VPC/network ID (use as vpcId)")
    name: str = Field("", description="VPC display name")
    cidr: str = Field("", description="CIDR range")
    status: str = Field("", description="VPC status")

    @classmethod
    def from_api(cls, v: dict) -> VpcItem:
        """Build a VpcItem from a raw vServer network dict."""
        return cls(
            id=v.get("id", ""),
            name=v.get("displayName", ""),
            cidr=v.get("cidr", ""),
            status=v.get("status", ""),
        )


class VpcListData(BaseModel):
    """Wrapper for list_vpcs response."""

    region: str = Field(..., description="Region name")
    vpcs: list[VpcItem] = Field(default_factory=list, description="List of VPCs")


class SubnetItem(BaseModel):
    """A subnet of a VPC from vServer."""

    id: str = Field(..., description="Subnet ID (use as subnetId)")
    name: str = Field("", description="Subnet name")
    cidr: str = Field("", description="CIDR range")
    status: str = Field("", description="Subnet status")
    secondary_subnets: list[str] = Field(
        default_factory=list,
        description="Secondary subnet IDs (use as secondarySubnets for CILIUM_NATIVE_ROUTING)",
    )

    @classmethod
    def from_api(cls, s: dict) -> SubnetItem:
        """Build a SubnetItem from a raw vServer subnet dict (id is 'uuid')."""
        return cls(
            id=s.get("uuid", ""),
            name=s.get("name", ""),
            cidr=s.get("cidr", ""),
            status=s.get("status", ""),
            secondary_subnets=[
                ss.get("uuid", "") if isinstance(ss, dict) else str(ss)
                for ss in (s.get("secondarySubnets") or [])
            ],
        )


class SubnetListData(BaseModel):
    """Wrapper for list_subnets response."""

    vpc_id: str = Field(..., description="Parent VPC ID")
    subnets: list[SubnetItem] = Field(default_factory=list, description="List of subnets")


class FlavorItem(BaseModel):
    """A cluster flavor from vServer, tagged with a deployment-need group."""

    id: str = Field(..., description="Flavor ID (use as flavorId)")
    name: str = Field("", description="Flavor name")
    vcpu: int | str = Field("", description="Number of vCPUs")
    ram_gb: int | str = Field("", description="RAM in GB")
    gpu: int | str = Field("", description="Number of GPUs")
    group: str = Field("", description="Suggested deployment-need group")

    @classmethod
    def from_api(cls, f: dict, group: str) -> FlavorItem:
        """Build a FlavorItem from a raw vServer flavor dict plus its group."""
        return cls(
            id=f.get("flavorId", ""),
            name=f.get("name", ""),
            vcpu=f.get("cpu", ""),
            ram_gb=f.get("memory", ""),
            gpu=f.get("gpu", ""),
            group=group,
        )


class FlavorListData(BaseModel):
    """Wrapper for list_flavors response."""

    need: str | None = Field(None, description="Applied need-group filter, if any")
    flavors: list[FlavorItem] = Field(default_factory=list, description="List of flavors")


class SshKeyItem(BaseModel):
    """An SSH key from vServer."""

    id: str = Field(..., description="SSH key ID (use as sshKeyId)")
    name: str = Field("", description="SSH key name")

    @classmethod
    def from_api(cls, k: dict) -> SshKeyItem:
        """Build an SshKeyItem from a raw vServer SSH-key dict."""
        return cls(id=k.get("id", ""), name=k.get("name", ""))


class SshKeyListData(BaseModel):
    """Wrapper for list_ssh_keys response."""

    ssh_keys: list[SshKeyItem] = Field(default_factory=list, description="List of SSH keys")


class SecgroupItem(BaseModel):
    """A security group from vServer."""

    id: str = Field(..., description="Security group ID (use in securityGroups)")
    name: str = Field("", description="Security group name")
    description: str = Field("", description="Description")
    status: str = Field("", description="Status")

    @classmethod
    def from_api(cls, g: dict) -> SecgroupItem:
        """Build a SecgroupItem from a raw vServer security-group dict."""
        return cls(
            id=g.get("id", ""),
            name=g.get("name", ""),
            description=g.get("description", ""),
            status=g.get("status", ""),
        )


class SecgroupListData(BaseModel):
    """Wrapper for list_security_groups response."""

    secgroups: list[SecgroupItem] = Field(default_factory=list, description="Security groups")


class PlacementGroupItem(BaseModel):
    """A placement group (vServer server group)."""

    id: str = Field(..., description="Placement group UUID (use as placementGroupId)")
    name: str = Field("", description="Placement group name")
    policy: str = Field("", description="Placement policy name, e.g. 'AFFINITY'")
    description: str = Field("", description="Description")

    @classmethod
    def from_api(cls, g: dict) -> PlacementGroupItem:
        """Build a PlacementGroupItem from a raw vServer server-group dict."""
        return cls(
            id=g.get("uuid", ""),
            name=g.get("name", ""),
            policy=g.get("policyName", g.get("policyId", "")),
            description=g.get("description", ""),
        )


class PlacementGroupListData(BaseModel):
    """Wrapper for list_placement_groups response."""

    placement_groups: list[PlacementGroupItem] = Field(
        default_factory=list, description="List of placement groups"
    )


class VolumeTypeItem(BaseModel):
    """A volume type from vServer. Its id is the diskType value for node groups."""

    id: str = Field(..., description="Volume type ID (use as diskType)")
    name: str = Field("", description="Volume type name")
    type_zone: str = Field("", description="Volume type zone name, e.g. 'SSD'")
    iops: int | str = Field("", description="IOPS")
    min_size_gb: int | str = Field("", description="Minimum size in GB")
    max_size_gb: int | str = Field("", description="Maximum size in GB")
    throughput: int | str = Field("", description="Throughput (MB/s)")

    @classmethod
    def from_api(cls, v: dict, type_zone: str = "") -> VolumeTypeItem:
        """Build a VolumeTypeItem from a raw vServer volume-type dict."""
        return cls(
            id=v.get("id", ""),
            name=v.get("name", ""),
            type_zone=type_zone,
            iops=v.get("iops", ""),
            min_size_gb=v.get("minSize", ""),
            max_size_gb=v.get("maxSize", ""),
            throughput=v.get("throughPut", ""),
        )


class VolumeTypeListData(BaseModel):
    """Wrapper for list_volume_types response."""

    zone_id: str | None = Field(None, description="Applied availability-zone filter, if any")
    volume_types: list[VolumeTypeItem] = Field(
        default_factory=list, description="List of volume types"
    )


class QuotaData(BaseModel):
    """VKS quota for the current user (get_quota response)."""

    max_clusters: int | str = Field("", description="Maximum number of clusters allowed")
    num_clusters: int | str = Field("", description="Number of clusters currently in use")
    max_node_groups_per_cluster: int | str = Field(
        "", description="Maximum node groups per cluster"
    )
    max_nodes_per_node_group: int | str = Field("", description="Maximum nodes per node group")

    @classmethod
    def from_api(cls, q: dict) -> QuotaData:
        """Build a QuotaData from the raw VKS quota dict."""
        return cls(
            max_clusters=q.get("maxClusters", ""),
            num_clusters=q.get("numClusters", ""),
            max_node_groups_per_cluster=q.get("maxNodeGroupsPerCluster", ""),
            max_nodes_per_node_group=q.get("maxNodesPerNodeGroup", ""),
        )


# ---------------------------------------------------------------------------
# Version models
# ---------------------------------------------------------------------------


class VersionItem(BaseModel):
    """An available Kubernetes cluster version."""

    version: str = Field(..., description="Version string, e.g. 'v1.29.0'")
    stage: str = Field("", description="Release stage (STABLE, RAPID)")
    deprecated_at: str = Field("", description="Deprecation date, if any")
    recommended: bool = Field(False, description="Whether this is the recommended version")


class VersionsData(BaseModel):
    """Wrapper for list_cluster_versions response."""

    recommended: str = Field("", description="Recommended version name")
    versions: list[VersionItem] = Field(default_factory=list, description="Available versions")


# ---------------------------------------------------------------------------
# Kubernetes resource models
# ---------------------------------------------------------------------------


class Operation(str, Enum):
    """Kubernetes resource operations."""

    CREATE = "create"
    REPLACE = "replace"
    PATCH = "patch"
    DELETE = "delete"
    READ = "read"


class ResourceSummary(BaseModel):
    """Summary of a Kubernetes resource."""

    name: str = Field(..., description="Name of the resource")
    namespace: Optional[str] = Field(None, description="Namespace of the resource")
    creation_timestamp: Optional[str] = Field(None, description="Creation timestamp")
    labels: Optional[Dict[str, str]] = Field(None, description="Resource labels")
    annotations: Optional[Dict[str, str]] = Field(None, description="Resource annotations")


class KubernetesResourceListData(BaseModel):
    """Data model for list_k8s_resources response."""

    kind: str = Field(..., description="Kind of the Kubernetes resources")
    api_version: str = Field(..., description="API version")
    namespace: Optional[str] = Field(None, description="Namespace")
    count: int = Field(..., description="Number of resources found")
    items: List[ResourceSummary] = Field(..., description="List of resources")


class KubernetesResourceData(BaseModel):
    """Data model for manage_k8s_resource response."""

    kind: str = Field(..., description="Kind of the Kubernetes resource")
    name: str = Field(..., description="Name of the Kubernetes resource")
    namespace: Optional[str] = Field(None, description="Namespace")
    api_version: str = Field(..., description="API version")
    operation: str = Field(..., description="Operation performed")
    resource: Optional[Dict[str, Any]] = Field(None, description="Resource data (for read)")


class EventItem(BaseModel):
    """Summary of a Kubernetes event."""

    first_timestamp: Optional[str] = Field(None, description="First timestamp")
    last_timestamp: Optional[str] = Field(None, description="Last timestamp")
    count: Optional[int] = Field(0, description="Count of occurrences", ge=0)
    message: str = Field(..., description="Event message")
    reason: str = Field(..., description="Reason for the event")
    reporting_component: str = Field(..., description="Reporting component")
    type: str = Field(..., description="Event type (Normal, Warning)")


class EventsData(BaseModel):
    """Data model for get_k8s_events response."""

    involved_object_kind: str = Field(..., description="Kind of the involved object")
    involved_object_name: str = Field(..., description="Name of the involved object")
    involved_object_namespace: Optional[str] = Field(None, description="Namespace")
    count: int = Field(..., description="Number of events found")
    events: List[EventItem] = Field(..., description="List of events")


class PodLogsData(BaseModel):
    """Data model for get_pod_logs response."""

    pod_name: str = Field(..., description="Name of the pod")
    namespace: str = Field(..., description="Namespace of the pod")
    container_name: Optional[str] = Field(None, description="Container name")
    log_lines: List[str] = Field(..., description="Pod log lines")


class ApplyYamlData(BaseModel):
    """Data model for apply_yaml response."""

    force_applied: bool = Field(False, description="Whether force was used")
    resources_created: int = Field(0, description="Number of resources created")
    resources_updated: int = Field(0, description="Number of resources updated")


class ApiVersionsData(BaseModel):
    """Data model for list_api_versions response."""

    cluster_id: str = Field(..., description="VKS Cluster ID")
    api_versions: List[str] = Field(..., description="Available API versions")
    count: int = Field(..., description="Number of API versions")


# ---------------------------------------------------------------------------
# Request DTOs (write tools)
# ---------------------------------------------------------------------------


class UpgradeConfig(BaseModel):
    """Per-node-group upgrade configuration.

    Defaults match the greennode-cli defaults (SURGE strategy, maxSurge=1, maxUnavailable=0).
    Calling ``UpgradeConfig()`` produces a non-empty body with sensible values.

    Unknown fields are rejected (``extra="forbid"``) so that typos surface immediately.
    The node OS image is set via ``os`` on the node group itself, not here.
    """

    model_config = ConfigDict(extra="forbid")

    maxSurge: int = Field(
        1, ge=1, le=100, description="Max nodes added above desired count during upgrade (1-100)"
    )
    maxUnavailable: int = Field(
        0, ge=0, le=100, description="Max nodes unavailable during upgrade, 0 = rolling (0-100)"
    )
    strategy: str = Field("SURGE", description="Upgrade strategy, e.g. 'SURGE'")


class NodeGroupTaint(BaseModel):
    """A Kubernetes taint applied to node-group nodes."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., description="Taint key")
    value: str = Field("", description="Taint value")
    effect: Literal["NoSchedule", "PreferNoSchedule", "NoExecute"] = Field(
        ..., description="Taint effect"
    )


class AutoScaleConfig(BaseModel):
    """Node-group autoscaling bounds."""

    model_config = ConfigDict(extra="forbid")

    minSize: int = Field(..., ge=0, description="Minimum number of nodes")
    maxSize: int = Field(..., ge=1, description="Maximum number of nodes")


class PlacementGroupConfig(BaseModel):
    """Placement-group configuration for a node group."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["NEW", "EXISTING"] = Field(..., description="Use a NEW or EXISTING group")
    placementGroupId: Optional[str] = Field(
        None, description="Group ID when type=EXISTING (from list_placement_groups)"
    )
    placementGroupName: Optional[str] = Field(None, description="Group name (when type=NEW)")


class NodeGroupSpec(BaseModel):
    """A node group entry inside a create body.

    Mirrors the greennode-cli ``create-nodegroup`` field set. Unknown fields are
    rejected (``extra="forbid"``) so typos surface immediately.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Node group name")
    flavorId: str = Field(..., description="Flavor ID (from list_flavors)")
    diskSize: int = Field(..., ge=20, le=5000, description="Disk size in GB (20-5000)")
    diskType: str = Field(..., description="Volume type ID (from list_volume_types)")
    numNodes: int = Field(..., ge=0, le=10, description="Number of nodes (0-10)")
    sshKeyId: str = Field(..., description="SSH key ID (from list_ssh_keys)")
    os: Literal["ubuntu", "linux", "rocky"] = Field("ubuntu", description="Node OS image type")
    enablePrivateNodes: bool = Field(False, description="Whether nodes are private")
    enabledEncryptionVolume: bool = Field(False, description="Whether to encrypt node volumes")
    securityGroups: list[str] = Field(
        default_factory=list, description="Security group IDs (from list_security_groups)"
    )
    upgradeConfig: UpgradeConfig = Field(
        default_factory=UpgradeConfig, description="Upgrade config (SURGE 1/0 by default)"
    )
    subnetId: Optional[str] = Field(None, description="Private subnet ID (from list_subnets)")
    secondarySubnets: Optional[list[str]] = Field(None, description="Secondary subnet IDs")
    labels: Optional[dict[str, str]] = Field(None, description="Node labels")
    taints: Optional[list[NodeGroupTaint]] = Field(None, description="Node taints")
    tags: Optional[dict[str, str]] = Field(None, description="Node tags")
    autoScaleConfig: Optional[AutoScaleConfig] = Field(None, description="Autoscaling bounds")
    placementGroupConfigDto: Optional[PlacementGroupConfig] = Field(
        None, description="Placement-group configuration"
    )


class AutoUpgradeConfig(BaseModel):
    """Cluster auto-upgrade schedule."""

    model_config = ConfigDict(extra="forbid")

    weekdays: str = Field(..., description="Comma-separated weekdays, e.g. 'Mon,Wed,Fri'")
    time: str = Field(..., description="Time of day in HH:mm, e.g. '03:00'")


class AutoHealingConfig(BaseModel):
    """Cluster auto-healing configuration."""

    model_config = ConfigDict(extra="forbid")

    enableAutoHealing: bool = Field(..., description="Enable or disable auto-healing")
    maxUnhealthy: Optional[str] = Field(
        None, description="Max unhealthy nodes before remediation, e.g. '2' or '40%'"
    )
    unhealthyRange: Optional[str] = Field(
        None, description="Range of unhealthy nodes allowed, e.g. '[2-5]'"
    )
    timeoutUnhealthy: Optional[int] = Field(
        None, ge=5, le=180, description="Minutes before a node is considered unhealthy (5-180)"
    )


class CreateClusterComboDto(BaseModel):
    """Body for create_cluster. Mirrors the greennode-cli ``create-cluster`` field set.

    Creates the control plane only; add workers afterwards via create_nodegroup. The
    API's ``nodeGroups`` array is deprecated and not accepted here. Unknown fields are
    rejected (``extra="forbid"``), so passing ``nodeGroups`` is an error.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Cluster name (5-20 chars)")
    version: str = Field(..., description="Kubernetes version (from list_cluster_versions)")
    networkType: Literal["CILIUM_OVERLAY", "CILIUM_NATIVE_ROUTING", "TIGERA"] = Field(
        ..., description="Network type"
    )
    vpcId: str = Field(..., description="VPC ID (from list_vpcs)")
    releaseChannel: Literal["RAPID", "STABLE"] = Field("STABLE", description="Release channel")
    enablePrivateCluster: bool = Field(False, description="Whether the cluster is private")
    enabledLoadBalancerPlugin: bool = Field(True, description="Enable the load-balancer plugin")
    enabledBlockStoreCsiPlugin: bool = Field(True, description="Enable the block-store CSI plugin")
    enabledServiceEndpoint: bool = Field(False, description="Enable the service endpoint")
    azStrategy: Literal["SINGLE", "MULTI"] = Field(
        "SINGLE", description="Availability-zone strategy"
    )
    description: Optional[str] = Field(None, description="Cluster description")
    subnetId: Optional[str] = Field(None, description="Subnet ID (from list_subnets)")
    cidr: Optional[str] = Field(
        None, description="Required when networkType is CILIUM_OVERLAY or TIGERA"
    )
    secondarySubnets: Optional[list[str]] = Field(
        None, description="Required when networkType is CILIUM_NATIVE_ROUTING"
    )
    listSubnetIds: Optional[list[str]] = Field(None, description="Subnet IDs for the cluster")
    nodeNetmaskSize: Optional[int] = Field(None, description="Node netmask size")
    autoUpgradeConfig: Optional[AutoUpgradeConfig] = Field(
        None, description="Auto-upgrade schedule"
    )
    autoHealingConfig: Optional[AutoHealingConfig] = Field(
        None, description="Auto-healing configuration"
    )


class UpdateClusterDto(BaseModel):
    """Body for update_cluster (``PUT /v1/clusters/{id}``).

    Mirrors the greennode-cli ``update-cluster`` command: it changes the Kubernetes
    version and the node whitelist CIDRs, and can toggle the LB / block-store plugins.
    (Name, description, and release channel are NOT editable via this endpoint.)
    Unknown fields are rejected (``extra="forbid"``).
    """

    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., description="Target Kubernetes version (from list_cluster_versions)")
    whitelistNodeCIDRs: list[str] = Field(..., description="Whitelist node CIDRs")
    enabledLoadBalancerPlugin: Optional[bool] = Field(
        None, description="Toggle the load-balancer plugin; omit to leave unchanged"
    )
    enabledBlockStoreCsiPlugin: Optional[bool] = Field(
        None, description="Toggle the block-store CSI plugin; omit to leave unchanged"
    )


class CreateNodeGroupDto(NodeGroupSpec):
    """Body for create_nodegroup (same shape as NodeGroupSpec).

    Inherits ``extra="forbid"`` from NodeGroupSpec: unknown fields are rejected.
    """


class UpdateNodeGroupDto(BaseModel):
    """Partial-update body for update_nodegroup. All fields optional.

    Scoped to what the greennode-cli ``update-nodegroup`` command sends. Labels,
    tags, and taints are updated separately via ``update_nodegroup_metadata``
    (see UpdateNodeGroupMetadataDto). Unknown fields are rejected (``extra="forbid"``).
    """

    model_config = ConfigDict(extra="forbid")

    numNodes: Optional[int] = Field(None, ge=0, le=10, description="Number of nodes (0-10)")
    securityGroups: Optional[list[str]] = Field(None, description="Security group IDs")
    autoScaleConfig: Optional[AutoScaleConfig] = Field(None, description="Autoscaling bounds")
    upgradeConfig: Optional[UpgradeConfig] = Field(None, description="Upgrade config")


class UpdateNodeGroupMetadataDto(BaseModel):
    """Body for update_nodegroup_metadata (labels, tags, taints).

    Mirrors the greennode-cli ``update-nodegroup-metadata`` command, targeting the
    ``/metadata`` endpoint. All fields optional; at least one must be set. Unknown
    fields are rejected (``extra="forbid"``).
    """

    model_config = ConfigDict(extra="forbid")

    labels: Optional[dict[str, str]] = Field(None, description="Node labels (key=value)")
    tags: Optional[dict[str, str]] = Field(None, description="Node tags (key=value)")
    taints: Optional[list[NodeGroupTaint]] = Field(None, description="Node taints")
