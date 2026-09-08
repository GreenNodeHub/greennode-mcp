"""Pytest fixtures for the vLB MCP server tests.

Plain constants and response builders live in ``helpers.py``; this module holds
fixtures only.
"""

import pytest
from greennode.mcp_core.auth import TokenManager
from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import load_config
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache


@pytest.fixture
def sample_config(tmp_path):
    """Fake greenode directory with credentials and config INI files."""
    greenode_dir = tmp_path / ".greenode"
    greenode_dir.mkdir()

    credentials = greenode_dir / "credentials"
    credentials.write_text(
        "[default]\nclient_id = test-client-id\nclient_secret = test-client-secret\n"
    )

    config = greenode_dir / "config"
    config.write_text("[default]\nregion = HCM-3\noutput = json\nproject_id = pro-test-0001\n")

    return greenode_dir


@pytest.fixture
def config(sample_config):
    """Loaded VlbConfig backed by the fake credentials directory."""
    return load_config(sample_config)


@pytest.fixture
def config_no_project(sample_config):
    """Config with no configured project_id, forcing discovery via vServer."""
    cfg = load_config(sample_config)
    cfg.project_id = None
    return cfg


@pytest.fixture
def client(config):
    """VlbClient wired to the fake config."""
    return VlbClient(config, TokenManager(config))


@pytest.fixture
def cache():
    """A fresh discovery cache, so one test never sees another's cached value."""
    return DiscoveryCache()


@pytest.fixture
def no_cache():
    """A discovery cache with no configured TTLs — every fetch hits the API.

    Handlers wrap reads in ``get_or_fetch``; a tool with no TTL entry is never
    cached, which is what a test asserting on request counts needs.
    """
    return DiscoveryCache(ttl_config={})
