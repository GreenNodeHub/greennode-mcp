"""Node group management handler for GreenNode MCP Server."""

from __future__ import annotations

import json
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import Region, VksConfig
from greennode.vks_mcp_server.models import (
    CreateNodeGroupDto,
    NodeGroupDetail,
    NodeGroupListData,
    NodeGroupSummary,
    NodeItem,
    NodesData,
    UpdateNodeGroupDto,
    UpdateNodeGroupMetadataDto,
)
from greennode.vks_mcp_server.paging import fetch_all_vks_items
from greennode.vks_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vks_mcp_server.validators import validate_id
from pydantic import Field


# ---------------------------------------------------------------------------
# Internal implementation functions
# ---------------------------------------------------------------------------


async def _nodegroup_list(
    config: VksConfig,
    client: VksClient,
    cluster_id: str,
    region: Region | None = None,
) -> NodeGroupListData:
    """Fetch node groups and cluster name, return NodeGroupListData model.

    Args:
        config: VKS configuration.
        client: VKS API client.
        cluster_id: VKS Cluster ID.
        region: Optional region override.

    Returns:
        NodeGroupListData model (structured) with node groups for the cluster.
    """
    validate_id(cluster_id, "cluster_id")
    items = await fetch_all_vks_items(
        client, f"/v1/clusters/{cluster_id}/node-groups", region=region
    )

    # Try to get cluster name
    cluster_name = cluster_id
    try:
        cluster = await client.get(f"/v1/clusters/{cluster_id}", region=region)
        cluster_name = cluster.get("name", cluster_id)
    except Exception:
        pass

    return NodeGroupListData(
        region=region or config.default_region,
        cluster_name=cluster_name,
        node_groups=[NodeGroupSummary.from_api(ng) for ng in items],
    )


async def _nodegroup_delete_dryrun(
    config: VksConfig,
    client: VksClient,
    cluster_id: str,
    nodegroup_id: str,
    region: str | None = None,
) -> str:
    """Preview node group deletion."""
    validate_id(cluster_id, "cluster_id")
    validate_id(nodegroup_id, "nodegroup_id")
    ng = await client.get(
        f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}",
        region=region,
    )

    name = ng.get("name", nodegroup_id)
    ngid = ng.get("uid", "") or ng.get("id", nodegroup_id)
    status = ng.get("status", "")
    node_count = ng.get("nodeCount", ng.get("numNodes", ""))

    warning = (
        f"WARNING: YOU ARE ABOUT TO DELETE NODE GROUP:\n\n"
        f"| Property | Value |\n"
        f"|---|---|\n"
        f"| Name | {name} |\n"
        f"| ID | {ngid} |\n"
        f"| Cluster ID | {cluster_id} |\n"
        f"| Status | {status} |\n"
        f"| Node count | {node_count} |\n\n"
        f"This action is IRREVERSIBLE. Call `delete_nodegroup` to confirm deletion."
    )

    return warning


# ---------------------------------------------------------------------------
# NodeGroupHandler class
# ---------------------------------------------------------------------------


