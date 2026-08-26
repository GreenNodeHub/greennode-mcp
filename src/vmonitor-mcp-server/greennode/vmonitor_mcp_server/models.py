"""Pydantic BaseModel classes for vMonitor MCP server responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Literal


_CONDITION_ALIASES = {
    ">": "gt",
    ">=": "gte",
    "≥": "gte",
    "<": "lt",
    "<=": "lte",
    "≤": "lte",
}


def _norm_severity(value: Any) -> Any:
    """Normalise an alarm severity to the UPPER-CASE wire form (LOW/MEDIUM/...)."""
    if isinstance(value, str):
        return value.strip().upper()
    return value


def _norm_condition(value: Any) -> Any:
    """Normalise a comparison operator to the lower-case wire form (gt/gte/lt/lte).

    Accepts case variants (``GT``) and the symbol forms (``>``, ``>=``, ``<``,
    ``<=``) an agent naturally reaches for.
    """
    if isinstance(value, str):
        token = value.strip()
        token = _CONDITION_ALIASES.get(token, token)
        return token.lower()
    return value


def _unwrap(data: Any) -> dict:
    """Return the dashboard payload from either a bare dict or a ``{data: ...}`` envelope.

    Some vMonitor endpoints answer with the DashboardDto directly (create,
    rename, favorite) and others wrap it in a ``ResponseResult`` envelope
    (clone) — this normalises both to the inner dict.
    """
    if not isinstance(data, dict):
        return {}
    inner = data.get("data")
    return inner if isinstance(inner, dict) else data


class DashboardSummary(BaseModel):
    """Summary of a vMonitor dashboard, used in list responses."""

    id: str = Field(..., description="Dashboard ID")
    name: str = Field("", description="Dashboard name")
    favorite: bool = Field(False, description="Whether the dashboard is marked as favorite")
    system: bool = Field(False, description="Whether this is a system (built-in) dashboard")
    dark_mode: bool = Field(False, description="Whether dark mode is enabled")
    time_range: str = Field("", description="Configured time range")
    time_range_type: str = Field("", description="Time range type")
    refresh_active: bool = Field(False, description="Whether auto-refresh is active")
    refresh_interval: int = Field(0, description="Auto-refresh interval in seconds")
    created_user: int | str = Field("", description="ID of the user who created the dashboard")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> DashboardSummary:
        """Build a DashboardSummary from a raw vMonitor API dashboard dict."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            favorite=bool(data.get("favorite", False)),
            system=bool(data.get("system", False)),
            dark_mode=bool(data.get("darkMode", False)),
            time_range=str(data.get("timeRange", "")),
            time_range_type=str(data.get("timeRangeType", "")),
            refresh_active=bool(data.get("refreshActive", False)),
            refresh_interval=data.get("refreshInterval", 0) or 0,
            created_user=data.get("createdUser", ""),
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
        )


class DashboardListData(BaseModel):
    """Structured output for list_dashboards (paging envelope + items)."""

    page: int = Field(
        0, description="Current page (1-based; 0 when all items returned in one page)"
    )
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of dashboards matching the query")
    total_page: int = Field(0, description="Total number of pages")
    items: list[DashboardSummary] = Field(default_factory=list, description="Dashboards")

    @classmethod
    def from_api(cls, data: dict) -> DashboardListData:
        """Build a DashboardListData from the raw vMonitor paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=data.get("page", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_item=data.get("totalItem", 0) or 0,
            total_page=data.get("totalPage", 0) or 0,
            items=[DashboardSummary.from_api(i) for i in items],
        )


class DashboardDetail(BaseModel):
    """A single vMonitor dashboard, as returned by get/create/rename/favorite/clone."""

    id: str = Field(..., description="Dashboard ID")
    name: str = Field("", description="Dashboard name")
    favorite: bool = Field(False, description="Whether the dashboard is marked as favorite")
    system: bool = Field(False, description="Whether this is a system (built-in) dashboard")
    dark_mode: bool = Field(False, description="Whether dark mode is enabled")
    time_range: str = Field("", description="Configured time range (raw JSON-encoded string)")
    time_range_type: str = Field("", description="Time range type")
    refresh_active: bool = Field(False, description="Whether auto-refresh is active")
    refresh_interval: int = Field(0, description="Auto-refresh interval in seconds")
    view_selected_id: str = Field("", description="ID of the currently selected view")
    widget_count: int = Field(0, description="Number of widgets on the dashboard")
    created_user: int | str = Field("", description="ID of the user who created the dashboard")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> DashboardDetail:
        """Build a DashboardDetail from a raw vMonitor dashboard payload (any envelope)."""
        payload = _unwrap(data)
        widgets = payload.get("widgets") or []
        return cls(
            id=payload.get("id", ""),
            name=payload.get("name", ""),
            favorite=bool(payload.get("favorite", False)),
            system=bool(payload.get("system", False)),
            dark_mode=bool(payload.get("darkMode", False)),
            time_range=str(payload.get("timeRange", "")),
            time_range_type=str(payload.get("timeRangeType", "")),
            refresh_active=bool(payload.get("refreshActive", False)),
            refresh_interval=payload.get("refreshInterval", 0) or 0,
            view_selected_id=str(payload.get("viewSelectedId", "") or ""),
            widget_count=len(widgets) if isinstance(widgets, list) else 0,
            created_user=payload.get("createdUser", ""),
            created_at=payload.get("createdAt", ""),
            updated_at=payload.get("updatedAt", ""),
        )


class CreateDashboardDto(BaseModel):
    """Request body for create_dashboard (``POST /api/v1/dashboards``).

    Only ``name`` is required; the remaining fields configure the dashboard's
    default time range and are optional (omit them to use the platform default
    range). Unknown fields are rejected (``extra="forbid"``) so typos surface
    immediately.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Dashboard name")
    period: int | None = Field(
        None, description="Relative time-range length for the default view; omit for the default"
    )
    startTime: str | None = Field(
        None,
        description="Absolute time-range start (ISO-8601 date-time); pair with endTime, omit for a relative range",
    )
    endTime: str | None = Field(
        None,
        description="Absolute time-range end (ISO-8601 date-time); pair with startTime, omit for a relative range",
    )
    extra: dict[str, Any] | None = Field(
        None, description="Optional provider-specific extra configuration for the dashboard"
    )


class UpdateDashboardDto(BaseModel):
    """Request body for update_dashboard (``PUT /api/v1/dashboards``).

    The general dashboard-settings editor (distinct from the dedicated rename /
    favorite endpoints): ``id`` is required, every other field is an optional
    setting to change. Unknown fields are rejected (``extra="forbid"``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="ID of the dashboard to update")
    name: str | None = Field(None, description="New dashboard name")
    darkMode: bool | None = Field(None, description="Enable/disable dark mode")
    favorite: bool | None = Field(None, description="Mark/unmark as favorite")
    refreshActive: bool | None = Field(None, description="Enable/disable auto-refresh")
    refreshInterval: int | None = Field(None, description="Auto-refresh interval in seconds")
    timeRange: str | None = Field(None, description="Serialized time range (JSON string)")
    timeRangeType: str | None = Field(None, description="Time range type")
    viewSelectedId: str | None = Field(None, description="ID of the view to select by default")


def _pick_resource_id(data: dict) -> str:
    """Return the underlying product resource ID of a host (e.g. server_id, vas_id).

    Host payloads name this field ``<product>_id`` (server_id, load_balancer_id,
    database_id, ...). ``user_id`` is the owner, not the resource, so it is skipped.
    """
    for key, value in data.items():
        if key.endswith("_id") and key != "user_id":
            return str(value or "")
    return ""


def _pick_resource_name(data: dict) -> str:
    """Return the display name of a host from its ``name`` or ``<product>_name`` field."""
    if data.get("name"):
        return str(data["name"])
    for key, value in data.items():
        if key.endswith("_name"):
            return str(value or "")
    return ""


class HostSummary(BaseModel):
    """One monitored infrastructure host, normalised across every product type.

    A host is a resource that pushes metrics to vMonitor (a server running the
    Metric Agent, or a GreenNode product resource such as a vServer / vLB /
    vDB / vStorage). Each host owns an auto-generated default dashboard named
    after it.
    """

    id: str = Field(
        ..., description="Host ID (use this to fetch the host or its default dashboard)"
    )
    kind: str = Field(
        "", description="Infrastructure type: host, vserver, vlb, vdb, vstorage, ..."
    )
    name: str = Field("", description="Host display name (the product resource name)")
    resource_id: str = Field("", description="Underlying product resource ID, when applicable")
    os: str = Field("", description="Operating system (agent-based hosts only)")
    enabled: bool = Field(False, description="Whether the agent-based host is enabled")
    monitor_enabled: bool = Field(False, description="Whether monitoring is enabled for this host")
    blocked: bool = Field(False, description="Whether the host is blocked")
    user_id: int | str = Field("", description="ID of the owning user")
    plugin_count: int = Field(0, description="Number of enabled agent plugins (agent-based hosts)")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")
    deleted_at: str = Field("", description="Deletion timestamp, if the host was removed")

    @classmethod
    def from_api(cls, data: dict, kind: str) -> HostSummary:
        """Build a HostSummary from a raw vMonitor host dict of the given *kind*."""
        plugins = data.get("plugins") or []
        return cls(
            id=str(data.get("id", "")),
            kind=kind,
            name=_pick_resource_name(data),
            resource_id=_pick_resource_id(data),
            os=str(data.get("os", "") or ""),
            enabled=bool(data.get("enabled", False)),
            monitor_enabled=bool(data.get("monitor_enabled", False)),
            blocked=bool(data.get("blocked", False)),
            user_id=data.get("user_id", ""),
            plugin_count=len(plugins) if isinstance(plugins, list) else 0,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            deleted_at=data.get("deleted_at", "") or "",
        )


class HostListData(BaseModel):
    """Structured output for the infrastructure host-listing tools (paging envelope + items)."""

    kind: str = Field("", description="Infrastructure type these hosts belong to")
    page: int = Field(0, description="Current page (1-based)")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of hosts matching the query")
    total_page: int = Field(0, description="Total number of pages")
    items: list[HostSummary] = Field(default_factory=list, description="Hosts on this page")

    @classmethod
    def from_api(cls, data: dict, kind: str) -> HostListData:
        """Build a HostListData from the raw vMonitor paging envelope for *kind*."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            kind=kind,
            page=data.get("page", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_item=data.get("totalItem", 0) or 0,
            total_page=data.get("totalPage", 0) or 0,
            items=[HostSummary.from_api(i, kind) for i in items],
        )


class HostDetail(BaseModel):
    """A single agent-based infrastructure host, as returned by get/enable/disable."""

    id: str = Field(..., description="Host ID")
    name: str = Field("", description="Host name (the hostname reported by the Metric Agent)")
    os: str = Field("", description="Operating system reported by the agent")
    enabled: bool = Field(False, description="Whether monitoring is enabled for this host")
    plugin_count: int = Field(0, description="Number of enabled agent plugins")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> HostDetail:
        """Build a HostDetail from a raw vMonitor HostDetailResponse dict."""
        if not isinstance(data, dict):
            data = {}
        plugins = data.get("plugins") or []
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "") or ""),
            os=str(data.get("os", "") or ""),
            enabled=bool(data.get("enabled", False)),
            plugin_count=len(plugins) if isinstance(plugins, list) else 0,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


class MetricSample(BaseModel):
    """A single point-in-time metric value for a host."""

    name: str = Field("", description="Metric name")
    value: str = Field("", description="Metric value (as reported by the API)")
    created_at: str = Field("", description="Timestamp of the sample")

    @classmethod
    def from_api(cls, data: Any) -> MetricSample | None:
        """Build a MetricSample from a raw HostBasicPattern dict, or None when absent."""
        if not isinstance(data, dict):
            return None
        return cls(
            name=str(data.get("name", "") or ""),
            value=str(data.get("value", "") or ""),
            created_at=data.get("createdAt") or "",
        )


