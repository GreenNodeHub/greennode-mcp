"""Example handler for the {{PRODUCT}} MCP server.

Follow this pattern for every handler (see the repo-root CLAUDE.md):
- tools named ``verb_noun`` (mirror the greennode-cli command where one exists)
- ``validate_id()`` on every ID used in URL construction
- write tools gated behind ``allow_write`` and documented with ``## Requirements``
- typed Pydantic DTOs (``extra="forbid"``) for create/update bodies
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.{{product_snake}}_mcp_server.client import {{Product}}Client
from greennode.{{product_snake}}_mcp_server.config import Region, {{Product}}Config
from pydantic import BaseModel, Field


class ExampleItem(BaseModel):
    """One {{PRODUCT}} resource, as returned by list_examples."""

    id: str = Field(..., description="Resource ID")
    name: str = Field("", description="Resource name")
    status: str = Field("", description="Resource status")

    @classmethod
    def from_api(cls, data: dict) -> "ExampleItem":
        """Build an ExampleItem from a raw API dict."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            status=data.get("status", ""),
        )


class ExampleListData(BaseModel):
    """Wrapper for the list_examples response (structured output)."""

    region: str = Field(..., description="Region name")
    items: list[ExampleItem] = Field(default_factory=list, description="Resources")


class ExampleHandler:
    """Register and serve {{PRODUCT}} MCP tools."""

    def __init__(
        self,
        mcp,
        config: {{Product}}Config,
        client: {{Product}}Client,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # Read-only tools (always registered)
        self.mcp.tool(name="list_examples")(self.list_examples)

        # Write tools (only registered with --allow-write)
        # if self.allow_write:
        #     self.mcp.tool(name="create_example")(self.create_example)

    async def list_examples(
        self,
        region: Region | None = Field(None, description="Region override"),
    ) -> ExampleListData:
        """List {{PRODUCT}} resources. Replace with your first real tool."""
        data = await self.client.get("/v1/examples", region=region)
        items = data.get("items", data) if isinstance(data, dict) else data
        resolved_region = region or self.config.default_region
        return ExampleListData(
            region=resolved_region,
            items=[ExampleItem.from_api(i) for i in items],
        )
