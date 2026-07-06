"""Cluster management handler for GreenNode MCP Server."""

from __future__ import annotations

import re
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import Region, VksConfig
from greennode.vks_mcp_server.models import (
    ClusterDetail,
    ClusterListData,
    ClusterSummary,
    CreateClusterComboDto,
    UpdateClusterDto,
    format_cluster_detail,
)
from greennode.vks_mcp_server.validators import validate_id
from mcp import types
from pydantic import Field


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_CLUSTER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{3,18}[a-z0-9]$")

_VALID_NETWORK_TYPES = {"CILIUM_OVERLAY", "CILIUM_NATIVE_ROUTING", "TIGERA"}
_NETWORK_NEEDS_CIDR = {"CILIUM_OVERLAY", "TIGERA"}
_NETWORK_NEEDS_SECONDARY_SUBNETS = {"CILIUM_NATIVE_ROUTING"}

_REQUIRED_CLUSTER_FIELDS = ["vpcId", "networkType", "version", "releaseChannel"]


# ---------------------------------------------------------------------------
# Internal implementation functions
# ---------------------------------------------------------------------------

# These accept (client, arguments) and return list[types.TextContent].
# The ClusterHandler methods delegate to these after building an arguments dict.


async def _cluster_list(
    client: VksClient,
    arguments: dict,
) -> ClusterListData:
    params = {}
    if "page" in arguments:
        params["page"] = arguments["page"]
    if "pageSize" in arguments:
        params["pageSize"] = arguments["pageSize"]
    region = arguments.get("region")

    data = await client.get("/v1/clusters", region=region, params=params or None)
    items = data.get("items", data) if isinstance(data, dict) else data
    resolved_region = region or client._config.default_region
    return ClusterListData(
        region=resolved_region,
        clusters=[ClusterSummary.from_api(c) for c in items],
    )


async def _cluster_get(
    client: VksClient,
    arguments: dict,
) -> ClusterDetail:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    region = arguments.get("region")
    data = await client.get(f"/v1/clusters/{cluster_id}", region=region)
    return ClusterDetail.from_api(data)