class HostStatusInfo(BaseModel):
    """The reported status of a host."""

    name: str = Field("", description="Status metric name")
    status: str = Field("", description="Status label (e.g. UP / DOWN)")
    value: str = Field("", description="Raw status value")
    created_at: str = Field("", description="Timestamp of the status sample")

    @classmethod
    def from_api(cls, data: Any) -> HostStatusInfo | None:
        """Build a HostStatusInfo from a raw HostStatus dict, or None when absent."""
        if not isinstance(data, dict):
            return None
        return cls(
            name=str(data.get("name", "") or ""),
            status=str(data.get("status", "") or ""),
            value=str(data.get("value", "") or ""),
            created_at=data.get("createdAt") or "",
        )


class HostMetricInfo(BaseModel):
    """Current metric snapshot for a host (get_host_metrics / getMoreInfoHostById)."""

    status: HostStatusInfo | None = Field(None, description="Host status")
    cpu: MetricSample | None = Field(None, description="CPU count / info sample")
    cpu_load: MetricSample | None = Field(None, description="CPU load sample")
    cpu_usage: MetricSample | None = Field(None, description="CPU usage sample")
    iowait: MetricSample | None = Field(None, description="I/O wait sample")
    load: MetricSample | None = Field(None, description="System load sample")
    mem_avail: MetricSample | None = Field(None, description="Available memory sample")

    @classmethod
    def from_api(cls, data: dict) -> HostMetricInfo:
        """Build a HostMetricInfo from a raw HostDetailInfoResponse dict."""
        if not isinstance(data, dict):
            data = {}
        return cls(
            status=HostStatusInfo.from_api(data.get("status")),
            cpu=MetricSample.from_api(data.get("cpu")),
            cpu_load=MetricSample.from_api(data.get("cpuLoad")),
            cpu_usage=MetricSample.from_api(data.get("cpuUsage")),
            iowait=MetricSample.from_api(data.get("iowait")),
            load=MetricSample.from_api(data.get("load")),
            mem_avail=MetricSample.from_api(data.get("memAvail")),
        )


class HostMetricSnapshot(BaseModel):
    """Current metric snapshot for a product host (vServer / vLB / vDB / ...).

    Each product type exposes its own set of metric keys (e.g. vServerCPUUsage,
    vLBActiveConnection, backupQuotaUsed), so the samples are kept in a generic
    ``metrics`` map keyed by the API's metric name; ``status`` is common to all.
    A metric with no current data point is ``null``.
    """

    kind: str = Field("", description="Infrastructure type this snapshot belongs to")
    status: HostStatusInfo | None = Field(None, description="Host status")
    metrics: dict[str, MetricSample | None] = Field(
        default_factory=dict, description="Metric samples keyed by metric name"
    )

    @classmethod
    def from_api(cls, data: dict, kind: str) -> HostMetricSnapshot:
        """Build a HostMetricSnapshot from a raw per-product metric dict."""
        if not isinstance(data, dict):
            data = {}
        metrics = {
            key: MetricSample.from_api(value) for key, value in data.items() if key != "status"
        }
        return cls(
            kind=kind,
            status=HostStatusInfo.from_api(data.get("status")),
            metrics=metrics,
        )


class MetricDimension(BaseModel):
    """One dimension of a metric plus the distinct values observed for it.

    A metric (e.g. vServerCPUUsage) is emitted with a set of dimensions that
    scope each data point (server id, instance, mount point, ...). This is one
    such dimension key together with the values that metric currently carries.
    """

    key: str = Field("", description="Dimension name (e.g. server_id, instance)")
    values: list[str] = Field(
        default_factory=list, description="Distinct values observed for this dimension"
    )

    @classmethod
    def from_api(cls, data: Any) -> MetricDimension:
        """Build a MetricDimension from a raw GetMetricFiltersResponse dict."""
        if not isinstance(data, dict):
            return cls()
        values = data.get("value") or []
        return cls(
            key=str(data.get("key", "") or ""),
            values=[str(v) for v in values] if isinstance(values, list) else [],
        )


class MetricDimensionData(BaseModel):
    """Structured output for get_metric_dimensions (the metric plus its dimensions)."""

    metric_name: str = Field("", description="Metric these dimensions belong to")
    items: list[MetricDimension] = Field(
        default_factory=list, description="Dimensions of the metric"
    )

    @classmethod
    def from_api(cls, data: Any, metric_name: str) -> MetricDimensionData:
        """Build a MetricDimensionData from the raw array of dimension filters."""
        items = data if isinstance(data, list) else []
        return cls(
            metric_name=metric_name,
            items=[MetricDimension.from_api(i) for i in items],
        )


class MetricUnitSummary(BaseModel):
    """One selectable metric unit, used when overriding a metric's display unit."""

    id: int | str = Field("", description="Metric unit ID")
    name: str = Field("", description="Unit name (e.g. Bytes, Percent, Count)")
    new_unit: str = Field("", description="Converted/display unit applied above the threshold")
    threshold: int | str = Field("", description="Value threshold at which the unit converts")

    @classmethod
    def from_api(cls, data: dict) -> MetricUnitSummary:
        """Build a MetricUnitSummary from a raw MetricUnitDto dict."""
        return cls(
            id=data.get("id", ""),
            name=str(data.get("name", "") or ""),
            new_unit=str(data.get("newUnit", "") or ""),
            threshold=data.get("threshold", ""),
        )


class MetricUnitListData(BaseModel):
    """Structured output for list_metric_units (paging envelope + items)."""

    page: int = Field(0, description="Current page (1-based; 0 when all items returned at once)")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of metric units")
    total_page: int = Field(0, description="Total number of pages")
    items: list[MetricUnitSummary] = Field(default_factory=list, description="Metric units")

    @classmethod
    def from_api(cls, data: dict) -> MetricUnitListData:
        """Build a MetricUnitListData from the raw vMonitor paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=data.get("page", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_item=data.get("totalItem", 0) or 0,
            total_page=data.get("totalPage", 0) or 0,
            items=[MetricUnitSummary.from_api(i) for i in items],
        )


class MetricUnitMappingSummary(BaseModel):
    """One metric-to-unit mapping shown in a metric's information panel.

    ``metric_unit_mapping_user_id`` is set only when the current user has
    overridden the default unit for this metric; when empty the mapping is the
    platform default. Pass that id to delete_metric_unit_mapping to reset the
    override back to the default.
    """

    id: str = Field("", description="Metric unit mapping ID")
    metric_name: str = Field("", description="Metric this unit applies to")
    unit: str = Field("", description="Unit currently mapped to the metric")
    description: str = Field("", description="Mapping description")
    metric_unit_mapping_user_id: str = Field(
        "", description="User override ID (empty when this is the platform default)"
    )

    @classmethod
    def from_api(cls, data: dict) -> MetricUnitMappingSummary:
        """Build a MetricUnitMappingSummary from a raw MetricUnitMappingDto dict."""
        return cls(
            id=str(data.get("id", "") or ""),
            metric_name=str(data.get("metricName", "") or ""),
            unit=str(data.get("unit", "") or ""),
            description=str(data.get("description", "") or ""),
            metric_unit_mapping_user_id=str(data.get("metricUnitMappingUserId", "") or ""),
        )


class MetricUnitMappingListData(BaseModel):
    """Structured output for list_metric_unit_mappings (paging envelope + items)."""

    page: int = Field(0, description="Current page (1-based; 0 when all items returned at once)")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of metric unit mappings")
    total_page: int = Field(0, description="Total number of pages")
    items: list[MetricUnitMappingSummary] = Field(
        default_factory=list, description="Metric unit mappings"
    )

    @classmethod
    def from_api(cls, data: dict) -> MetricUnitMappingListData:
        """Build a MetricUnitMappingListData from the raw vMonitor paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=data.get("page", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_item=data.get("totalItem", 0) or 0,
            total_page=data.get("totalPage", 0) or 0,
            items=[MetricUnitMappingSummary.from_api(i) for i in items],
        )


class MetricUnitMappingUserDetail(BaseModel):
    """A user-defined metric-to-unit override, returned by create_metric_unit_mapping."""

    id: str = Field(
        "", description="User mapping ID (pass to delete_metric_unit_mapping to reset)"
    )
    metric_name: str = Field("", description="Metric the override applies to")
    unit: str = Field("", description="Unit the metric is now displayed in")
    description: str = Field("", description="Mapping description")

    @classmethod
    def from_api(cls, data: dict) -> MetricUnitMappingUserDetail:
        """Build a MetricUnitMappingUserDetail from a raw MetricUnitMappingUserDto dict."""
        if not isinstance(data, dict):
            data = {}
        return cls(
            id=str(data.get("id", "") or ""),
            metric_name=str(data.get("metricName", "") or ""),
            unit=str(data.get("unit", "") or ""),
            description=str(data.get("description", "") or ""),
        )


class CreateMetricUnitMappingDto(BaseModel):
    """Request body for create_metric_unit_mapping (``POST /api/v1/metric-unit-mapping-users``).

    Overrides the display unit of a metric for the current user. ``metricName``
    and ``unit`` are required; ``description`` is optional. Unknown fields are
    rejected (``extra="forbid"``) so typos surface immediately.
    """

    model_config = ConfigDict(extra="forbid")

    metricName: str = Field(..., description="Metric name whose display unit to override")
    unit: str = Field(
        ..., description="Unit to display the metric in (from list_metric_units names)"
    )
    description: str | None = Field(None, description="Optional description for the override")


def _str_field_list(data: Any, key: str) -> list[str]:
    """Flatten a raw API array of ``{key: value}`` objects into a list of strings.

    The metric-catalogue endpoints wrap each entry in a single-field object
    (``{"name": ...}``, ``{"dimension_name": ...}``, ``{"dimension_value": ...}``)
    rather than returning bare strings. Bare strings are tolerated defensively.
    """
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, dict):
            value = item.get(key)
        else:
            value = item
        if value is not None:
            out.append(str(value))
    return out


class MetricNameListData(BaseModel):
    """Structured output for get_metric_names (the catalogue of metric names)."""

    count: int = Field(0, description="Number of metric names returned")
    items: list[str] = Field(default_factory=list, description="Metric names")

    @classmethod
    def from_api(cls, data: Any) -> MetricNameListData:
        """Build a MetricNameListData from the raw array of ``{name}`` objects."""
        items = _str_field_list(data, "name")
        return cls(count=len(items), items=items)


class MetricDimensionNameListData(BaseModel):
    """Structured output for list_metric_dimension_names (all dimension keys)."""

    count: int = Field(0, description="Number of dimension names returned")
    items: list[str] = Field(default_factory=list, description="Dimension names (keys)")

    @classmethod
    def from_api(cls, data: Any) -> MetricDimensionNameListData:
        """Build from the raw array of ``{dimension_name}`` objects."""
        items = _str_field_list(data, "dimension_name")
        return cls(count=len(items), items=items)


class MetricDimensionValueListData(BaseModel):
    """Structured output for list_metric_dimension_values (values of one dimension)."""

    dimension_name: str = Field("", description="Dimension the values belong to")
    count: int = Field(0, description="Number of values returned")
    items: list[str] = Field(default_factory=list, description="Observed dimension values")

    @classmethod
    def from_api(cls, data: Any, dimension_name: str) -> MetricDimensionValueListData:
        """Build from the raw array of ``{dimension_value}`` objects."""
        items = _str_field_list(data, "dimension_value")
        return cls(dimension_name=dimension_name, count=len(items), items=items)


