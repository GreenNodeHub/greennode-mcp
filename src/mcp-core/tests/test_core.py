"""Tests for greennode.mcp_core (config, auth, http, validators, cache)."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.mcp_core import (
    IAM_TOKEN_URL,
    BaseClient,
    DiscoveryCache,
    ProfileSettings,
    TokenManager,
    load_profile,
    resolve_config_dir,
    validate_id,
)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@pytest.fixture
def config_dir(tmp_path):
    d = tmp_path / ".greenode"
    d.mkdir()
    (d / "credentials").write_text("[default]\nclient_id = cid\nclient_secret = sec\n")
    (d / "config").write_text("[default]\nregion = HAN\nproject_id = pro-1\n")
    return d


def test_load_profile_reads_ini(config_dir):
    p = load_profile(config_dir)
    assert p == ProfileSettings(
        client_id="cid", client_secret="sec", region="HAN", project_id="pro-1"
    )


def test_load_profile_env_overrides(config_dir, monkeypatch):
    monkeypatch.setenv("GRN_CLIENT_ID", "env-id")
    monkeypatch.setenv("GRN_CLIENT_SECRET", "env-sec")
    monkeypatch.setenv("GRN_DEFAULT_REGION", "HCM-3")
    monkeypatch.setenv("GRN_PROJECT_ID", "pro-env")
    p = load_profile(config_dir)
    assert (p.client_id, p.client_secret, p.region, p.project_id) == (
        "env-id",
        "env-sec",
        "HCM-3",
        "pro-env",
    )


def test_load_profile_missing_credentials(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile(tmp_path / "nowhere")


def test_resolve_config_dir_prefers_greennode(tmp_path):
    (tmp_path / ".greennode").mkdir()
    (tmp_path / ".greenode").mkdir()  # legacy also present — must be ignored
    assert resolve_config_dir(tmp_path) == tmp_path / ".greennode"


def test_resolve_config_dir_falls_back_to_legacy(tmp_path):
    (tmp_path / ".greenode").mkdir()  # only the legacy dir exists
    assert resolve_config_dir(tmp_path) == tmp_path / ".greenode"


def test_resolve_config_dir_defaults_to_greennode_when_neither(tmp_path):
    assert resolve_config_dir(tmp_path) == tmp_path / ".greennode"


# ---------------------------------------------------------------------------
# auth + http
# ---------------------------------------------------------------------------


class _FakeConfig:
    client_id = "cid"
    client_secret = "sec"

    def get_base_url(self, region, service):
        return f"https://{service}.example.test"


def _mock_iam(mock):
    mock.post(IAM_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@respx.mock
@pytest.mark.asyncio
async def test_base_client_uses_default_service_and_bearer():
    _mock_iam(respx.mock)
    route = respx.get("https://api.example.test/v1/things").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()))
    assert await client.get("/v1/things") == {"ok": True}
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
@pytest.mark.asyncio
async def test_base_client_sends_user_agent_when_configured():
    """A product server can brand its traffic for API-side tracking; without
    the option, httpx's default UA is left untouched."""
    _mock_iam(respx.mock)
    route = respx.get("https://api.example.test/v1/things").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    branded = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()), user_agent="acme-mcp/1.0")
    await branded.get("/v1/things")
    assert route.calls.last.request.headers["user-agent"] == "acme-mcp/1.0"

    plain = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()))
    await plain.get("/v1/things")
    assert route.calls.last.request.headers["user-agent"].startswith("python-httpx/")


@respx.mock
@pytest.mark.asyncio
async def test_base_client_service_override():
    _mock_iam(respx.mock)
    respx.get("https://other.example.test/v1/x").mock(return_value=httpx.Response(200, json=[1]))
    client = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()), default_service="api")
    assert await client._request("GET", "/v1/x", service="other") == [1]


@respx.mock
@pytest.mark.asyncio
async def test_base_client_refreshes_token_on_401():
    _mock_iam(respx.mock)
    route = respx.get("https://api.example.test/v1/secure").mock(
        side_effect=[
            httpx.Response(401, json={"message": "expired"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()))
    assert await client.get("/v1/secure") == {"ok": True}
    assert route.call_count == 2


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def test_validate_id_accepts_safe_ids():
    validate_id("k8s-abc-123", "cluster_id")


@pytest.mark.parametrize("bad", ["", "-lead", "trail-", "a/../b", "a b"])
def test_validate_id_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        validate_id(bad, "cluster_id")


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_cache_caches_and_refreshes():
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return calls["n"]

    cache = DiscoveryCache({"list_things": 60})
    assert await cache.get_or_fetch("list_things", "k", fetch) == 1
    assert await cache.get_or_fetch("list_things", "k", fetch) == 1  # cached
    assert await cache.get_or_fetch("list_things", "k", fetch, refresh=True) == 2


@pytest.mark.asyncio
async def test_discovery_cache_unconfigured_tool_never_cached():
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return calls["n"]

    cache = DiscoveryCache({})
    assert await cache.get_or_fetch("nope", "k", fetch) == 1
    assert await cache.get_or_fetch("nope", "k", fetch) == 2


@respx.mock
@pytest.mark.asyncio
async def test_base_client_tolerates_empty_success_body():
    """VKS returns 202 with an EMPTY body for several write operations —
    resp.json() on that raised 'Expecting value: line 1 column 1 (char 0)'.
    An empty success body must come back as None, not an exception."""
    _mock_iam(respx.mock)
    respx.put("https://api.example.test/v1/things/x").mock(
        return_value=httpx.Response(202)  # no body at all
    )
    client = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()))
    assert await client.put("/v1/things/x", json={"a": 1}) is None


@respx.mock
@pytest.mark.asyncio
async def test_base_client_uses_passthrough_user_token():
    """When a per-request user token is set (HTTP passthrough mode), every API
    call carries THAT token — not the shared service account's."""
    from greennode.mcp_core.http import user_token_var

    _mock_iam(respx.mock)
    route = respx.get("https://api.example.test/v1/things").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()))
    token = user_token_var.set("user-iam-token-abc")
    try:
        await client.get("/v1/things")
    finally:
        user_token_var.reset(token)
    assert route.calls.last.request.headers["Authorization"] == "Bearer user-iam-token-abc"


@respx.mock
@pytest.mark.asyncio
async def test_base_client_never_falls_back_to_service_account_on_user_401():
    """A rejected user token must NOT silently retry with the service account —
    that would be privilege confusion. It raises immediately."""
    from greennode.mcp_core.http import user_token_var

    _mock_iam(respx.mock)
    route = respx.get("https://api.example.test/v1/things").mock(
        return_value=httpx.Response(401, json={"message": "token expired"})
    )
    client = BaseClient(_FakeConfig(), TokenManager(_FakeConfig()))
    token = user_token_var.set("expired-user-token")
    try:
        with pytest.raises(RuntimeError, match="user token"):
            await client.get("/v1/things")
    finally:
        user_token_var.reset(token)
    assert route.call_count == 1  # no second attempt with a different identity


def test_current_identity_hashes_token_and_defaults_to_service():
    """Cache-isolation key: sha256 of the user token, 'service' when none."""
    from greennode.mcp_core.http import current_identity, user_token_var

    assert current_identity() == "service"
    tok = user_token_var.set("user-iam-token-abc")
    try:
        ident = current_identity()
        assert ident != "service" and len(ident) == 16
        assert "user-iam-token-abc" not in ident  # never the raw token
    finally:
        user_token_var.reset(tok)
