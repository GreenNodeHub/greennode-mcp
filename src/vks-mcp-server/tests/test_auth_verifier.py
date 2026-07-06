"""Tests for the JWT Bearer token verifier."""

from __future__ import annotations

import jwt
import pytest
import time
from cryptography.hazmat.primitives.asymmetric import rsa
from greennode.vks_mcp_server.auth_verifier import JwtAuthConfig, JwtTokenVerifier
from types import SimpleNamespace


ISSUER = "https://iam.example.com"
AUDIENCE = "vks-mcp"


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(rsa_key, monkeypatch):
    cfg = JwtAuthConfig(
        issuer=ISSUER,
        jwks_uri="https://iam.example.com/jwks",
        audience=AUDIENCE,
        resource_url="https://mcp.example.com/mcp",
    )
    v = JwtTokenVerifier(cfg)
    pub = rsa_key.public_key()
    monkeypatch.setattr(
        v._jwks_client,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=pub),
    )
    return v


def _make_token(rsa_key, **overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "scope": "mcp:use mcp:tools",
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, rsa_key, algorithm="RS256")


@pytest.mark.asyncio
async def test_valid_token_returns_access_token(verifier, rsa_key):
    token = _make_token(rsa_key)
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.subject == "user-123"
    assert set(result.scopes) == {"mcp:use", "mcp:tools"}
    assert result.claims["iss"] == ISSUER


@pytest.mark.asyncio
async def test_wrong_audience_rejected(verifier, rsa_key):
    token = _make_token(rsa_key, aud="someone-else")
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_wrong_issuer_rejected(verifier, rsa_key):
    token = _make_token(rsa_key, iss="https://evil.example.com")
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_expired_token_rejected(verifier, rsa_key):
    token = _make_token(rsa_key, exp=int(time.time()) - 10)
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_tampered_signature_rejected(verifier, rsa_key):
    token = _make_token(rsa_key) + "tamper"
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_alg_none_rejected(verifier):
    # Unsigned token (alg=none) must be rejected.
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "u", "exp": int(time.time()) + 60},
        key=None,
        algorithm="none",
    )
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_hs256_algorithm_confusion_rejected(verifier):
    # Token signed with HS256 must be rejected (only RS256/ES256 allowed).
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "u", "exp": int(time.time()) + 60},
        "any-shared-secret",
        algorithm="HS256",
    )
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_missing_exp_rejected(verifier, rsa_key):
    # exp is now required.
    token = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "u"}, rsa_key, algorithm="RS256")
    assert await verifier.verify_token(token) is None
