"""get_pod_logs edge cases (the kubernetes client is faked at the cache layer)."""

from __future__ import annotations

import pytest
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.k8s_handler import K8sHandler
from mcp.server.fastmcp import FastMCP


class _FakeK8sApis:
    def __init__(self, logs):
        self._logs = logs

    def get_pod_logs(self, **kwargs):
        return self._logs


@pytest.fixture
def handler(sample_config):
    config = load_config(sample_config)
    client = VksClient(config, TokenManager(config))
    return K8sHandler(
        FastMCP("t"), config, client, allow_write=False, allow_sensitive_data_access=True
    )


@pytest.mark.asyncio
async def test_get_pod_logs_handles_none_logs(handler, monkeypatch):
    """A pod with no logs yet (Pending / container not started) returns None
    from the kubernetes client — the tool must report 'no logs', not crash
    with 'NoneType' object has no attribute 'splitlines' (QC finding)."""

    async def fake_get_client(cluster_id, region=None):
        return _FakeK8sApis(logs=None)

    monkeypatch.setattr(handler, "get_client", fake_get_client)
    result = await handler.get_pod_logs(
        cluster_id="k8s-abc",
        pod_name="cilium-operator-x",
        namespace="kube-system",
        container_name=None,
        since_seconds=None,
        tail_lines=None,
        limit_bytes=None,
        previous=False,
        region=None,
    )
    assert result.log_lines == []


@pytest.mark.asyncio
async def test_get_pod_logs_happy_path(handler, monkeypatch):
    async def fake_get_client(cluster_id, region=None):
        return _FakeK8sApis(logs="line1\nline2")

    monkeypatch.setattr(handler, "get_client", fake_get_client)
    result = await handler.get_pod_logs(
        cluster_id="k8s-abc",
        pod_name="coredns-x",
        namespace="kube-system",
        container_name=None,
        since_seconds=None,
        tail_lines=None,
        limit_bytes=None,
        previous=False,
        region=None,
    )
    assert result.log_lines == ["line1", "line2"]


class _FakeApplyApis:
    def __init__(self):
        self.received = None

    def apply_from_yaml(self, yaml_objects, namespace, force):
        self.received = (yaml_objects, namespace, force)
        return ([], len(yaml_objects), 0)


@pytest.fixture
def write_handler(sample_config):
    config = load_config(sample_config)
    client = VksClient(config, TokenManager(config))
    return K8sHandler(
        FastMCP("t-w"), config, client, allow_write=True, allow_sensitive_data_access=False
    )


@pytest.mark.asyncio
async def test_apply_yaml_takes_inline_content(write_handler, monkeypatch):
    """QC finding: yaml_path pointed at the SERVER's filesystem, so a remote
    (HTTP) deployment could never apply the user's local file. The tool now
    takes the YAML content itself — transport-independent."""
    fake = _FakeApplyApis()

    async def fake_get_client(cluster_id, region=None):
        return fake

    monkeypatch.setattr(write_handler, "get_client", fake_get_client)
    content = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: a\n---\napiVersion: v1\nkind: Namespace\nmetadata:\n  name: b\n"
    result = await write_handler.apply_yaml(
        yaml_content=content, cluster_id="k8s-abc", namespace="default", force=True, region=None
    )
    assert result.resources_created == 2
    assert len(fake.received[0]) == 2  # multi-doc parsed


@pytest.mark.asyncio
async def test_generate_app_manifest_returns_yaml_no_disk(handler):
    """Remote-safe: the manifest comes back in the response (no output_dir, no
    file stranded inside the server container); pipe it to apply_yaml."""
    result = await handler.generate_app_manifest(
        app_name="demo-app",
        image_uri="vcr.vngcloud.vn/repo/app:1.0",
        port=80,
        replicas=2,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    assert "kind: Deployment" in result and "kind: Service" in result
    assert "saved to" not in result  # no filesystem claim