class StatisticData(BaseModel):
    """Structured output for the statistics tools (a metric's queried data series).

    The statistics endpoints return an array of series objects whose exact shape
    varies (dimensions, statistics point pairs, group-by keys). To avoid dropping
    fields the raw series dicts are kept generically in ``series``.
    """

    count: int = Field(0, description="Number of series returned")
    series: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw statistic series objects as returned by the API"
    )

    @classmethod
    def from_api(cls, data: Any) -> StatisticData:
        """Build a StatisticData from the raw statistics array."""
        items = [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []
        return cls(count=len(items), series=items)


class VariableSummary(BaseModel):
    """One dashboard variable (a reusable, dashboard-scoped value used in queries)."""

    id: str = Field("", description="Variable ID")
    key: str = Field("", description="Variable key referenced in queries (e.g. $host)")
    name: str = Field("", description="Display name")
    current_value: str = Field("", description="Currently selected value")
    default_value: str = Field("", description="Default value")
    values: list[str] = Field(default_factory=list, description="Selectable values")
    is_dynamic: bool = Field(False, description="Whether values are resolved dynamically")
    dashboard_id: str = Field("", description="Dashboard this variable belongs to")

    @classmethod
    def from_api(cls, data: Any) -> VariableSummary:
        """Build a VariableSummary from a raw VariableDto dict."""
        if not isinstance(data, dict):
            return cls()
        values = data.get("values") or []
        return cls(
            id=str(data.get("id", "") or ""),
            key=str(data.get("key", "") or ""),
            name=str(data.get("name", "") or ""),
            current_value=str(data.get("currentValue", "") or ""),
            default_value=str(data.get("defaultValue", "") or ""),
            values=[str(v) for v in values] if isinstance(values, list) else [],
            is_dynamic=bool(data.get("isDynamic", False)),
            dashboard_id=str(data.get("dashboardId", "") or ""),
        )


class VariableListData(BaseModel):
    """Structured output for list_dashboard_variables (a dashboard's variables)."""

    count: int = Field(0, description="Number of variables returned")
    items: list[VariableSummary] = Field(default_factory=list, description="Dashboard variables")

    @classmethod
    def from_api(cls, data: Any) -> VariableListData:
        """Build a VariableListData from the raw array of variables."""
        items = data if isinstance(data, list) else []
        return cls(
            count=len(items),
            items=[VariableSummary.from_api(i) for i in items],
        )


class ViewSummary(BaseModel):
    """One saved dashboard view (a named query/filter/time-range preset)."""

    id: str = Field("", description="View ID")
    name: str = Field("", description="View name")
    dashboard_id: str = Field("", description="Dashboard this view belongs to")
    filters: str = Field("", description="Serialized filter state")
    query: str = Field("", description="Serialized query state")
    time_range: str = Field("", description="Serialized time-range state")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: Any) -> ViewSummary:
        """Build a ViewSummary from a raw ViewDto dict."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            dashboard_id=str(data.get("dashboardId", "") or ""),
            filters=str(data.get("filters", "") or ""),
            query=str(data.get("query", "") or ""),
            time_range=str(data.get("timeRange", "") or ""),
            created_at=str(data.get("createdAt", "") or ""),
            updated_at=str(data.get("updatedAt", "") or ""),
        )


class ViewListData(BaseModel):
    """Structured output for list_dashboard_views (a dashboard's saved views)."""

    count: int = Field(0, description="Number of views returned")
    items: list[ViewSummary] = Field(default_factory=list, description="Dashboard views")

    @classmethod
    def from_api(cls, data: Any) -> ViewListData:
        """Build a ViewListData from the raw array of views."""
        items = data if isinstance(data, list) else []
        return cls(count=len(items), items=[ViewSummary.from_api(i) for i in items])


class WidgetDetail(BaseModel):
    """Structured output for get_widget / create_widget (one dashboard widget/chart).

    Widget-level fields are typed; the graph payloads (metric graphs, log graphs,
    custom formulas) are kept generically since they are a deeply-nested,
    polymorphic chart-builder structure the API itself types as opaque objects.
    """

    id: str = Field("", description="Widget ID")
    name: str = Field("", description="Widget title")
    type: str = Field("", description="Widget data source type (e.g. Metric)")
    type_chart: str = Field("", description="Chart type (line, bar, number, table, ...)")
    layout: str = Field("", description="Serialized grid layout")
    position: str = Field("", description="Legend position")
    period: int | str = Field("", description="Refresh period in seconds")
    description: str = Field("", description="Widget description")
    metric_graphs: list[dict[str, Any]] = Field(
        default_factory=list, description="Metric graph specs (raw)"
    )
    log_graphs: list[dict[str, Any]] = Field(
        default_factory=list, description="Log graph specs (raw)"
    )
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: Any) -> WidgetDetail:
        """Build a WidgetDetail from a ResponseResult_WidgetDto_ or bare WidgetDto."""
        payload = _unwrap(data)
        metric_graphs = payload.get("metricGraphs") or []
        log_graphs = payload.get("logGraphs") or []
        raw_type = payload.get("type")
        type_name = raw_type.get("name", "") if isinstance(raw_type, dict) else raw_type
        return cls(
            id=str(payload.get("id", "") or ""),
            name=str(payload.get("name", "") or ""),
            type=str(type_name or ""),
            type_chart=str(payload.get("typeChart", "") or ""),
            layout=str(payload.get("layout", "") or ""),
            position=str(payload.get("position", "") or ""),
            period=payload.get("period", "") or "",
            description=str(payload.get("description", "") or ""),
            metric_graphs=[g for g in metric_graphs if isinstance(g, dict)],
            log_graphs=[g for g in log_graphs if isinstance(g, dict)],
            created_at=str(payload.get("createdAt", "") or ""),
            updated_at=str(payload.get("updatedAt", "") or ""),
        )


class WidgetMetricQuery(BaseModel):
    """One metric query stored inside a dashboard widget, ready to be replayed.

    Every field maps 1:1 onto a ``get_statistics_v2`` SIMPLE ``graph``:
    ``metric_name`` → ``name``, ``statistic`` → ``statistics``, ``dimensions`` →
    ``dimensions``, ``group_by`` → ``group_by``. A widget on a resource's default
    dashboard therefore already contains a working query — no dimension
    discovery, and no detailed monitoring, needed to reproduce its chart.
    """

    metric_name: str = Field(
        "", description="Metric name — the `name` of a get_statistics_v2 graph"
    )
    statistic: str = Field(
        "", description="Statistic function (avg/max/sum/...) — the `statistics` field"
    )
    dimensions: str = Field(
        "",
        description="Dimension filter as comma-separated `key:value` pairs — pass "
        "STRAIGHT into a query's `dimensions` (it already carries the resource_id)",
    )
    group_by: str = Field("", description="Grouping dimension ('none' when not grouped)")
    alias: str = Field("", description="Series label the chart shows")
    limit: str = Field("", description="Max series ('' for no limit)")
    product: str = Field("", description="Product scope, when the widget sets one")
    enabled: bool = Field(True, description="Whether this query is plotted on the widget")

    @classmethod
    def from_api(cls, data: dict) -> WidgetMetricQuery:
        """Build a WidgetMetricQuery from a raw widget ``metricGraphs`` item."""
        return cls(
            metric_name=str(data.get("name", "") or ""),
            statistic=str(data.get("statistic", "") or ""),
            dimensions=str(data.get("filter", "") or ""),
            group_by=str(data.get("groupBy", "") or ""),
            alias=str(data.get("alias", "") or ""),
            limit=str(data.get("limit", "") or ""),
            product=str(data.get("product", "") or ""),
            enabled=bool(data.get("enabled", True)),
        )


class WidgetSummary(BaseModel):
    """One widget of a dashboard, with the metric queries it plots."""

    id: str = Field("", description="Widget ID")
    name: str = Field("", description="Widget title")
    type: str = Field("", description="Widget data source type (e.g. Metric)")
    type_chart: str = Field("", description="Chart type (LINE, BAR, NUMBER, TABLE, ...)")
    layout: str = Field("", description="Grid layout string 'cols:C, rows:R, x:X, y:Y'")
    period: int | str = Field(
        "", description="Chart resolution in seconds — reuse it as a query's `period`"
    )
    description: str = Field("", description="Widget description")
    metric_queries: list[WidgetMetricQuery] = Field(
        default_factory=list,
        description="The metric queries this widget plots, each replayable via get_statistics_v2",
    )
    log_graph_count: int = Field(
        0, description="Number of log-based graphs on this widget (queried via search_logs)"
    )

    @classmethod
    def from_api(cls, data: dict) -> WidgetSummary:
        """Build a WidgetSummary from a raw widget dict of a dashboard detail."""
        metric_graphs = data.get("metricGraphs") or []
        log_graphs = data.get("logGraphs") or []
        raw_type = data.get("type")
        type_name = raw_type.get("name", "") if isinstance(raw_type, dict) else raw_type
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            type=str(type_name or ""),
            type_chart=str(data.get("typeChart", "") or ""),
            layout=str(data.get("layout", "") or ""),
            period=data.get("period", "") or "",
            description=str(data.get("description", "") or ""),
            metric_queries=[
                WidgetMetricQuery.from_api(g) for g in metric_graphs if isinstance(g, dict)
            ],
            log_graph_count=len(log_graphs) if isinstance(log_graphs, list) else 0,
        )


class WidgetListData(BaseModel):
    """Structured output for list_widgets (the widgets embedded in a dashboard)."""

    dashboard_id: str = Field("", description="Dashboard the widgets belong to")
    dashboard_name: str = Field("", description="Dashboard name")
    system: bool = Field(
        False,
        description="Whether this is a system (auto-generated, read-only) dashboard — "
        "true for a resource's default dashboard",
    )
    total_item: int = Field(0, description="Number of widgets on the dashboard")
    items: list[WidgetSummary] = Field(default_factory=list, description="Widgets")

    @classmethod
    def from_api(cls, data: Any) -> WidgetListData:
        """Build a WidgetListData from a raw dashboard detail payload (any envelope)."""
        payload = _unwrap(data)
        widgets = payload.get("widgets") or []
        items = [WidgetSummary.from_api(w) for w in widgets if isinstance(w, dict)]
        return cls(
            dashboard_id=str(payload.get("id", "") or ""),
            dashboard_name=str(payload.get("name", "") or ""),
            system=bool(payload.get("system", False)),
            total_item=len(items),
            items=items,
        )


class StatisticGraphDto(BaseModel):
    """One metric graph inside a v2 statistics query.

    The vMonitor statistics backend does not validate this payload: a wrong
    field *type* (e.g. ``statistics`` as a list, ``dimensions`` as an object)
    raises an uncaught 500 rather than a 4xx. Typing every field here makes the
    correct wire types explicit in the tool schema, so the caller cannot send a
    shape that crashes the backend.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ..., description="Metric name (e.g. vstorage.bucket_requests.method.value_rate)"
    )
    statistics: str = Field(
        ..., description="Statistic function as a STRING, not a list (e.g. 'max', 'avg', 'sum')"
    )
    dimensions: str | None = Field(
        None,
        description="Dimension filter as a comma-separated STRING of `key:value` "
        "pairs (colon between key and value, NOT '='), e.g. "
        "'resource_id:ins-0001,product:vserver'. "
        "Discover the keys/values with get_metric_dimensions.",
    )
    group_by: str | None = Field(
        None, description="Dimension(s) to group by; the string 'none' for no grouping"
    )
    offset: int | None = Field(None, description="Result offset (0)")
    limit: str | int | None = Field(None, description="Max series ('' for no limit, or a number)")
    rollup: str | None = Field(None, description="Rollup function ('' for none)")
    rate: int | float | None = Field(
        None, description="Rate flag (0 = value, 1 = per-second rate)"
    )


class StatisticDataDto(BaseModel):
    """The ``data`` payload of a v2 statistics query (SIMPLE or CUSTOM).

    SIMPLE fills ``graph``; CUSTOM fills ``expression`` + ``graphs``. Time bounds
    are **epoch millis** — an ISO-8601 string crashes the backend with a 500, so
    they are typed ``int`` to reject that shape at the schema level.
    """

    model_config = ConfigDict(extra="forbid")

    graph: StatisticGraphDto | None = Field(None, description="SIMPLE: the single metric graph")
    expression: str | None = Field(None, description="CUSTOM: formula over the named graphs")
    graphs: dict[str, StatisticGraphDto] | None = Field(
        None, description="CUSTOM: named graphs (a/b/... -> graph)"
    )
    start_time: int | None = Field(None, description="Window start (epoch MILLIS, not ISO)")
    end_time: int | None = Field(None, description="Window end (epoch MILLIS, not ISO)")
    period: int | None = Field(None, description="Aggregation period in seconds")
    alarm: bool | None = Field(None, description="Scope to an alarm evaluation")
    offset: int | None = Field(None, description="CUSTOM: result offset")
    limit: str | int | None = Field(None, description="CUSTOM: max series")
    reduction: Any | None = Field(None, description="Optional reduction config")


class StatisticQueryDto(BaseModel):
    """Request body for get_statistics_v2 (``POST /api/v1/statistics``).

    ``type`` selects the query kind and ``data`` carries its (typed) parameters.
    Both branches are typed because the backend answers any malformed ``data``
    with an uncaught 500 instead of a 4xx.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["SIMPLE", "CUSTOM"] = Field(
        ...,
        description="Query kind: SIMPLE (one metric graph) or CUSTOM (formula over graphs)",
    )
    data: StatisticDataDto = Field(..., description="Query parameters for the selected type")


