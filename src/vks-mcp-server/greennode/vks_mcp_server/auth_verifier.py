"""JWT Bearer-token verification for the streamable-http Resource Server.

Used only in `--auth-mode jwt`: validates that an inbound Bearer token is a JWT
issued by the configured issuer, for this server (audience), and unexpired.
"""

from __future__ import annotations

import jwt
from dataclasses import dataclass
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier


@dataclass
class JwtAuthConfig:
    """Configuration for JWT verification (from CLI/env)."""

    issuer: str
    jwks_uri: str
    audience: str
    resource_url: str
    required_scopes: list[str] | None = None


class JwtTokenVerifier(TokenVerifier):
    """Verify a Bearer JWT against the issuer's JWKS and expose its claims."""

    def __init__(self, config: JwtAuthConfig) -> None:
        self._config = config
        self._jwks_client = PyJWKClient(config.jwks_uri)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a Bearer JWT and return its AccessToken, or None if invalid."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self._config.audience,
                issuer=self._config.issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception:
            return None

        scope = claims.get("scope", "")
        scopes = scope.split() if isinstance(scope, str) else list(scope or [])
        return AccessToken(
            token=token,
            client_id=claims.get("azp") or claims.get("client_id") or claims.get("sub", ""),
            scopes=scopes,
            expires_at=claims.get("exp"),
            subject=claims.get("sub"),
            claims=claims,
        )
