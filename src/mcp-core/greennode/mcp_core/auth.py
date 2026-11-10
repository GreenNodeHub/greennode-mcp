"""GreenNode IAM token management shared by all GreenNode MCP servers."""

from __future__ import annotations

import asyncio
import base64
import httpx
import os
import time
from typing import Protocol


IAM_TOKEN_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
"""The production IAM token endpoint."""

IAM_TOKEN_URL_ENV = "GRN_IAM_TOKEN_URL"
"""Environment variable overriding the IAM token endpoint.

Point it at a non-production IAM when live-probing a staging gateway whose
service accounts the production IAM does not know. Unset, the production
endpoint above answers. Product servers pair it with their own gateway
override (vdb: ``GRN_VDB_GATEWAY_URL``).
"""

DEFAULT_TIMEOUT = 30  # seconds


class _HasClientCredentials(Protocol):
    client_id: str
    client_secret: str


class TokenManager:
    """Manages IAM access tokens with auto-refresh via client credentials.

    Works with any config object exposing ``client_id`` and ``client_secret``.
    Note: the GreenNode IAM API uses camelCase fields (``grantType``, ``accessToken``,
    ``expiresIn``) — not the snake_case OAuth2 standard.

    The token endpoint is ``token_url`` when given, else ``GRN_IAM_TOKEN_URL``
    (see ``IAM_TOKEN_URL_ENV``), else the production ``IAM_TOKEN_URL``.
    """

    def __init__(self, config: _HasClientCredentials, token_url: str | None = None) -> None:
        self._config = config
        self._token_url = token_url or os.environ.get(IAM_TOKEN_URL_ENV) or IAM_TOKEN_URL
        self._access_token = None
        self._expires_at = 0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token

        async with self._lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            await self._fetch_token()
            return self._access_token

    async def _fetch_token(self) -> None:
        """Fetch a new token from the IAM API."""
        credentials = f"{self._config.client_id}:{self._config.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()

        headers = {"Authorization": f"Basic {encoded}"}
        body = {"grantType": "client_credentials"}

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(self._token_url, json=body, headers=headers)
        except httpx.ConnectError as exc:
            raise RuntimeError(f"Cannot connect to IAM API: {exc}") from exc

        if response.status_code != 200:
            raise RuntimeError(f"Authentication failed: HTTP {response.status_code} from IAM API.")

        data = response.json()
        self._access_token = data["accessToken"]
        expires_in = data.get("expiresIn", 1800)
        self._expires_at = time.time() + expires_in
