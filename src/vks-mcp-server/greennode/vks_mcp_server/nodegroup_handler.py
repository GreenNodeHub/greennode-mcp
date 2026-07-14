"""Node group management handler for GreenNode MCP Server."""

from __future__ import annotations

import json
import re
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import Region, VksConfig
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
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


_NODEGROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{3,13}[a-z0-9]$")


class NodeGroupHandler:
    """Register and serve VKS node-group-management MCP tools."""

    def __init__(
        self,
        mcp,
        config: VksConfig,
        client: VksClient,
        allow_write: bool = False,
        cache: DiscoveryCache | None = None,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write
        # Shared with the discovery tools when the server wires it; a private
        # instance otherwise (tests, standalone use).
        self.cache = cache or DiscoveryCache()

        # Read-only tools
        self.mcp.tool(name="validate_nodegroup_create", annotations=READ)(
            self.validate_nodegroup_create
        )
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

    async def validate_nodegroup_create(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID the node group will join"),
        body: CreateNodeGroupDto = Field(
            ..., description="The exact body you intend to pass to create_nodegroup"
        ),
    ) -> str:
        """Validate a create_nodegroup body BEFORE creating — free and non-mutating.

        Local rules (name 5-15 chars: lowercase + digits + hyphens, letter/digit
        at both ends; autoscale bounds) plus cross-checks against live discovery
        (cached): the subnet belongs to the cluster's VPC, flavorId and diskType
        exist in the subnet's availability zone, sshKeyId and securityGroups
        exist in the cluster's region. Returns "valid" or every problem found,
        each with the discovery tool that fixes it.

        ## Workflow
        - create_nodegroup flow: call this right after collecting all settings
          and BEFORE presenting the final plan — fix every reported error,
          re-validate, then present the plan and create.
        """
        validate_id(cluster_id, "cluster_id")
        errors: list[str] = []

        if not _NODEGROUP_NAME_RE.match(body.name):
            errors.append(
                "name: must be 5-15 chars (lowercase letters, digits, hyphens; "
                "letter/digit at both ends)"
            )
        if body.autoScaleConfig and body.autoScaleConfig.minSize > body.autoScaleConfig.maxSize:
            errors.append("autoScaleConfig: minSize must be <= maxSize")
        if not body.subnetId:
            errors.append("subnetId: required — let the user pick one via list_subnets")
            return self._validation_report(errors)

        from greennode.vks_mcp_server.discovery_handler import (
            _flavor_list,
            _resolve_zone_context,
            _secgroup_list,
            _sshkey_list,
            _volumetype_list,
        )

        try:
            region, zone = await _resolve_zone_context(
                self.config, self.client, self.cache, cluster_id, body.subnetId
            )
        except ValueError as exc:
            errors.append(str(exc))
            return self._validation_report(errors)

        flavors = await _flavor_list(
            self.config, self.client, self.cache, zone=zone, region=region
        )
        if body.flavorId not in {f.id for f in flavors.flavors}:
            errors.append(
                f"flavorId '{body.flavorId}' is not an available worker flavor in zone "
                f"{zone} — pick from list_flavors(cluster_id, subnet_id)"
            )

        vtypes = await _volumetype_list(
            self.config, self.client, self.cache, zone=zone, region=region
        )
        if body.diskType not in {v.id for v in vtypes.volume_types}:
            errors.append(
                f"diskType '{body.diskType}' is not a volume-type id in zone {zone} — "
                "pick from list_volume_types(cluster_id, subnet_id)"
            )

        keys = await _sshkey_list(self.config, self.client, self.cache, region=region)
        if body.sshKeyId not in {k.id for k in keys.ssh_keys}:
            errors.append(
                f"sshKeyId '{body.sshKeyId}' does not exist in region {region} — "
                "pick from list_ssh_keys"
            )

        if body.securityGroups:
            sgs = await _secgroup_list(self.config, self.client, self.cache, region=region)
            unknown = set(body.securityGroups) - {g.id for g in sgs.secgroups}
            if unknown:
                errors.append(
                    f"securityGroups {sorted(unknown)} do not exist in region {region} — "
                    "pick from list_security_groups"
                )

        return self._validation_report(errors)

    @staticmethod
    def _validation_report(errors: list[str]) -> str:
        if not errors:
            return "valid"
        return "invalid:\n- " + "\n- ".join(errors)

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

        ## Workflow
        1. get_creation_guide(resource="nodegroup") -> conduct the whole
           conversation exactly as it says (question order, one setting per
           question, confirm gate).
        2. Resolve ids via discovery, all in the cluster's region:
           get_cluster (vpcId) -> list_subnets (subnetId; its availability
           zone scopes the next two) -> list_flavors(cluster_id, subnet_id)
           (flavorId) -> list_volume_types(cluster_id, subnet_id) (diskType)
           -> list_ssh_keys (sshKeyId); get_quota before starting.
        3. validate_nodegroup_create with the body -> fix every reported
           error, then present the FULL body in the same message as the
           confirmation question, wait for explicit confirmation, then call
           and poll get_nodegroup until ACTIVE.

        IMPORTANT: call get_creation_guide FIRST, and never invent an id —
        every id above comes from a discovery tool.
        """
        validate_id(cluster_id, "cluster_id")
        try:
            result = await self.client.post(
                f"/v1/clusters/{cluster_id}/node-groups",
                region=region,
                json=body.model_dump(exclude_none=True),
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}\nTip: call get_creation_guide(resource='nodegroup') for the "
                "required flow and field rules, then rebuild the body."
            ) from exc
        text = "Node group created successfully."
        if result:
            text += f"\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"
        return text

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
        text = "Node group updated successfully."
        if result:
            text += f"\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"
        return text

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
        text = "Node group metadata updated successfully."
        if result:
            text += f"\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"
        return text

    async def delete_nodegroup(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        nodegroup_id: str = Field(..., description="Node Group ID to delete. IRREVERSIBLE."),
        force_delete: bool = Field(
            False,
            description=(
                "Force the deletion on the API side (forceDelete=true). Use ONLY "
                "as an escalation after a normal delete failed or the node group "
                "is stuck (e.g. ERROR state) — and confirm with the user first."
            ),
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Delete a node group. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - Call delete_nodegroup_dryrun first to preview what will be removed.
        - If a normal delete fails or the node group is stuck, ask the user
          before retrying with force_delete=true.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(nodegroup_id, "nodegroup_id")
        await self.client.delete(
            f"/v1/clusters/{cluster_id}/node-groups/{nodegroup_id}",
            region=region,
            params={"forceDelete": "true"} if force_delete else None,
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
