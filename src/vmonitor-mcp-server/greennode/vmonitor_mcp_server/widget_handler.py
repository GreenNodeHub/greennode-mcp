"""Dashboard widget handler for the vMonitor MCP server.

A widget is one chart on a dashboard: a chart type plus a set of queries (its
``graphs``) that produce the plotted series. These tools read, create, edit and
remove widgets.

- ``list_widgets`` lists a dashboard's widgets and the metric query each plots —
  the shortcut that turns a resource's default dashboard into ready-to-run
  metric queries without enabling detailed monitoring.
- ``get_widget`` reads one widget.
- ``create_widget`` adds a widget (the current v2 create shape).
- ``update_widget`` / ``update_widget_v2`` edit a widget's content (v1 uses
  metricGraphs/logGraphs arrays; v2 uses the graphs map).
- ``update_widget_layout`` moves/resizes a widget and adjusts its time window.
- ``delete_widget`` removes a widget (irreversible).

The chart content (``graphs`` / metricGraphs / logGraphs) is a deeply-nested,
polymorphic chart-builder payload the API types opaquely: widget-level fields are
typed, the graph payloads pass through as-is.
"""

from __future__ import annotations

import re
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateWidgetDto,
    UpdateWidgetDto,
    UpdateWidgetLayoutDto,
    UpdateWidgetV2Dto,
    WidgetDetail,
    WidgetListData,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


_GRID_COLS = 10
_LAYOUT_RE = re.compile(r"cols:(\d+),\s*rows:(\d+),\s*x:(\d+),\s*y:(\d+)")


def _parse_layout(spec: str | None) -> tuple[int, int, int, int] | None:
    """Parse a ``"cols:C, rows:R, x:X, y:Y"`` layout string into (x, y, cols, rows)."""
    match = _LAYOUT_RE.search(spec or "")
    if not match:
        return None
    cols, rows, x, y = (int(g) for g in match.groups())
    return x, y, cols, rows


def _next_grid_slot(existing: list[str], cols: int, rows: int) -> str:
    """First-fit a ``cols``x``rows`` widget into the 10-column grid.

    Walks the grid top-to-bottom, left-to-right and returns the first slot whose
    cells are all free of the ``existing`` widgets' footprints — so a new widget
    never overlaps one already on the dashboard (empty dashboard → ``x:0, y:0``;
    default half-width widgets pack two per row).
    """
    occupied: set[tuple[int, int]] = set()
    for spec in existing:
        parsed = _parse_layout(spec)
        if not parsed:
            continue
        x, y, c, r = parsed
        for dy in range(r):
            for dx in range(c):
                occupied.add((x + dx, y + dy))
    for y in range(1000):
        for x in range(0, _GRID_COLS - cols + 1):
            block = [(x + dx, y + dy) for dy in range(rows) for dx in range(cols)]
            if not any(cell in occupied for cell in block):
                return f"cols:{cols}, rows:{rows}, x:{x}, y:{y}"
    return f"cols:{cols}, rows:{rows}, x:0, y:0"


