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

    region: str = Field("", description="Region these node groups belong to")
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

    region: str = Field("", description="Region these nodes belong to")
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
    """An ACTIVE VPC/network — minimal projection for the user to choose from."""

    id: str = Field(..., description="VPC/network ID — use as `vpcId` in create_cluster")
    name: str = Field("", description="VPC display name — show this to the user")
    enabled_dns: bool = Field(
        False,
        description=(
            "Whether vDNS is enabled on this VPC — clusters with azStrategy=MULTI "
            "can only use a vDNS-enabled VPC"
        ),
    )

    @classmethod
    def from_api(cls, v: dict) -> VpcItem:
        """Build a VpcItem from a raw vServer network dict."""
        return cls(
            id=v.get("id", ""),
            name=v.get("displayName", ""),
            enabled_dns=v.get("dnsStatus", "") == "ENABLED",
        )


class VpcListData(BaseModel):
    """Wrapper for list_vpcs response (ACTIVE VPCs only)."""

    region: str = Field(..., description="Region name")
    vpcs: list[VpcItem] = Field(
        default_factory=list, description="ACTIVE VPCs (non-ACTIVE ones are filtered out)"
    )


class ZoneRef(BaseModel):
    """Availability-zone reference (uuid + name), from a subnet's `zone`."""

    uuid: str = Field("", description="Zone UUID — flavors/volume types are zone-scoped by this")
    name: str = Field("", description="Zone name, e.g. 'HCM03-1A' — show this to the user")


class SubnetItem(BaseModel):
    """An ACTIVE subnet of a VPC — minimal projection for the user to choose from."""

    id: str = Field(
        ..., description="Subnet ID — use as `subnetId` in create_cluster / create_nodegroup"
    )
    name: str = Field("", description="Subnet name — show this to the user")
    zone: Optional[ZoneRef] = Field(
        None,
        description="Availability zone of this subnet — flavors and volume types are scoped to it",
    )
    secondary_subnets: list[str] = Field(
        default_factory=list,
        description=(
            "Secondary subnet CIDRs (e.g. '10.5.60.0/22') — in a "
            "CILIUM_NATIVE_ROUTING cluster, pass this list verbatim as the node "
            "group's `secondarySubnets` (the API takes CIDRs, not sec-sub ids; "
            "empty ⇒ this subnet cannot host that cluster's node groups). Other "
            "networkTypes don't use it (omit the field)"
        ),
    )

    @classmethod
    def from_api(cls, s: dict) -> SubnetItem:
        """Build a SubnetItem from a raw vServer subnet dict (id is 'uuid')."""
        zone = s.get("zone")
        return cls(
            id=s.get("uuid", ""),
            name=s.get("name", ""),
            zone=(
                ZoneRef(uuid=zone.get("uuid", ""), name=zone.get("name", ""))
                if isinstance(zone, dict)
                else None
            ),
            secondary_subnets=[
                ss.get("cidr", "") if isinstance(ss, dict) else str(ss)
                for ss in (s.get("secondarySubnets") or [])
            ],
        )


class SubnetListData(BaseModel):
    """Wrapper for list_subnets response (ACTIVE subnets only)."""

    vpc_id: str = Field(..., description="Parent VPC ID")
    subnets: list[SubnetItem] = Field(
        default_factory=list, description="ACTIVE subnets (non-ACTIVE ones are filtered out)"
    )


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
    """Wrapper for list_flavors response (available worker flavors of one zone)."""

    region: str = Field(..., description="Region these flavors belong to")
    zone: str = Field("", description="Availability-zone uuid these flavors belong to")
    need: str | None = Field(None, description="Applied need-group filter, if any")
    flavors: list[FlavorItem] = Field(
        default_factory=list, description="Available flavors (sold-out ones filtered out)"
    )


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

    region: str = Field(..., description="Region these keys belong to (echoes the query region)")
    ssh_keys: list[SshKeyItem] = Field(default_factory=list, description="List of SSH keys")


class SecgroupItem(BaseModel):
    """An ACTIVE security group — minimal projection {id, name}."""

    id: str = Field(..., description="Security group ID — use in `securityGroups`")
    name: str = Field("", description="Security group name — show this to the user")

    @classmethod
    def from_api(cls, g: dict) -> SecgroupItem:
        """Build a SecgroupItem from a raw vServer security-group dict."""
        return cls(id=g.get("id", ""), name=g.get("name", ""))


class SecgroupListData(BaseModel):
    """Wrapper for list_security_groups response (ACTIVE only)."""

    region: str = Field(..., description="Region these groups belong to (echoes the query region)")
    secgroups: list[SecgroupItem] = Field(
        default_factory=list, description="ACTIVE security groups (non-ACTIVE filtered out)"
    )


class PlacementGroupItem(BaseModel):
    """A placement group (vServer server group) — minimal projection {id, name}."""

    id: str = Field(
        ..., description="Placement group UUID — use as `placementGroupId` with type=EXISTING"
    )
    name: str = Field("", description="Placement group name — show this to the user")

    @classmethod
    def from_api(cls, g: dict) -> PlacementGroupItem:
        """Build a PlacementGroupItem from a raw vServer server-group dict."""
        return cls(id=g.get("uuid", ""), name=g.get("name", ""))


class PlacementGroupListData(BaseModel):
    """Wrapper for list_placement_groups response."""

    placement_groups: list[PlacementGroupItem] = Field(
        default_factory=list, description="List of placement groups"
    )


class VolumeTypeItem(BaseModel):
    """A volume type tier — the user picks one by IOPS; its id is the diskType."""

    id: str = Field(..., description="Volume type ID — use as `diskType` in create_nodegroup")
    iops: int | str = Field("", description="Provisioned IOPS — what the user chooses by")

    @classmethod
    def from_api(cls, v: dict) -> VolumeTypeItem:
        """Build a VolumeTypeItem from a raw vServer volume-type dict."""
        return cls(id=v.get("id", ""), iops=v.get("iops", ""))


