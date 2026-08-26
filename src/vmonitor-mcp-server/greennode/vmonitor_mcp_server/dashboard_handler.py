"""Dashboard handler for the vMonitor MCP server."""

from __future__ import annotations

import urllib.parse
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateDashboardDto,
    DashboardDetail,
    DashboardListData,
    UpdateDashboardDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


def _name_segment(name: str) -> str:
    """URL-encode a dashboard *name* for use as a single path segment.

    Rejects path separators (defence in depth against path traversal) and
    percent-encodes the rest, so a name with spaces or reserved characters
    stays a single, safe segment.
    """
    if not name or "/" in name or "\\" in name:
        raise ValueError(
            f"Invalid dashboard name: '{name}'. Must be non-empty and contain no path separators."
        )
    return urllib.parse.quote(name, safe="")


class DashboardHandler:
    """Register and serve vMonitor dashboard MCP tools."""

    def __init__(
        self,
        mcp,
        config: VmonitorConfig,
        client: VmonitorClient,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_dashboards", annotations=READ)(self.list_dashboards)
        self.mcp.tool(name="get_dashboard", annotations=READ)(self.get_dashboard)
        self.mcp.tool(name="get_dashboard_by_name", annotations=READ)(self.get_dashboard_by_name)

        if self.allow_write:
            self.mcp.tool(name="create_dashboard", annotations=WRITE)(self.create_dashboard)
            self.mcp.tool(name="create_dashboard_clone", annotations=WRITE)(
                self.create_dashboard_clone
            )
            self.mcp.tool(name="update_dashboard", annotations=WRITE)(self.update_dashboard)
            self.mcp.tool(name="update_dashboard_name", annotations=WRITE)(
                self.update_dashboard_name
            )
            self.mcp.tool(name="update_dashboard_favorite", annotations=WRITE)(
                self.update_dashboard_favorite
            )
            self.mcp.tool(name="delete_dashboard", annotations=DESTRUCTIVE)(self.delete_dashboard)

    async def list_dashboards(
        self,
        searching_text: str | None = Field(
            None, description="Text to filter dashboards by (matched against searching_field)"
        ),
        searching_field: str = Field(
            "name", description="Field the searching_text is matched against (default: 'name')"
        ),
        page: int | None = Field(
            None,
            ge=1,
            description="1-based page number. Omit to return every matching dashboard in one response.",
        ),
        size: int | None = Field(
            None, ge=1, description="Page size. Only applies when page is set."
        ),
    ) -> DashboardListData:
        """List vMonitor dashboards.

        Returns the dashboards visible to the caller with their paging envelope.
        By default (no page) the vMonitor API returns every matching dashboard
        in a single response; pass page (1-based) and size to paginate. Use
        searching_text to filter by searching_field (defaults to the name).
        """
        params: dict[str, Any] = {}
        if searching_text:
            params["searching-field"] = searching_field
            params["searching-text"] = searching_text
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size

        data = await self.client.get("/api/v1/dashboards", params=params)
        return DashboardListData.from_api(data if isinstance(data, dict) else {})

    async def get_dashboard(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID to retrieve"),
    ) -> DashboardDetail:
        """Get a single vMonitor dashboard by ID, including its widget count.

        This returns the dashboard's own settings and how many widgets it holds,
        not the widgets themselves — call `list_widgets` for those. On a
        resource's default (system) dashboard those widgets already carry the
        metric name and `dimensions` of every chart, so `list_widgets` is the
        shortest route from "which dashboard" to actual metric data.
        """
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.get(f"/api/v1/dashboards/{dashboard_id}")
        return DashboardDetail.from_api(data if isinstance(data, dict) else {})

    async def get_dashboard_by_name(
        self,
        name: str = Field(..., description="Exact dashboard name to look up"),
    ) -> DashboardDetail:
        """Get a single vMonitor dashboard by its exact name.

        Useful when you know a dashboard's name (e.g. a host's auto-generated
        default dashboard) but not its ID. Chain it into `list_widgets` to read
        the metric queries that dashboard already plots.
        """
        segment = _name_segment(name)
        data = await self.client.get(f"/api/v1/dashboards/name/{segment}")
        return DashboardDetail.from_api(data if isinstance(data, dict) else {})

    async def create_dashboard(
        self,
        body: CreateDashboardDto = Field(
            ...,
            description=(
                "CreateDashboardDto body. Required: name. Optional time-range fields "
                "(period, or startTime + endTime) and extra; omit them for the "
                "platform default range. A new dashboard has no widgets."
            ),
        ),
    ) -> DashboardDetail:
        """Create a new (empty) vMonitor dashboard.

        ## Requirements
        - Server must run with --allow-write
        - `name` is required; a fresh dashboard starts with no widgets or views.
        """
        data = await self.client.post(
            "/api/v1/dashboards", json=body.model_dump(exclude_none=True)
        )
        return DashboardDetail.from_api(data if isinstance(data, dict) else {})

    async def create_dashboard_clone(
        self,
        dashboard_id: str = Field(..., description="ID of the dashboard to clone"),
        name: str = Field(..., description="Name for the new cloned dashboard"),
    ) -> DashboardDetail:
        """Clone an existing vMonitor dashboard into a new one.

        Copies the source dashboard (widgets and views) into a new dashboard
        under the given name; the source is left unchanged.

        ## Requirements
        - Server must run with --allow-write
        - `dashboard_id` must reference an existing dashboard (see list_dashboards).
        """
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.post(
            "/api/v1/dashboards/clone", json={"id": dashboard_id, "name": name}
        )
        return DashboardDetail.from_api(data if isinstance(data, dict) else {})

    async def update_dashboard(
        self,
        body: UpdateDashboardDto = Field(
            ...,
            description=(
                "UpdateDashboardDto body. Required: id. Optional settings to change: "
                "name, darkMode, favorite, refreshActive, refreshInterval, timeRange, "
                "timeRangeType, viewSelectedId."
            ),
        ),
    ) -> DashboardDetail:
        """Update a dashboard's general settings (the full-settings editor).

        This is the general PUT editor for a dashboard's settings (dark mode,
        auto-refresh, default time range, selected view, ...). For the single-field
        rename / favorite actions prefer update_dashboard_name / update_dashboard_favorite.

        This is a **full-object replace, not a partial patch**: the API rejects a
        body missing its core settings (e.g. `Missing field favorite` /
        `Missing field time range type`). Read the current dashboard with
        get_dashboard first, then resend the complete settings with your edits
        applied — at minimum `favorite`, `timeRange` and `timeRangeType` alongside
        `id`.

        ## Requirements
        - Server must run with --allow-write
        - `id` is required; send the full current settings (read-modify-write),
          not just the changed field.
        """
        validate_id(body.id, "id")
        data = await self.client.put("/api/v1/dashboards", json=body.model_dump(exclude_none=True))
        return DashboardDetail.from_api(data if isinstance(data, dict) else {})

    async def update_dashboard_name(
        self,
        dashboard_id: str = Field(..., description="ID of the dashboard to rename"),
        name: str = Field(..., description="New dashboard name"),
    ) -> DashboardDetail:
        """Rename a vMonitor dashboard.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.put(
            "/api/v1/dashboards/rename", json={"id": dashboard_id, "name": name}
        )
        return DashboardDetail.from_api(data if isinstance(data, dict) else {})

    async def update_dashboard_favorite(
        self,
        dashboard_id: str = Field(..., description="ID of the dashboard to update"),
        favorite: bool = Field(
            ..., description="True to mark as favorite, False to remove from favorites"
        ),
    ) -> DashboardDetail:
        """Mark a vMonitor dashboard as favorite (or remove the favorite flag).

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.put(
            "/api/v1/dashboards/favorite", json={"id": dashboard_id, "favorite": favorite}
        )
        return DashboardDetail.from_api(data if isinstance(data, dict) else {})

    async def delete_dashboard(
        self,
        dashboard_id: str = Field(..., description="ID of the dashboard to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a vMonitor dashboard. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Deletion cannot be undone; confirm the id with get_dashboard first.
        """
        validate_id(dashboard_id, "dashboard_id")
        await self.client.delete(f"/api/v1/dashboards/{dashboard_id}")
        return f"Dashboard {dashboard_id} deleted."
