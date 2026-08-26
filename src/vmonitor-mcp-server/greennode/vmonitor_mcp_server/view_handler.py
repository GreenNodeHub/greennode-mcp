"""Dashboard view handler for the vMonitor MCP server.

A dashboard view is a saved preset of the dashboard's query, filters, time range
and variable selections — one click re-applies a whole exploration state. These
tools are the CRUD surface for those saved views.

- ``list_dashboard_views`` / ``get_dashboard_view`` read a dashboard's views.
- ``create_dashboard_view`` / ``update_dashboard_view`` save or edit a view.
- ``delete_dashboard_view`` removes a saved view (irreversible).
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateViewDto,
    UpdateViewDto,
    ViewListData,
    ViewSummary,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


class ViewHandler:
    """Register and serve vMonitor dashboard-view MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_dashboard_views", annotations=READ)(self.list_dashboard_views)
        self.mcp.tool(name="get_dashboard_view", annotations=READ)(self.get_dashboard_view)

        if self.allow_write:
            self.mcp.tool(name="create_dashboard_view", annotations=WRITE)(
                self.create_dashboard_view
            )
            self.mcp.tool(name="update_dashboard_view", annotations=WRITE)(
                self.update_dashboard_view
            )
            self.mcp.tool(name="delete_dashboard_view", annotations=DESTRUCTIVE)(
                self.delete_dashboard_view
            )

    async def list_dashboard_views(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID to list views for"),
    ) -> ViewListData:
        """List the saved views of a dashboard."""
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.get(f"/api/v1/dashboards/{dashboard_id}/views")
        return ViewListData.from_api(data)

    async def get_dashboard_view(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the view belongs to"),
        view_id: str = Field(..., description="View ID to retrieve"),
    ) -> ViewSummary:
        """Get a single saved dashboard view by ID."""
        validate_id(dashboard_id, "dashboard_id")
        validate_id(view_id, "view_id")
        data = await self.client.get(f"/api/v1/dashboards/{dashboard_id}/views/{view_id}")
        return ViewSummary.from_api(data)

    async def create_dashboard_view(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID to save the view under"),
        body: CreateViewDto = Field(
            ...,
            description=(
                "CreateViewDto body. Required: name. Optional: variables (key->value "
                "map), filters, query, timeRange (serialized state strings)."
            ),
        ),
    ) -> ViewSummary:
        """Save the current exploration state of a dashboard as a named view.

        Only `name` is required; the state fields (`variables`, `filters`,
        `query`, `timeRange`) capture the exploration to restore. The backend
        answers 500 when any of the four is absent, so a name-only body defaults
        them to empty state here and saves an empty view.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(dashboard_id, "dashboard_id")
        payload = body.model_dump(exclude_none=True)
        payload.setdefault("variables", {})
        payload.setdefault("filters", "[]")
        payload.setdefault("query", "{}")
        payload.setdefault("timeRange", "{}")
        data = await self.client.post(
            f"/api/v1/dashboards/{dashboard_id}/views",
            json=payload,
        )
        return ViewSummary.from_api(data)

    async def update_dashboard_view(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the view belongs to"),
        view_id: str = Field(..., description="View ID to update"),
        body: UpdateViewDto = Field(
            ...,
            description=(
                "UpdateViewDto body. Optional: variables (bindings), filters, query, "
                "timeRange — only send the state you want to change."
            ),
        ),
    ) -> ViewSummary:
        """Update a saved dashboard view's stored state.

        Full-state replace: send `variables`, `filters`, `query` and `timeRange`
        together (a partial body answers 500). `variables` is the list of the
        dashboard's variable bindings ({variableId, value, key, id?}); read the
        current view via list_dashboard_views to rebuild it. The view name is not
        editable here.

        ## Requirements
        - Server must run with --allow-write
        - Send the full view state, not just the changed field.
        """
        validate_id(dashboard_id, "dashboard_id")
        validate_id(view_id, "view_id")
        data = await self.client.put(
            f"/api/v1/dashboards/{dashboard_id}/views/{view_id}",
            json=body.model_dump(exclude_none=True),
        )
        return ViewSummary.from_api(data)

    async def delete_dashboard_view(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the view belongs to"),
        view_id: str = Field(..., description="View ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a saved dashboard view. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with list_dashboard_views first; deletion cannot be undone.
        """
        validate_id(dashboard_id, "dashboard_id")
        validate_id(view_id, "view_id")
        await self.client.delete(f"/api/v1/dashboards/{dashboard_id}/views/{view_id}")
        return f"View {view_id} deleted from dashboard {dashboard_id}."
