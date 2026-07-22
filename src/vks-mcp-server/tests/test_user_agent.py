"""Every outbound API request must carry the vks-mcp-server User-Agent,
so the platform can attribute and count MCP-originated traffic."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.useragent import USER_AGENT


VKS_BASE = "https://vks.api.vngcloud.vn"
VS_BASE = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"


def _mock_iam():
    respx.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "t", "expiresIn": 1800})
    )


def test_user_agent_names_the_server_and_version():
    assert USER_AGENT.startswith("vks-mcp-server/")
    assert USER_AGENT != "vks-mcp-server/"  # some version resolved


@respx.mock
@pytest.mark.asyncio
async def test_vks_requests_carry_user_agent(sample_config):
    _mock_iam()
    route = respx.get(f"{VKS_BASE}/v1/clusters").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    config = load_config(sample_config)
    client = VksClient(config, TokenManager(config))
    await client.get("/v1/clusters")
    assert route.calls.last.request.headers["user-agent"] == USER_AGENT


@respx.mock
@pytest.mark.asyncio
async def test_vserver_requests_carry_user_agent(sample_config):
    _mock_iam()
    route = respx.get(f"{VS_BASE}/v1/projects").mock(
        return_value=httpx.Response(200, json={"projects": []})
    )
    config = load_config(sample_config)
    client = VksClient(config, TokenManager(config))
    await client.vserver_get("/v1/projects")
    assert route.calls.last.request.headers["user-agent"] == USER_AGENT
