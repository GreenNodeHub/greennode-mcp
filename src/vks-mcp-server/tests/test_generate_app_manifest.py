"""Tests for the generate_app_manifest tool."""

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


@pytest.mark.asyncio
async def test_requires_write_access(handler_factory, tmp_path):
    h = handler_factory(allow_write=False)
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="allow-write"):
        await h.generate_app_manifest(app_name="web", image_uri="img:1", output_dir=str(out))
    assert not out.exists()


@pytest.mark.asyncio
async def test_requires_absolute_output_dir(handler_factory):
    h = handler_factory(allow_write=True)
    with pytest.raises(RuntimeError, match="absolute"):
        await h.generate_app_manifest(app_name="web", image_uri="img:1", output_dir="relative/dir")


@pytest.mark.asyncio
async def test_rejects_invalid_app_name(handler_factory, tmp_path):
    h = handler_factory(allow_write=True)
    with pytest.raises(ValueError):
        await h.generate_app_manifest(
            app_name="Bad_Name", image_uri="img:1", output_dir=str(tmp_path)
        )


@pytest.mark.asyncio
async def test_happy_path_writes_manifest(handler_factory, tmp_path):
    h = handler_factory(allow_write=True)
    result = await h.generate_app_manifest(
        app_name="web",
        image_uri="vcr.vngcloud.vn/demo/web:1.0",
        output_dir=str(tmp_path),
        port=8080,
        replicas=3,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    out = tmp_path / "web-manifest.yaml"
    assert out.exists()
    text = out.read_text()
    assert "vcr.vngcloud.vn/demo/web:1.0" in text
    assert "vks.vngcloud.vn/scheme: internal" in text
    assert "type: LoadBalancer" in text

    docs = list(yaml.safe_load_all(text))
    assert len(docs) == 2
    assert {d["kind"] for d in docs} == {"Deployment", "Service"}
    dep = next(d for d in docs if d["kind"] == "Deployment")
    assert dep["spec"]["replicas"] == 3
    assert dep["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"] == 8080
    assert "web-manifest.yaml" in result


@pytest.mark.asyncio
async def test_image_uri_with_placeholder_substring_preserved(handler_factory, tmp_path):
    """An image URI containing an UPPERCASE placeholder substring must not be corrupted."""
    h = handler_factory(allow_write=True)
    image = "registry.example.com/PORTAL/CPU-app:MEMORY-1"
    await h.generate_app_manifest(
        app_name="web",
        image_uri=image,
        output_dir=str(tmp_path),
        port=8080,
        replicas=2,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    text = (tmp_path / "web-manifest.yaml").read_text()
    assert image in text


@pytest.mark.asyncio
async def test_rejects_invalid_namespace(handler_factory, tmp_path):
    h = handler_factory(allow_write=True)
    with pytest.raises(ValueError):
        await h.generate_app_manifest(
            app_name="web",
            image_uri="img:1",
            output_dir=str(tmp_path),
            port=80,
            replicas=1,
            cpu="100m",
            memory="128Mi",
            namespace="Bad_NS",
            load_balancer_scheme="internal",
        )


@pytest.mark.asyncio
async def test_value_containing_delimited_token_not_re_substituted(handler_factory, tmp_path):
    """A value that literally contains a delimited token must survive single-pass substitution."""
    h = handler_factory(allow_write=True)
    image = "registry.example.com/x__PORT__y:1"
    await h.generate_app_manifest(
        app_name="web",
        image_uri=image,
        output_dir=str(tmp_path),
        port=8080,
        replicas=2,
        cpu="100m",
        memory="128Mi",
        namespace="default",
        load_balancer_scheme="internal",
    )
    text = (tmp_path / "web-manifest.yaml").read_text()
    assert image in text
