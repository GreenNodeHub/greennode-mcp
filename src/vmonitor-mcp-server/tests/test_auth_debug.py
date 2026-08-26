"""Tests for the opt-in redacted inbound-auth diagnostic."""

from __future__ import annotations

import jwt
import pytest
import time
from greennode.vmonitor_mcp_server.auth_debug import summarize_request
from greennode.vmonitor_mcp_server.server import create_server


def _token(**extra) -> str:
    claims = {
        "iss": "https://iam.vng.local",
        "aud": "vmonitor-mcp",
        "sub": "alice@vng",
        "scope": "mcp:use",
        "exp": int(time.time()) + 3600,
        **extra,
    }
    return jwt.encode(
        claims,
        "dummy-secret-at-least-32-bytes-long!!",
        algorithm="HS256",
        headers={"kid": "key-1"},
    )


def test_summary_without_authorization():
    summary = summarize_request("GET", "/mcp", {})

    assert summary == {
        "method": "GET",
        "path": "/mcp",
        "has_authorization": False,
        "auth_scheme": None,
        "forwarding_headers": {},
    }


def test_summary_never_exposes_the_full_token():
    token = _token(ssn="SECRET-SHOULD-NOT-APPEAR")

    summary = summarize_request("GET", "/mcp", {"authorization": f"Bearer {token}"})

    rendered = repr(summary)
    assert token not in rendered
    assert "SECRET-SHOULD-NOT-APPEAR" not in rendered
    assert summary["token_prefix"] == token[:6]
    assert summary["token_len"] == len(token)


def test_summary_decodes_allow_listed_claims_without_verifying():
    """The signature is never checked — this is a diagnostic, not an authenticator."""
    summary = summarize_request("GET", "/mcp", {"authorization": f"Bearer {_token()}"})

    assert summary["auth_scheme"] == "Bearer"
    assert summary["jwt_header"]["kid"] == "key-1"
    assert summary["jwt_claims"]["sub"] == "alice@vng"
    assert summary["jwt_claims"]["scope"] == "mcp:use"


def test_summary_collects_forwarding_headers():
    summary = summarize_request(
        "GET",
        "/mcp",
        {"X-GreenNode-User": "alice", "X-Forwarded-For": "10.1.2.3", "Accept": "*/*"},
    )

    assert summary["forwarding_headers"] == {
        "x-greennode-user": "alice",
        "x-forwarded-for": "10.1.2.3",
    }


def test_summary_survives_a_malformed_jwt():
    summary = summarize_request("GET", "/mcp", {"authorization": "Bearer not.a.jwt"})

    assert summary["has_authorization"] is True
    assert "jwt_decode_error" in summary


@pytest.mark.parametrize("auth_debug", [False, True])
def test_whoami_route_is_registered_only_with_auth_debug(auth_debug):
    server = create_server(auth_debug=auth_debug, allow_write=False)

    paths = {getattr(r, "path", None) for r in server.streamable_http_app().routes}

    assert ("/whoami" in paths) is auth_debug
    assert "/health" in paths