class WidgetHandler:
    """Register and serve vMonitor dashboard-widget MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_widgets", annotations=READ)(self.list_widgets)
        self.mcp.tool(name="get_widget", annotations=READ)(self.get_widget)

        if self.allow_write:
            self.mcp.tool(name="create_widget", annotations=WRITE)(self.create_widget)
            self.mcp.tool(name="update_widget", annotations=WRITE)(self.update_widget)
            self.mcp.tool(name="update_widget_v2", annotations=WRITE)(self.update_widget_v2)
            self.mcp.tool(name="update_widget_layout", annotations=WRITE)(
                self.update_widget_layout
            )
            self.mcp.tool(name="delete_widget", annotations=DESTRUCTIVE)(self.delete_widget)

    async def list_widgets(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID whose widgets to list"),
    ) -> WidgetListData:
        """List a dashboard's widgets together with the metric query behind each one.

        This is the cheap path to "show me this resource's metrics". Every
        GreenNode resource owns an auto-generated **default (system) dashboard**
        whose widgets already store the exact query the console plots: metric
        name, statistic, grouping and the full `dimensions` string (which already
        carries the `resource_id`). Reading them here means you do **not** need
        detailed monitoring enabled, and you do not have to walk
        get_metric_names / get_metric_dimensions and guess a filter.

        Flow: find the dashboard (`list_dashboards searching_text=<resource
        name>` or `get_dashboard_by_name`) → `list_widgets` → for each
        `metric_queries` entry call `get_statistics_v2` with
        `{"type": "SIMPLE", "data": {"graph": {"name": <metric_name>,
        "statistics": <statistic>, "dimensions": <dimensions>,
        "group_by": <group_by>, "offset": 0, "limit": "", "rollup": "",
        "rate": 0}, "start_time": <epoch_ms>, "end_time": <epoch_ms>,
        "period": <the widget's period>, "alarm": false}}`.

        Each widget's `period` is the resolution the console uses for that chart
        — reuse it so your numbers match what the dashboard shows. Widgets with
        `log_graph_count > 0` plot log data instead; query those with search_logs.
        """
        validate_id(dashboard_id, "dashboard_id")
        data = await self.client.get(f"/api/v1/dashboards/{dashboard_id}")
        return WidgetListData.from_api(data)

    async def get_widget(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the widget belongs to"),
        widget_id: str = Field(..., description="Widget ID to retrieve"),
    ) -> WidgetDetail:
        """Get a single dashboard widget by ID (its chart config and graph specs)."""
        validate_id(dashboard_id, "dashboard_id")
        validate_id(widget_id, "widget_id")
        data = await self.client.get(f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}")
        return WidgetDetail.from_api(data)

    async def _existing_layouts(self, dashboard_id: str) -> list[str]:
        """Read the layout strings of the widgets already on a dashboard.

        Used to place a new widget without overlapping; failures degrade to an
        empty list (the new widget then lands at the top-left slot).
        """
        try:
            detail = await self.client.get(f"/api/v1/dashboards/{dashboard_id}")
        except Exception:
            return []
        widgets = detail.get("widgets") if isinstance(detail, dict) else None
        return [w.get("layout", "") for w in (widgets or []) if isinstance(w, dict)]

    async def create_widget(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID to add the widget to"),
        body: CreateWidgetDto = Field(
            ...,
            description=(
                "CreateWidgetDto body. Required: name, typeChart, graphs (query map "
                "key->{type,data}). Optional widget-level fields (layout, yAxis*, "
                "period, ...). Omit `layout` to auto-place the widget on the grid."
            ),
        ),
    ) -> WidgetDetail:
        """Add a widget (chart) to a dashboard.

        The chart content is the `graphs` map: one entry per query (keyed a, b,
        ...), each `{type, data}`. For a metric chart, `type` is `METRIC_GRAPH`
        and `data` is `{name (metric), statistic (avg/max/...), alias, groupBy,
        color (#hex), filter, enabled: true}`.

        Placement: `layout` is a grid string `"cols:C, rows:R, x:X, y:Y"` on a
        **10-column** grid. **Omit it and the widget is auto-placed** in the first
        free slot (default `cols:5, rows:2`, packed two per row) so it never
        overlaps existing widgets — a bare create then renders cleanly on the web.
        `position` (legend) and `fixedTimeRange` default to the native look.

        ## Requirements
        - Server must run with --allow-write
        - `name` (5-255 chars), `typeChart` and at least one `graphs` entry are
          required; `type` is the string `"Metric"`.
        """
        validate_id(dashboard_id, "dashboard_id")
        payload = body.model_dump(exclude_none=True)
        if not payload.get("layout"):
            cols, rows = (
                (3, 2) if (body.typeChart or "").upper() in {"NUMBER", "GAUGE"} else (5, 2)
            )
            payload["layout"] = _next_grid_slot(
                await self._existing_layouts(dashboard_id), cols, rows
            )
        payload.setdefault("position", "BOTTOM")
        payload.setdefault("fixedTimeRange", "global")
        data = await self.client.post(
            f"/api/v1/dashboards/{dashboard_id}/widgets/v2",
            json=payload,
        )
        return WidgetDetail.from_api(data)

    async def update_widget(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the widget belongs to"),
        widget_id: str = Field(..., description="Widget ID to update"),
        body: UpdateWidgetDto = Field(
            ...,
            description=(
                "UpdateWidgetDto body (v1 shape). Chart content is passed as "
                "metricGraphs / logGraphs arrays; all fields optional."
            ),
        ),
    ) -> str:
        """Edit a widget using the v1 update shape (metricGraphs / logGraphs arrays).

        Legacy path that can answer 500 on some accounts — **prefer
        update_widget_v2** for all edits. Kept for widgets whose stored content is
        metricGraphs / logGraphs arrays. Like v2 it is a
        full-object replace (send `name`, `typeChart`, `type` and the graph
        arrays), and each metricGraph item uses `statistic` (singular), `name`,
        `alias`, `enabled`, ...

        ## Requirements
        - Server must run with --allow-write
        - Prefer update_widget_v2; if used, send the full widget shape (read it
          first with get_widget).
        """
        validate_id(dashboard_id, "dashboard_id")
        validate_id(widget_id, "widget_id")
        await self.client.put(
            f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}",
            json=body.model_dump(exclude_none=True),
        )
        return f"Widget {widget_id} updated on dashboard {dashboard_id}."

    async def update_widget_v2(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the widget belongs to"),
        widget_id: str = Field(..., description="Widget ID to update"),
        body: UpdateWidgetV2Dto = Field(
            ...,
            description=(
                "UpdateWidgetV2Dto body. Same typed-shell + graphs-map shape as "
                "create_widget; all fields optional (partial edit)."
            ),
        ),
    ) -> str:
        """Edit a widget using the v2 update shape (the graphs map).

        This is the current, recommended edit path. It is a
        **full-object replace, not a partial patch**: a thin body is rejected with
        `Missing field widget type id`. Read the widget first with get_widget, then
        resend the complete shape with your edits — at minimum `name`, `typeChart`,
        `type` (the string `"Metric"`) and the `graphs` map.

        ## Requirements
        - Server must run with --allow-write
        - Send the full widget shape (read-modify-write), not just the changed field.
        """
        validate_id(dashboard_id, "dashboard_id")
        validate_id(widget_id, "widget_id")
        await self.client.put(
            f"/api/v1/dashboards/{dashboard_id}/widgets/v2/{widget_id}",
            json=body.model_dump(exclude_none=True),
        )
        return f"Widget {widget_id} updated on dashboard {dashboard_id}."

    async def update_widget_layout(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the widget belongs to"),
        widget_id: str = Field(..., description="Widget ID whose layout to update"),
        body: UpdateWidgetLayoutDto = Field(
            ...,
            description=(
                "UpdateWidgetLayoutDto body. Optional: layout (grid position/size), "
                "period, startTime, endTime, extra."
            ),
        ),
    ) -> str:
        """Move/resize a widget on the dashboard grid and adjust its time window.

        This only changes placement and the widget's time range, not its chart
        content or queries.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(dashboard_id, "dashboard_id")
        validate_id(widget_id, "widget_id")
        await self.client.put(
            f"/api/v1/dashboards/{dashboard_id}/widgets/layout/{widget_id}",
            json=body.model_dump(exclude_none=True),
        )
        return f"Widget {widget_id} layout updated on dashboard {dashboard_id}."

    async def delete_widget(
        self,
        dashboard_id: str = Field(..., description="Dashboard ID the widget belongs to"),
        widget_id: str = Field(..., description="Widget ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a widget from a dashboard. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_widget first; deletion cannot be undone.
        """
        validate_id(dashboard_id, "dashboard_id")
        validate_id(widget_id, "widget_id")
        await self.client.delete(f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}")
        return f"Widget {widget_id} deleted from dashboard {dashboard_id}."
