"""Server wiring: config, region endpoints, project resolution, identity scoping."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import PROJECT, VLB_HAN, VLB_HCM3, VSERVER_HAN, VSERVER_HCM3, mock_iam, mock_project
from greennode.mcp_core.http import user_token_var
from greennode.vlb_mcp_server import __version__
from greennode.vlb_mcp_server.config import (
    REGIONS,
    VLB_SERVICE,
    VSERVER_SERVICE,
    load_config,
)
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.server import _mode_addendum, create_server


# --- server -------------------------------------------------------------------


def test_create_server():
    server = create_server()
    assert server.name == "vlb-mcp-server"


def test_create_server_reports_package_version():
    assert create_server().version == __version__


def test_mode_addendum_states_write_enabled():
    text = _mode_addendum(True)
    assert "Write: ENABLED" in text


def test_mode_addendum_tells_agent_not_to_start_a_write_flow():
    text = _mode_addendum(False)
    assert "Write: OFF" in text
    assert "--allow-write" in text


# --- regions and endpoints ----------------------------------------------------


def test_regions_cover_both_regions_and_both_services():
    assert set(REGIONS) == {"HCM-3", "HAN"}
    for services in REGIONS.values():
        assert set(services) == {VLB_SERVICE, VSERVER_SERVICE}


def test_region_endpoints_are_the_documented_gateways():
    assert REGIONS["HCM-3"][VLB_SERVICE] == VLB_HCM3
    assert REGIONS["HAN"][VLB_SERVICE] == VLB_HAN
    # The short /vserver-gateway spelling 404s in HAN — both regions must use
    # the /vserver/vserver-gateway form.
    assert REGIONS["HCM-3"][VSERVER_SERVICE] == VSERVER_HCM3
    assert REGIONS["HAN"][VSERVER_SERVICE] == VSERVER_HAN


def test_get_base_url_defaults_to_the_configured_region(config):
    assert config.get_base_url(None, VLB_SERVICE) == VLB_HCM3


def test_get_base_url_honours_an_explicit_region(config):
    assert config.get_base_url("HAN", VLB_SERVICE) == VLB_HAN


def test_get_base_url_routes_the_vserver_service_to_the_vserver_gateway(config):
    assert config.get_base_url("HCM-3", VSERVER_SERVICE) == VSERVER_HCM3


def test_get_base_url_rejects_an_unknown_region(config):
    with pytest.raises(ValueError, match="does not exist in configuration"):
        config.get_base_url("SGN", VLB_SERVICE)


def test_load_config_reads_the_profile(sample_config):
    cfg = load_config(sample_config)
    assert cfg.client_id == "test-client-id"
    assert cfg.default_region == "HCM-3"
    assert cfg.project_id == PROJECT


# --- project resolution -------------------------------------------------------


@pytest.mark.asyncio
async def test_require_project_id_uses_the_configured_project(config, client):
    assert await require_project_id(config, client, "HCM-3") == PROJECT


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_discovers_via_the_vserver_gateway(config_no_project, client):
    mock_iam(respx.mock)
    route = mock_project(respx.mock)
    # vLB has no project-listing endpoint; the lookup must not hit its gateway.
    vlb_route = respx.get(f"{VLB_HCM3}/v1/projects").mock(return_value=httpx.Response(500))

    assert await require_project_id(config_no_project, client, "HCM-3") == PROJECT
    assert route.called
    assert not vlb_route.called


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_caches_per_region(config_no_project, client):
    mock_iam(respx.mock)
    route = mock_project(respx.mock)

    await require_project_id(config_no_project, client, "HCM-3")
    await require_project_id(config_no_project, client, "HCM-3")
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_resolves_each_region_separately(config_no_project, client):
    mock_iam(respx.mock)
    hcm = mock_project(respx.mock, VSERVER_HCM3, "pro-hcm")
    han = mock_project(respx.mock, VSERVER_HAN, "pro-han")

    assert await require_project_id(config_no_project, client, "HCM-3") == "pro-hcm"
    assert await require_project_id(config_no_project, client, "HAN") == "pro-han"
    assert hcm.call_count == 1
    assert han.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_passthrough_user_never_inherits_the_service_account_project(config, client):
    """A configured project_id belongs to the service account, not to a caller."""
    mock_iam(respx.mock)
    mock_project(respx.mock, VSERVER_HCM3, "pro-of-the-caller")

    token = user_token_var.set("caller-token")
    try:
        assert await require_project_id(config, client, "HCM-3") == "pro-of-the-caller"
    finally:
        user_token_var.reset(token)

    # ...and the service identity still sees its own.
    assert await require_project_id(config, client, "HCM-3") == PROJECT


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_errors_when_no_project_exists(config_no_project, client):
    mock_iam(respx.mock)
    respx.get(f"{VSERVER_HCM3}/v1/projects").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    with pytest.raises(ValueError, match="GRN_PROJECT_ID"):
        await require_project_id(config_no_project, client, "HCM-3")
