"""Log refill handler for the vMonitor MCP server (Log API).

A refill re-ingests logs from external storage back into a log project over a
time range — either from raw storage settings, or from an existing archive.
These tools list, read, create and remove refill jobs, and test a storage
connection first.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateRefillDto,
    CreateRefillFromArchiveDto,
    LogPageData,
    LogResource,
    TestStorageDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class LogRefillHandler:
    """Register and serve vMonitor Log API refill MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_refills", annotations=READ)(self.list_refills)
        self.mcp.tool(name="get_refill", annotations=READ)(self.get_refill)
        self.mcp.tool(name="validate_refill_connection", annotations=READ)(
            self.validate_refill_connection
        )

        if self.allow_write:
            self.mcp.tool(name="create_refill", annotations=WRITE)(self.create_refill)
            self.mcp.tool(name="create_refill_from_archive", annotations=WRITE)(
                self.create_refill_from_archive
            )
            self.mcp.tool(name="delete_refill", annotations=DESTRUCTIVE)(self.delete_refill)

    async def list_refills(
        self,
        project_id: str = Field(..., description="Log project ID to list refills for (required)"),
        query: str | None = Field(None, description="Free-text filter"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List refill jobs for a log project."""
        validate_id(project_id, "project_id")
        params: dict[str, Any] = {"project_id": project_id}
        if query:
            params["query"] = query
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        data = await self.client.get("/v1/refills", params=params)
        return LogPageData.from_api(data)

    async def get_refill(
        self,
        refill_id: str = Field(..., description="Refill ID to retrieve"),
    ) -> LogResource:
        """Get a single refill job by ID."""
        validate_id(refill_id, "refill_id")
        data = await self.client.get(f"/v1/refills/{refill_id}")
        return LogResource.from_api(data)

    async def create_refill(
        self,
        body: CreateRefillDto = Field(
            ...,
            description=(
                "CreateRefillDto body. Required: name, projectId, storageType, "
                "storageSettings, startAt, endAt. Optional: filter, description."
            ),
        ),
    ) -> LogResource:
        """Create a refill job (re-ingest logs from external storage into a project).

        ## Requirements
        - Server must run with --allow-write
        - Validate the storage first with validate_refill_connection.
        """
        data = await self.client.post("/v1/refills", json=body.model_dump(exclude_none=True))
        return LogResource.from_api(data)

    async def create_refill_from_archive(
        self,
        body: CreateRefillFromArchiveDto = Field(
            ...,
            description=(
                "CreateRefillFromArchiveDto body. Required: name, projectId, "
                "archiveId, startAt, endAt. Optional: filter, description."
            ),
        ),
    ) -> LogResource:
        """Create a refill job that re-ingests from an existing archive.

        ## Requirements
        - Server must run with --allow-write
        - `archiveId` must reference an existing archive (see list_archives).
        """
        data = await self.client.post(
            "/v1/refills/collections", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)

    async def delete_refill(
        self,
        refill_id: str = Field(..., description="Refill ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a refill job. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_refill first.
        """
        validate_id(refill_id, "refill_id")
        await self.client.delete(f"/v1/refills/{refill_id}")
        return f"Refill {refill_id} deleted."

    async def validate_refill_connection(
        self,
        body: TestStorageDto = Field(
            ...,
            description=(
                "TestStorageDto body. Required: storageType, storageSettings. "
                "Checks the storage is reachable before creating a refill."
            ),
        ),
    ) -> LogResource:
        """Test that a refill's source storage settings are reachable (read-only check)."""
        data = await self.client.post(
            "/v1/refills/test-connection", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)
