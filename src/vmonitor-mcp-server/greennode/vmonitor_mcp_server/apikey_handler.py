"""Metric API key handler for the vMonitor MCP server.

Metric API keys authenticate external agents/clients that push custom metrics
into vMonitor. These tools list, create and revoke those keys.

- ``list_metric_api_keys`` lists existing keys.
- ``create_metric_api_key`` issues a new key.
- ``delete_metric_api_key`` revokes a key (irreversible).
"""

from __future__ import annotations

import urllib.parse
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    ApiKeyListData,
    ApiKeySummary,
    CreateMetricApiKeyDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


def _key_segment(key: str) -> str:
    """URL-encode an API key for use as a single path segment (traversal-safe)."""
    if not key or "/" in key or "\\" in key:
        raise ValueError(
            f"Invalid API key: '{key}'. Must be non-empty and contain no path separators."
        )
    return urllib.parse.quote(key, safe="")


class ApiKeyHandler:
    """Register and serve vMonitor metric API key MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_metric_api_keys", annotations=READ)(self.list_metric_api_keys)

        if self.allow_write:
            self.mcp.tool(name="create_metric_api_key", annotations=WRITE)(
                self.create_metric_api_key
            )
            self.mcp.tool(name="delete_metric_api_key", annotations=DESTRUCTIVE)(
                self.delete_metric_api_key
            )

    async def list_metric_api_keys(
        self,
        name: str | None = Field(None, description="Filter keys by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(100, ge=1, description="Page size"),
    ) -> ApiKeyListData:
        """List the metric API keys issued for pushing custom metrics into vMonitor.

        The upstream endpoint returns 500 ("Internal Error") unless page and size
        are present, so both are always sent (defaulting to page 1, size 100).
        """
        params: dict[str, Any] = {"page": page, "size": size}
        if name:
            params["name"] = name
        data = await self.client.get("/api/v1/apikeys/metric/list", params=params)
        return ApiKeyListData.from_api(data if isinstance(data, dict) else {})

    async def create_metric_api_key(
        self,
        body: CreateMetricApiKeyDto = Field(
            ...,
            description="CreateMetricApiKeyDto body. Required: name. Optional: description.",
        ),
    ) -> ApiKeySummary:
        """Issue a new metric API key.

        The returned `key` is the **secret** shown once here — store it securely;
        it authenticates external clients pushing custom metrics (it can contain
        `/`). Note this secret is NOT the value used to delete the key: the delete
        identifier is a different `key` value returned by list_metric_api_keys.

        ## Requirements
        - Server must run with --allow-write
        - `name` is required; `description` (optional) must not contain spaces.
        """
        data = await self.client.post(
            "/api/v1/apikeys/metric", json=body.model_dump(exclude_none=True)
        )
        return ApiKeySummary.from_api(data if isinstance(data, dict) else {})

    async def delete_metric_api_key(
        self,
        key: str = Field(..., description="The API key value to revoke. IRREVERSIBLE."),
    ) -> str:
        """Revoke a metric API key. IRREVERSIBLE.

        Clients still using the key will stop being able to push metrics.

        Pass the `key` value from **list_metric_api_keys** (the deletion
        identifier) — NOT the longer secret returned by create_metric_api_key.
        Always look the key up via list_metric_api_keys right before deleting.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the key with list_metric_api_keys first; revocation cannot be undone.
        """
        segment = _key_segment(key)
        await self.client.delete(f"/api/v1/apikeys/metric/{segment}")
        return f"Metric API key '{key}' revoked."
