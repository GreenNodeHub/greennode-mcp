"""get_access_token must never hand the shared service-account token to an HTTP caller."""

from __future__ import annotations

import pytest
from greennode.mcp_core.http import user_token_var
from greennode.vlb_mcp_server.auth_handler import AuthHandler
from mcp.server.mcpserver import MCPServer


SERVICE_TOKEN = "svc-token-should-stay-private"
USER_TOKEN = "user-token-from-authorization-header"


class _StubTokenManager:
    async def get_token(self) -> str:
        return SERVICE_TOKEN


def _handler(config, expose_service_token: bool) -> AuthHandler:
    return AuthHandler(
        MCPServer("t"),
        config,
        _StubTokenManager(),
        expose_service_token=expose_service_token,
    )


@pytest.mark.asyncio
async def test_stdio_returns_the_service_token(config):
    """stdio is single-user and local: the operator owns these credentials."""
    result = await _handler(config, expose_service_token=True).get_access_token()
    assert SERVICE_TOKEN in result


@pytest.mark.asyncio
async def test_http_caller_gets_their_own_token_back(config):
    """PR #29 review: over HTTP the tool returned the service token even to a
    caller who sent their own, more limited token."""
    reset = user_token_var.set(USER_TOKEN)
    try:
        result = await _handler(config, expose_service_token=False).get_access_token()
    finally:
        user_token_var.reset(reset)
    assert USER_TOKEN in result
    assert SERVICE_TOKEN not in result


@pytest.mark.asyncio
async def test_http_caller_without_a_token_is_refused(config):
    """With no caller token the requests run as the service account, but its
    raw token must still not leave the server."""
    with pytest.raises(ValueError, match="not available") as exc:
        await _handler(config, expose_service_token=False).get_access_token()
    assert SERVICE_TOKEN not in str(exc.value)
