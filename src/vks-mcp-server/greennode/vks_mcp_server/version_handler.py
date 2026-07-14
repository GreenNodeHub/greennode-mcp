"""Version and image handler for GreenNode MCP Server."""

from __future__ import annotations

from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import Region, VksConfig
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.models import VersionItem, VersionsData
from greennode.vks_mcp_server.tool_annotations import READ
from pydantic import Field


# ---------------------------------------------------------------------------
# Internal implementation functions
# ---------------------------------------------------------------------------


async def _cluster_versions_list(
    config: VksConfig,
    client: VksClient,
    region: str | None = None,
) -> VersionsData:
    """Fetch available Kubernetes cluster versions as structured data."""
    data = await client.get("/v1/cluster-versions", region=region)
    if isinstance(data, dict):
        items = data.get("items", data.get("data", []))
    elif isinstance(data, list):
        items = data
    else:
        items = []
    if not isinstance(items, list):
        items = []
    items = [v for v in items if isinstance(v, dict) and v.get("enable", True)]

    stable = [
        v for v in items if v.get("stage", "").upper() == "STABLE" and not v.get("deprecatedAt")
    ]
    stable.sort(key=lambda v: v.get("version", ""), reverse=True)
    recommended_name = stable[0].get("version", "") if stable else ""

    versions = [
        VersionItem(
            version=v.get("version", ""),
            stage=v.get("stage", ""),
            deprecated_at=v.get("deprecatedAt") or "",
            recommended=v.get("version", "") == recommended_name,
        )
        for v in items
    ]
    return VersionsData(recommended=recommended_name, versions=versions)


# ---------------------------------------------------------------------------
# VersionHandler class
# ---------------------------------------------------------------------------


class VersionHandler:
    """Register and serve Kubernetes version-listing MCP tools."""

    def __init__(self, mcp, config: VksConfig, client: VksClient, cache: DiscoveryCache):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        self.mcp.tool(name="list_cluster_versions", annotations=READ)(self.list_cluster_versions)

    async def list_cluster_versions(
        self,
        region: Region = Field(
            "HCM-3", description="Region: 'HCM-3' or 'HAN'. Defaults to 'HCM-3'."
        ),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and fetch fresh from the VKS API.",
        ),
    ) -> VersionsData:
        """List available Kubernetes versions for VKS clusters.

        Only shows enabled versions and marks the latest stable non-deprecated
        version as recommended. Call this before create_cluster to choose a
        valid version and releaseChannel.
        """
        effective_region = region or self.config.default_region
        key = ("list_cluster_versions", effective_region)

        async def fetch():
            return await _cluster_versions_list(self.config, self.client, region)

        return await self.cache.get_or_fetch("list_cluster_versions", key, fetch, refresh)
