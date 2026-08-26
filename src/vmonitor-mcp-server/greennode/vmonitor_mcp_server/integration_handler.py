"""Integration (app) handler for the vMonitor MCP server.

An integration is an installable app/plugin (e.g. a product agent) that adds a
metric source to vMonitor. These tools list them, read one, install/uninstall
them, and remove them.

- ``list_integrations`` / ``get_integration`` read the catalogue.
- ``update_integration_installed`` / ``update_integration_uninstalled`` toggle
  the installed state (install carries optional config).
- ``delete_integration`` removes an integration (irreversible).
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    InstallIntegrationDto,
    IntegrationDetail,
    IntegrationListData,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class IntegrationHandler:
    """Register and serve vMonitor integration MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_integrations", annotations=READ)(self.list_integrations)
        self.mcp.tool(name="get_integration", annotations=READ)(self.get_integration)

        if self.allow_write:
            self.mcp.tool(name="update_integration_installed", annotations=WRITE)(
                self.update_integration_installed
            )
            self.mcp.tool(name="update_integration_uninstalled", annotations=WRITE)(
                self.update_integration_uninstalled
            )
            self.mcp.tool(name="delete_integration", annotations=DESTRUCTIVE)(
                self.delete_integration
            )

    async def list_integrations(
        self,
        page: int | None = Field(
            None,
            ge=1,
            description="1-based page number. Omit to return every integration at once.",
        ),
        size: int | None = Field(
            None, ge=1, description="Page size. Only applies when page is set."
        ),
    ) -> IntegrationListData:
        """List the available integrations and whether each is installed."""
        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        data = await self.client.get("/api/v1/integrations/list", params=params)
        return IntegrationListData.from_api(data if isinstance(data, dict) else {})

    async def get_integration(
        self,
        integration_id: str = Field(..., description="Integration ID to retrieve"),
    ) -> IntegrationDetail:
        """Get a single integration by ID (with its configuration/overview text)."""
        validate_id(integration_id, "integration_id")
        data = await self.client.get(f"/api/v1/integrations/{integration_id}")
        return IntegrationDetail.from_api(data if isinstance(data, dict) else {})

    async def update_integration_installed(
        self,
        integration_id: str = Field(..., description="Integration ID to install"),
        body: InstallIntegrationDto = Field(
            ...,
            description=(
                "InstallIntegrationDto body. Optional: logProjectId (bind the "
                "integration to a log project if it collects logs)."
            ),
        ),
    ) -> IntegrationDetail:
        """Install an integration (enable the app as a metric source).

        ## Requirements
        - Server must run with --allow-write
        - Provide logProjectId only if the integration collects logs.
        """
        validate_id(integration_id, "integration_id")
        data = await self.client.put(
            f"/api/v1/integrations/install/{integration_id}",
            json=body.model_dump(exclude_none=True),
        )
        return IntegrationDetail.from_api(data if isinstance(data, dict) else {})

    async def update_integration_uninstalled(
        self,
        integration_id: str = Field(..., description="Integration ID to uninstall"),
    ) -> str:
        """Uninstall an integration (disable the app) without deleting it.

        ## Requirements
        - Server must run with --allow-write
        - The integration stays in the catalogue; use delete_integration to remove it.
        """
        validate_id(integration_id, "integration_id")
        await self.client.put(f"/api/v1/integrations/uninstall/{integration_id}")
        return f"Integration {integration_id} uninstalled."

    async def delete_integration(
        self,
        integration_id: str = Field(..., description="Integration ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete an integration from the catalogue. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with list_integrations first; deletion cannot be undone.
        """
        validate_id(integration_id, "integration_id")
        await self.client.delete(f"/api/v1/integrations/{integration_id}")
        return f"Integration {integration_id} deleted."
