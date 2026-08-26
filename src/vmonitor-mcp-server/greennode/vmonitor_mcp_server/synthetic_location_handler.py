"""Synthetic location handler for the vMonitor MCP server (uptime manager).

A location is a vantage point a synthetic monitor probes from — PUBLIC ones are
provided by the platform, PRIVATE ones are workers the user runs. These tools
list/read locations and (with --allow-write) manage private ones. Uses
``VmonitorUptimeClient`` (same host as the uptime monitors).
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorUptimeClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateLocationDto,
    SyntheticListData,
    SyntheticResource,
    UpdateLocationDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


class SyntheticLocationHandler:
    """Register and serve vMonitor synthetic-location MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorUptimeClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_locations", annotations=READ)(self.list_locations)
        self.mcp.tool(name="get_location", annotations=READ)(self.get_location)

        if self.allow_write:
            self.mcp.tool(name="create_location", annotations=WRITE)(self.create_location)
            self.mcp.tool(name="update_location", annotations=WRITE)(self.update_location)
            self.mcp.tool(name="delete_location", annotations=DESTRUCTIVE)(self.delete_location)

    async def list_locations(self) -> SyntheticListData:
        """List probing locations available to the user (PUBLIC + own PRIVATE)."""
        data = await self.client.get("/locations")
        return SyntheticListData.from_api(data)

    async def get_location(
        self,
        location_id: str = Field(..., description="Location ID to retrieve"),
    ) -> SyntheticResource:
        """Get a single location by ID."""
        validate_id(location_id, "location_id")
        data = await self.client.get(f"/locations/{location_id}")
        return SyntheticResource.from_api(data)

    async def create_location(
        self,
        body: CreateLocationDto = Field(
            ...,
            description="CreateLocationDto body. Required: name. Optional: type (default PRIVATE), description.",
        ),
    ) -> SyntheticResource:
        """Create a private probing location.

        ## Requirements
        - Server must run with --allow-write
        - Install the location worker afterwards using get_uptime_config instructions.
        """
        data = await self.client.post("/locations", json=body.model_dump(exclude_none=True))
        return SyntheticResource.from_api(data)

    async def update_location(
        self,
        location_id: str = Field(..., description="Location ID to update"),
        body: UpdateLocationDto = Field(
            ..., description="UpdateLocationDto body: name, optional description/type."
        ),
    ) -> SyntheticResource:
        """Update a location.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(location_id, "location_id")
        data = await self.client.put(
            f"/locations/{location_id}", json=body.model_dump(exclude_none=True)
        )
        return SyntheticResource.from_api(data)

    async def delete_location(
        self,
        location_id: str = Field(..., description="Location ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a location. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_location first; monitors using it must be updated.
        """
        validate_id(location_id, "location_id")
        await self.client.delete(f"/locations/{location_id}")
        return f"Location {location_id} deleted."
