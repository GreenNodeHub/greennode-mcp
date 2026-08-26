"""Synthetic uptime handler for the vMonitor MCP server (uptime manager).

A synthetic (uptime) monitor periodically probes a target from one or more
locations and alarms on failures (the "API test" capability). These tools
list/read monitors, preview a probe, and (with --allow-write) create,
edit, toggle and delete monitors. Uses ``VmonitorUptimeClient`` (separate host,
same IAM auth; note this host returns snake_case fields).
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorUptimeClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateUptimeDto,
    SyntheticListData,
    SyntheticResource,
    UpdateUptimeDto,
    ValidateUptimeDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


_DEFAULT_UPTIME_NOTIFICATIONS = {"Undetermined": [], "Up": [], "In-alarm": []}


class SyntheticUptimeHandler:
    """Register and serve vMonitor synthetic uptime-monitor MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorUptimeClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_uptimes", annotations=READ)(self.list_uptimes)
        self.mcp.tool(name="get_uptime", annotations=READ)(self.get_uptime)
        self.mcp.tool(name="get_uptime_config", annotations=READ)(self.get_uptime_config)
        self.mcp.tool(name="validate_uptime", annotations=READ)(self.validate_uptime)

        if self.allow_write:
            self.mcp.tool(name="create_uptime", annotations=WRITE)(self.create_uptime)
            self.mcp.tool(name="update_uptime", annotations=WRITE)(self.update_uptime)
            self.mcp.tool(name="update_uptime_status", annotations=WRITE)(
                self.update_uptime_status
            )
            self.mcp.tool(name="delete_uptime", annotations=DESTRUCTIVE)(self.delete_uptime)

    async def list_uptimes(self) -> SyntheticListData:
        """List the user's synthetic uptime monitors."""
        data = await self.client.get("/uptimes")
        return SyntheticListData.from_api(data)

    async def get_uptime(
        self,
        uptime_id: str = Field(..., description="Uptime monitor ID to retrieve"),
    ) -> SyntheticResource:
        """Get a single uptime monitor by ID (config, options, locations, status)."""
        validate_id(uptime_id, "uptime_id")
        data = await self.client.get(f"/uptimes/{uptime_id}")
        return SyntheticResource.from_api(data)

    async def get_uptime_config(self) -> SyntheticResource:
        """Get uptime-manager config (private-location worker install instructions)."""
        data = await self.client.get("/configs")
        return SyntheticResource.from_api(data)

    async def validate_uptime(
        self,
        body: ValidateUptimeDto = Field(
            ...,
            description="ValidateUptimeDto body: subtype, config (assertions+request), optional locations.",
        ),
    ) -> SyntheticResource:
        """Run a probe once without saving, to preview the result (read-only)."""
        data = await self.client.post("/uptimes/test", json=body.model_dump(exclude_none=True))
        return SyntheticResource.from_api(data)

    async def create_uptime(
        self,
        body: CreateUptimeDto = Field(
            ...,
            description=(
                "CreateUptimeDto body. Required: name, subtype, config, locations. "
                "Optional: type (default 'API'), options, notifications."
            ),
        ),
    ) -> SyntheticResource:
        """Create a synthetic uptime monitor.

        ## Requirements
        - Server must run with --allow-write
        - Preview the probe first with validate_uptime.
        - `locations` are location IDs (see list_locations).
        - `notifications` must carry the three alarm-state keys
          (`Undetermined`, `Up`, `In-alarm`), each a list of notification channel
          IDs; omit it and an empty-per-state default is sent (monitor with no
          alerting). A missing/partial map is rejected upstream (500).
        """
        payload = body.model_dump(exclude_none=True)
        payload.setdefault("notifications", dict(_DEFAULT_UPTIME_NOTIFICATIONS))
        data = await self.client.post("/uptimes", json=payload)
        return SyntheticResource.from_api(data)

    async def update_uptime(
        self,
        uptime_id: str = Field(..., description="Uptime monitor ID to update"),
        body: UpdateUptimeDto = Field(
            ...,
            description="UpdateUptimeDto body (same shape as create): name, subtype, config, locations, ...",
        ),
    ) -> SyntheticResource:
        """Update a synthetic uptime monitor.

        Full-object replace (same shape as create): send name/subtype/config/
        locations together. `notifications` must carry the three alarm-state keys
        (`Undetermined`, `Up`, `In-alarm`); omit it and an empty-per-state default
        is sent.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(uptime_id, "uptime_id")
        payload = body.model_dump(exclude_none=True)
        payload.setdefault("notifications", dict(_DEFAULT_UPTIME_NOTIFICATIONS))
        data = await self.client.put(f"/uptimes/{uptime_id}", json=payload)
        return SyntheticResource.from_api(data)

    async def update_uptime_status(
        self,
        uptime_id: str = Field(
            ..., description="Uptime monitor ID to toggle (enable <-> disable)"
        ),
    ) -> str:
        """Toggle a monitor's status (enabled <-> disabled).

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(uptime_id, "uptime_id")
        await self.client.put(f"/uptimes/status/{uptime_id}")
        return f"Uptime monitor {uptime_id} status toggled."

    async def delete_uptime(
        self,
        uptime_id: str = Field(..., description="Uptime monitor ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a synthetic uptime monitor. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_uptime first.
        """
        validate_id(uptime_id, "uptime_id")
        await self.client.delete(f"/uptimes/{uptime_id}")
        return f"Uptime monitor {uptime_id} deleted."
