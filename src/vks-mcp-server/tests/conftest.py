"""Pytest fixtures for VKS MCP server tests."""

import pytest


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
