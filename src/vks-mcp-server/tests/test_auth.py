"""Tests for the auth module (TokenManager)."""

from __future__ import annotations

import pytest
import respx
from greennode.vks_mcp_server.auth import IAM_TOKEN_URL, TokenManager
from greennode.vks_mcp_server.config import load_config
from httpx import Response


IAM_SUCCESS_BODY = {
    "accessToken": "new-iam-token",
    "refreshToken": "new-refresh-token",
    "expiresIn": 1800,
    "refreshExpiresIn": 3600,
}


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@respx.mock
@pytest.mark.asyncio
async def test_token_manager_client_credentials(config):
    """get_token fetches from IAM and returns the accessToken."""
    respx.post(IAM_TOKEN_URL).mock(return_value=Response(200, json=IAM_SUCCESS_BODY))
    manager = TokenManager(config)
    token = await manager.get_token()
    assert token == "new-iam-token"
    assert respx.calls.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_token_manager_uses_cached_token(config):
    """get_token called twice only hits IAM once."""
    respx.post(IAM_TOKEN_URL).mock(return_value=Response(200, json=IAM_SUCCESS_BODY))
    manager = TokenManager(config)
    token1 = await manager.get_token()
    token2 = await manager.get_token()
    assert token1 == token2 == "new-iam-token"
    assert respx.calls.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_token_manager_refreshes_expired(config):
    """After expiry, get_token hits IAM again."""
    respx.post(IAM_TOKEN_URL).mock(return_value=Response(200, json=IAM_SUCCESS_BODY))
    manager = TokenManager(config)
    await manager.get_token()
    manager._expires_at = 1  # force expiry
    await manager.get_token()
    assert respx.calls.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_token_manager_iam_auth_failure(config):
    """IAM 401 causes RuntimeError with 'Authentication failed'."""
    respx.post(IAM_TOKEN_URL).mock(return_value=Response(401, json={"error": "unauthorized"}))
    manager = TokenManager(config)
    with pytest.raises(RuntimeError, match="Authentication failed"):
        await manager.get_token()
