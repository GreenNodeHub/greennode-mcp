"""GreenNode IAM token management shared by all GreenNode MCP servers."""

from __future__ import annotations

import base64
import httpx
import time
from typing import Protocol


IAM_TOKEN_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"

DEFAULT_TIMEOUT = 30  # seconds


class _HasClientCredentials(Protocol):
    client_id: str
    client_secret: str


class TokenManager:
    """Manages IAM access tokens with auto-refresh via client credentials.

    Works with any config object exposing ``client_id`` and ``client_secret``.
    Note: the GreenNode IAM API uses camelCase fields (``grantType``, ``accessToken``,
    ``expiresIn``) — not the snake_case OAuth2 standard.
    """

    def __init__(self, config: _HasClientCredentials) -> None:
        self._config = config
        self._access_token = None
        self._expires_at = 0

    async def get_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
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
                response = await client.post(IAM_TOKEN_URL, json=body, headers=headers)
        except httpx.ConnectError as exc:
            raise RuntimeError(f"Cannot connect to IAM API: {exc}") from exc

        if response.status_code != 200:
            raise RuntimeError(f"Authentication failed: HTTP {response.status_code} from IAM API.")

        data = response.json()
        self._access_token = data["accessToken"]
        expires_in = data.get("expiresIn", 1800)
        self._expires_at = time.time() + expires_in
