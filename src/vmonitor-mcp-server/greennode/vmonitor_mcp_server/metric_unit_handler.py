"""Metric-information (unit) handler for the vMonitor MCP server.

Metric information is about how a metric's value is *displayed*: which unit it
uses. These tools list the assignable units and the current metric-to-unit
mappings, and let a user override or reset a metric's display unit.

- ``list_metric_units`` lists the units that may be assigned to a metric.
- ``list_metric_unit_mappings`` lists the current metric-to-unit mappings.
- ``create_metric_unit_mapping`` / ``delete_metric_unit_mapping`` override a
  metric's display unit for the current user, or reset it back to the default.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateMetricUnitMappingDto,
    MetricUnitListData,
    MetricUnitMappingListData,
    MetricUnitMappingUserDetail,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class MetricUnitHandler:
    """Register and serve vMonitor metric-information (unit) MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_metric_units", annotations=READ)(self.list_metric_units)
        self.mcp.tool(name="list_metric_unit_mappings", annotations=READ)(
            self.list_metric_unit_mappings
        )

        if self.allow_write:
            self.mcp.tool(name="create_metric_unit_mapping", annotations=WRITE)(
                self.create_metric_unit_mapping
            )
            self.mcp.tool(name="delete_metric_unit_mapping", annotations=DESTRUCTIVE)(
                self.delete_metric_unit_mapping
            )

    async def list_metric_units(
        self,
        page: int | None = Field(
            None,
            ge=1,
            description="1-based page number. Omit to return every unit in one response.",
        ),
        size: int | None = Field(
            None, ge=1, description="Page size. Only applies when page is set."
        ),
    ) -> MetricUnitListData:
        """List the metric units that can be assigned to a metric.

        These are the selectable units used when overriding a metric's display
        unit with create_metric_unit_mapping (pass a unit's `name`).
        """
        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        data = await self.client.get("/api/v1/metricUnits/list", params=params)
        return MetricUnitListData.from_api(data if isinstance(data, dict) else {})

    async def list_metric_unit_mappings(
        self,
        name: str | None = Field(
            None, description="Filter mappings by metric name (partial match)"
        ),
        is_default: bool | None = Field(
            None,
            description="True to return only platform-default mappings, False to return only user overrides",
        ),
        page: int | None = Field(
            None,
            ge=1,
            description="1-based page number. Omit to return every mapping in one response.",
        ),
        size: int | None = Field(
            None, ge=1, description="Page size. Only applies when page is set."
        ),
    ) -> MetricUnitMappingListData:
        """List the metric-to-unit mappings shown in metric information panels.

        Each mapping tells which unit a metric is displayed in. When a mapping's
        `metric_unit_mapping_user_id` is set it is a user override (reset it with
        delete_metric_unit_mapping); otherwise it is the platform default.
        """
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if is_default is not None:
            params["isDefault"] = is_default
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        data = await self.client.get("/api/v1/metric-unit-mappings/list", params=params)
        return MetricUnitMappingListData.from_api(data if isinstance(data, dict) else {})

    async def create_metric_unit_mapping(
        self,
        body: CreateMetricUnitMappingDto = Field(
            ...,
            description=(
                "CreateMetricUnitMappingDto body. Required: metricName, unit "
                "(a unit name from list_metric_units). Optional: description."
            ),
        ),
    ) -> MetricUnitMappingUserDetail:
        """Override the display unit of a metric for the current user.

        Creates a user metric-unit mapping so the metric is shown in the chosen
        unit instead of its default. Reset it later with delete_metric_unit_mapping.

        ## Requirements
        - Server must run with --allow-write
        - `unit` should be one of the names from list_metric_units.
        """
        data = await self.client.post(
            "/api/v1/metric-unit-mapping-users", json=body.model_dump(exclude_none=True)
        )
        return MetricUnitMappingUserDetail.from_api(data if isinstance(data, dict) else {})

    async def delete_metric_unit_mapping(
        self,
        mapping_user_id: str = Field(
            ...,
            description="User mapping ID to remove (the metric_unit_mapping_user_id / create result id)",
        ),
    ) -> str:
        """Reset a metric's display unit by removing the user override. IRREVERSIBLE.

        Deletes the user metric-unit mapping identified by *mapping_user_id*,
        restoring the metric to its platform-default unit.

        ## Requirements
        - Server must run with --allow-write
        - Use the `metric_unit_mapping_user_id` from list_metric_unit_mappings
          (or the id returned by create_metric_unit_mapping).
        """
        validate_id(mapping_user_id, "mapping_user_id")
        await self.client.delete(f"/api/v1/metric-unit-mapping-users/{mapping_user_id}")
        return f"Metric unit mapping {mapping_user_id} deleted (reset to default)."