class UpdateVariableListDto(BaseModel):
    """Request body for update_dashboard_variables (``PUT .../variables``).

    Replaces the dashboard's variable list. Each item mirrors a VariableDto; the
    nested ``queries`` / ``filters`` are kept as passthrough objects.
    """

    model_config = ConfigDict(extra="forbid")

    variables: list[dict[str, Any]] = Field(
        ..., description="Full variable list to save (VariableDto objects, camelCase keys)"
    )


class CreateViewDto(BaseModel):
    """Request body for create_dashboard_view (``POST .../views``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="View name")
    variables: dict[str, str] | None = Field(
        None, description="Map of variable key -> selected value for this view"
    )
    filters: str | None = Field(None, description="Serialized filter state")
    query: str | None = Field(None, description="Serialized query state (JSON string)")
    timeRange: str | None = Field(None, description="Serialized time-range state (JSON string)")


class UpdateViewDto(BaseModel):
    """Request body for update_dashboard_view (``PUT .../views/{view_id}``)."""

    model_config = ConfigDict(extra="forbid")

    variables: list[dict[str, Any]] | None = Field(
        None, description="Variable bindings ({variableId, value, id?} objects)"
    )
    filters: str | None = Field(None, description="Serialized filter state")
    query: str | None = Field(None, description="Serialized query state (JSON string)")
    timeRange: str | None = Field(None, description="Serialized time-range state (JSON string)")


class GraphRequestDto(BaseModel):
    """One entry of a widget's ``graphs`` map: a typed query kind + opaque data.

    ``type`` selects the query kind (metric graph, log graph, custom formula) and
    ``data`` is the chart-builder payload for that kind — a polymorphic object the
    API types opaquely, so it is accepted as a passthrough map.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="Query kind (e.g. METRIC_GRAPH, LOG_GRAPH)")
    data: dict[str, Any] = Field(..., description="Chart-builder payload for this query kind")


class CreateWidgetDto(BaseModel):
    """Request body for create_widget (``POST .../widgets/v2``, CreateWidgetRequestV2).

    Widget-level fields are typed; the chart content is the ``graphs`` map (one
    GraphRequest per query, keyed a/b/c...) whose leaf ``data`` stays opaque, plus
    the optional ``extra`` / ``topListChart`` passthrough objects. Build ``graphs``
    from the metric/log/formula queries the user wants plotted.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Widget title")
    typeChart: str = Field(..., description="Chart type (line, bar, number, table, ...)")
    graphs: dict[str, GraphRequestDto] = Field(
        ..., description="Query map: key (a, b, ...) -> GraphRequest"
    )
    type: str = Field("Metric", description="Widget data source type")
    description: str | None = Field(None, description="Widget description")
    layout: str | None = Field(
        None,
        description="Grid placement 'cols:C, rows:R, x:X, y:Y' on a 10-column grid "
        "(e.g. 'cols:5, rows:2, x:0, y:0'). Omit to auto-place without overlap.",
    )
    position: str | None = Field(None, description="Legend position")
    period: int | None = Field(None, description="Refresh period in seconds")
    periodWidget: int | None = Field(None, description="Per-widget refresh period in seconds")
    fixedTimeRange: str | None = Field(None, description="Serialized fixed time range")
    showDataLabel: bool | None = Field(None, description="Show data labels on the chart")
    connectNulls: bool | None = Field(None, description="Connect null gaps in lines")
    smooth: bool | None = Field(None, description="Smooth line rendering")
    chartExtra: str | None = Field(None, description="Serialized extra chart config (JSON string)")
    yAxisLabel: str | None = Field(None, description="Y-axis label")
    yAxisType: str | None = Field(None, description="Y-axis type")
    yAxisScaleType: str | None = Field(None, description="Y-axis scale type (linear/log)")
    yAxisMin: int | None = Field(None, description="Y-axis minimum")
    yAxisMax: int | None = Field(None, description="Y-axis maximum")
    topListChart: dict[str, Any] | None = Field(None, description="Top-list chart config (opaque)")
    extra: dict[str, Any] | None = Field(None, description="Extra widget config (opaque)")


class UpdateWidgetV2Dto(BaseModel):
    """Request body for update_widget_v2 (``PUT .../widgets/v2/{widget_id}``).

    Same typed-shell + opaque-``graphs`` shape as CreateWidgetDto; every field is
    optional so a partial edit only sends what changes.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, description="Widget title")
    typeChart: str | None = Field(None, description="Chart type")
    graphs: dict[str, GraphRequestDto] | None = Field(
        None, description="Query map: key (a, b, ...) -> GraphRequest"
    )
    type: str | None = Field(None, description="Widget data source type")
    description: str | None = Field(None, description="Widget description")
    position: str | None = Field(None, description="Legend position")
    periodWidget: int | None = Field(None, description="Per-widget refresh period in seconds")
    fixedTimeRange: str | None = Field(None, description="Serialized fixed time range")
    showDataLabel: bool | None = Field(None, description="Show data labels on the chart")
    connectNulls: bool | None = Field(None, description="Connect null gaps in lines")
    smooth: bool | None = Field(None, description="Smooth line rendering")
    chartExtra: str | None = Field(None, description="Serialized extra chart config (JSON string)")
    yAxisLabel: str | None = Field(None, description="Y-axis label")
    yAxisType: str | None = Field(None, description="Y-axis type")
    yAxisScaleType: str | None = Field(None, description="Y-axis scale type")
    yAxisMin: int | None = Field(None, description="Y-axis minimum")
    yAxisMax: int | None = Field(None, description="Y-axis maximum")
    topListChart: dict[str, Any] | None = Field(None, description="Top-list chart config (opaque)")


class UpdateWidgetDto(BaseModel):
    """Request body for update_widget (``PUT .../widgets/{widget_id}``, v1 shape).

    The v1 edit passes chart content as ``metricGraphs`` / ``logGraphs`` arrays
    (rather than the v2 ``graphs`` map); their items are kept opaque.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, description="Widget title")
    typeChart: str | None = Field(None, description="Chart type")
    type: str | None = Field(None, description="Widget data source type")
    description: str | None = Field(None, description="Widget description")
    metricGraphs: list[dict[str, Any]] | None = Field(
        None, description="Metric graph specs (raw objects)"
    )
    logGraphs: list[dict[str, Any]] | None = Field(
        None, description="Log graph specs (raw objects)"
    )
    position: str | None = Field(None, description="Legend position")
    period: int | None = Field(None, description="Refresh period in seconds")
    periodWidget: int | None = Field(None, description="Per-widget refresh period in seconds")
    fixedTimeRange: str | None = Field(None, description="Serialized fixed time range")
    showDataLabel: bool | None = Field(None, description="Show data labels on the chart")
    connectNulls: bool | None = Field(None, description="Connect null gaps in lines")
    smooth: bool | None = Field(None, description="Smooth line rendering")
    chartExtra: str | None = Field(None, description="Serialized extra chart config (JSON string)")
    yAxisLabel: str | None = Field(None, description="Y-axis label")
    yAxisType: str | None = Field(None, description="Y-axis type")
    yAxisScaleType: str | None = Field(None, description="Y-axis scale type")
    yAxisMin: int | None = Field(None, description="Y-axis minimum")
    yAxisMax: int | None = Field(None, description="Y-axis maximum")
    extra: dict[str, Any] | None = Field(None, description="Extra widget config (opaque)")


class UpdateWidgetLayoutDto(BaseModel):
    """Request body for update_widget_layout (``PUT .../widgets/layout/{widget_id}``).

    Moves/resizes a widget on the dashboard grid and adjusts its time window only.
    """

    model_config = ConfigDict(extra="forbid")

    layout: str | None = Field(
        None,
        description="Grid placement 'cols:C, rows:R, x:X, y:Y' on a 10-column grid "
        "(e.g. 'cols:5, rows:2, x:5, y:0').",
    )
    period: int | None = Field(None, description="Refresh period in seconds")
    startTime: str | None = Field(None, description="Window start (ISO-8601)")
    endTime: str | None = Field(None, description="Window end (ISO-8601)")
    extra: dict[str, Any] | None = Field(None, description="Extra layout config (opaque)")


def _unwrap_list(data: Any) -> list[dict]:
    """Return the inner list from a ``{data: [...]}`` envelope (or a bare list)."""
    if isinstance(data, dict):
        data = data.get("data")
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []


class AlarmSummary(BaseModel):
    """One alarm as shown in the alarm list (metric, log, or change-detection)."""

    id: str = Field("", description="Alarm ID")
    name: str = Field("", description="Alarm name")
    type: str = Field("", description="Alarm type (e.g. Metric, Log, Change)")
    severity: str = Field("", description="Severity (e.g. Low, Medium, High)")
    description: str = Field("", description="Alarm description")
    progress_status: str = Field("", description="Current state (e.g. OK, ALARM, UNDETERMINED)")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: Any) -> AlarmSummary:
        """Build an AlarmSummary from a raw AlarmDto dict."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            type=str(data.get("type", "") or ""),
            severity=str(data.get("severity", "") or ""),
            description=str(data.get("description", "") or ""),
            progress_status=str(data.get("progressStatus", "") or ""),
            created_at=str(data.get("createdAt", "") or ""),
            updated_at=str(data.get("updatedAt", "") or ""),
        )


class AlarmListData(BaseModel):
    """Structured output for list_alarms (paging envelope + items)."""

    page: int = Field(0, description="Current page (1-based; 0 when all items returned at once)")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of alarms")
    total_page: int = Field(0, description="Total number of pages")
    items: list[AlarmSummary] = Field(default_factory=list, description="Alarms")

    @classmethod
    def from_api(cls, data: dict) -> AlarmListData:
        """Build an AlarmListData from the raw vMonitor paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=data.get("page", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_item=data.get("totalItem", 0) or 0,
            total_page=data.get("totalPage", 0) or 0,
            items=[AlarmSummary.from_api(i) for i in items],
        )


class AlarmDetail(BaseModel):
    """A single alarm with its type-specific configuration block (raw).

    The base scalar fields are typed; the type-specific definition (metric / log /
    change) and the renotification block are kept as raw dicts since only one
    applies per alarm and their shapes are flat enough to read directly.
    """

    id: str = Field("", description="Alarm ID")
    name: str = Field("", description="Alarm name")
    type: str = Field("", description="Alarm type (Metric, Log, Change)")
    severity: str = Field("", description="Severity")
    description: str = Field("", description="Alarm description")
    progress_status: str = Field("", description="Current state")
    alarm_metric: dict[str, Any] | None = Field(None, description="Metric-alarm config (if any)")
    alarm_log: dict[str, Any] | None = Field(None, description="Log-alarm config (if any)")
    change_alarm_metric: dict[str, Any] | None = Field(
        None, description="Change-alarm config (if any)"
    )
    renotification: dict[str, Any] | None = Field(None, description="Re-notification config")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: Any) -> AlarmDetail:
        """Build an AlarmDetail from a ResponseResult_AlarmDto_ or bare AlarmDto."""
        payload = _unwrap(data)

        def _block(key: str) -> dict | None:
            value = payload.get(key)
            return value if isinstance(value, dict) else None

        return cls(
            id=str(payload.get("id", "") or ""),
            name=str(payload.get("name", "") or ""),
            type=str(payload.get("type", "") or ""),
            severity=str(payload.get("severity", "") or ""),
            description=str(payload.get("description", "") or ""),
            progress_status=str(payload.get("progressStatus", "") or ""),
            alarm_metric=_block("alarmMetric"),
            alarm_log=_block("alarmLog"),
            change_alarm_metric=_block("changeAlarmMetric"),
            renotification=_block("renotificationDto"),
            created_at=str(payload.get("createdAt", "") or ""),
            updated_at=str(payload.get("updatedAt", "") or ""),
        )


class AlarmDefinitionData(BaseModel):
    """Structured output for the alarm-definition tools (metric/change definition).

    The definition is an upstream evaluator object (metric or change-detection)
    whose shape varies; a few common scalars are surfaced and the full object is
    kept raw.
    """

    id: str = Field("", description="Definition ID")
    name: str = Field("", description="Definition name")
    severity: str = Field("", description="Severity")
    description: str = Field("", description="Description")
    definition: dict[str, Any] = Field(
        default_factory=dict, description="Full raw definition object"
    )

    @classmethod
    def from_api(cls, data: Any) -> AlarmDefinitionData:
        """Build from a ResponseResult_..Definition.. envelope."""
        payload = _unwrap(data)
        return cls(
            id=str(payload.get("id", "") or ""),
            name=str(payload.get("name", "") or ""),
            severity=str(payload.get("severity", "") or ""),
            description=str(payload.get("description", "") or ""),
            definition=payload,
        )


class AlarmHistoryData(BaseModel):
    """Structured output for the alarm-history tools (state-transition records)."""

    count: int = Field(0, description="Number of history entries returned")
    items: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw alarm history entries"
    )

    @classmethod
    def from_api(cls, data: Any) -> AlarmHistoryData:
        """Build from a ResponseListResult_..History.. envelope or bare list."""
        items = _unwrap_list(data)
        return cls(count=len(items), items=items)


class LogAlarmHistoryData(BaseModel):
    """Structured output for get log-alarm history."""

    total: int = Field(0, description="Total number of history entries")
    status: dict[str, Any] | None = Field(None, description="Aggregate status object (raw)")
    items: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw history alarm entries"
    )

    @classmethod
    def from_api(cls, data: Any) -> LogAlarmHistoryData:
        """Build from the log-alarm history response envelope."""
        payload = _unwrap(data)
        alarms = payload.get("alarms") or []
        status = payload.get("status")
        return cls(
            total=payload.get("total", 0) or 0,
            status=status if isinstance(status, dict) else None,
            items=[a for a in alarms if isinstance(a, dict)] if isinstance(alarms, list) else [],
        )


class LogAlarmStatus(BaseModel):
    """Structured output for get_log_alarm_status (current log-alarm status)."""

    status: str = Field("", description="Current alarm status")
    updated_on: int | str = Field("", description="Last status update (epoch)")

    @classmethod
    def from_api(cls, data: Any) -> LogAlarmStatus:
        """Build from the log-alarm status response envelope."""
        payload = _unwrap(data)
        return cls(
            status=str(payload.get("status", "") or ""),
            updated_on=payload.get("updated_on", "") or "",
        )


class ApiKeySummary(BaseModel):
    """One metric API key (used to push custom metrics into vMonitor)."""

    name: str = Field("", description="API key name")
    key: str = Field("", description="The API key value")
    description: str = Field("", description="API key description")

    @classmethod
    def from_api(cls, data: Any) -> ApiKeySummary:
        """Build an ApiKeySummary from a raw MetricApiKeyResponse dict."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            name=str(data.get("name", "") or ""),
            key=str(data.get("key", "") or ""),
            description=str(data.get("description", "") or ""),
        )


