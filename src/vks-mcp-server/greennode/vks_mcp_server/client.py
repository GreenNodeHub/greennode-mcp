"""HTTP client for VKS API with retry on 5xx/timeout and auto-refresh on 401."""

from __future__ import annotations

import asyncio
import httpx
import logging
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.config import VksConfig
from typing import Any


LOG = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1  # seconds
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
DEFAULT_TIMEOUT = 30  # seconds


class VksClient:
    """Thin async HTTP client for the VKS API."""

    def __init__(self, config: VksConfig, token_manager: TokenManager) -> None:
        self._config = config
        self._token_manager = token_manager

    async def _request(
        self,
        method: str,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        raw_response: bool = False,
        service: str = "vks",
        _retried_auth: bool = False,
    ) -> Any:
        """Send an HTTP request to the VKS API.

        Retries up to ``MAX_RETRIES`` times on 5xx errors and network
        timeouts with exponential backoff (1s, 2s, 4s).  Automatically
        retries once on 401 by refreshing the access token.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            path: API path (e.g. ``/v1/clusters``).
            region: Region override; ``None`` uses the default region.
            params: Optional query parameters.
            json: Optional JSON body.
            raw_response: If ``True``, return the raw response text
                instead of parsed JSON.
            service: Target API — ``"vks"`` (default) uses the VKS base URL,
                ``"vserver"`` uses the vServer base URL.
            _retried_auth: Internal flag to prevent infinite 401 retry loops.
        """
        endpoints = self._config.get_endpoints(region)
        base = endpoints.vserver if service == "vserver" else endpoints.vks
        url = f"{base}{path}"

        for attempt in range(MAX_RETRIES + 1):
            token = await self._token_manager.get_token()
            headers = {"Authorization": f"Bearer {token}"}

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json,
                    )
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    LOG.debug(
                        "Request timeout/error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        MAX_RETRIES + 1,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError(
                    f"Request failed after {MAX_RETRIES + 1} attempts: {exc}"
                ) from exc

            # 401 — refresh token and retry once
            if resp.status_code == 401:
                if _retried_auth:
                    self._raise_error(resp)
                self._token_manager._expires_at = 0
                return await self._request(
                    method,
                    path,
                    region=region,
                    params=params,
                    json=json,
                    raw_response=raw_response,
                    service=service,
                    _retried_auth=True,
                )

            # Retryable server errors (5xx)
            if resp.status_code in RETRYABLE_STATUS_CODES:
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    LOG.debug(
                        "Server error %d (attempt %d/%d), retrying in %ds",
                        resp.status_code,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

            if not resp.is_success:
                self._raise_error(resp)

            if raw_response:
                return resp.text

            return resp.json()

        # Should not reach here, but just in case
        raise RuntimeError(f"Request failed after {MAX_RETRIES + 1} attempts")

    def _raise_error(self, resp: httpx.Response) -> None:
        """Raise a ``RuntimeError`` with a descriptive error message."""
        try:
            body = resp.json()

            msg = (
                body.get("message")
                or body.get("error")
                or body.get("errors", [{}])[0].get("message", "")
                or str(body)
            )
        except Exception:
            msg = resp.text or "unknown error"

        status = resp.status_code

        if status == 400:
            raise RuntimeError(f"Bad request: {msg}")
        if status == 401:
            raise RuntimeError(
                "Token expired or invalid. Please check your authentication configuration."
            )
        if status == 404:
            raise RuntimeError(f"Resource not found: {msg}")
        if status == 409:
            raise RuntimeError("Resource is being processed. Please wait and try again.")

        raise RuntimeError(f"API error ({status}): {msg}")

    async def get(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a GET request."""
        return await self._request("GET", path, region=region, params=params)

    async def post(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Send a POST request."""
        return await self._request("POST", path, region=region, params=params, json=json)

    async def put(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Send a PUT request."""
        return await self._request("PUT", path, region=region, params=params, json=json)

    async def patch(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Send a PATCH request."""
        return await self._request("PATCH", path, region=region, params=params, json=json)

    async def delete(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a DELETE request."""
        return await self._request("DELETE", path, region=region, params=params)

    async def get_raw(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Send a GET request and return the raw response text."""
        return await self._request("GET", path, region=region, params=params, raw_response=True)

    async def vserver_get(
        self,
        path: str,
        region: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a GET request to the vServer API (not the VKS API)."""
        return await self._request("GET", path, region=region, params=params, service="vserver")
