"""Tests for the opt-in auth-debug request summarizer."""

from __future__ import annotations

import jwt
from greennode.vks_mcp_server.auth_debug import summarize_request


def test_no_authorization_header():
    s = summarize_request("GET", "/mcp", {})
    assert s["method"] == "GET"
    assert s["path"] == "/mcp"
    assert s["has_authorization"] is False
    assert s["auth_scheme"] is None
    assert "token_prefix" not in s
    assert s["forwarding_headers"] == {}


def test_bearer_token_is_redacted_never_full():
    token = "abcdefghijklmnopqrstuvwxyz0123456789"
    s = summarize_request("POST", "/mcp", {"Authorization": f"Bearer {token}"})
    assert s["has_authorization"] is True
    assert s["auth_scheme"] == "Bearer"
    assert s["token_present"] is True
    assert s["token_len"] == len(token)
    assert s["token_prefix"] == token[:6]
    # The full token must never appear anywhere in the summary.
    assert token not in repr(s)


def _hs256(**claims) -> str:
    # Signature is irrelevant: summarize_request never verifies it.
    return jwt.encode(claims, "irrelevant-secret", algorithm="HS256")


def test_jwt_header_and_allowlisted_claims():
    token = _hs256(
        iss="https://iam.vng", aud="vks-mcp", sub="user-7", scope="mcp:use", exp=9999999999, iat=1
    )
    s = summarize_request("POST", "/mcp", {"Authorization": f"Bearer {token}"})
    assert s["jwt_header"]["alg"] == "HS256"
    assert s["jwt_claims"]["iss"] == "https://iam.vng"
    assert s["jwt_claims"]["aud"] == "vks-mcp"
    assert s["jwt_claims"]["sub"] == "user-7"
    assert s["jwt_claims"]["scope"] == "mcp:use"


def test_jwt_claims_allowlist_drops_unknown_sensitive_claims():
    token = _hs256(iss="i", aud="a", sub="s", ssn="123-45-6789", password="hunter2")
    s = summarize_request("POST", "/mcp", {"Authorization": f"Bearer {token}"})
    assert "ssn" not in s["jwt_claims"]
    assert "password" not in s["jwt_claims"]
    assert "ssn" not in repr(s)


def test_expired_or_wrong_issuer_jwt_still_summarizes():
    # No verification => an expired token still decodes (proves we observe, not reject).
    token = _hs256(iss="whatever", aud="x", sub="u", exp=1)
    s = summarize_request("POST", "/mcp", {"Authorization": f"Bearer {token}"})
    assert s["jwt_claims"]["exp"] == 1


def test_malformed_bearer_jwt_records_error_without_crashing():
    s = summarize_request("POST", "/mcp", {"Authorization": "Bearer not.a.jwt"})
    assert "jwt_decode_error" in s
    assert "token_prefix" in s  # still redacts the malformed token


def test_non_bearer_scheme_is_not_jwt_decoded():
    s = summarize_request("GET", "/mcp", {"Authorization": "Basic dXNlcjpwYXNz"})
    assert s["auth_scheme"] == "Basic"
    assert "jwt_header" not in s
    assert "jwt_claims" not in s


def test_forwarding_headers_captured_case_insensitively():
    headers = {
        "X-GreenNode-User": "alice",
        "X-GRN-Tenant": "team-9",
        "X-Forwarded-For": "10.0.0.1",
        "X-User-Id": "u-42",
        "Forwarded": "for=10.0.0.1",
        "Accept": "application/json",
    }
    s = summarize_request("GET", "/mcp", headers)
    fwd = s["forwarding_headers"]
    assert fwd["x-greennode-user"] == "alice"
    assert fwd["x-grn-tenant"] == "team-9"
    assert fwd["x-forwarded-for"] == "10.0.0.1"
    assert fwd["x-user-id"] == "u-42"
    assert fwd["forwarded"] == "for=10.0.0.1"
    assert "accept" not in fwd


def test_forwarding_headers_captured_when_token_present():
    headers = {
        "Authorization": "Bearer abcdef.ghijkl.mnopqr",
        "X-GreenNode-User": "bob",
        "X-Forwarded-For": "10.0.0.2",
    }
    s = summarize_request("POST", "/mcp", headers)
    assert s["token_present"] is True
    assert s["forwarding_headers"]["x-greennode-user"] == "bob"
    assert s["forwarding_headers"]["x-forwarded-for"] == "10.0.0.2"
