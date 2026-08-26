"""Alarm handler for the vMonitor MCP server.

vMonitor alarms come in two families handled here — **metric** alarms (evaluate a
metric statistic against a threshold; also a *synthetic* variant) and **log**
alarms (evaluate a log query's aggregate). Change-detection alarms live in
``change_alarm_handler``. These tools list, read, create, edit and delete alarms,
and read their upstream definitions, histories and status.

- Read: ``list_alarms``, ``get_alarm``,
  ``get_metric_alarm_definition``, ``list_metric_alarm_histories``,
  ``get_synthetic_alarm_definition``, ``list_synthetic_alarm_histories``,
  ``list_log_alarm_histories``, ``get_log_alarm_status``.
- Write: ``create_metric_alarm``, ``update_metric_alarm``, ``delete_metric_alarm``,
  ``delete_metric_sub_alarm``, ``create_log_alarm``, ``update_log_alarm``,
  ``delete_log_alarm``.
"""

from __future__ import annotations

import time
from greennode.vmonitor_mcp_server.client import VmonitorClient, VmonitorNotificationClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    AlarmDefinitionData,
    AlarmDetail,
    AlarmHistoryData,
    AlarmListData,
    AlarmSummary,
    CreateLogAlarmDto,
    CreateMetricAlarmDto,
    LogAlarmHistoryData,
    LogAlarmStatus,
    UpdateLogAlarmDto,
    UpdateMetricAlarmDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any, Literal


_MATCH_ALL_LOG_FILTER = {"type": "match_all", "value": {}}

_ACTION_FIELDS = ("inAlarm", "ok", "undetermined")