class ApiKeyListData(BaseModel):
    """Structured output for list_metric_api_keys (paging envelope + items)."""

    page: int = Field(0, description="Current page (1-based; 0 when all items returned at once)")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of API keys")
    total_page: int = Field(0, description="Total number of pages")
    items: list[ApiKeySummary] = Field(default_factory=list, description="Metric API keys")

    @classmethod
    def from_api(cls, data: dict) -> ApiKeyListData:
        """Build an ApiKeyListData from the raw vMonitor paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=data.get("page", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_item=data.get("totalItem", 0) or 0,
            total_page=data.get("totalPage", 0) or 0,
            items=[ApiKeySummary.from_api(i) for i in items],
        )


class IntegrationSummary(BaseModel):
    """One integration app (e.g. an agent/plugin) available in vMonitor."""

    id: str = Field("", description="Integration ID")
    name: str = Field("", description="Integration name")
    description: str = Field("", description="Short description")
    installed: bool = Field(False, description="Whether the integration is installed")

    @classmethod
    def from_api(cls, data: Any) -> IntegrationSummary:
        """Build an IntegrationSummary from a raw AppDto dict."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
            installed=bool(data.get("installed", False)),
        )


class IntegrationListData(BaseModel):
    """Structured output for list_integrations (paging envelope + items)."""

    page: int = Field(0, description="Current page (1-based; 0 when all items returned at once)")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of integrations")
    total_page: int = Field(0, description="Total number of pages")
    items: list[IntegrationSummary] = Field(default_factory=list, description="Integration apps")

    @classmethod
    def from_api(cls, data: dict) -> IntegrationListData:
        """Build an IntegrationListData from the raw vMonitor paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=data.get("page", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_item=data.get("totalItem", 0) or 0,
            total_page=data.get("totalPage", 0) or 0,
            items=[IntegrationSummary.from_api(i) for i in items],
        )


class IntegrationDetail(BaseModel):
    """A single integration app with its documentation/configuration text (raw)."""

    id: str = Field("", description="Integration ID")
    name: str = Field("", description="Integration name")
    description: str = Field("", description="Short description")
    installed: bool = Field(False, description="Whether the integration is installed")
    configuration: str = Field("", description="Configuration guide/template text")
    overview: str = Field("", description="Overview/documentation text")
    metrics: str = Field("", description="Metrics documentation text")

    @classmethod
    def from_api(cls, data: Any) -> IntegrationDetail:
        """Build an IntegrationDetail from a ResponseResult_AppDto_ or bare AppDto."""
        payload = _unwrap(data)
        return cls(
            id=str(payload.get("id", "") or ""),
            name=str(payload.get("name", "") or ""),
            description=str(payload.get("description", "") or ""),
            installed=bool(payload.get("installed", False)),
            configuration=str(payload.get("configuration", "") or ""),
            overview=str(payload.get("overview", "") or ""),
            metrics=str(payload.get("metrics", "") or ""),
        )


class CreateMetricApiKeyDto(BaseModel):
    """Request body for create_metric_api_key (``POST /api/v1/apikeys/metric``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="API key name")
    description: str | None = Field(
        None,
        description=(
            "Optional API key description. Must not contain spaces — the API "
            "rejects a value with spaces (400 'the field description is an invalid')."
        ),
    )


class InstallIntegrationDto(BaseModel):
    """Request body for update_integration_installed (``PUT .../integrations/install/{id}``)."""

    model_config = ConfigDict(extra="forbid")

    logProjectId: str | None = Field(
        None, description="Log project to bind the integration to (if it collects logs)"
    )


class CreateMetricAlarmDto(BaseModel):
    """Request body for create_metric_alarm (``POST /api/v1/alarms/metrics``).

    A metric alarm evaluates a metric statistic against a threshold. The metric
    selection (``metricName`` / ``metricStatistic``), the ``condition`` and the
    ``severity`` are required — the API rejects a body missing any of them
    ("Missing field ...") and answers 500 when the evaluation timing is absent, so
    ``metricPeriod`` / ``interval`` / ``checkTime`` default here. Add
    ``thresholdValue`` for a static threshold and the notification actions
    (inAlarm / ok / undetermined) to route alerts.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Alarm name")
    metricName: str = Field(..., description="Metric to evaluate (e.g. cpu.usage_guest)")
    metricStatistic: str = Field(..., description="Statistic (avg, max, sum, min, ...)")
    condition: Literal["gt", "gte", "lt", "lte"] = Field(
        ...,
        description="Comparison operator: gt, gte, lt, lte "
        "(case-insensitive; the symbols >, >=, <, <= are also accepted)",
    )
    severity: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        ..., description="Severity: LOW, MEDIUM or HIGH (case-insensitive)"
    )
    metricPeriod: int = Field(60, description="Metric aggregation period in seconds")
    interval: int = Field(60, description="Evaluation interval in seconds")
    checkTime: int = Field(1, description="Number of periods to evaluate over")
    formula: str | None = Field(
        "a",
        description="Evaluation formula over the metric graph(s). A single-metric "
        "alarm is graph 'a', so the formula is 'a' (the default). Required by the "
        "evaluator — leave as 'a' unless composing multiple graphs.",
    )
    timeshift: str | None = Field(
        None,
        description="Evaluation-window offset in seconds as a string (e.g. '-300'). "
        "Defaults to -metricPeriod when omitted.",
    )
    description: str | None = Field(None, description="Alarm description")
    metricProduct: str | None = Field(
        None,
        description="Product the metric belongs to; the empty string '' for "
        "agent/custom metrics (the common case). Defaults to ''.",
    )
    metricGroupBy: str | None = Field(None, description="Dimension(s) to group by; 'none' if not")
    metricFilter: dict[str, Any] | None = Field(
        None,
        description="Dimension filter map, e.g. {'resource_id': 'ins-0001'}. {} for no filter.",
    )
    thresholdMethod: str | None = Field(
        None,
        description="Threshold method (static, pct_change, change, flatline, ...). "
        "Defaults to 'static'.",
    )
    thresholdValue: float | None = Field(None, description="Threshold value (for a static method)")
    inAlarm: str | None = Field(
        None,
        description="Notification action(s) for the ALARM state: a notification "
        "channel's `metric_mapping_id` (NOT its `id`) from list_notifications. The "
        "server auto-resolves a channel id to its metric_mapping_id, so either works.",
    )
    ok: str | None = Field(
        None, description="Notification action(s) for the OK state (channel metric_mapping_id)"
    )
    undetermined: str | None = Field(
        None,
        description="Notification action(s) for the UNDETERMINED state (channel metric_mapping_id)",
    )
    resendEnabled: bool | None = Field(None, description="Re-send notifications while in alarm")
    resendStatus: str | None = Field(None, description="State(s) to re-notify for")
    resendPeriod: int | None = Field(None, description="Re-send period (seconds)")
    resendTimes: int | None = Field(None, description="Maximum number of re-sends")

    _norm_sev = field_validator("severity", mode="before")(_norm_severity)
    _norm_cond = field_validator("condition", mode="before")(_norm_condition)


class UpdateMetricAlarmDto(BaseModel):
    """Request body for update_metric_alarm (``PUT /api/v1/alarms/metrics/{alarmId}``).

    Every field is optional; the alarm id comes from the path. Note the metric
    selection (metricName / metricStatistic / metricGroupBy / metricFilter) is not
    editable here — only thresholds, timing, notification and metadata are.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, description="Alarm name")
    description: str | None = Field(None, description="Alarm description")
    severity: str | None = Field(None, description="Severity — UPPER-CASE: LOW, MEDIUM, HIGH")
    metricProduct: str | None = Field(
        None, description="Product the metric belongs to ('' for custom)"
    )
    metricPeriod: int | None = Field(None, description="Metric aggregation period (seconds)")
    interval: int | None = Field(None, description="Evaluation interval (seconds)")
    checkTime: int | None = Field(None, description="Number of periods to evaluate over")
    formula: str | None = Field(
        None, description="Evaluation formula ('a' for a single metric graph)"
    )
    timeshift: str | None = Field(
        None, description="Evaluation-window offset in seconds (e.g. '-300')"
    )
    condition: str | None = Field(None, description="Comparison operator")
    thresholdMethod: str | None = Field(None, description="Threshold method")
    thresholdValue: float | None = Field(None, description="Threshold value")
    inAlarm: str | None = Field(None, description="Notification action(s) for the ALARM state")
    ok: str | None = Field(None, description="Notification action(s) for the OK state")
    undetermined: str | None = Field(
        None, description="Notification action(s) for the UNDETERMINED state"
    )
    resendEnabled: bool | None = Field(None, description="Re-send notifications while in alarm")
    resendStatus: str | None = Field(None, description="State(s) to re-notify for")
    resendPeriod: int | None = Field(None, description="Re-send period (seconds)")
    resendTimes: int | None = Field(None, description="Maximum number of re-sends")

    _norm_sev = field_validator("severity", mode="before")(_norm_severity)
    _norm_cond = field_validator("condition", mode="before")(_norm_condition)


