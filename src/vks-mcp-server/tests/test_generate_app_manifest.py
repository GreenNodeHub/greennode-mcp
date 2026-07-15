"""Tests for the generate_app_manifest tool (pure generator — no filesystem)."""

from __future__ import annotations

import pytest
import yaml
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.k8s_handler import K8sHandler
from mcp.server.fastmcp import FastMCP


@pytest.fixture
def handler_factory(sample_config):
    config = load_config(sample_config)
    client = VksClient(config, TokenManager(config))

    def make(allow_write: bool) -> K8sHandler:
        return K8sHandler(FastMCP("test"), config, client, allow_write=allow_write)

    return make


def _yaml_block(result: str) -> str:
    """Extract the fenced YAML block from the tool's markdown response."""
    return result.split("```yaml\n", 1)[1].rsplit("\n```", 1)[0]


@pytest.mark.asyncio
async def test_available_without_write_access(handler_factory):
    """A pure generator writes nothing anywhere — no --allow-write needed."""
    h = handler_factory(allow_write=False)
    result = await h.generate_app_manifest(
        app_name="web",
        image_uri="img:1",
        port=80,
        replicas=1,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    assert "kind: Deployment" in result


@pytest.mark.asyncio
async def test_rejects_invalid_app_name(handler_factory):
    h = handler_factory(allow_write=True)
    with pytest.raises(ValueError):
        await h.generate_app_manifest(
            app_name="Bad_Name",
            image_uri="img:1",
            port=80,
            replicas=1,
            cpu="100m",
            memory="128Mi",
            namespace="default",
            load_balancer_scheme="internal",
        )


@pytest.mark.asyncio
async def test_happy_path_returns_manifest(handler_factory):
    h = handler_factory(allow_write=True)
    result = await h.generate_app_manifest(
        app_name="web",
        image_uri="vcr.vngcloud.vn/demo/web:1.0",
        port=8080,
        replicas=3,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    text = _yaml_block(result)
    assert "vcr.vngcloud.vn/demo/web:1.0" in text
    assert "vks.vngcloud.vn/scheme: internal" in text
    assert "type: LoadBalancer" in text

    docs = list(yaml.safe_load_all(text))
    assert len(docs) == 2
    assert {d["kind"] for d in docs} == {"Deployment", "Service"}
    dep = next(d for d in docs if d["kind"] == "Deployment")
    assert dep["spec"]["replicas"] == 3
    assert dep["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"] == 8080
    # remote-safe: the response points at apply_yaml, no filesystem claim
    assert "apply_yaml" in result
    assert "saved to" not in result


@pytest.mark.asyncio
async def test_image_uri_with_placeholder_substring_preserved(handler_factory):
    """An image URI containing an UPPERCASE placeholder substring must not be corrupted."""
    h = handler_factory(allow_write=True)
    image = "registry.example.com/PORTAL/CPU-app:MEMORY-1"
    result = await h.generate_app_manifest(
        app_name="web",
        image_uri=image,
        port=8080,
        replicas=2,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    assert image in _yaml_block(result)


@pytest.mark.asyncio
async def test_rejects_invalid_namespace(handler_factory):
    h = handler_factory(allow_write=True)
    with pytest.raises(ValueError):
        await h.generate_app_manifest(
            app_name="web",
            image_uri="img:1",
            port=80,
            replicas=1,
            cpu="100m",
            memory="128Mi",
            namespace="Bad_NS",
            load_balancer_scheme="internal",
        )


@pytest.mark.asyncio
async def test_value_containing_delimited_token_not_re_substituted(handler_factory):
    """A value that literally contains a delimited token must survive single-pass substitution."""
    h = handler_factory(allow_write=True)
    image = "registry.example.com/x__PORT__y:1"
    result = await h.generate_app_manifest(
        app_name="web",
        image_uri=image,
        port=8080,
        replicas=2,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    assert image in _yaml_block(result)
