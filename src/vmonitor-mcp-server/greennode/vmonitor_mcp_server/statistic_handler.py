"""Statistics handler for the vMonitor MCP server.

The statistics endpoints return the actual time-series data behind a chart: for
a metric (optionally filtered by dimensions and grouped) they return the value
points over a time window. They are the read side that turns a metric chosen
from the catalogue (see ``metric_catalogue_handler``) into plottable data.

- ``get_statistics`` queries one metric's series via query parameters.
- ``get_statistics_synthetic`` returns the synthetic (aggregated) variant.
- ``get_statistics_v2`` runs a typed statistic query (``type`` + ``data``).
"""

from __future__ import annotations

import time
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import StatisticData, StatisticQueryDto
from greennode.vmonitor_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Any


class StatisticHandler:
    """Register and serve vMonitor statistics (metric time-series) MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="get_statistics", annotations=READ)(self.get_statistics)
        self.mcp.tool(name="get_statistics_synthetic", annotations=READ)(
            self.get_statistics_synthetic
        )
        self.mcp.tool(name="get_statistics_v2", annotations=READ)(self.get_statistics_v2)

    @staticmethod
    def _default_window(
        start_time: str | None, end_time: str | None
    ) -> tuple[str | None, str | None]:
        """Default a query window when start_time is omitted.

        The metric backend answers a missing start_time with an uncaught 500 (not
        a 4xx), so a caller that leaves the window blank gets an error instead of
        an empty result. Default the last hour so a bare
        ``get_statistics(name, statistics)`` call returns data.
        """
        if start_time is None:
            now = int(time.time() * 1000)
            start_time = str(now - 3600_000)
            if end_time is None:
                end_time = str(now)
        return start_time, end_time

    async def get_statistics(
        self,
        name: str | None = Field(None, description="Metric name to query (e.g. vServerCPUUsage)"),
        statistics: str | None = Field(
            None, description="Statistic function(s) to compute (e.g. avg, max, sum)"
        ),
        dimensions: str | None = Field(
            None,
            description="Dimension filter as comma-separated `key:value` pairs (colon, "
            "NOT '='), e.g. 'resource_id:ins-0001,product:vserver'",
        ),
        start_time: str | None = Field(
            None, description="Window start (epoch millis or ISO-8601)"
        ),
        end_time: str | None = Field(None, description="Window end (epoch millis or ISO-8601)"),
        group_by: str | None = Field(
            None, description="Dimension(s) to group the series by; 'none' for no grouping"
        ),
        period: str | None = Field(None, description="Aggregation period/resolution in seconds"),
        alarm: str | None = Field(None, description="Alarm ID to scope the query to"),
        limit: str | None = Field(None, description="Maximum number of series to return"),
    ) -> StatisticData:
        """Query a metric's time-series data via flat query params (the GET path).

        For most queries prefer `get_statistics_v2` (the POST SIMPLE path) — it is
        the primary way vMonitor itself queries metrics and takes a typed body that
        cannot be shaped wrong. Use this GET form for a quick single-metric read.
        Pick the metric with get_metric_names, discover its dimensions (including
        `resource_id`) with get_metric_dimensions, then read its points here.
        `dimensions` is `key:value` pairs joined by commas (e.g.
        `resource_id:ins-...,product:vserver`); `group_by` is `none` when not
        grouping. Returns one series per group with its statistic value points.
        """
        start_time, end_time = self._default_window(start_time, end_time)
        params: dict[str, Any] = {}
        for key, value in (
            ("name", name),
            ("statistics", statistics),
            ("dimensions", dimensions),
            ("start_time", start_time),
            ("end_time", end_time),
            ("group_by", group_by),
            ("period", period),
            ("alarm", alarm),
            ("limit", limit),
        ):
            if value is not None:
                params[key] = value
        data = await self.client.get("/api/v1/statistics", params=params)
        return StatisticData.from_api(data)

    async def get_statistics_synthetic(
        self,
        name: str | None = Field(None, description="Metric name to query (e.g. vServerCPUUsage)"),
        statistics: str | None = Field(
            None, description="Statistic function(s) to compute (e.g. avg, max, sum)"
        ),
        dimensions: str | None = Field(
            None,
            description="Dimension filter as comma-separated `key:value` pairs (colon, "
            "NOT '='), e.g. 'resource_id:ins-0001,product:vserver'",
        ),
        start_time: str | None = Field(
            None, description="Window start (epoch millis or ISO-8601)"
        ),
        end_time: str | None = Field(None, description="Window end (epoch millis or ISO-8601)"),
        group_by: str | None = Field(
            None, description="Dimension(s) to group the series by; 'none' for no grouping"
        ),
        period: str | None = Field(None, description="Aggregation period/resolution in seconds"),
        alarm: str | None = Field(None, description="Alarm ID to scope the query to"),
    ) -> StatisticData:
        """Query a metric's synthetic (single aggregated) statistic.

        The synthetic variant collapses the window into one aggregated value per
        series (used by number/single-stat widgets) rather than a full time
        series.
        """
        start_time, end_time = self._default_window(start_time, end_time)
        params: dict[str, Any] = {}
        for key, value in (
            ("name", name),
            ("statistics", statistics),
            ("dimensions", dimensions),
            ("start_time", start_time),
            ("end_time", end_time),
            ("group_by", group_by),
            ("period", period),
            ("alarm", alarm),
        ):
            if value is not None:
                params[key] = value
        data = await self.client.get("/api/v1/statistics/synthetics", params=params)
        return StatisticData.from_api(data)

    async def get_statistics_v2(
        self,
        body: StatisticQueryDto = Field(
            ...,
            description=(
                "StatisticQueryDto body. Required: type (query kind), data (the "
                "query parameters for that kind)."
            ),
        ),
    ) -> StatisticData:
        """Run a typed statistic query — the PRIMARY way to query metric data.

        This POST body-based endpoint is how vMonitor itself queries metrics (for
        both infrastructure and user charts); prefer it over the flat-param
        `get_statistics`. `type` is `SIMPLE` (fill `data.graph`) or `CUSTOM` (fill
        `data.expression` + `data.graphs`).

        A SIMPLE query for one instance's CPU looks like:
        `{"type": "SIMPLE", "data": {"graph": {"name": "vserver.cpu.utilization_norm_perc",
        "dimensions": "resource_id:ins-...,product:vserver", "statistics": "avg",
        "group_by": "none", "offset": 0, "limit": "", "rollup": "", "rate": 0},
        "start_time": <epoch_ms>, "end_time": <epoch_ms>, "period": 60,
        "alarm": false}}`.

        All fields are typed because the backend answers a malformed `data` with an
        uncaught **500**, not a 4xx: `statistics` must be a STRING (not a list),
        `dimensions` a comma-separated `key:value` STRING (not an object), and
        `start_time`/`end_time` epoch MILLIS (not ISO strings). Discover the metric
        with get_metric_names and its `resource_id`/dimensions with
        get_metric_dimensions. This is a read-only query even though it is a POST.

        Shortcut when the question is about a specific resource: `list_widgets` on
        that resource's default dashboard hands you `metric_name`, `statistic`,
        `dimensions` and `group_by` already filled in — no catalogue walk and no
        detailed monitoring required.
        """
        data = await self.client.post(
            "/api/v1/statistics", json=body.model_dump(exclude_none=True)
        )
        return StatisticData.from_api(data)