class CreateLogAlarmDto(BaseModel):
    """Request body for create_log_alarm (``POST /api/v1/alarms/logs``).

    A log alarm evaluates a log query's aggregated result against a threshold.
    ``name`` is required; supply the log source (logProjectId / queryString),
    the aggregation (metricAggType / metricAggKey) and the threshold.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Alarm name")
    description: str | None = Field(None, description="Alarm description")
    severity: str | None = Field(None, description="Severity — UPPER-CASE: LOW, MEDIUM, HIGH")
    logProjectId: str | None = Field(None, description="Log project to evaluate")
    projectName: str | None = Field(None, description="Log project name")
    queryString: str | None = Field(None, description="Log query string")
    logSearchQuery: str | None = Field(None, description="Serialized log search query")
    filter: dict[str, Any] | None = Field(None, description="Log filter map")
    groupByField: str | None = Field(None, description="Field to group results by")
    metricAggType: str | None = Field(None, description="Aggregation type (count, avg, ...)")
    metricAggKey: str | None = Field(None, description="Field the aggregation is applied to")
    timeFrame: int | None = Field(None, description="Evaluation time frame (seconds)")
    condition: str | None = Field(None, description="Comparison operator")
    thresholdType: str | None = Field(None, description="Threshold type")
    thresholdValue: int | None = Field(None, description="Threshold value")
    reason: str | None = Field(None, description="Notification reason/message")
    inAlarm: str | None = Field(None, description="Notification action(s) for the ALARM state")
    ok: str | None = Field(None, description="Notification action(s) for the OK state")

    _norm_sev = field_validator("severity", mode="before")(_norm_severity)
    _norm_cond = field_validator("condition", mode="before")(_norm_condition)


class UpdateLogAlarmDto(BaseModel):
    """Request body for update_log_alarm (``PUT /api/v1/alarms/logs/{alarmId}``).

    Every field is optional; the alarm id comes from the path.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, description="Alarm name")
    description: str | None = Field(None, description="Alarm description")
    severity: str | None = Field(None, description="Severity — UPPER-CASE: LOW, MEDIUM, HIGH")
    logProjectId: str | None = Field(None, description="Log project to evaluate")
    projectName: str | None = Field(None, description="Log project name")
    queryString: str | None = Field(None, description="Log query string")
    logSearchQuery: str | None = Field(None, description="Serialized log search query")
    filter: dict[str, Any] | None = Field(None, description="Log filter map")
    groupByField: str | None = Field(None, description="Field to group results by")
    metricAggType: str | None = Field(None, description="Aggregation type")
    metricAggKey: str | None = Field(None, description="Field the aggregation is applied to")
    timeFrame: int | None = Field(None, description="Evaluation time frame (seconds)")
    condition: str | None = Field(None, description="Comparison operator")
    thresholdType: str | None = Field(None, description="Threshold type")
    thresholdValue: int | None = Field(None, description="Threshold value")
    reason: str | None = Field(None, description="Notification reason/message")
    inAlarm: str | None = Field(None, description="Notification action(s) for the ALARM state")
    ok: str | None = Field(None, description="Notification action(s) for the OK state")
    zone: str | None = Field(None, description="Zone the log alarm applies to")

    _norm_sev = field_validator("severity", mode="before")(_norm_severity)
    _norm_cond = field_validator("condition", mode="before")(_norm_condition)


class CreateChangeAlarmDto(BaseModel):
    """Request body for create_change_alarm (``POST /api/v1/alarms/change-method``).

    A change alarm compares a metric to its own past (timeshift) to detect a
    change. ``name`` is required; supply the metric selection, the change
    threshold (thresholdMethod / thresholdValue / timeshift) and notifications.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Alarm name")
    description: str | None = Field(None, description="Alarm description")
    severity: str | None = Field(None, description="Severity — UPPER-CASE: LOW, MEDIUM, HIGH")
    metricProduct: str | None = Field(None, description="Product the metric belongs to")
    metricName: str | None = Field(None, description="Metric to evaluate")
    metricStatistic: str | None = Field(None, description="Statistic (avg, max, sum, ...)")
    metricGroupBy: str | None = Field(None, description="Dimension(s) to group by")
    metricFilter: dict[str, Any] | None = Field(None, description="Dimension filter map")
    metricPeriod: int | None = Field(None, description="Metric aggregation period (seconds)")
    interval: int | None = Field(None, description="Evaluation interval (seconds)")
    checkTime: int | None = Field(None, description="Number of periods to evaluate over")
    timeshift: int | None = Field(None, description="Time shift to compare against (seconds)")
    condition: str | None = Field(None, description="Comparison operator")
    thresholdMethod: str | None = Field(None, description="Threshold method (change, pct_change)")
    thresholdValue: float | None = Field(None, description="Threshold value")
    inAlarm: str | None = Field(None, description="Notification action(s) for the ALARM state")
    ok: str | None = Field(None, description="Notification action(s) for the OK state")
    undetermined: str | None = Field(
        None, description="Notification action(s) for the UNDETERMINED state"
    )

    _norm_sev = field_validator("severity", mode="before")(_norm_severity)
    _norm_cond = field_validator("condition", mode="before")(_norm_condition)


class UpdateChangeAlarmDto(BaseModel):
    """Request body for update_change_alarm (``PUT /api/v1/alarms/change-method/{alarm_id}``).

    Every field is optional; the alarm id comes from the path.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, description="Alarm name")
    description: str | None = Field(None, description="Alarm description")
    severity: str | None = Field(None, description="Severity — UPPER-CASE: LOW, MEDIUM, HIGH")
    metricProduct: str | None = Field(None, description="Product the metric belongs to")
    metricPeriod: int | None = Field(None, description="Metric aggregation period (seconds)")
    interval: int | None = Field(None, description="Evaluation interval (seconds)")
    checkTime: int | None = Field(None, description="Number of periods to evaluate over")
    timeshift: int | None = Field(None, description="Time shift to compare against (seconds)")
    condition: str | None = Field(None, description="Comparison operator")
    thresholdMethod: str | None = Field(None, description="Threshold method")
    thresholdValue: float | None = Field(None, description="Threshold value")
    inAlarm: str | None = Field(None, description="Notification action(s) for the ALARM state")
    ok: str | None = Field(None, description="Notification action(s) for the OK state")
    undetermined: str | None = Field(
        None, description="Notification action(s) for the UNDETERMINED state"
    )

    _norm_sev = field_validator("severity", mode="before")(_norm_severity)
    _norm_cond = field_validator("condition", mode="before")(_norm_condition)


class LogPageData(BaseModel):
    """Structured output for Log API list endpoints (the ``PageDto`` envelope).

    The Log API paginates with a different envelope than the metric API
    (``content`` / ``currentPage`` / ``pageSize`` / ``totalElements`` /
    ``totalPages``). Items are kept as raw dicts so no per-resource field is
    dropped across the many log resource types.
    """

    current_page: int = Field(0, description="Current page number")
    page_size: int = Field(0, description="Page size used by the API")
    total_elements: int = Field(0, description="Total number of items")
    total_pages: int = Field(0, description="Total number of pages")
    items: list[dict[str, Any]] = Field(default_factory=list, description="Page items (raw)")

    @classmethod
    def from_api(cls, data: Any) -> LogPageData:
        """Build a LogPageData from a raw Log API PageDto envelope."""
        if not isinstance(data, dict):
            return cls()
        content = data.get("content") or []
        return cls(
            current_page=data.get("currentPage", 0) or 0,
            page_size=data.get("pageSize", 0) or 0,
            total_elements=data.get("totalElements", 0) or 0,
            total_pages=data.get("totalPages", 0) or 0,
            items=[i for i in content if isinstance(i, dict)] if isinstance(content, list) else [],
        )


class LogResource(BaseModel):
    """Generic structured output for a single Log API resource.

    Surfaces the common ``id`` / ``name`` / ``status`` scalars when present and
    keeps the full object in ``data`` — used across the many log resource detail
    endpoints (archive, refill, pipeline, project, processor group, mapping, ...)
    whose per-type fields vary.
    """

    id: str = Field("", description="Resource ID")
    name: str = Field("", description="Resource name")
    status: str = Field("", description="Resource status (when applicable)")
    data: dict[str, Any] = Field(default_factory=dict, description="Full raw resource object")

    @classmethod
    def from_api(cls, data: Any) -> LogResource:
        """Build a LogResource from a raw Log API object (or ``{data: ...}``)."""
        payload = _unwrap(data)
        return cls(
            id=str(payload.get("id", "") or ""),
            name=str(payload.get("name", "") or ""),
            status=str(payload.get("status", "") or ""),
            data=payload,
        )


class CreateArchiveDto(BaseModel):
    """Request body for create_archive (``POST /v1/archives``).

    An archive is an external storage destination logs are exported to.
    ``storageSettings`` is a storage-type-specific object (credentials, bucket,
    endpoint, ...) kept as a passthrough map.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Archive name")
    projectId: str = Field(..., description="Log project the archive belongs to")
    storageType: str = Field(..., description="Storage backend type (e.g. S3, VStorage)")
    storageSettings: dict[str, Any] = Field(
        ..., description="Storage-type-specific settings (bucket, endpoint, credentials, ...)"
    )
    filter: str | None = Field(None, description="Optional log filter to archive")
    description: str | None = Field(None, description="Optional description")


class UpdateArchiveDto(BaseModel):
    """Request body for update_archive (``PUT /v1/archives/{archive_id}``)."""

    model_config = ConfigDict(extra="forbid")

    projectId: str = Field(..., description="Log project the archive belongs to")
    storageType: str = Field(..., description="Storage backend type")
    storageSettings: dict[str, Any] = Field(..., description="Storage-type-specific settings")
    filter: str | None = Field(None, description="Optional log filter to archive")


class TestStorageDto(BaseModel):
    """Request body for validate_archive_connection / validate_refill_connection.

    (``POST .../test-connection``, ``RestStorage``) — checks that the given
    storage settings are reachable before creating an archive/refill.
    """

    model_config = ConfigDict(extra="forbid")

    storageType: str = Field(..., description="Storage backend type")
    storageSettings: dict[str, Any] = Field(..., description="Storage-type-specific settings")


class CreateRefillDto(BaseModel):
    """Request body for create_refill (``POST /v1/refills``).

    A refill re-ingests logs from external storage back into a project over a
    time range. ``storageSettings`` is a passthrough map.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Refill job name")
    projectId: str = Field(..., description="Target log project to refill into")
    storageType: str = Field(..., description="Source storage backend type")
    storageSettings: dict[str, Any] = Field(..., description="Source storage settings")
    startAt: str = Field(..., description="Start of the time range to re-ingest")
    endAt: str = Field(..., description="End of the time range to re-ingest")
    filter: str | None = Field(None, description="Optional log filter")
    description: str | None = Field(None, description="Optional description")


