"""Log pipeline handler for the vMonitor MCP server (Log API).

A pipeline is a container of processor groups that transform logs as they flow
from a source project to a destination project. These tools manage the pipelines
themselves; their processor groups and processors live in
``log_processor_handler``.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import LogPageData, LogResource, PipelineDto
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class LogPipelineHandler:
    """Register and serve vMonitor Log API pipeline MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_pipelines", annotations=READ)(self.list_pipelines)
        self.mcp.tool(name="get_pipeline", annotations=READ)(self.get_pipeline)

        if self.allow_write:
            self.mcp.tool(name="create_pipeline", annotations=WRITE)(self.create_pipeline)
            self.mcp.tool(name="update_pipeline", annotations=WRITE)(self.update_pipeline)
            self.mcp.tool(name="delete_pipeline", annotations=DESTRUCTIVE)(self.delete_pipeline)

    async def list_pipelines(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List log processing pipelines."""
        params: dict[str, Any] = {}
        if query:
            params["query"] = query
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        data = await self.client.get("/v1/pipelines", params=params)
        return LogPageData.from_api(data)

    async def get_pipeline(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID to retrieve"),
    ) -> LogResource:
        """Get a single pipeline by ID (with its processor groups)."""
        validate_id(pipeline_id, "pipeline_id")
        data = await self.client.get(f"/v1/pipelines/{pipeline_id}")
        return LogResource.from_api(data)

    async def create_pipeline(
        self,
        body: PipelineDto = Field(
            ..., description="PipelineDto body. Required: name. Optional: description."
        ),
    ) -> LogResource:
        """Create a log processing pipeline.

        ## Requirements
        - Server must run with --allow-write
        - `name` is required; add processor groups afterwards with create_processor_group.
        """
        data = await self.client.post("/v1/pipelines", json=body.model_dump(exclude_none=True))
        return LogResource.from_api(data)

    async def update_pipeline(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID to update"),
        body: PipelineDto = Field(
            ..., description="PipelineDto body. Required: name. Optional: description."
        ),
    ) -> str:
        """Rename/redescribe a pipeline.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(pipeline_id, "pipeline_id")
        await self.client.put(
            f"/v1/pipelines/{pipeline_id}", json=body.model_dump(exclude_none=True)
        )
        return f"Pipeline {pipeline_id} updated."

    async def delete_pipeline(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a pipeline and its processor groups. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_pipeline first.
        """
        validate_id(pipeline_id, "pipeline_id")
        await self.client.delete(f"/v1/pipelines/{pipeline_id}")
        return f"Pipeline {pipeline_id} deleted."
