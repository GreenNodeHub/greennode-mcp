"""Log project handler for the vMonitor MCP server (Log API).

A log project is the top-level container that ingested logs are stored and
searched under. These tools list/read projects and edit their settings and field
mappings. Its client certificates are managed under the Integration group (see
``certificate_handler``).
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    LogPageData,
    LogResource,
    UpdateProjectDto,
    UpdateProjectMappingsDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class LogProjectHandler:
    """Register and serve vMonitor Log API project MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_projects", annotations=READ)(self.list_projects)
        self.mcp.tool(name="get_project", annotations=READ)(self.get_project)
        self.mcp.tool(name="get_project_mappings", annotations=READ)(self.get_project_mappings)

        if self.allow_write:
            self.mcp.tool(name="update_project", annotations=WRITE)(self.update_project)
            self.mcp.tool(name="update_project_mappings", annotations=WRITE)(
                self.update_project_mappings
            )

    async def list_projects(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        status: str | None = Field(None, description="Filter by project status"),
        project_type: str | None = Field(None, description="Filter by project type"),
        billing_status: str | None = Field(None, description="Filter by billing status"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List log projects with optional filters."""
        params: dict[str, Any] = {}
        for key, value in (
            ("query", query),
            ("status", status),
            ("project_type", project_type),
            ("billing_status", billing_status),
            ("page", page),
            ("size", size),
        ):
            if value is not None:
                params[key] = value
        data = await self.client.get("/v1/projects", params=params)
        return LogPageData.from_api(data)

    async def get_project(
        self,
        project_id: str = Field(..., description="Log project ID to retrieve"),
    ) -> LogResource:
        """Get a single log project by ID."""
        validate_id(project_id, "project_id")
        data = await self.client.get(f"/v1/projects/{project_id}")
        return LogResource.from_api(data)

    async def get_project_mappings(
        self,
        project_id: str = Field(..., description="Log project ID"),
        refresh: bool | None = Field(
            None, description="Re-detect mappings from recent data instead of the cached set"
        ),
    ) -> LogResource:
        """Get a log project's field mappings (detected log fields and their types)."""
        validate_id(project_id, "project_id")
        params = {"refresh": refresh} if refresh is not None else {}
        data = await self.client.get(f"/v1/projects/{project_id}/mappings", params=params)
        return LogResource.from_api(data)

    async def update_project(
        self,
        project_id: str = Field(..., description="Log project ID to update"),
        body: UpdateProjectDto = Field(
            ...,
            description="UpdateProjectDto body. Optional: description, mappings.",
        ),
    ) -> LogResource:
        """Update a log project's description and/or field mappings.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(project_id, "project_id")
        data = await self.client.patch(
            f"/v1/projects/{project_id}", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)

    async def update_project_mappings(
        self,
        project_id: str = Field(..., description="Log project ID"),
        body: UpdateProjectMappingsDto = Field(
            ...,
            description=(
                "UpdateProjectMappingsDto body — set a field's mapping format "
                "(name, type, format, inputFormat, outputFormat, datePattern)."
            ),
        ),
    ) -> str:
        """Set the mapping format of a project field.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(project_id, "project_id")
        await self.client.put(
            f"/v1/projects/{project_id}/mappings", json=body.model_dump(exclude_none=True)
        )
        return f"Project {project_id} mappings updated."
