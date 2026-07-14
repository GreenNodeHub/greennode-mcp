"""Kubernetes client cache for GreenNode MCP Server.

Manages K8s API clients per cluster with TTL-based caching.
Fetches kubeconfig from VKS API and creates kubernetes clients.
"""

from __future__ import annotations

import yaml
from cachetools import TTLCache
from greennode.mcp_core.http import current_identity
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.k8s_apis import K8sApis
from greennode.vks_mcp_server.kubeconfig import extract_kubeconfig


# 14 minutes TTL — kubeconfig tokens typically last 15m
CLIENT_TTL = 840


class K8sClientCache:
    """Cache for Kubernetes API clients keyed by VKS cluster ID."""

    def __init__(self, vks_client: VksClient) -> None:
        self._vks_client = vks_client
        self._cache = TTLCache(maxsize=100, ttl=CLIENT_TTL)

    async def get_client(self, cluster_id: str, region: str | None = None) -> K8sApis:
        """Get a K8sApis client for the given cluster.

        Fetches kubeconfig from VKS API on cache miss, creates a
        kubernetes client, and caches it with TTL. Keyed by caller identity
        too: under token passthrough, a client built from user A's
        kubeconfig must never be served to user B.
        """
        key = (current_identity(), cluster_id)
        if key not in self._cache:
            self._cache[key] = await self._create_client(cluster_id, region)
        return self._cache[key]

    async def _create_client(self, cluster_id: str, region: str | None) -> K8sApis:
        """Fetch kubeconfig from VKS API and create a K8sApis instance."""
        from kubernetes import config as k8s_config

        raw = await self._vks_client.get_raw(
            f"/v1/clusters/{cluster_id}/kubeconfig",
            region=region,
        )
        kubeconfig_dict = yaml.safe_load(extract_kubeconfig(raw))
        api_client = k8s_config.new_client_from_config_dict(kubeconfig_dict)
        return K8sApis.from_api_client(api_client)