class NodeGroupHandler:
    """Register and serve VKS node-group-management MCP tools."""

    def __init__(self, mcp, config: VksConfig, client: VksClient, allow_write: bool = False):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # Read-only tools
        self.mcp.tool(name="list_nodegroups", annotations=READ)(self.list_nodegroups)
        self.mcp.tool(name="get_nodegroup", annotations=READ)(self.get_nodegroup)
        self.mcp.tool(name="list_nodes", annotations=READ)(self.list_nodes)
        self.mcp.tool(name="delete_nodegroup_dryrun", annotations=READ)(
            self.delete_nodegroup_dryrun
        )

        # Write tools
        if self.allow_write:
            self.mcp.tool(name="create_nodegroup", annotations=WRITE)(self.create_nodegroup)
            self.mcp.tool(name="update_nodegroup", annotations=WRITE)(self.update_nodegroup)
            self.mcp.tool(name="update_nodegroup_metadata", annotations=WRITE)(
                self.update_nodegroup_metadata
            )
            self.mcp.tool(name="delete_nodegroup", annotations=DESTRUCTIVE)(self.delete_nodegroup)
            self.mcp.tool(name="upgrade_nodegroup_version", annotations=DESTRUCTIVE)(
                self.upgrade_nodegroup_version
            )

    async def list_nodegroups(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> NodeGroupListData:
        """Lists all node groups in a VKS cluster.

        Returns a NodeGroupListData model (structured) containing the cluster name and
        a list of NodeGroupSummary items. Call .to_markdown() to render as a table.
        """
        return await _nodegroup_list(
            self.config,
            self.client,
            cluster_id=cluster_id,
            region=region,
        )

    async def get_nodegroup(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(
            ..., description="Node Group ID, e.g. 'ng-f5674ebc-30be-47e2-b4ef-5d4474deae58'"
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> NodeGroupDetail:
        """Gets full detail of a specific node group.

        Returns a NodeGroupDetail model (structured). Call .to_markdown() to render as
        a key-value table.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(nodegroup_id, "nodegroup_id")
        ng = await self.client.get(
            f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}",
            region=region,
        )
        return NodeGroupDetail.from_api(ng)

    async def create_nodegroup(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID to add the node group to"),
        body: CreateNodeGroupDto = Field(
            ...,
            description=(
                "CreateNodeGroupDto body. Required: name, flavorId, diskType, sshKeyId, "
                "diskSize (20-5000), numNodes (0-10), subnetId. Optional groups — offer "
                "each to the user (see the tool's Workflow): os (ubuntu|linux|rocky, "
                "default ubuntu) and upgradeConfig; networking/security "
                "(enablePrivateNodes, enabledEncryptionVolume, securityGroups, "
                "secondarySubnets); scaling (autoScaleConfig); scheduling metadata "
                "(labels, taints, tags); placement (placementGroupConfigDto)."
            ),
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Create a new node group in a VKS cluster.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow (run every discovery call in the cluster's region)
        Ask the user in THIS order — ONE setting per question. Never bundle
        two settings into one question (e.g. public/private together with
        os), and never merge several optional steps into one multi-select —
        each step below is its own question. Never skip a step silently.
        1. get_cluster(cluster_id) -> the cluster's `vpcId` and region;
           get_quota to check node-group/node limits before starting.
        2. Ask `name` and `numNodes` (0-10).
        3. Ask public/private: enablePrivateNodes (default false = nodes get
           PUBLIC IPs).
        4. Ask `os`: ubuntu (default) | linux | rocky.
        5. list_subnets(vpc_id) -> user picks a subnet -> `subnetId`. Its
           availability zone scopes flavors and volume types — the tools
           derive it themselves.
        6. Optional: securityGroups (ids via list_security_groups);
           secondarySubnets if needed.
        7. list_flavors(cluster_id, subnet_id) -> user picks -> `flavorId`.
        8. Volume settings: list_volume_types(cluster_id, subnet_id) -> user
           picks an IOPS tier -> `diskType` (a volume-type id, never a string
           like "SSD"); ask `diskSize` (20-5000 GB); ask
           enabledEncryptionVolume (default false = disks unencrypted).
        9. list_ssh_keys -> user picks -> `sshKeyId`.
        10. Optional: autoScaleConfig (min/max — interacts with numNodes).
        11. Optional: upgradeConfig (surge behaviour; default SURGE
            maxSurge=1 / maxUnavailable=0).
        12. Optional: placementGroupConfigDto (ids via list_placement_groups,
            type=EXISTING).
        13. Optional: scheduling metadata — labels, taints, tags.
        14. Present the FULL body — every field with its value, defaults
            marked — in the SAME message as the confirmation question, the
            table immediately above it. Never ask "confirm?" while only
            referring back to configuration shown earlier ("the config
            above"). Wait for explicit confirmation before calling.

        IMPORTANT: resolve every id above via the discovery tools — never
        invent one. Full guided flow: prompt `vks_create_nodegroup`.
        """
        validate_id(cluster_id, "cluster_id")
        result = await self.client.post(
            f"/v1/clusters/{cluster_id}/node-groups",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return f"Node group created successfully:\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"

    async def update_nodegroup(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(..., description="Node Group ID to update"),
        body: UpdateNodeGroupDto = Field(
            ...,
            description=(
                "Update body. No fields required. Optional: numNodes (0-10), securityGroups, "
                "autoScaleConfig, upgradeConfig. To change labels/tags/taints use "
                "update_nodegroup_metadata."
            ),
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Update a node group's size, security groups, autoscaling, or upgrade config.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - Labels, tags, and taints are updated separately via update_nodegroup_metadata.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(nodegroup_id, "nodegroup_id")
        payload = body.model_dump(exclude_none=True)
        if not payload:
            return (
                "Nothing to update: provide at least one of numNodes, securityGroups, "
                "autoScaleConfig, or upgradeConfig (use update_nodegroup_metadata for "
                "labels/tags/taints)."
            )
        result = await self.client.put(
            f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}",
            region=region,
            json=payload,
        )
        return f"Node group updated successfully:\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"

    async def update_nodegroup_metadata(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(..., description="Node Group ID"),
        body: UpdateNodeGroupMetadataDto = Field(
            ...,
            description=(
                "Metadata body. No fields required, but at least one must be set: "
                "labels, tags, taints."
            ),
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Update a node group's labels, tags, and taints.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - Targets the node group's /metadata endpoint (PATCH), separate from update_nodegroup.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(nodegroup_id, "nodegroup_id")
        payload = body.model_dump(exclude_none=True)
        if not payload:
            return "Nothing to update: provide at least one of labels, tags, or taints."
        result = await self.client.patch(
            f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}/metadata",
            region=region,
            json=payload,
        )
        return f"Node group metadata updated successfully:\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"

    async def delete_nodegroup(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(..., description="Node Group ID to delete. IRREVERSIBLE."),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Delete a node group. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - Call delete_nodegroup_dryrun first to preview what will be removed.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(nodegroup_id, "nodegroup_id")
        await self.client.delete(
            f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}",
            region=region,
        )
        return (
            f"Delete request for node group `{nodegroup_id}`"
            f" in cluster `{cluster_id}` submitted successfully."
        )

    async def upgrade_nodegroup_version(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(..., description="Node Group ID to upgrade"),
        kubernetes_version: str = Field(
            ...,
            description="Target Kubernetes version. Use list_cluster_versions to see valid versions.",
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Upgrade a node group's Kubernetes version (irreversible — no downgrade).

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        1. get_cluster -> the control plane `version`. A node group can never be
           newer than the control plane — raise it first via update_cluster if
           needed.
        2. get_nodegroup -> current version and `upgradeConfig` (surge behaviour
           of the rolling node replacement).
        3. Call this tool, then poll get_nodegroup until `status` is ACTIVE again.

        IMPORTANT: this rolls every node in the group and cannot be rolled back.
        Present current -> target version to the user and wait for explicit
        confirmation before calling.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(nodegroup_id, "nodegroup_id")
        result = await self.client.post(
            f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}/upgrade-version",
            region=region,
            json={"kubernetesVersion": kubernetes_version},
        )
        return (
            f"Node group `{nodegroup_id}` upgrade to Kubernetes version "
            f"`{kubernetes_version}` requested successfully.\n```json\n"
            f"{json.dumps(result, indent=2, ensure_ascii=False)}\n```"
        )

    async def list_nodes(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(..., description="Node Group ID"),
        region: Region = Field("HCM-3", description="Region the cluster lives in"),
    ) -> NodesData:
        """List every node of a node group (all pages fetched automatically).

        Returns a NodesData model (structured) with the nodes of a node group.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(nodegroup_id, "nodegroup_id")
        nodes = await fetch_all_vks_items(
            self.client,
            f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}/nodes",
            region=region,
        )
        return NodesData(
            region=region or self.config.default_region,
            nodegroup_id=nodegroup_id,
            nodes=[NodeItem.from_api(n) for n in nodes],
        )

    async def delete_nodegroup_dryrun(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(..., description="Node Group ID to preview deletion for"),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Preview what will be deleted when deleting a node group."""
        return await _nodegroup_delete_dryrun(
            self.config,
            self.client,
            cluster_id=cluster_id,
            nodegroup_id=nodegroup_id,
            region=region,
        )
