"""Kubernetes client cache for GreenNode MCP Server.

Manages K8s API clients per cluster with TTL-based caching.
Fetches kubeconfig from VKS API and creates kubernetes clients.
"""

from __future__ import annotations

import asyncio
import socket
import yaml
from cachetools import TTLCache
from greennode.mcp_core.http import current_identity
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.k8s_apis import K8sApis
from greennode.vks_mcp_server.kubeconfig import extract_kubeconfig
from urllib.parse import urlparse


# 14 minutes TTL — kubeconfig tokens typically last 15m
CLIENT_TTL = 840

# TCP probe timeout for the cluster API endpoint. Without it, a PRIVATE
# cluster's endpoint hangs in the OS connect timeout (minutes) on the first
# kubernetes call — long enough that MCP clients give up and every later
# tool call looks broken too.
PROBE_TIMEOUT = 5.0


def _probe_endpoint(server_url: str, timeout: float = PROBE_TIMEOUT) -> None:
    """Fail fast (and clearly) when the cluster API endpoint is unreachable.

    Runs a plain TCP connect with a short timeout. Raises ValueError with an
    actionable message instead of letting the kubernetes client hang for the
    OS connect timeout deep inside a tool call.
    """
    parsed = urlparse(server_url)
    host = parsed.hostname
    port = parsed.port or 443
    if not host:
        raise ValueError(f"Kubeconfig has no usable API server address: '{server_url}'")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as exc:
        raise ValueError(
            f"The cluster API endpoint {server_url} is not reachable from this "
            f"MCP server (TCP connect failed within {timeout:.0f}s: {exc}). This "
            "usually means a PRIVATE cluster (enablePrivateCluster=true — check "
            "get_cluster): its API server is only reachable from inside the VPC, "
            "so the MCP server must run there or have network access into it. "
            "Retrying will not help until that network path exists; other "
            "clusters are unaffected."
        ) from exc


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
        """Fetch kubeconfig from VKS API and create a K8sApis instance.

        The blocking parts (TCP probe, kubeconfig loading — it writes temp CA
        files) run in a worker thread so they never stall the event loop.
        """
        from kubernetes import config as k8s_config

        raw = await self._vks_client.get_raw(
            f"/v1/clusters/{cluster_id}/kubeconfig",
            region=region,
        )
        kubeconfig_dict = yaml.safe_load(extract_kubeconfig(raw))
        server_url = _server_url_of(kubeconfig_dict)
        await asyncio.to_thread(_probe_endpoint, server_url)
        api_client = await asyncio.to_thread(
            k8s_config.new_client_from_config_dict, kubeconfig_dict
        )
        return K8sApis.from_api_client(api_client)


def _server_url_of(kubeconfig_dict: dict) -> str:
    """Extract the API server URL of the kubeconfig's current context."""
    clusters = kubeconfig_dict.get("clusters") or []
    context_name = kubeconfig_dict.get("current-context")
    wanted = None
    for ctx in kubeconfig_dict.get("contexts") or []:
        if ctx.get("name") == context_name:
            wanted = (ctx.get("context") or {}).get("cluster")
            break
    for entry in clusters:
        if wanted is None or entry.get("name") == wanted:
            return (entry.get("cluster") or {}).get("server", "")
    return ""