class CreateRefillFromArchiveDto(BaseModel):
    """Request body for create_refill_from_archive (``POST /v1/refills/collections``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Refill job name")
    projectId: str = Field(..., description="Target log project to refill into")
    archiveId: str = Field(..., description="Source archive to re-ingest from")
    startAt: str = Field(..., description="Start of the time range to re-ingest")
    endAt: str = Field(..., description="End of the time range to re-ingest")
    filter: str | None = Field(None, description="Optional log filter")
    description: str | None = Field(None, description="Optional description")


class PipelineDto(BaseModel):
    """Request body for create_pipeline / update_pipeline (``PipelineRequest``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Pipeline name")
    description: str | None = Field(None, description="Optional description")


class CreateProcessorGroupDto(BaseModel):
    """Request body for create_processor_group / create_processor_group_library.

    A processor group routes logs from a ``source`` project to a ``destination``
    project and applies its ordered processors. ``source`` / ``destination`` are
    project references and ``processors`` a list of processor specs — kept as
    passthrough objects.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Processor group name")
    pipelineId: str = Field(..., description="Pipeline the group belongs to")
    source: dict[str, Any] = Field(..., description="Source project reference")
    destination: dict[str, Any] = Field(..., description="Destination project reference")
    description: str | None = Field(None, description="Optional description")
    filter: str | None = Field(None, description="Optional log filter")
    query: str | None = Field(None, description="Optional query")
    queryArr: str | None = Field(None, description="Optional serialized query array")
    editorEnable: bool | None = Field(None, description="Whether the query editor is enabled")
    dropFilter: bool | None = Field(None, description="Whether to drop non-matching logs")
    processors: list[dict[str, Any]] | None = Field(
        None, description="Ordered processor specs (raw)"
    )


class UpdateProcessorGroupDto(BaseModel):
    """Request body for update_processor_group (``ProcessorGroupUpdateRequest``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Processor group name")
    pipelineId: str = Field(..., description="Pipeline the group belongs to")
    description: str | None = Field(None, description="Optional description")
    filter: str | None = Field(None, description="Optional log filter")
    query: str | None = Field(None, description="Optional query")
    queryArr: str | None = Field(None, description="Optional serialized query array")
    editorEnable: bool | None = Field(None, description="Whether the query editor is enabled")
    dropFilter: bool | None = Field(None, description="Whether to drop non-matching logs")


class ReorderProcessorsDto(BaseModel):
    """Request body for update_processor_order (``ReOrderProcessorRequest``)."""

    model_config = ConfigDict(extra="forbid")

    pipelineId: str = Field(..., description="Pipeline the group belongs to")
    processorGroupId: str = Field(..., description="Processor group being reordered")
    processors: list[str] = Field(..., description="Processor IDs in the new order")


class ProcessorDto(BaseModel):
    """Request body for create_processor / update_processor (``ProcessorRequest``).

    A processor parses/transforms logs (grok, date, ...). ``parserType`` selects
    the kind and ``parserRule`` is its rule; ``rulePreset`` is an optional
    library preset kept as a passthrough object.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Processor name")
    pipelineId: str = Field(..., description="Pipeline the processor belongs to")
    processorGroupId: str = Field(..., description="Processor group the processor belongs to")
    parserType: str = Field(..., description="Parser type (e.g. grok, date, json)")
    parserRule: str = Field(..., description="Parser rule/pattern")
    filter: str | None = Field(None, description="Optional log filter")
    query: str | None = Field(None, description="Optional query")
    queryArr: str | None = Field(None, description="Optional serialized query array")
    rulePreset: dict[str, Any] | None = Field(None, description="Optional library rule preset")


class DebugGrokDto(BaseModel):
    """Request body for validate_grok_parser (``POST /v1/processors/debug-grok-parser``)."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(..., description="Grok pattern to test")
    log: str | None = Field(None, description="Sample log line to test the pattern against")


class UpdateProjectDto(BaseModel):
    """Request body for update_project (``PATCH /v1/projects/{project_id}``).

    ``mappings`` is a list of field-mapping objects kept as a passthrough.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, description="Project description")
    mappings: list[dict[str, Any]] | None = Field(
        None, description="Project field mappings (raw objects)"
    )


class UpdateProjectMappingsDto(BaseModel):
    """Request body for update_project_mappings (``ProjectMappingFormat``)."""

    model_config = ConfigDict(extra="forbid")

    projectId: str | None = Field(None, description="Project ID")
    name: str | None = Field(None, description="Mapping/field name")
    type: str | None = Field(None, description="Field type")
    format: str | None = Field(None, description="Field format")
    inputFormat: str | None = Field(None, description="Input format")
    outputFormat: str | None = Field(None, description="Output format")
    datePattern: str | None = Field(None, description="Date pattern (for date fields)")


class LogSearchDto(BaseModel):
    """Request body for search_logs / search_logs_default (``LogSearchRequest``).

    ``query`` is a structured log query object, ``sorts`` / ``aggregations`` are
    lists of query clauses — all kept as passthrough objects. ``from_offset`` /
    ``size`` paginate the result window.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    query: dict[str, Any] | None = Field(
        None,
        description="Structured query clause `{type, value}`. Types: `match` "
        "(value {field, value}), `range` (value {field, gte/lte/gt/lt}), `exists` "
        "(value {field}), or `bool` (value with filter/should/must/mustNot arrays). "
        "Omit to match every log (`match_all` is NOT accepted). Elasticsearch "
        "shorthands are translated server-side.",
    )
    sorts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Sort clauses as {field, order} (order asc/desc), e.g. "
        "[{'field': '@timestamp', 'order': 'desc'}]",
    )
    aggregations: list[dict[str, Any]] = Field(
        default_factory=list, description="Aggregation clauses"
    )
    size: int | None = Field(None, description="Number of results to return")
    from_offset: int | None = Field(None, alias="from", description="Result offset (start index)")


class ExportLogDto(BaseModel):
    """Request body for create_log_export (``ExportLogSearchRequest``)."""

    model_config = ConfigDict(extra="forbid")

    query: dict[str, Any] = Field(..., description="Structured log query object")
    sorts: list[dict[str, Any]] = Field(default_factory=list, description="Sort clauses")


class LogMappingEnableDto(BaseModel):
    """Request body for enabling a resource log mapping (``...EnableRequest``)."""

    model_config = ConfigDict(extra="forbid")

    logProjectId: str = Field(..., description="Log project to route the resource's logs into")
    status: str = Field(..., description="Target status (enable)")


class LogMappingDisableDto(BaseModel):
    """Request body for disabling a resource log mapping (``...DisableRequest``)."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="Target status (disable)")
    logProjectId: str | None = Field(None, description="Log project (optional on disable)")


class LogMappingEditDto(BaseModel):
    """Request body for editing a resource log mapping (``...EditRequest``)."""

    model_config = ConfigDict(extra="forbid")

    logProjectId: str = Field(..., description="Log project to route the resource's logs into")


class BucketLogMappingUpdateDto(BaseModel):
    """Request body for update_vstorage_bucket_log_mapping."""

    model_config = ConfigDict(extra="forbid")

    logProjectId: str = Field(..., description="Log project to route the bucket's logs into")
    vstorageProjectId: str = Field(..., description="vStorage project the bucket belongs to")
    status: str = Field(..., description="Target status")


NotificationChannel = Literal["Email", "SMS", "Slack", "Webhook", "Telegram", "Teams"]


class NotificationType(BaseModel):
    """One notification channel type supported by the gateway (Email, SMS, ...)."""

    id: str = Field("", description="Type ID")
    name: str = Field("", description="Type name (Email, SMS, Slack, Webhook, Telegram, Teams)")
    description: str = Field("", description="Type description")

    @classmethod
    def from_api(cls, data: Any) -> NotificationType:
        """Build a NotificationType from a raw type dict."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
        )


class NotificationTypeListData(BaseModel):
    """Structured output for list_notification_types (paging envelope + items)."""

    page: int = Field(0, description="Current page (0/None when all returned at once)")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of types")
    total_page: int = Field(0, description="Total number of pages")
    items: list[NotificationType] = Field(default_factory=list, description="Notification types")

    @classmethod
    def from_api(cls, data: Any) -> NotificationTypeListData:
        """Build from the notification gateway ``lstData`` paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=(data.get("page") or 0) if isinstance(data, dict) else 0,
            page_size=(data.get("pageSize") or 0) if isinstance(data, dict) else 0,
            total_item=(data.get("totalItem") or 0) if isinstance(data, dict) else 0,
            total_page=(data.get("totalPage") or 0) if isinstance(data, dict) else 0,
            items=[NotificationType.from_api(i) for i in items],
        )


class NotificationSummary(BaseModel):
    """One notification channel configured by the user."""

    id: str = Field(
        "",
        description="Notification channel ID. NOTE: this is NOT the value alarms want "
        "for inAlarm/ok/undetermined — use `metric_mapping_id` for those.",
    )
    name: str = Field("", description="Notification name")
    address: str = Field("", description="Destination address (email, phone, chat ID, URL, ...)")
    header: str = Field("", description="Extra header payload (webhook only; JSON string)")
    type_id: str = Field("", description="Channel type ID")
    type_name: str = Field("", description="Channel type name (Email, SMS, ...)")
    created_date: str = Field("", description="Creation timestamp")
    updated_date: str = Field("", description="Last update timestamp")
    metric_mapping_id: str = Field(
        "",
        description="The channel's metric-mapping ID — THIS is the value an alarm's "
        "inAlarm/ok/undetermined fields take (passing the plain `id` there 500s).",
    )

    @classmethod
    def from_api(cls, data: Any) -> NotificationSummary:
        """Build a NotificationSummary from a raw notification dict."""
        if not isinstance(data, dict):
            return cls()
        type_notification = data.get("typeNotification") or {}
        if not isinstance(type_notification, dict):
            type_notification = {}
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            address=str(data.get("address", "") or ""),
            header=str(data.get("header", "") or ""),
            type_id=str(type_notification.get("id", "") or ""),
            type_name=str(type_notification.get("name", "") or ""),
            created_date=str(data.get("createdDate", "") or ""),
            updated_date=str(data.get("updatedDate", "") or ""),
            metric_mapping_id=str(data.get("metricMappingId", "") or ""),
        )


class NotificationListData(BaseModel):
    """Structured output for list_notifications (paging envelope + items)."""

    page: int = Field(0, description="Current page")
    page_size: int = Field(0, description="Page size used by the API")
    total_item: int = Field(0, description="Total number of notifications")
    total_page: int = Field(0, description="Total number of pages")
    items: list[NotificationSummary] = Field(default_factory=list, description="Notifications")

    @classmethod
    def from_api(cls, data: Any) -> NotificationListData:
        """Build from the notification gateway ``lstData`` paging envelope."""
        items = data.get("lstData", []) if isinstance(data, dict) else []
        return cls(
            page=(data.get("page") or 0) if isinstance(data, dict) else 0,
            page_size=(data.get("pageSize") or 0) if isinstance(data, dict) else 0,
            total_item=(data.get("totalItem") or 0) if isinstance(data, dict) else 0,
            total_page=(data.get("totalPage") or 0) if isinstance(data, dict) else 0,
            items=[NotificationSummary.from_api(i) for i in items],
        )


class NotificationOtpResult(BaseModel):
    """Generic result of an OTP send/validate call.

    ``ref`` + ``expired_at`` come back from a send; ``code`` comes back from a
    successful validate (feed it as ``otp_code`` into create/update). The full
    raw object is kept in ``data``.
    """

    id: str = Field(
        "",
        description="Created/updated channel ID (returned by create/update; needed to update or delete)",
    )
    ref: str = Field("", description="OTP reference (returned by send; needed to validate)")
    expired_at: str = Field("", description="OTP expiry (epoch millis, when present)")
    code: str = Field("", description="Validated OTP code (returned by validate; use for create)")
    data: dict[str, Any] = Field(default_factory=dict, description="Full raw response object")

    @classmethod
    def from_api(cls, data: Any) -> NotificationOtpResult:
        """Build a NotificationOtpResult from a raw OTP or channel response."""
        payload = data if isinstance(data, dict) else {}
        return cls(
            id=str(payload.get("id", "") or ""),
            ref=str(payload.get("ref", "") or ""),
            expired_at=str(payload.get("expiredAt", "") or ""),
            code=str(payload.get("code", "") or ""),
            data=payload,
        )


class CreateNotificationOtpDto(BaseModel):
    """Request body for create_notification_otp (``POST /v1/notification/otps``).

    Sends a real OTP to *address* over *type* (an email / SMS is dispatched).
    """

    model_config = ConfigDict(extra="forbid")

    type: NotificationChannel = Field(..., description="Channel type to send the OTP over")
    address: str = Field(..., description="Destination address the OTP is sent to")
    header: str = Field("", description="Extra header payload (webhook only; JSON string)")


class ValidateNotificationOtpDto(BaseModel):
    """Request body for validate_notification_otp (``POST /v1/notification/otps/validate``)."""

    model_config = ConfigDict(extra="forbid")

    otp: str = Field(..., description="OTP code the user received")
    address: str = Field(..., description="Address the OTP was sent to")
    ref: str = Field(..., description="OTP reference from create_notification_otp")
    header: str = Field("", description="Extra header payload (webhook only; JSON string)")


class CreateNotificationDto(BaseModel):
    """Request body for create_notification (``POST /v1/notification``).

    For OTP-verified channels (Email, SMS, Slack, Telegram) ``otpCode`` is the
    validated ``code`` from validate_notification_otp; Webhook needs no OTP.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Notification name")
    address: str = Field(..., description="Destination address")
    type: NotificationChannel = Field(..., description="Channel type")
    header: str = Field("", description="Extra header payload (webhook only; JSON string)")
    otpCode: str | None = Field(
        None, description="Validated OTP code (from validate_notification_otp)"
    )


