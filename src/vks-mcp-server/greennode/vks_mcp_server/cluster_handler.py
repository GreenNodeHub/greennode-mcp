"""Cluster management handler for GreenNode MCP Server."""

from __future__ import annotations

import re
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import Region, VksConfig
from greennode.vks_mcp_server.kubeconfig import extract_kubeconfig
from greennode.vks_mcp_server.models import (
    ClusterDetail,
    ClusterListData,
    ClusterSummary,
    CreateClusterComboDto,
    UpdateClusterDto,
    format_cluster_detail,
)
from greennode.vks_mcp_server.paging import fetch_all_vks_items
from greennode.vks_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vks_mcp_server.validators import validate_id
from mcp import types
from pydantic import Field


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_CLUSTER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{3,18}[a-z0-9]$")
# API contract for description (F-05a): ASCII subset only, max 255.
_DESCRIPTION_RE = re.compile(r"^[a-zA-Z0-9-_. @]{0,255}$")

_VALID_NETWORK_TYPES = {"CILIUM_OVERLAY", "CILIUM_NATIVE_ROUTING", "TIGERA"}
_NETWORK_NEEDS_CIDR = {"CILIUM_OVERLAY", "TIGERA"}

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
    """Fetch every cluster in the region (all pages)."""
    region = arguments.get("region")
    resolved_region = region or client._config.default_region
    collected = await fetch_all_vks_items(client, "/v1/clusters", region=region)
    return ClusterListData(
        region=resolved_region,
        clusters=[ClusterSummary.from_api(c) for c in collected],
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
    raw = await client.get_raw(f"/v1/clusters/{cluster_id}/kubeconfig", region=region)
    return [types.TextContent(type="text", text=extract_kubeconfig(raw))]


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
    text = f"Auto-upgrade configuration for cluster `{cluster_id}` updated successfully."
    if data:
        text += f"\n{data}"
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
    # Page through: the preview must list EVERY node group that will be deleted.
    node_groups = await fetch_all_vks_items(
        client, f"/v1/clusters/{cluster_id}/node-groups", region=region
    )

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

    # description: the API rejects anything outside its ASCII subset (400)
    desc = body.get("description")
    if desc is not None and not _DESCRIPTION_RE.match(desc):
        errors.append(
            "description: only ASCII letters, digits, spaces and '-_.@' are "
            "allowed (no accented characters), max 255 chars — "
            "must match ^[a-zA-Z0-9-_. @]{0,255}$"
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

    def __init__(
        self,
        mcp,
        config: VksConfig,
        client: VksClient,
        allow_write: bool = False,
        allow_sensitive_data_access: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write
        self.allow_sensitive_data_access = allow_sensitive_data_access

        # Read-only tools (always registered)
        self.mcp.tool(name="list_clusters", annotations=READ)(self.list_clusters)
        self.mcp.tool(name="get_cluster", annotations=READ)(self.get_cluster)
        self.mcp.tool(name="get_cluster_kubeconfig", annotations=READ)(self.get_cluster_kubeconfig)
        self.mcp.tool(name="get_cluster_events", annotations=READ)(self.get_cluster_events)
        self.mcp.tool(name="delete_cluster_dryrun", annotations=READ)(self.delete_cluster_dryrun)
        self.mcp.tool(name="validate_cluster_create", annotations=READ)(
            self.validate_cluster_create
        )

        # Write tools (only registered if allow_write is True)
        if self.allow_write:
            self.mcp.tool(name="create_cluster", annotations=WRITE)(self.create_cluster)
            self.mcp.tool(name="update_cluster", annotations=WRITE)(self.update_cluster)
            self.mcp.tool(name="delete_cluster", annotations=DESTRUCTIVE)(self.delete_cluster)
            self.mcp.tool(name="configure_auto_upgrade", annotations=WRITE)(
                self.configure_auto_upgrade
            )
            self.mcp.tool(name="delete_auto_upgrade", annotations=DESTRUCTIVE)(
                self.delete_auto_upgrade
            )
            self.mcp.tool(name="configure_auto_healing", annotations=WRITE)(
                self.configure_auto_healing
            )
            self.mcp.tool(name="generate_kubeconfig", annotations=WRITE)(self.generate_kubeconfig)

    async def list_clusters(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Region to list clusters in: 'HCM-3' or 'HAN'. Clusters are "
                "region-scoped — if the user's cluster is not in the result, "
                "retry with the other region before concluding it doesn't exist."
            ),
        ),
    ) -> ClusterListData:
        """List every VKS cluster in a region (all pages fetched automatically).

        Returns ClusterListData: the queried `region` plus ClusterSummary items
        {id, name, status, version, ...}. Use this to resolve a cluster name the
        user mentions to its `id`, then call get_cluster for full detail —
        exactly one match: use it; several matches: list them and ask the user.
        """
        return await _cluster_list(self.client, {"region": region})

    async def get_cluster(
        self,
        cluster_id: str = Field(
            ...,
            description=(
                "VKS Cluster ID, e.g. 'k8s-2ff9b24c-a58c-497c-b526-79630b0d3c92'. "
                "Resolve it from a name via list_clusters."
            ),
        ),
        region: Region = Field("HCM-3", description="Region the cluster lives in"),
    ) -> ClusterDetail:
        """Get full detail of one VKS cluster.

        Returns ClusterDetail (structured): status, version, network type, the
        cluster's `vpc_id` and `subnet_id`, plugin toggles, and whitelist CIDRs.

        ## Workflow
        - create_nodegroup flow, step 1: this tool is the source of `vpc_id`
          (feed it to list_subnets) — and run every later discovery call in
          this cluster's region.
        - After create/update/delete operations, poll this tool until `status`
          is ACTIVE (or the cluster is gone).
        """
        return await _cluster_get(self.client, {"cluster_id": cluster_id, "region": region})

    async def create_cluster(
        self,
        body: CreateClusterComboDto = Field(
            ...,
            description=(
                "CreateClusterComboDto body. Required: name, version, networkType, vpcId. "
                "Creates the control plane only — add workers afterwards via create_nodegroup "
                "(the deprecated nodeGroups array is not accepted). Optional: enablePrivateCluster, "
                "releaseChannel, enabledLoadBalancerPlugin, enabledBlockStoreCsiPlugin, "
                "enabledServiceEndpoint (private clusters only, default true), "
                "azStrategy, description, subnetId, cidr, listSubnetIds, "
                "nodeNetmaskSize, autoUpgradeConfig, autoHealingConfig. Secondary "
                "subnets are NOT set on the cluster — each node group sets its own "
                "secondarySubnets at creation."
            ),
        ),
        poc: bool = Field(False, description="Whether this is a Proof-of-Concept cluster"),
        autoRenewal: bool = Field(
            True, description="Enable auto-renewal for cluster subscription"
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Create a new VKS cluster (control plane only — workers come later).

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        1. get_creation_guide(resource="cluster") -> conduct the whole
           conversation exactly as it says (question order, one setting per
           question, confirm gate).
        2. Resolve ids via discovery, all in the target region: get_quota
           first -> list_vpcs (vpcId) -> list_cluster_versions (version) ->
           list_subnets (subnetId / listSubnetIds).
        3. validate_cluster_create -> fix every reported error -> present the
           FULL body in the same message as the confirmation question ->
           create_cluster, then poll get_cluster until ACTIVE (~15-20 min)
           and add workers via create_nodegroup.

        IMPORTANT: call get_creation_guide FIRST, and never invent an id —
        `vpcId` and every subnet id come from the discovery tools.
        """
        args = {"body": body.model_dump(exclude_none=True), "poc": poc, "autoRenewal": autoRenewal}
        if region is not None:
            args["region"] = region
        try:
            result = await _cluster_create(self.client, args)
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}\nTip: call get_creation_guide(resource='cluster') for the "
                "required flow and field rules, then rebuild the body."
            ) from exc
        return result[0].text

    async def update_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID to update"),
        body: UpdateClusterDto = Field(
            ...,
            description=(
                "Partial-update body — send ONLY the fields to change: version "
                "(target Kubernetes version), whitelistNodeCIDRs, and plugin toggles "
                "enabledLoadBalancerPlugin / enabledBlockStoreCsiPlugin. At least one "
                "field required. Name, description, and release channel are NOT "
                "editable here."
            ),
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Update a VKS cluster: version, node whitelist CIDRs, and/or plugins.

        Partial update — only the fields present in the body are changed.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - When changing `version`: use list_cluster_versions to pick a valid
          target first.
        """
        wire_body = body.model_dump(exclude_none=True)
        if not wire_body:
            return (
                "Nothing to update: the body is empty. Set at least one of version, "
                "whitelistNodeCIDRs, enabledLoadBalancerPlugin, enabledBlockStoreCsiPlugin."
            )
        result = await _cluster_update(
            self.client,
            {
                "cluster_id": cluster_id,
                "body": wire_body,
                "region": region,
            },
        )
        return result[0].text

    async def delete_cluster(
        self,
        cluster_id: str = Field(
            ..., description="Cluster ID to delete. IRREVERSIBLE. Use delete_cluster_dryrun first."
        ),
        region: Region = Field("HCM-3", description="Region override"),
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
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Gets the kubeconfig YAML for a VKS cluster. Returns raw YAML text.

        ## Requirements
        - Server must run with --allow-sensitive-data-access (the kubeconfig
          carries cluster-admin credentials)

        ## Workflow
        - A NEW cluster has no kubeconfig until one is generated: call
          generate_kubeconfig(cluster_id) once, then poll this tool until it
          returns YAML (generation is asynchronous).
        """
        if not self.allow_sensitive_data_access:
            raise RuntimeError(
                "Access denied: the kubeconfig carries cluster-admin credentials "
                "(certificate + private key); reading it requires the "
                "--allow-sensitive-data-access flag."
            )
        result = await _cluster_get_kubeconfig(
            self.client,
            {"cluster_id": cluster_id, "region": region},
        )
        return result[0].text

    async def generate_kubeconfig(
        self,
        cluster_id: str = Field(..., description="Cluster ID to generate a kubeconfig for"),
        expiration_days: int = Field(
            ...,
            ge=1,
            le=365,
            description=(
                "Days until the kubeconfig expires (1-365). A user decision — "
                "ask the user for the value; never pick one for them."
            ),
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Generate (mint) a kubeconfig for a VKS cluster.

        ## Requirements
        - Server must run with --allow-write

        ## Workflow
        - Required once for a NEW cluster before get_cluster_kubeconfig or any
          Kubernetes tool can work. Generation is asynchronous: after calling
          this, poll get_cluster_kubeconfig until it returns YAML.
        """
        validate_id(cluster_id, "cluster_id")
        await self.client.post(
            f"/v1/clusters/{cluster_id}/kubeconfig",
            region=region,
            json={"expirationDays": expiration_days},
        )
        return (
            f"Kubeconfig generation requested for cluster `{cluster_id}` "
            f"(expires in {expiration_days} days). Generation is asynchronous — "
            "poll get_cluster_kubeconfig until it returns YAML."
        )

    async def get_cluster_events(
        self,
        cluster_id: str = Field(..., description="Cluster ID"),
        page: int | None = Field(None, ge=0, description="Page number (starts at 0)"),
        pageSize: int | None = Field(None, ge=1, description="Items per page (default 20)"),
        region: Region = Field("HCM-3", description="Region override"),
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
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Set (or change) the weekly auto-upgrade schedule of a VKS cluster.

        Enabling auto-upgrade = calling this with the desired schedule
        (weekdays + time). Turning it OFF is a separate tool:
        delete_auto_upgrade. To only READ the current schedule, use
        get_cluster (auto_upgrade_config) instead.

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
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Turn OFF auto-upgrade for a VKS cluster (delete its schedule).

        The counterpart of configure_auto_upgrade; re-enable any time by
        configuring a new schedule.

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
        region: Region = Field("HCM-3", description="Region override"),
    ) -> str:
        """Enable, disable, or tune auto-healing for a VKS cluster.

        This is the ONE tool for every auto-healing change: turn it on
        (enable_auto_healing=true), turn it off (false), and tune when nodes
        are remediated — maxUnhealthy (count or percentage, e.g. '2' or
        '40%'), unhealthyRange (e.g. '[3-5]'), timeoutUnhealthy (minutes,
        5-180). To only READ the current auto-healing config, use get_cluster
        instead.

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
        text = (
            f"Auto-healing configuration for cluster `{cluster_id}` "
            f"updated successfully (enabled={enable_auto_healing})."
        )
        if data:
            text += f"\n{data}"
        return text

    async def delete_cluster_dryrun(
        self,
        cluster_id: str = Field(..., description="Cluster ID to preview deletion for"),
        region: Region = Field("HCM-3", description="Region override"),
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
