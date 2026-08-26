"""Log archive handler for the vMonitor MCP server (Log API).

An archive is an external storage destination (S3-style bucket, vStorage, ...)
that a log project's data is continuously exported to for long-term retention.
These tools list, read, create, edit and remove archives, and test a storage
connection before committing to it.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateArchiveDto,
    LogPageData,
    LogResource,
    TestStorageDto,
    UpdateArchiveDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class LogArchiveHandler:
    """Register and serve vMonitor Log API archive MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_archives", annotations=READ)(self.list_archives)
        self.mcp.tool(name="get_archive", annotations=READ)(self.get_archive)
        self.mcp.tool(name="validate_archive_connection", annotations=READ)(
            self.validate_archive_connection
        )

        if self.allow_write:
            self.mcp.tool(name="create_archive", annotations=WRITE)(self.create_archive)
            self.mcp.tool(name="update_archive", annotations=WRITE)(self.update_archive)
            self.mcp.tool(name="delete_archive", annotations=DESTRUCTIVE)(self.delete_archive)

    async def list_archives(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        project_id: str | None = Field(None, description="Filter by log project ID"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List log archives (external storage export destinations)."""
        params: dict[str, Any] = {}
        if query:
            params["query"] = query
        if project_id:
            params["project_id"] = project_id
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        data = await self.client.get("/v1/archives", params=params)
        return LogPageData.from_api(data)

    async def get_archive(
        self,
        archive_id: str = Field(..., description="Archive ID to retrieve"),
    ) -> LogResource:
        """Get a single log archive by ID."""
        validate_id(archive_id, "archive_id")
        data = await self.client.get(f"/v1/archives/{archive_id}")
        return LogResource.from_api(data)

    async def create_archive(
        self,
        body: CreateArchiveDto = Field(
            ...,
            description=(
                "CreateArchiveDto body. Required: name, projectId, storageType, "
                "storageSettings (storage-type-specific). Optional: filter, description."
            ),
        ),
    ) -> LogResource:
        """Create a log archive (start exporting a project's logs to external storage).

        ## Requirements
        - Server must run with --allow-write
        - Validate the storage first with validate_archive_connection.
        """
        data = await self.client.post("/v1/archives", json=body.model_dump(exclude_none=True))
        return LogResource.from_api(data)

    async def update_archive(
        self,
        archive_id: str = Field(..., description="Archive ID to update"),
        body: UpdateArchiveDto = Field(
            ...,
            description=(
                "UpdateArchiveDto body. Required: projectId, storageType, "
                "storageSettings. Optional: filter."
            ),
        ),
    ) -> LogResource:
        """Update a log archive's storage settings/filter.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(archive_id, "archive_id")
        data = await self.client.put(
            f"/v1/archives/{archive_id}", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)

    async def delete_archive(
        self,
        archive_id: str = Field(..., description="Archive ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a log archive. IRREVERSIBLE.

        Stops exporting to it; already-archived data in the external storage is
        not removed by this call.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_archive first.
        """
        validate_id(archive_id, "archive_id")
        await self.client.delete(f"/v1/archives/{archive_id}")
        return f"Archive {archive_id} deleted."

    async def validate_archive_connection(
        self,
        body: TestStorageDto = Field(
            ...,
            description=(
                "TestStorageDto body. Required: storageType, storageSettings. "
                "Checks the storage is reachable before creating an archive."
            ),
        ),
    ) -> LogResource:
        """Test that an archive's storage settings are reachable (read-only check)."""
        data = await self.client.post(
            "/v1/archives/test-connection", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)