class UpdateNotificationDto(BaseModel):
    """Request body for update_notification (``PUT /v1/notification``)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Notification ID to update")
    name: str = Field(..., description="Notification name")
    address: str = Field(..., description="Destination address")
    type: NotificationChannel = Field(..., description="Channel type")
    header: str = Field("", description="Extra header payload (webhook only; JSON string)")
    otpCode: str | None = Field(
        None, description="Validated OTP code (required if address changed)"
    )


class BillingResource(BaseModel):
    """Generic structured output for a single billing / quota-usage object.

    Surfaces common ``id`` / ``name`` / ``status`` / ``type`` scalars when present
    and keeps the full object in ``data`` — billing responses (usages, quota,
    quota-detail, tier, package, settings, price, ...) have heterogeneous shapes.
    """

    id: str = Field("", description="Resource ID (when present)")
    name: str = Field("", description="Resource name (when present)")
    status: str = Field("", description="Resource status (when present)")
    type: str = Field("", description="Resource type (when present)")
    data: dict[str, Any] = Field(default_factory=dict, description="Full raw response object")

    @classmethod
    def from_api(cls, data: Any) -> BillingResource:
        """Build a BillingResource from a raw billing object."""
        payload = data if isinstance(data, dict) else {}
        return cls(
            id=str(payload.get("id", "") or ""),
            name=str(payload.get("name", "") or ""),
            status=str(payload.get("status", "") or ""),
            type=str(payload.get("type", "") or ""),
            data=payload,
        )


class BillingListData(BaseModel):
    """Generic structured output for a billing list (tiers, packages, quota-classes, ...)."""

    total_item: int = Field(0, description="Number of items")
    items: list[dict[str, Any]] = Field(default_factory=list, description="List items (raw)")

    @classmethod
    def from_api(cls, data: Any) -> BillingListData:
        """Build a BillingListData from a bare list or ``{data: [...]}`` envelope."""
        if isinstance(data, dict):
            data = data.get("data", [])
        items = [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []
        return cls(total_item=len(items), items=items)


class BillingOrderResult(BaseModel):
    """Structured output for a placed billing order (buy / resize a quota).

    A billing order is what actually spends money. ``payment_url`` is only
    returned when the order still has to be paid (``pay=false``, or a prepaid
    account): open it to complete the purchase — until then the order is
    pending and the quota is NOT resized yet. On a post-paid account that
    charges immediately the field is usually empty.
    """

    order_id: str = Field("", description="Billing order ID")
    amount: float = Field(0.0, description="Amount charged for this order")
    payment_url: str = Field(
        "", description="URL to open to complete payment (empty when already charged)"
    )
    data: dict[str, Any] = Field(default_factory=dict, description="Full raw response object")

    @classmethod
    def from_api(cls, data: Any) -> BillingOrderResult:
        """Build a BillingOrderResult from a raw ``OrderResult`` response."""
        payload = data if isinstance(data, dict) else {}
        try:
            amount = float(payload.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        return cls(
            order_id=str(payload.get("orderId", "") or ""),
            amount=amount,
            payment_url=str(payload.get("paymentUrl", "") or ""),
            data=payload,
        )


class ResizeMetricQuotaDto(BaseModel):
    """Request body for resize_metric_quota (``POST /v2/metric/quota/resize``)."""

    model_config = ConfigDict(extra="forbid")

    packageId: str = Field(
        ...,
        description=(
            "Target package ID — the `packageId` of the retention entry you picked "
            "inside a metric quota class (list_quota_classes category=metric)"
        ),
    )
    quantity: int = Field(
        ...,
        ge=1,
        description=(
            "New host count. Must respect the chosen retention's minResource / "
            "maxResource / step, and stay above current usage"
        ),
    )
    redirectUrl: str | None = Field(
        None,
        min_length=1,
        description=(
            "Where the payment gateway returns the user after paying. Leave unset to use "
            "the vMonitor console's own quota page. The upstream API checks this against "
            "an allow-list, so an empty string or an arbitrary URL is rejected with "
            "'redirect URL is incorrect' — only override it with another console URL"
        ),
    )
    pay: bool = Field(
        False,
        description=(
            "true charges the account immediately; false creates the order and returns "
            "a paymentUrl to complete it"
        ),
    )


class CreateLogProjectDto(BaseModel):
    """Request body for create_log_project (``POST /v2/log/quotas``).

    Buying a log quota is what creates the log project — the project name and
    description travel with the order.
    """

    model_config = ConfigDict(extra="forbid")

    projectName: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z]$|^[a-z](?:[a-z\d-]){0,61}[a-z\d]$",
        description=(
            "Log project name: 1-63 chars, lowercase letters/digits/hyphen, must start "
            "with a letter and end with a letter or digit"
        ),
    )
    packageId: str = Field(
        ...,
        description=(
            "Package ID — the `packageId` of the retention entry you picked inside a log "
            "quota class (list_quota_classes category=log)"
        ),
    )
    quantity: int = Field(
        ...,
        ge=1,
        description=(
            "Total quota in GB-days = (log size per day in GB) x (retention days). The "
            "per-day size must respect the retention's minSize / maxSize / step"
        ),
    )
    projectDescription: str = Field(
        "",
        max_length=300,
        pattern=r"^[A-Za-z\d_.\- ]{0,300}$",
        description="Optional description (letters, digits, space, . _ -; max 300 chars)",
    )
    monthPeriod: int = Field(
        1, ge=1, description="Billing period in months (prepaid; the console always sends 1)"
    )
    buyWith: dict[str, str] | None = Field(
        None,
        description=(
            "Optionally buy notification quota in the same order, e.g. "
            '{"email": "<email package id>", "sms": "<sms package id>"} — only for '
            "categories the account does not already own"
        ),
    )
    redirectUrl: str | None = Field(
        None,
        min_length=1,
        description=(
            "Where the payment gateway returns the user after paying. Leave unset to use "
            "the vMonitor console's own quota page. The upstream API checks this against "
            "an allow-list, so an empty string or an arbitrary URL is rejected with "
            "'redirect URL is incorrect' — only override it with another console URL"
        ),
    )
    pay: bool = Field(
        False,
        description=(
            "true charges the account immediately; false creates the order and returns "
            "a paymentUrl to complete it"
        ),
    )


class ResizeLogProjectDto(BaseModel):
    """Request body for resize_log_project (``POST /v2/log/quotas/{id}/resize``)."""

    model_config = ConfigDict(extra="forbid")

    packageId: str = Field(
        ...,
        description=(
            "Target package ID — the `packageId` of the retention entry you picked inside "
            "a log quota class (list_quota_classes category=log)"
        ),
    )
    quantity: int = Field(
        ...,
        ge=1,
        description=(
            "New total quota in GB-days = (log size per day in GB) x (retention days); "
            "must stay above what the project already stores"
        ),
    )
    redirectUrl: str | None = Field(
        None,
        min_length=1,
        description=(
            "Where the payment gateway returns the user after paying. Leave unset to use "
            "the vMonitor console's own quota page. The upstream API checks this against "
            "an allow-list, so an empty string or an arbitrary URL is rejected with "
            "'redirect URL is incorrect' — only override it with another console URL"
        ),
    )
    pay: bool = Field(
        False,
        description=(
            "true charges the account immediately; false creates the order and returns "
            "a paymentUrl to complete it"
        ),
    )


class ResizeNotificationQuotaDto(BaseModel):
    """Request body for resize_sms_quota / resize_email_quota (``POST /v1/{sms|email}/quota/resize``).

    Notification packages are fixed bundles, so a resize is only a package
    swap — there is no quantity to choose.
    """

    model_config = ConfigDict(extra="forbid")

    packageId: str = Field(
        ...,
        description=(
            "Target package ID from list_packages (category sms or email). Must differ "
            "from the current package and hold at least the amount already used"
        ),
    )
    redirectUrl: str | None = Field(
        None,
        min_length=1,
        description=(
            "Where the payment gateway returns the user after paying. Leave unset to use "
            "the vMonitor console's own quota page. The upstream API checks this against "
            "an allow-list, so an empty string or an arbitrary URL is rejected with "
            "'redirect URL is incorrect' — only override it with another console URL"
        ),
    )
    pay: bool = Field(
        False,
        description=(
            "true charges the account immediately; false creates the order and returns "
            "a paymentUrl to complete it"
        ),
    )


class SyntheticResource(BaseModel):
    """Generic structured output for a single synthetic/uptime object.

    Surfaces the common ``id`` / ``name`` / ``status`` / ``type`` scalars (this
    host uses snake_case fields) and keeps the full object in ``data`` — used for
    uptime monitors, locations and the config-instructions endpoint whose shapes
    differ.
    """

    id: str = Field("", description="Resource ID")
    name: str = Field("", description="Resource name")
    status: str = Field("", description="Resource status (e.g. ENABLED, REPORTING)")
    type: str = Field("", description="Resource type (e.g. API, PUBLIC)")
    data: dict[str, Any] = Field(default_factory=dict, description="Full raw response object")

    @classmethod
    def from_api(cls, data: Any) -> SyntheticResource:
        """Build a SyntheticResource from a raw uptime-manager object."""
        payload = data if isinstance(data, dict) else {}
        return cls(
            id=str(payload.get("id", "") or ""),
            name=str(payload.get("name", "") or ""),
            status=str(payload.get("status", "") or ""),
            type=str(payload.get("type", "") or ""),
            data=payload,
        )


class SyntheticListData(BaseModel):
    """Generic structured output for a synthetic/uptime list (bare JSON array)."""

    total_item: int = Field(0, description="Number of items")
    items: list[dict[str, Any]] = Field(default_factory=list, description="List items (raw)")

    @classmethod
    def from_api(cls, data: Any) -> SyntheticListData:
        """Build a SyntheticListData from a bare list (or ``{data: [...]}``)."""
        if isinstance(data, dict):
            data = data.get("data", [])
        items = [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []
        return cls(total_item=len(items), items=items)


class CreateUptimeDto(BaseModel):
    """Request body for create_uptime (``POST /uptimes``).

    A synthetic (uptime) monitor periodically probes a target from one or more
    locations. The probe definition (``config`` = assertions + request), the
    scheduling (``options``) and the per-state notification routing
    (``notifications``) are genuinely nested/opaque and pass through as maps.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Monitor name")
    type: str = Field("API", description="Monitor family (currently 'API')")
    subtype: str = Field(..., description="Probe protocol (e.g. HTTP, PING, TCP, SSL)")
    config: dict[str, Any] = Field(
        ..., description="Probe definition: {assertions:[...], request:{url,method,...}}"
    )
    locations: list[str] = Field(..., description="Location IDs to run the probe from")
    options: dict[str, Any] | None = Field(
        None, description="Scheduling: {test_frequency, tests, failed_locations}"
    )
    notifications: dict[str, Any] | None = Field(
        None,
        description="Per-state notification IDs: {'In-alarm':[...],'Up':[...],'Undetermined':[...]}",
    )


class UpdateUptimeDto(BaseModel):
    """Request body for update_uptime (``PUT /uptimes/{id}``) — same shape as create."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Monitor name")
    type: str = Field("API", description="Monitor family (currently 'API')")
    subtype: str = Field(..., description="Probe protocol (e.g. HTTP, PING, TCP, SSL)")
    config: dict[str, Any] = Field(..., description="Probe definition (assertions + request)")
    locations: list[str] = Field(..., description="Location IDs to run the probe from")
    options: dict[str, Any] | None = Field(None, description="Scheduling options")
    notifications: dict[str, Any] | None = Field(None, description="Per-state notification IDs")


class ValidateUptimeDto(BaseModel):
    """Request body for validate_uptime (``POST /uptimes/test``).

    Runs the probe once against the given locations without persisting a monitor,
    to preview the result before create/update.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field("API", description="Monitor family (currently 'API')")
    subtype: str = Field(..., description="Probe protocol (e.g. HTTP, PING, TCP, SSL)")
    config: dict[str, Any] = Field(..., description="Probe definition (assertions + request)")
    locations: list[str] | None = Field(None, description="Location IDs to run the test from")


class CreateLocationDto(BaseModel):
    """Request body for create_location (``POST /locations``).

    A private probing location the user runs (a worker); public locations are
    provided by the platform.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Location name")
    type: str = Field(
        "PRIVATE", description="Location type (PUBLIC provided by platform, PRIVATE self-run)"
    )
    description: str | None = Field(None, description="Optional description")


class UpdateLocationDto(BaseModel):
    """Request body for update_location (``PUT /locations/{id}``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Location name")
    description: str | None = Field(None, description="Optional description")
    type: str | None = Field(None, description="Location type (PUBLIC/PRIVATE)")
