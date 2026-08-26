"""Metric catalogue handler for the vMonitor MCP server (Query feature group).

The metric catalogue is the read chain for *querying* a metric: discover which
metrics exist, then how each can be scoped/grouped by its dimensions. It is the
entry point for building a widget, an alarm or a statistic query (paired with
``statistic_handler`` and ``log_search_handler`` under the Query group).

- ``get_metric_names`` lists the metric catalogue (every metric collected).
- ``list_metric_dimension_names`` lists every dimension key known across metrics.
- ``list_metric_dimension_values`` lists the values observed for one dimension.
- ``get_metric_dimensions`` lists the dimensions (and observed values) of a metric.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    MetricDimensionData,
    MetricDimensionNameListData,
    MetricDimensionValueListData,
    MetricNameListData,
)
from greennode.vmonitor_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Any


class MetricCatalogueHandler:
    """Register and serve vMonitor metric-catalogue (query discovery) MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="get_metric_names", annotations=READ)(self.get_metric_names)
        self.mcp.tool(name="list_metric_dimension_names", annotations=READ)(
            self.list_metric_dimension_names
        )
        self.mcp.tool(name="list_metric_dimension_values", annotations=READ)(
            self.list_metric_dimension_values
        )
        self.mcp.tool(name="get_metric_dimensions", annotations=READ)(self.get_metric_dimensions)

    async def get_metric_names(
        self,
        start_time: str | None = Field(
            None,
            description="Optional window start (epoch millis or ISO-8601) to scope the catalogue",
        ),
        end_time: str | None = Field(
            None,
            description="Optional window end (epoch millis or ISO-8601) to scope the catalogue",
        ),
    ) -> MetricNameListData:
        """List the names of every metric vMonitor is collecting.

        This is the metric catalogue — the entry point for building a widget or
        an alarm: pick a metric name here, then explore it with
        get_metric_dimensions. Prefer passing a time window (start_time/end_time,
        epoch millis) — on data-heavy accounts the unbounded call can fail (500),
        and a window also scopes the list to metrics that reported within it.
        """
        params: dict[str, Any] = {}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        data = await self.client.get("/api/v1/metrics/metric-name", params=params)
        return MetricNameListData.from_api(data)

    async def list_metric_dimension_names(self) -> MetricDimensionNameListData:
        """List every dimension name (key) known across metrics.

        Dimension names are the labels metrics are scoped/grouped by (host, cpu,
        instance, ...). Use list_metric_dimension_values to see the values a
        single dimension currently carries.
        """
        data = await self.client.get("/api/v1/metrics/dimensions-names")
        return MetricDimensionNameListData.from_api(data)

    async def list_metric_dimension_values(
        self,
        dimension_name: str = Field(
            ..., description="Dimension name to list values for (e.g. host, instance)"
        ),
        dimensions: str | None = Field(
            None, description="Optional other-dimension filter to scope the returned values"
        ),
        start_time: str | None = Field(
            None,
            description="Optional window start (epoch millis or ISO-8601) to sample values from",
        ),
        end_time: str | None = Field(
            None,
            description="Optional window end (epoch millis or ISO-8601) to sample values up to",
        ),
    ) -> MetricDimensionValueListData:
        """List the distinct values observed for a single dimension.

        Use this to populate a filter/group-by choice (e.g. the list of hosts for
        the `host` dimension) when composing a widget, alarm, or variable.
        """
        params: dict[str, Any] = {"dimension_name": dimension_name}
        if dimensions:
            params["dimensions"] = dimensions
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        data = await self.client.get("/api/v1/metrics/dimensions-values", params=params)
        return MetricDimensionValueListData.from_api(data, dimension_name)

    async def get_metric_dimensions(
        self,
        name: str = Field(
            ..., description="Metric name to list dimensions for (e.g. vServerCPUUsage)"
        ),
        dimensions: str | None = Field(
            None, description="Optional dimension filter to scope the returned values"
        ),
        start_time: str | None = Field(
            None,
            description="Optional window start (epoch millis or ISO-8601) to sample values from",
        ),
        end_time: str | None = Field(
            None,
            description="Optional window end (epoch millis or ISO-8601) to sample values up to",
        ),
    ) -> MetricDimensionData:
        """List the dimensions of a single metric and the values each currently carries.

        Dimensions are the labels that scope a metric's data points (server id,
        instance, mount point, ...). Use this to discover how a metric can be
        filtered or grouped before building a widget or an alarm on it.
        """
        params: dict[str, Any] = {"name": name}
        if dimensions:
            params["dimensions"] = dimensions
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        data = await self.client.get("/api/v1/metrics/dimensions", params=params)
        return MetricDimensionData.from_api(data, name)
