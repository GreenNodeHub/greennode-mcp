"""The HTTP transport: identity resolution, /health, and the auth-debug redaction.

Everything here was verified end to end against a locally running server
(`--transport streamable-http`), including a real `POST /mcp initialize`. These
tests pin the parts that must not regress: which requests reach the app, which
identity they run as, and what the diagnostic summary is allowed to reveal.

The tokens are locally minted JWTs that authenticate nothing. They carry decoy
`ssn` and `email` claims so "was the allow-list applied" is checkable without a
real credential.
"""

from __future__ import annotations

import base64
import httpx
import json
import pytest
import time
from greennode.mcp_core.http import user_token_var
from greennode.vlb_mcp_server.auth_debug import summarize_request
from greennode.vlb_mcp_server.server import UpstreamIdentityMiddleware
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


def _fake_jwt(**extra) -> str:
    """A syntactically valid JWT that is not a credential for anything."""

    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    claims = {
        "iss": "https://iamapis.vngcloud.vn",
        "sub": "iam-user-42",
        "exp": int(time.time()) + 3600,
        "ssn": "123-45-6789",
        "email": "probe@example.test",
        **extra,
    }
    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"


TOKEN = _fake_jwt()


def _app(has_service_credentials: bool) -> Starlette:
    """A stand-in app that reports the identity the middleware resolved."""

    async def whoami(request):
        return JSONResponse({"user_token": user_token_var.get(None)})

    async def health(request):
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/mcp", whoami), Route("/health", health)])
    app.add_middleware(UpstreamIdentityMiddleware, has_service_credentials=has_service_credentials)
    return app


def _client(has_service_credentials: bool) -> httpx.AsyncClient:
    app = _app(has_service_credentials)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


# --- identity resolution ------------------------------------------------------


@pytest.mark.asyncio
async def test_health_needs_no_credential_even_with_none_configured():
    """A container healthcheck must not depend on auth being configured."""
    async with _client(has_service_credentials=False) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_a_bearer_token_becomes_the_request_identity():
    async with _client(has_service_credentials=True) as c:
        r = await c.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.json()["user_token"] == TOKEN


@pytest.mark.asyncio
async def test_the_identity_does_not_leak_into_the_next_request():
    """The contextvar is reset in a finally, so one caller cannot inherit another's."""
    async with _client(has_service_credentials=True) as c:
        await c.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
        r = await c.get("/mcp")
    assert r.json()["user_token"] is None


@pytest.mark.asyncio
async def test_no_token_falls_back_to_the_service_account():
    async with _client(has_service_credentials=True) as c:
        r = await c.get("/mcp")
    assert r.status_code == 200
    assert r.json()["user_token"] is None


@pytest.mark.asyncio
async def test_no_token_and_no_service_account_is_401_not_anonymous():
    """Verified live: 401 with WWW-Authenticate, never a silent anonymous call."""
    async with _client(has_service_credentials=False) as c:
        r = await c.get("/mcp")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"
    assert "bearer token" in r.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["Bearer", "Bearer    ", "Basic abc123", "", "token abc"])
async def test_an_unusable_authorization_header_is_not_treated_as_an_identity(header):
    """An empty or non-bearer scheme must not become a token.

    `auth[7:]` on "Bearer" alone is the empty string; setting that as the
    identity would send `Authorization: Bearer ` upstream and fail as a
    confusing 401 from the gateway rather than here.
    """
    async with _client(has_service_credentials=False) as c:
        r = await c.get("/mcp", headers={"Authorization": header} if header else {})
    assert r.status_code == 401


# --- the auth-debug summary ----------------------------------------------------


def test_the_summary_never_carries_the_token():
    summary = summarize_request("GET", "/whoami", {"authorization": f"Bearer {TOKEN}"})
    blob = json.dumps(summary)
    assert TOKEN not in blob
    assert TOKEN.split(".")[1] not in blob  # not even the payload segment
    assert summary["token_len"] == len(TOKEN)
    assert summary["token_prefix"] == TOKEN[:6]
    assert len(summary["token_prefix"]) == 6


def test_the_claim_allowlist_drops_everything_else():
    """Only the identity claims are echoed; anything else could be personal data."""
    summary = summarize_request("GET", "/whoami", {"authorization": f"Bearer {TOKEN}"})
    assert set(summary["jwt_claims"]) == {"iss", "sub", "exp"}
    blob = json.dumps(summary)
    assert "123-45-6789" not in blob
    assert "ssn" not in blob
    assert "probe@example.test" not in blob


def test_forwarding_headers_are_collected_and_others_are_not():
    summary = summarize_request(
        "GET",
        "/whoami",
        {
            "x-greennode-user": "u1",
            "x-forwarded-for": "203.0.113.9",
            "cookie": "session=secret",
            "x-api-key": "should-not-appear",
        },
    )
    assert summary["forwarding_headers"] == {
        "x-greennode-user": "u1",
        "x-forwarded-for": "203.0.113.9",
    }
    blob = json.dumps(summary)
    assert "secret" not in blob
    assert "should-not-appear" not in blob


def test_a_request_with_no_authorization_is_summarised_without_raising():
    summary = summarize_request("GET", "/health", {})
    assert summary["has_authorization"] is False
    assert summary["auth_scheme"] is None
    assert "token_prefix" not in summary


def test_an_opaque_token_is_summarised_without_pretending_it_is_a_jwt():
    summary = summarize_request("GET", "/whoami", {"authorization": "Bearer not-a-jwt"})
    assert summary["token_present"] is True
    assert "jwt_claims" not in summary


def test_a_malformed_jwt_reports_the_failure_instead_of_raising():
    summary = summarize_request("GET", "/whoami", {"authorization": "Bearer a.b.c"})
    assert "jwt_decode_error" in summary
    assert "jwt_claims" not in summary
