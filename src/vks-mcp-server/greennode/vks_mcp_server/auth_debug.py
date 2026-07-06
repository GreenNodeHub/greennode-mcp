"""Opt-in diagnostic helpers to summarize an inbound request's auth surface.

DIAGNOSTIC ONLY. Never verifies JWT signatures and never includes the full
bearer token (only a short prefix + length). Used by the --auth-debug flag to
measure what an upstream (e.g. the MCP Gateway) actually sends downstream.
"""

from __future__ import annotations

import jwt
from typing import Mapping


_TOKEN_PREFIX_LEN = 6

# Allow-list of claims we surface. Anything else (incl. unknown sensitive
# claims in an unverified token) is dropped.
_CLAIM_ALLOWLIST = ("iss", "aud", "sub", "azp", "client_id", "scope", "exp", "iat")

# Header names/prefixes that may carry upstream identity propagation.
_FORWARD_PREFIXES = ("x-greennode-", "x-grn-", "x-forwarded-")
_FORWARD_NAMES = ("x-user-id", "x-tenant-id", "forwarded")


def _redact_token(token: str) -> dict:
    """Return non-reversible token metadata only (never the full token)."""
    return {
        "token_present": True,
        "token_len": len(token),
        "token_prefix": token[:_TOKEN_PREFIX_LEN],
    }


def _collect_forwarding_headers(headers: Mapping[str, str]) -> dict:
    """Collect headers that carry upstream identity propagation."""
    out: dict = {}
    for name, value in headers.items():
        lname = name.lower()
        if lname.startswith(_FORWARD_PREFIXES) or lname in _FORWARD_NAMES:
            out[lname] = value
    return out


def _decode_jwt_unverified(token: str) -> dict:
    """Decode a JWT WITHOUT verifying its signature (diagnostic only)."""
    try:
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never propagate
        return {"jwt_decode_error": type(exc).__name__}
    return {
        "jwt_header": {k: header.get(k) for k in ("alg", "kid", "typ") if k in header},
        "jwt_claims": {k: claims[k] for k in _CLAIM_ALLOWLIST if k in claims},
    }


def summarize_request(method: str, path: str, headers: Mapping[str, str]) -> dict:
    """Build a safe, JSON-serializable summary of a request's auth surface.

    Never raises and never includes the full bearer token.
    """
    summary: dict = {"method": method, "path": path}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    summary["has_authorization"] = bool(auth)
    if not auth:
        summary["auth_scheme"] = None
        summary["forwarding_headers"] = _collect_forwarding_headers(headers)
        return summary
    parts = auth.split(" ", 1)
    summary["auth_scheme"] = parts[0]
    token = parts[1].strip() if len(parts) == 2 else ""
    if token:
        summary.update(_redact_token(token))
        if parts[0].lower() == "bearer" and token.count(".") == 2:
            summary.update(_decode_jwt_unverified(token))
    summary["forwarding_headers"] = _collect_forwarding_headers(headers)
    return summary