class AlarmHandler:
    """Register and serve vMonitor alarm MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_alarms", annotations=READ)(self.list_alarms)
        self.mcp.tool(name="get_alarm", annotations=READ)(self.get_alarm)
        self.mcp.tool(name="get_metric_alarm_definition", annotations=READ)(
            self.get_metric_alarm_definition
        )
        self.mcp.tool(name="list_metric_alarm_histories", annotations=READ)(
            self.list_metric_alarm_histories
        )
        self.mcp.tool(name="get_synthetic_alarm_definition", annotations=READ)(
            self.get_synthetic_alarm_definition
        )
        self.mcp.tool(name="list_synthetic_alarm_histories", annotations=READ)(
            self.list_synthetic_alarm_histories
        )
        self.mcp.tool(name="list_log_alarm_histories", annotations=READ)(
            self.list_log_alarm_histories
        )
        self.mcp.tool(name="get_log_alarm_status", annotations=READ)(self.get_log_alarm_status)

        if self.allow_write:
            self.mcp.tool(name="create_metric_alarm", annotations=WRITE)(self.create_metric_alarm)
            self.mcp.tool(name="update_metric_alarm", annotations=WRITE)(self.update_metric_alarm)
            self.mcp.tool(name="delete_metric_alarm", annotations=DESTRUCTIVE)(
                self.delete_metric_alarm
            )
            self.mcp.tool(name="delete_metric_sub_alarm", annotations=DESTRUCTIVE)(
                self.delete_metric_sub_alarm
            )
            self.mcp.tool(name="create_log_alarm", annotations=WRITE)(self.create_log_alarm)
            self.mcp.tool(name="update_log_alarm", annotations=WRITE)(self.update_log_alarm)
            self.mcp.tool(name="delete_log_alarm", annotations=DESTRUCTIVE)(self.delete_log_alarm)

    async def _notification_mapping(self) -> dict[str, str]:
        """Map each notification channel `id` to its `metricMappingId` (best-effort).

        The alarm-action fields (inAlarm / ok / undetermined) require a channel's
        `metricMappingId`, not its `id`; passing the id makes the backend answer a
        misleading 500 ("Internal server error"). Returns {} if the lookup fails so
        alarm creation is never blocked by this convenience.
        """
        try:
            client = VmonitorNotificationClient(self.config, self.client._token_manager)
            data = await client.get(
                "/v1/notification/list/typeSearch",
                params={"searchtext": "", "field": "", "type": "", "page": 1, "size": 200},
            )
        except Exception:
            return {}
        items = data.get("lstData", []) if isinstance(data, dict) else []
        mapping: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            channel_id = str(item.get("id", "") or "")
            mapping_id = str(item.get("metricMappingId", "") or "")
            if channel_id and mapping_id:
                mapping[channel_id] = mapping_id
        return mapping

    async def _resolve_notification_actions(self, payload: dict) -> None:
        """Rewrite channel ids in inAlarm/ok/undetermined to their metricMappingId.

        A first-time agent naturally passes the `id` from list_notifications, but
        the alarm API needs the channel's `metricMappingId` (a raw id 500s). Each
        comma-separated token that matches a known channel id is swapped for its
        mapping id; a token that is already a mapping id (or unknown) is left as-is.
        No-op when no action field is set or the lookup fails.
        """
        if not any(payload.get(field) for field in _ACTION_FIELDS):
            return
        mapping = await self._notification_mapping()
        if not mapping:
            return
        for field in _ACTION_FIELDS:
            raw = payload.get(field)
            if not isinstance(raw, str) or not raw:
                continue
            resolved = []
            for token in raw.split(","):
                stripped = token.strip()
                resolved.append(mapping.get(stripped, stripped) if stripped else "")
            payload[field] = ",".join(resolved)

    async def _fetch_alarms(
        self,
        name: str | None,
        severity: str | None,
        status: str | None,
        type_alarm: str | None,
        page: int | None,
        size: int | None,
    ) -> dict:
        """Fetch one alarm-list page. name/status/severity are always sent.

        The upstream endpoint returns 500 ("List alarm by userId is failed")
        unless name, status and severity are present, so they default to an
        empty/`any` (no-op) filter rather than being omitted.
        """
        params: dict[str, Any] = {
            "name": name or "",
            "status": status or "any",
            "severity": severity or "any",
        }
        if type_alarm:
            params["type-alarm"] = type_alarm
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size
        data = await self.client.get("/api/v1/alarms/list", params=params)
        return data if isinstance(data, dict) else {}

    async def list_alarms(
        self,
        name: str | None = Field(None, description="Filter alarms by name (partial match)"),
        severity: str | None = Field(
            None, description="Filter by severity (Low, Medium, High); omit for any"
        ),
        status: str | None = Field(
            None, description="Filter by current state (OK, ALARM, UNDETERMINED); omit for any"
        ),
        type_alarm: Literal["Metric", "Log", "Change"] | None = Field(
            None,
            description=(
                "Filter by alarm type. Omit to list all three types (Metric, Log, "
                "Change) merged — the API does not aggregate types on its own."
            ),
        ),
        page: int | None = Field(
            None,
            ge=1,
            description="1-based page number (only when type_alarm is set). Omit to return all.",
        ),
        size: int | None = Field(
            None, ge=1, description="Page size. Only applies when type_alarm and page are set."
        ),
    ) -> AlarmListData:
        """List alarms with optional name/severity/status/type filters.

        When ``type_alarm`` is set, returns that type's paged result. When it is
        omitted, the three alarm types (Metric, Log, Change) are fetched and
        merged into one list, because the endpoint requires a type filter and
        does not return a cross-type set on its own.
        """
        if type_alarm is not None:
            data = await self._fetch_alarms(name, severity, status, type_alarm, page, size)
            return AlarmListData.from_api(data)

        merged: list[AlarmSummary] = []
        for a_type in ("Metric", "Log", "Change"):
            data = await self._fetch_alarms(name, severity, status, a_type, None, None)
            merged.extend(AlarmSummary.from_api(i) for i in data.get("lstData", []))
        return AlarmListData(
            page=0,
            page_size=0,
            total_item=len(merged),
            total_page=1 if merged else 0,
            items=merged,
        )

    async def get_alarm(
        self,
        alarm_id: str = Field(..., description="Alarm ID to retrieve"),
    ) -> AlarmDetail:
        """Get a single alarm by ID, including its type-specific configuration."""
        validate_id(alarm_id, "alarm_id")
        data = await self.client.get(f"/api/v1/alarms/{alarm_id}")
        return AlarmDetail.from_api(data)

    async def get_metric_alarm_definition(
        self,
        alarm_id: str = Field(..., description="Metric alarm ID"),
    ) -> AlarmDefinitionData:
        """Get a metric alarm's upstream evaluator definition."""
        validate_id(alarm_id, "alarm_id")
        data = await self.client.get(f"/api/v1/alarms/metrics/mona/{alarm_id}")
        return AlarmDefinitionData.from_api(data)

    async def list_metric_alarm_histories(
        self,
        alarm_id: str = Field(
            ...,
            description=(
                "Evaluator sub-alarm ID (an `alarms[].id` from "
                "get_metric_alarm_definition), NOT the vMonitor alarm ID."
            ),
        ),
        start_time: str | None = Field(
            None, description="Window start (epoch millis or ISO-8601). Default: 7 days ago."
        ),
        end_time: str | None = Field(
            None, description="Window end (epoch millis or ISO-8601). Default: now."
        ),
        interval: str | None = Field(None, description="Bucket interval for the history"),
    ) -> AlarmHistoryData:
        """List a metric alarm's state-transition history over a time window.

        The `alarm_id` here is the upstream evaluator sub-alarm id — first call
        get_metric_alarm_definition with the vMonitor alarm id, then pass one of
        the returned `alarms[].id` values here. The endpoint returns 500 unless
        start_time, end_time and interval are all present, so start/end default to
        the last 7 days and interval defaults to 0 when not supplied.
        """
        validate_id(alarm_id, "alarm_id")
        now_ms = int(time.time() * 1000)
        params: dict[str, Any] = {
            "start_time": start_time if start_time else str(now_ms - 7 * 24 * 3600 * 1000),
            "end_time": end_time if end_time else str(now_ms),
            "interval": interval if interval else "0",
        }
        data = await self.client.get(
            f"/api/v1/alarms/metrics/mona/{alarm_id}/histories", params=params
        )
        return AlarmHistoryData.from_api(data)

    async def get_synthetic_alarm_definition(
        self,
        alarm_id: str = Field(..., description="Synthetic metric alarm ID"),
    ) -> AlarmDefinitionData:
        """Get a synthetic metric alarm's upstream evaluator definition."""
        validate_id(alarm_id, "alarm_id")
        data = await self.client.get(f"/api/v1/alarms/synthetic/metrics/mona/{alarm_id}")
        return AlarmDefinitionData.from_api(data)

    async def list_synthetic_alarm_histories(
        self,
        alarm_id: str = Field(
            ...,
            description=(
                "Evaluator sub-alarm ID (an `alarms[].id` from "
                "get_synthetic_alarm_definition), NOT the vMonitor alarm ID."
            ),
        ),
        start_time: str | None = Field(
            None, description="Window start (epoch millis or ISO-8601). Default: 7 days ago."
        ),
        end_time: str | None = Field(
            None, description="Window end (epoch millis or ISO-8601). Default: now."
        ),
        interval: str | None = Field(None, description="Bucket interval for the history"),
    ) -> AlarmHistoryData:
        """List a synthetic metric alarm's state-transition history over a time window.

        The `alarm_id` here is the upstream evaluator sub-alarm id — first call
        get_synthetic_alarm_definition with the vMonitor alarm id, then pass one of
        the returned `alarms[].id` values here. The endpoint returns 500 unless
        start_time, end_time and interval are all present, so start/end default to
        the last 7 days and interval defaults to 0 when not supplied.
        """
        validate_id(alarm_id, "alarm_id")
        now_ms = int(time.time() * 1000)
        params: dict[str, Any] = {
            "start_time": start_time if start_time else str(now_ms - 7 * 24 * 3600 * 1000),
            "end_time": end_time if end_time else str(now_ms),
            "interval": interval if interval else "0",
        }
        data = await self.client.get(
            f"/api/v1/alarms/synthetic/metrics/mona/{alarm_id}/histories", params=params
        )
        return AlarmHistoryData.from_api(data)

    async def list_log_alarm_histories(
        self,
        alarm_id: str = Field(..., description="Log alarm ID"),
        start: str | None = Field(
            None, description="Window start (epoch millis or ISO-8601). Default: 7 days ago."
        ),
        end: str | None = Field(
            None, description="Window end (epoch millis or ISO-8601). Default: now."
        ),
        order: str = Field("desc", description="Sort order (asc or desc)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        length: int = Field(100, ge=1, description="Number of entries per page"),
    ) -> LogAlarmHistoryData:
        """List a log alarm's history entries over a time window.

        The upstream endpoint returns 500 unless start, end, order, page and len
        are all present, so start/end default to the last 7 days and the paging /
        order params always default when not supplied.
        """
        validate_id(alarm_id, "alarm_id")
        now_ms = int(time.time() * 1000)
        params: dict[str, Any] = {
            "start": start if start else str(now_ms - 7 * 24 * 3600 * 1000),
            "end": end if end else str(now_ms),
            "order": order,
            "page": page,
            "len": length,
        }
        data = await self.client.get(
            f"/api/v1/alarms/logs/butler/{alarm_id}/histories", params=params
        )
        return LogAlarmHistoryData.from_api(data)

    async def get_log_alarm_status(
        self,
        alarm_id: str = Field(..., description="Log alarm ID"),
    ) -> LogAlarmStatus:
        """Get a log alarm's current status."""
        validate_id(alarm_id, "alarm_id")
        data = await self.client.get(f"/api/v1/alarms/logs/butler/{alarm_id}/status")
        return LogAlarmStatus.from_api(data)

    async def create_metric_alarm(
        self,
        body: CreateMetricAlarmDto = Field(
            ...,
            description=(
                "CreateMetricAlarmDto body. Required: name, metricName, "
                "metricStatistic, condition, severity. Add thresholdValue for a "
                "static threshold; metricPeriod/interval/checkTime default."
            ),
        ),
    ) -> AlarmSummary:
        """Create a metric alarm (evaluate a metric statistic against a threshold).

        A minimal alarm needs `name`, `metricName`, `metricStatistic`, `condition`,
        `severity` (plus `thresholdValue` for a static threshold); the evaluation
        timing (`metricPeriod`/`interval`/`checkTime`) defaults. `severity` is
        `LOW`/`MEDIUM`/`HIGH` (this product has no `CRITICAL` tier) and `condition`
        is `gt`/`gte`/`lt`/`lte` — both accepted case-insensitively (and `condition`
        also accepts the symbols `>`/`>=`/`<`/`<=`), normalised to the wire form.

        The evaluator fields the API needs are filled for you so the first
        reasonable payload creates a WORKING alarm: `formula="a"` (single metric
        graph), `thresholdMethod="static"`, `metricProduct=""` (agent/custom
        metrics), `metricGroupBy="none"`, `metricFilter={}` and `timeshift` =
        `-metricPeriod`. Scope the alarm to one resource by passing
        `metricFilter={"resource_id": "ins-..."}`. Route alerts by setting
        `inAlarm`/`ok`/`undetermined` to a channel from list_notifications — the
        API needs its **`metric_mapping_id`**, not its `id` (a raw channel id makes
        the backend answer a misleading 500). The server auto-resolves a channel
        `id` to its `metric_mapping_id`, so either value works here.

        ## Requirements
        - Server must run with --allow-write
        """
        payload = body.model_dump(exclude_none=True)
        payload.setdefault("formula", "a")
        payload.setdefault("thresholdMethod", "static")
        payload.setdefault("metricProduct", "")
        payload.setdefault("metricGroupBy", "none")
        payload.setdefault("metricFilter", {})
        payload.setdefault("timeshift", f"-{payload.get('metricPeriod', 60)}")
        await self._resolve_notification_actions(payload)
        data = await self.client.post("/api/v1/alarms/metrics", json=payload)
        return AlarmSummary.from_api(data)

    async def update_metric_alarm(
        self,
        alarm_id: str = Field(..., description="Metric alarm ID to update"),
        body: UpdateMetricAlarmDto = Field(
            ...,
            description="UpdateMetricAlarmDto body. All fields optional; id comes from the path.",
        ),
    ) -> str:
        """Update a metric alarm's thresholds, timing, notifications or metadata.

        Full-object replace, not a partial patch: a thin body is rejected (e.g.
        `Missing field severity`). Read the alarm first with get_alarm, then resend
        the complete definition (name, severity, metricProduct, metricPeriod,
        thresholdMethod, condition, thresholdValue, checkTime, interval) with your
        edits applied. Same lower-case enum forms as create_metric_alarm.

        ## Requirements
        - Server must run with --allow-write
        - Send the full definition (read-modify-write), not just the changed field.
        """
        validate_id(alarm_id, "alarm_id")
        payload = body.model_dump(exclude_none=True)
        payload["id"] = alarm_id
        await self._resolve_notification_actions(payload)
        await self.client.put(f"/api/v1/alarms/metrics/{alarm_id}", json=payload)
        return f"Metric alarm {alarm_id} updated."

    async def delete_metric_alarm(
        self,
        alarm_id: str = Field(..., description="Metric alarm ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a metric alarm. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_alarm first; deletion cannot be undone.
        """
        validate_id(alarm_id, "alarm_id")
        await self.client.delete(f"/api/v1/alarms/metrics/{alarm_id}")
        return f"Metric alarm {alarm_id} deleted."

    async def delete_metric_sub_alarm(
        self,
        alarm_id: str = Field(..., description="Sub-alarm ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a metric sub-alarm from the upstream evaluator. IRREVERSIBLE.

        A sub-alarm is one component of a composite metric alarm's expression.

        ## Requirements
        - Server must run with --allow-write
        - Deletion cannot be undone.
        """
        validate_id(alarm_id, "alarm_id")
        await self.client.delete(f"/api/v1/alarms/metrics/sub-alarms/{alarm_id}")
        return f"Metric sub-alarm {alarm_id} deleted."

    async def create_log_alarm(
        self,
        body: CreateLogAlarmDto = Field(
            ...,
            description=(
                "CreateLogAlarmDto body. Required: name. Supply the log source "
                "(logProjectId/queryString), aggregation (metricAggType/metricAggKey) "
                "and threshold."
            ),
        ),
    ) -> AlarmSummary:
        """Create a log alarm (evaluate a log query's aggregate against a threshold).

        Enum values (verified live): `thresholdType` is snake_case — `frequency`
        (count of matching logs), `flatline`, or `metric_aggregation` (aggregate a
        numeric field; also send `metricAggType` + `metricAggKey`). `condition` is
        lower-case — `gt`, `gte`, `lt`, `lte`; `severity` is UPPER-CASE — `LOW`,
        `MEDIUM`, `HIGH` (no `CRITICAL` tier; a title-case value 400s). `name` must
        be 5-255 chars with no spaces/illegal characters.

        `filter` is a required Elasticsearch-style query object (`{type, value}`);
        omit it and it defaults to match-all (`{"type":"match_all","value":{}}`) so
        the alarm evaluates every log in the project. A null/absent filter makes
        the API answer 500 ("Creating alarm log failed") — this default avoids that.

        Route alerts via `inAlarm`/`ok` using a channel's **`metric_mapping_id`**
        from list_notifications (a raw channel `id` 500s); the server auto-resolves
        a channel `id` to its `metric_mapping_id`, so either value works.

        ## Requirements
        - Server must run with --allow-write
        - `name` is required; the log source + aggregation + threshold define what fires.
        """
        payload = body.model_dump(exclude_none=True)
        payload.setdefault("filter", dict(_MATCH_ALL_LOG_FILTER))
        await self._resolve_notification_actions(payload)
        data = await self.client.post("/api/v1/alarms/logs", json=payload)
        return AlarmSummary.from_api(data)

    async def update_log_alarm(
        self,
        alarm_id: str = Field(..., description="Log alarm ID to update"),
        body: UpdateLogAlarmDto = Field(
            ...,
            description="UpdateLogAlarmDto body. All fields optional; id comes from the path.",
        ),
    ) -> str:
        """Update a log alarm's query, aggregation, threshold, notifications or metadata.

        Full-object replace, not a partial patch: the API rejects a thin body
        (e.g. `Missing field logProjectId`). Read the alarm first (list_alarms
        with type_alarm=Log exposes its `alarmLog`), then resend the complete
        definition — logProjectId, thresholdType, condition, timeFrame,
        thresholdValue and `filter` — with your edits applied. `filter` follows
        the same rule as create (defaults to match-all when omitted).

        ## Requirements
        - Server must run with --allow-write
        - Send the full definition (read-modify-write), not just the changed field.
        """
        validate_id(alarm_id, "alarm_id")
        payload = body.model_dump(exclude_none=True)
        payload["id"] = alarm_id
        payload.setdefault("filter", dict(_MATCH_ALL_LOG_FILTER))
        await self._resolve_notification_actions(payload)
        await self.client.put(f"/api/v1/alarms/logs/{alarm_id}", json=payload)
        return f"Log alarm {alarm_id} updated."

    async def delete_log_alarm(
        self,
        alarm_id: str = Field(..., description="Log alarm ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a log alarm. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_alarm first; deletion cannot be undone.
        """
        validate_id(alarm_id, "alarm_id")
        await self.client.delete(f"/api/v1/alarms/logs/{alarm_id}")
        return f"Log alarm {alarm_id} deleted."