class VolumeTypeListData(BaseModel):
    """Wrapper for list_volume_types response (volume types of one zone)."""

    region: str = Field(..., description="Region these volume types belong to")
    type_name: str = Field(
        "", description="Resolved disk type (NVME or SSD) these tiers belong to"
    )
    zone: str = Field("", description="Availability-zone uuid these volume types belong to")
    volume_types: list[VolumeTypeItem] = Field(
        default_factory=list, description="Volume types of the resolved type, one per IOPS tier"
    )


class QuotaData(BaseModel):
    """VKS quota for the current user in one region (get_quota response)."""

    region: str = Field("", description="Region this quota applies to (echoes the query region)")
    max_clusters: int | str = Field("", description="Maximum number of clusters allowed")
    num_clusters: int | str = Field("", description="Number of clusters currently in use")
    max_node_groups_per_cluster: int | str = Field(
        "", description="Maximum node groups per cluster"
    )
    max_nodes_per_node_group: int | str = Field("", description="Maximum nodes per node group")

    @classmethod
    def from_api(cls, q: dict, region: str = "") -> QuotaData:
        """Build a QuotaData from the raw VKS quota dict."""
        return cls(
            region=region,
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

    name: str = Field(
        ...,
        description=(
            "Node group name: 5-15 chars, lowercase letters + digits + hyphens, "
            "letter/digit at both ends"
        ),
    )
    flavorId: str = Field(..., description="Flavor ID (from list_flavors)")
    diskSize: int = Field(..., ge=20, le=5000, description="Disk size in GB (20-5000)")
    diskType: str = Field(..., description="Volume type ID (from list_volume_types)")
    numNodes: int = Field(..., ge=0, le=10, description="Number of nodes (0-10)")
    sshKeyId: str = Field(..., description="SSH key ID (from list_ssh_keys)")
    os: Literal["ubuntu", "linux", "rocky"] = Field("ubuntu", description="Node OS image type")
    enablePrivateNodes: bool = Field(
        False, description="false (default) = nodes get PUBLIC IPs; true = private-only nodes"
    )
    enabledEncryptionVolume: bool = Field(
        False, description="false (default) = node disks are NOT encrypted; true = encrypt them"
    )
    securityGroups: list[str] = Field(
        default_factory=list, description="Security group IDs (from list_security_groups)"
    )
    upgradeConfig: UpgradeConfig = Field(
        default_factory=UpgradeConfig, description="Upgrade config (SURGE 1/0 by default)"
    )
    subnetId: str = Field(
        ..., description="Subnet ID the nodes join (from list_subnets; the user's choice)"
    )
    secondarySubnets: Optional[list[str]] = Field(
        None,
        description=(
            "CILIUM_NATIVE_ROUTING clusters ONLY — there it is required: the "
            "`secondary_subnets` CIDRs of the chosen subnetId, copied verbatim from "
            "that subnet's list_subnets entry (e.g. ['10.5.60.0/22'], non-empty — "
            "the subnet must HAVE secondary subnets; CIDR strings, NOT sec-sub "
            "ids). Omit for any other networkType"
        ),
    )
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

    name: str = Field(
        ...,
        description=(
            "Cluster name: 5-20 chars, lowercase letters + digits + hyphens, "
            "letter/digit at both ends"
        ),
    )
    version: str = Field(..., description="Kubernetes version (from list_cluster_versions)")
    networkType: Literal["CILIUM_OVERLAY", "CILIUM_NATIVE_ROUTING", "TIGERA"] = Field(
        ..., description="Network type"
    )
    vpcId: str = Field(..., description="VPC ID (from list_vpcs)")
    releaseChannel: Literal["RAPID", "STABLE"] = Field("STABLE", description="Release channel")
    enablePrivateCluster: bool = Field(False, description="Whether the cluster is private")
    enabledLoadBalancerPlugin: bool = Field(True, description="Enable the load-balancer plugin")
    enabledBlockStoreCsiPlugin: bool = Field(True, description="Enable the block-store CSI plugin")
    enabledServiceEndpoint: Optional[bool] = Field(
        None,
        description=(
            "Service endpoint — PRIVATE clusters only (enablePrivateCluster=true), "
            "default true there. Omit for public clusters (not applicable)."
        ),
    )
    azStrategy: Literal["SINGLE", "MULTI"] = Field(
        "SINGLE", description="Availability-zone strategy"
    )
    description: Optional[str] = Field(
        None,
        description=(
            "Cluster description — ASCII only: letters, digits, spaces and "
            "'-_.@' (NO accented/Unicode characters), max 255 chars"
        ),
    )
    subnetId: Optional[str] = Field(None, description="Subnet ID (from list_subnets)")
    cidr: Optional[str] = Field(
        None, description="Required when networkType is CILIUM_OVERLAY or TIGERA"
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
    """Partial-update body for update_cluster (``PUT /v1/clusters/{id}``).

    All fields optional — send only what changes (the API no longer requires
    version/whitelistNodeCIDRs on every update). At least one field must be
    set; the handler rejects an empty body. (Name, description, and release
    channel are NOT editable via this endpoint.) Unknown fields are rejected
    (``extra="forbid"``).
    """

    model_config = ConfigDict(extra="forbid")

    version: Optional[str] = Field(
        None, description="Target Kubernetes version (from list_cluster_versions); omit to keep"
    )
    whitelistNodeCIDRs: Optional[list[str]] = Field(
        None, description="Whitelist node CIDRs; omit to leave unchanged"
    )
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