async def _cluster_create(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    body = arguments["body"]
    poc = arguments.get("poc", False)
    auto_renewal = arguments.get("autoRenewal", True)
    region = arguments.get("region")
    params = {"poc": str(poc).lower(), "autoRenewal": str(auto_renewal).lower()}
    data = await client.post("/v1/clusters", region=region, params=params, json=body)
    name = data.get("name", data.get("uid", ""))
    text = f"Cluster **{name}** created successfully.\n" + format_cluster_detail(data)
    return [types.TextContent(type="text", text=text)]


async def _cluster_update(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    body = arguments["body"]
    region = arguments.get("region")
    data = await client.put(f"/v1/clusters/{cluster_id}", region=region, json=body)
    text = f"Cluster `{cluster_id}` updated successfully.\n" + format_cluster_detail(data)
    return [types.TextContent(type="text", text=text)]


async def _cluster_delete(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    region = arguments.get("region")
    await client.delete(f"/v1/clusters/{cluster_id}", region=region)
    text = f"Cluster `{cluster_id}` deleted successfully."
    return [types.TextContent(type="text", text=text)]


async def _cluster_get_kubeconfig(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    region = arguments.get("region")
    yaml_text = await client.get_raw(f"/v1/clusters/{cluster_id}/kubeconfig", region=region)
    return [types.TextContent(type="text", text=yaml_text)]


async def _cluster_get_events(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    region = arguments.get("region")
    params = {}
    if "page" in arguments:
        params["page"] = arguments["page"]
    if "pageSize" in arguments:
        params["pageSize"] = arguments["pageSize"]

    data = await client.get(
        f"/v1/clusters/{cluster_id}/events",
        region=region,
        params=params or None,
    )

    items = data.get("items", data) if isinstance(data, dict) else data
    if not items:
        text = f"No events found for cluster `{cluster_id}`."
        return [types.TextContent(type="text", text=text)]

    header = f"Events for cluster `{cluster_id}`:"
    table_header = "| # | Type | Reason | Message | Timestamp |"
    separator = "|---|---|---|---|---|"
    rows = []
    for i, ev in enumerate(items, start=1):
        ev_type = ev.get("type", "")
        reason = ev.get("reason", "")
        message = ev.get("message", "")
        ts = str(ev.get("lastTimestamp", ev.get("eventTime", ev.get("createdAt", ""))))[:19]
        rows.append(f"| {i} | {ev_type} | {reason} | {message} | {ts} |")

    text = "\n".join([header, table_header, separator] + rows)
    return [types.TextContent(type="text", text=text)]


async def _cluster_auto_upgrade_config(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    region = arguments.get("region")
    body = {"weekdays": arguments["weekdays"], "time": arguments["time"]}
    data = await client.put(
        f"/v1/clusters/{cluster_id}/auto-upgrade-config",
        region=region,
        json=body,
    )
    text = f"Auto-upgrade configuration for cluster `{cluster_id}` updated successfully.\n{data}"
    return [types.TextContent(type="text", text=text)]


async def _cluster_auto_upgrade_delete(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    region = arguments.get("region")
    await client.delete(
        f"/v1/clusters/{cluster_id}/auto-upgrade-config",
        region=region,
    )
    text = f"Auto-upgrade configuration for cluster `{cluster_id}` deleted successfully."
    return [types.TextContent(type="text", text=text)]


async def _cluster_delete_dryrun(
    client: VksClient,
    arguments: dict,
) -> list[types.TextContent]:
    cluster_id = arguments["cluster_id"]
    validate_id(cluster_id, "cluster_id")
    region = arguments.get("region")

    cluster = await client.get(f"/v1/clusters/{cluster_id}", region=region)
    ng_data = await client.get(f"/v1/clusters/{cluster_id}/node-groups", region=region)

    node_groups = ng_data.get("items", ng_data) if isinstance(ng_data, dict) else ng_data

    cluster_name = cluster.get("name", cluster_id)
    cluster_status = cluster.get("status", "")
    cluster_version = cluster.get("version", cluster.get("kubernetesVersion", ""))
    node_count = cluster.get("nodeCount", cluster.get("numNodes", ""))

    lines = [
        f"WARNING: YOU ARE ABOUT TO DELETE CLUSTER: **{cluster_name}**",
        "",
        "| Property | Value |",
        "|---|---|",
        f"| ID | {cluster_id} |",
        f"| Name | {cluster_name} |",
        f"| Status | {cluster_status} |",
        f"| Version | {cluster_version} |",
        f"| Node count | {node_count} |",
        "",
        f"**Node groups to be deleted ({len(node_groups)}):**",
        "",
        "| # | Name | ID | Node count |",
        "|---|---|---|---|",
    ]

    for i, ng in enumerate(node_groups, start=1):
        ng_name = ng.get("name", "")
        ng_id = ng.get("uid", ng.get("id", ""))
        ng_nodes = ng.get("nodeCount", ng.get("numNodes", ""))
        lines.append(f"| {i} | {ng_name} | {ng_id} | {ng_nodes} |")

    lines += ["", "**This action is irreversible. Confirm by calling `delete_cluster`.**"]

    text = "\n".join(lines)
    return [types.TextContent(type="text", text=text)]


def _cluster_create_validate(
    arguments: dict,
) -> list[types.TextContent]:
    body = arguments.get("body", arguments)
    errors = []

    # Validate cluster name
    name = body.get("name", "")
    if not _CLUSTER_NAME_RE.match(name):
        errors.append(
            f"Cluster name '{name}' is invalid. "
            "Must match ^[a-z0-9][a-z0-9\\-]{{3,18}}[a-z0-9]$"
        )

    # Check required fields
    for field in _REQUIRED_CLUSTER_FIELDS:
        if not body.get(field):
            errors.append(f"Missing required field: {field}")

    # enablePrivateCluster is a boolean - check presence not truthiness
    if "enablePrivateCluster" not in body:
        errors.append("Missing required field: enablePrivateCluster")

    # Check network-type-specific fields
    network_type = body.get("networkType", "")
    if network_type and network_type not in _VALID_NETWORK_TYPES:
        errors.append(
            f"networkType '{network_type}' is invalid. "
            "Must be one of: CILIUM_OVERLAY, CILIUM_NATIVE_ROUTING, TIGERA"
        )
    if network_type in _NETWORK_NEEDS_CIDR:
        if not body.get("cidr"):
            errors.append(f"networkType={network_type} requires 'cidr' field.")

    if network_type in _NETWORK_NEEDS_SECONDARY_SUBNETS:
        if not body.get("secondarySubnets"):
            errors.append(f"networkType={network_type} requires 'secondarySubnets' field.")

    if errors:
        text = "\n".join(errors)
    else:
        text = "valid"

    return [types.TextContent(type="text", text=text)]


# ---------------------------------------------------------------------------
# ClusterHandler class
# ---------------------------------------------------------------------------


class ClusterHandler:
    """Register and serve VKS cluster-management MCP tools."""

    def __init__(self, mcp, config: VksConfig, client: VksClient, allow_write: bool = False):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # Read-only tools (always registered)
        self.mcp.tool(name="list_clusters")(self.list_clusters)
        self.mcp.tool(name="get_cluster")(self.get_cluster)
        self.mcp.tool(name="get_cluster_kubeconfig")(self.get_cluster_kubeconfig)
        self.mcp.tool(name="get_cluster_events")(self.get_cluster_events)
        self.mcp.tool(name="delete_cluster_dryrun")(self.delete_cluster_dryrun)
        self.mcp.tool(name="validate_cluster_create")(self.validate_cluster_create)

        # Write tools (only registered if allow_write is True)
        if self.allow_write:
            self.mcp.tool(name="create_cluster")(self.create_cluster)
            self.mcp.tool(name="update_cluster")(self.update_cluster)
            self.mcp.tool(name="delete_cluster")(self.delete_cluster)
            self.mcp.tool(name="configure_auto_upgrade")(self.configure_auto_upgrade)
            self.mcp.tool(name="delete_auto_upgrade")(self.delete_auto_upgrade)
            self.mcp.tool(name="configure_auto_healing")(self.configure_auto_healing)

    async def list_clusters(
        self,
        page: int | None = Field(None, ge=0, description="Page number (starts at 0)"),
        pageSize: int | None = Field(
            None, ge=1, description="Number of clusters per page (default 50)"
        ),
        region: Region | None = Field(
            None, description="Region override, e.g. 'HCM-3' or 'HAN'. Defaults to config region"
        ),
    ) -> ClusterListData:
        """Returns a ClusterListData model (structured) with cluster summaries. Supports pagination."""
        args = {}
        if page is not None:
            args["page"] = page
        if pageSize is not None:
            args["pageSize"] = pageSize
        if region is not None:
            args["region"] = region
        return await _cluster_list(self.client, args)

    async def get_cluster(
        self,
        cluster_id: str = Field(
            ..., description="VKS Cluster ID, e.g. 'k8s-2ff9b24c-a58c-497c-b526-79630b0d3c92'"
        ),
        region: Region | None = Field(None, description="Region override"),
    ) -> ClusterDetail:
        """Returns a ClusterDetail model (structured) with all cluster properties."""
        return await _cluster_get(self.client, {"cluster_id": cluster_id, "region": region})

    async def create_cluster(
        self,
        body: CreateClusterComboDto = Field(
            ...,
            description=(
                "CreateClusterComboDto body. Required: name, version, networkType, vpcId. "
                "Creates the control plane only — add workers afterwards via create_nodegroup "
                "(the deprecated nodeGroups array is not accepted). Optional: enablePrivateCluster, "
                "releaseChannel, enabled{LoadBalancer,BlockStoreCsi,ServiceEndpoint}Plugin, "
                "azStrategy, description, subnetId, cidr, secondarySubnets, listSubnetIds, "
                "nodeNetmaskSize, autoUpgradeConfig, autoHealingConfig."
            ),
        ),
        poc: bool = Field(False, description="Whether this is a Proof-of-Concept cluster"),
        autoRenewal: bool = Field(
            True, description="Enable auto-renewal for cluster subscription"
        ),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Create a new VKS cluster.

        ## Requirements
        - Server must run with --allow-write
        - Call validate_cluster_create first; fix any reported errors before creating

        ## Workflow
        1. list_cluster_versions   -> choose version / releaseChannel
        2. validate_cluster_create -> confirm the body is valid
        3. create_cluster
        """
        args = {"body": body.model_dump(exclude_none=True), "poc": poc, "autoRenewal": autoRenewal}
        if region is not None:
            args["region"] = region
        result = await _cluster_create(self.client, args)
        return result[0].text

    async def update_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID to update"),
        body: UpdateClusterDto = Field(
            ...,
            description=(
                "UpdateClusterDto body. Required: version (target Kubernetes version) and "
                "whitelistNodeCIDRs. Optional plugin toggles: enabledLoadBalancerPlugin, "
                "enabledBlockStoreCsiPlugin (omit to leave unchanged). Name, description, and "
                "release channel are NOT editable here."
            ),
        ),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Update a VKS cluster's Kubernetes version, node whitelist CIDRs, and plugins.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - Use list_cluster_versions to choose a valid target version.
        """
        result = await _cluster_update(
            self.client,
            {
                "cluster_id": cluster_id,
                "body": body.model_dump(exclude_none=True),
                "region": region,
            },
        )
        return result[0].text

    async def delete_cluster(
        self,
        cluster_id: str = Field(
            ..., description="Cluster ID to delete. IRREVERSIBLE. Use delete_cluster_dryrun first."
        ),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Delete a VKS cluster. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - Call delete_cluster_dryrun first to preview what will be removed.
        """
        result = await _cluster_delete(
            self.client,
            {"cluster_id": cluster_id, "region": region},
        )
        return result[0].text

    async def get_cluster_kubeconfig(
        self,
        cluster_id: str = Field(..., description="Cluster ID to get kubeconfig for"),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Gets the kubeconfig YAML for a VKS cluster. Returns raw YAML text."""
        result = await _cluster_get_kubeconfig(
            self.client,
            {"cluster_id": cluster_id, "region": region},
        )
        return result[0].text

    async def get_cluster_events(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        page: int | None = Field(None, ge=0, description="Page number (starts at 0)"),
        pageSize: int | None = Field(None, ge=1, description="Items per page (default 20)"),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Gets events for a VKS cluster. Returns a markdown table of events."""
        args = {"cluster_id": cluster_id}
        if page is not None:
            args["page"] = page
        if pageSize is not None:
            args["pageSize"] = pageSize
        if region is not None:
            args["region"] = region
        result = await _cluster_get_events(self.client, args)
        return result[0].text

    async def configure_auto_upgrade(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        weekdays: str = Field(
            ..., description="Comma-separated days of week for auto-upgrade, e.g. 'Mon,Wed,Fri'"
        ),
        time: str = Field(..., description="Time of day in HH:mm format, e.g. '03:00'"),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Configure the auto-upgrade schedule for a VKS cluster.

        ## Requirements
        - Server must run with --allow-write
        """
        args = {"cluster_id": cluster_id, "weekdays": weekdays, "time": time}
        if region is not None:
            args["region"] = region
        result = await _cluster_auto_upgrade_config(self.client, args)
        return result[0].text

    async def delete_auto_upgrade(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Delete the auto-upgrade configuration for a VKS cluster.

        ## Requirements
        - Server must run with --allow-write
        """
        result = await _cluster_auto_upgrade_delete(
            self.client,
            {"cluster_id": cluster_id, "region": region},
        )
        return result[0].text

    async def configure_auto_healing(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        enable_auto_healing: bool = Field(
            ..., description="Enable or disable auto-healing for the cluster"
        ),
        max_unhealthy: str | None = Field(
            None,
            description="Max number or percentage of unhealthy nodes before remediation, e.g. '2' or '40%'",
        ),
        unhealthy_range: str | None = Field(
            None, description="Range of unhealthy nodes allowed before remediation, e.g. '[3-5]'"
        ),
        timeout_unhealthy: int | None = Field(
            None, ge=5, le=180, description="Minutes before considering a node unhealthy (5-180)"
        ),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Configure auto-healing for a VKS cluster.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(cluster_id, "cluster_id")
        body: dict = {"enableAutoHealing": enable_auto_healing}
        if max_unhealthy is not None:
            body["maxUnhealthy"] = max_unhealthy
        if unhealthy_range is not None:
            body["unhealthyRange"] = unhealthy_range
        if timeout_unhealthy is not None:
            body["timeoutUnhealthy"] = timeout_unhealthy
        data = await self.client.patch(
            f"/v1/clusters/{cluster_id}/auto-healing-config", region=region, json=body
        )
        return (
            f"Auto-healing configuration for cluster `{cluster_id}` "
            f"updated successfully (enabled={enable_auto_healing}).\n{data}"
        )

    async def delete_cluster_dryrun(
        self,
        cluster_id: str = Field(..., description="Cluster ID to preview deletion for"),
        region: Region | None = Field(None, description="Region override"),
    ) -> str:
        """Preview what will be deleted when deleting a cluster. Shows cluster info and all node groups that will be removed."""
        result = await _cluster_delete_dryrun(
            self.client,
            {"cluster_id": cluster_id, "region": region},
        )
        return result[0].text

    def validate_cluster_create(
        self,
        body: CreateClusterComboDto = Field(
            ...,
            description="CreateClusterComboDto body to validate. Checks name regex, required fields, disk size, node count, network type logic.",
        ),
    ) -> str:
        """Validates a CreateClusterComboDto body without actually creating a cluster. Returns 'valid' or a list of validation errors."""
        result = _cluster_create_validate({"body": body.model_dump(exclude_none=True)})
        return result[0].text
