"""Dashboard variable handler for the vMonitor MCP server.

A dashboard variable is a reusable, dashboard-scoped value (e.g. ``$host``) that
its widgets' queries reference, so one control re-scopes every chart at once.

- ``list_dashboard_variables`` / ``get_dashboard_variable`` read a dashboard's
  variables.
- ``update_dashboard_variables`` replaces the whole variable list (write).
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    UpdateVariableListDto,
    VariableListData,
    VariableSummary,
)
from greennode.vmonitor_mcp_server.tool_annotations import READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


class VariableHandler:
    """Register and serve vMonitor dashboard-variable MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_dashboard_variables", annotations=READ)(
            self.list_dashboard_variables
        )
        self.mcp.tool(name="get_dashboard_variable", annotations=READ)(self.get_dashboard_variable)

        if self.allow_write:
            self.mcp.tool(name="update_dashboard_variables", annotations=WRITE)(
                self.update_dashboard_variables
            )

    async def list_dashboard_variables(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID to list variables for"),
    ) -> VariableListData:
        """List the variables defined on a dashboard.

        Variables are the dashboard's shared query inputs (their keys appear in
        widget queries as `$key`). Use this before updating them.
        """
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.get(f"/api/v1/dashboards/{dashboard_id}/variables")
        return VariableListData.from_api(data)

    async def get_dashboard_variable(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the variable belongs to"),
        variable_id: str = Field(..., description="Variable ID to retrieve"),
    ) -> VariableSummary:
        """Get a single dashboard variable by ID."""
        validate_id(dashboard_id, "dashboard_id")
        validate_id(variable_id, "variable_id")
        data = await self.client.get(f"/api/v1/dashboards/{dashboard_id}/variables/{variable_id}")
        return VariableSummary.from_api(data)

    async def update_dashboard_variables(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID whose variables to replace"),
        body: UpdateVariableListDto = Field(
            ...,
            description=(
                "UpdateVariableListDto body. Required: variables (the FULL variable "
                "list to save, as camelCase VariableDto objects)."
            ),
        ),
    ) -> VariableListData:
        """Replace the full variable list of a dashboard.

        This is a whole-list replace, not a per-variable patch: send every
        variable you want to keep. Read the current list first with
        list_dashboard_variables, edit it, then submit the complete set.

        ## Requirements
        - Server must run with --allow-write
        - `variables` must be the complete list (omitted variables are removed).
        """
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.put(
            f"/api/v1/dashboards/{dashboard_id}/variables",
            json=body.model_dump(exclude_none=True),
        )
        return VariableListData.from_api(data)
