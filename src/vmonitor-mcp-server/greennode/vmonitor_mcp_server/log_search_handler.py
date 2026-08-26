"""Log search/export handler for the vMonitor MCP server (Log API).

These tools query a log project's ingested data: run a structured search, check
whether any data exists, and prepare/track asynchronous exports of search
results.
"""

from __future__ import annotations

import json
from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import ExportLogDto, LogResource, LogSearchDto
from greennode.vmonitor_mcp_server.tool_annotations import READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


class LogSearchHandler:
    """Register and serve vMonitor Log API search/export MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="search_logs", annotations=READ)(self.search_logs)
        self.mcp.tool(name="search_logs_default", annotations=READ)(self.search_logs_default)
        self.mcp.tool(name="get_project_log_data_exists", annotations=READ)(
            self.get_project_log_data_exists
        )
        self.mcp.tool(name="get_log_export", annotations=READ)(self.get_log_export)

        if self.allow_write:
            self.mcp.tool(name="create_log_export", annotations=WRITE)(self.create_log_export)

    _MATCH_ALL_BOOL = {
        "type": "bool",
        "value": {"filter": [], "should": [], "must": [], "mustNot": []},
    }

    _VALID_QUERY_TYPES = frozenset({"bool", "match", "range", "exists"})

    @staticmethod
    def _as_text(data) -> str:
        """Return a log-search response as readable text (raw JSON already is a string)."""
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False, default=str)

    @staticmethod
    def _field_value(body) -> dict:
        """Map an ES ``{field: value}`` / ``{field: {query: value}}`` to ``{field, value}``."""
        if isinstance(body, dict) and len(body) == 1:
            field, value = next(iter(body.items()))
            if isinstance(value, dict):
                value = value.get("query", value.get("value", value))
            return {"field": field, "value": value}
        return body if isinstance(body, dict) else {}

    @classmethod
    def _to_clause(cls, node):
        """Coerce one query node into the vMonitor ``{type, value}`` clause shape.

        The endpoint speaks a small DSL — clause types ``match`` / ``range`` /
        ``exists`` / ``bool`` — not raw Elasticsearch. A first-time agent reaching
        for ES syntax (``{"match": {...}}``, ``{"range": {...}}``, a ``bool`` with
        ``must_not``) otherwise 400s, so the recurring ES shorthands are translated
        to the accepted form. Returns ``None`` to drop a clause (match-all).
        """
        if not isinstance(node, dict) or not node:
            return None
        node_type = node.get("type")
        if node_type is not None:
            if node_type == "match_all":
                return None
            if node_type == "bool":
                return {"type": "bool", "value": cls._bool_value(node.get("value") or {})}
            value = node.get("value")
            return {"type": node_type, "value": value if value is not None else {}}
        if "match_all" in node:
            return None
        if "bool" in node:
            return {"type": "bool", "value": cls._bool_value(node.get("bool") or {})}
        for key in ("match", "match_phrase", "term"):
            if key in node:
                return {"type": "match", "value": cls._field_value(node.get(key) or {})}
        if "range" in node:
            body = node.get("range") or {}
            if isinstance(body, dict) and len(body) == 1:
                field, bounds = next(iter(body.items()))
                out = {"field": field}
                if isinstance(bounds, dict):
                    out.update(bounds)
                return {"type": "range", "value": out}
            return {"type": "range", "value": body if isinstance(body, dict) else {}}
        if "exists" in node:
            body = node.get("exists")
            field = body.get("field") if isinstance(body, dict) else body
            return {"type": "exists", "value": {"field": field}}
        return node

    @classmethod
    def _bool_value(cls, value) -> dict:
        """Fill a bool clause's four arrays, translating nested clauses.

        Maps the ES ``must_not`` key to vMonitor's ``mustNot`` and drops any
        ``match_all`` nested inside (the endpoint rejects it).
        """
        value = value if isinstance(value, dict) else {}
        out: dict = {"filter": [], "should": [], "must": [], "mustNot": []}
        for src, dst in (
            ("filter", "filter"),
            ("should", "should"),
            ("must", "must"),
            ("mustNot", "mustNot"),
            ("must_not", "mustNot"),
        ):
            for clause in value.get(src) or []:
                translated = cls._to_clause(clause)
                if translated is not None:
                    out[dst].append(translated)
        return out

    @classmethod
    def _normalize_query(cls, query) -> dict:
        """Coerce a query into a shape this endpoint accepts.

        Empty / missing / ``match_all`` becomes the match-all empty bool; any
        other query (native vMonitor DSL or an Elasticsearch shorthand) is
        translated via ``_to_clause``.
        """
        clause = cls._to_clause(query) if isinstance(query, dict) and query else None
        if clause is None:
            return dict(cls._MATCH_ALL_BOOL)
        return clause

    @classmethod
    def _normalize_sorts(cls, sorts) -> list:
        """Coerce sort clauses into the required ``{type:"field_sort", value:{field, order}}``.

        Accepts the natural ``{field, order}`` / ``{field, direction}`` shape and
        the ES ``{field: "desc"}`` / ``{field: {order: "desc"}}`` shorthand; a
        clause already in the ``field_sort`` shape passes through. ``order``
        defaults to ``desc`` (newest first) when omitted.
        """
        out: list = []
        for item in sorts or []:
            if not isinstance(item, dict) or not item:
                continue
            if item.get("type") == "field_sort" and isinstance(item.get("value"), dict):
                out.append(item)
                continue
            field = item.get("field") or item.get("name")
            order = item.get("order") or item.get("direction")
            if field is None and "type" not in item and "value" not in item:
                field, spec = next(iter(item.items()))
                order = spec.get("order") if isinstance(spec, dict) else spec
            if field is None:
                out.append(item)
                continue
            out.append(
                {
                    "type": "field_sort",
                    "value": {"field": field, "order": str(order).lower() if order else "desc"},
                }
            )
        return out

    async def search_logs(
        self,
        project_id: str = Field(..., description="Log project ID to search in"),
        body: LogSearchDto = Field(
            ...,
            description=(
                "LogSearchDto body. Optional: query (a `{type, value}` clause — "
                "match/range/exists/bool; omit to match every log), sorts, "
                "aggregations, size, from (result offset)."
            ),
        ),
    ) -> str:
        """Run a structured search over a log project's data (returns raw result JSON).

        `query` is a `{type, value}` clause, NOT raw Elasticsearch. Valid types:
        `match` (value `{field, value}`), `range` (value `{field, gte/lte/gt/lt}`),
        `exists` (value `{field}`), and `bool` (value with `filter`/`must`/`should`/
        `mustNot` arrays of clauses). Omit `query` to match every log (`match_all`
        is not accepted here). Common Elasticsearch shorthands (`{"match": {...}}`,
        `{"range": {...}}`, a `bool` with `must_not`) are translated automatically.

        `sorts` items are `{field, order}` (order `asc`/`desc`, default newest
        first) — e.g. `[{"field": "@timestamp", "order": "desc"}]`.
        """
        validate_id(project_id, "project_id")
        payload = body.model_dump(by_alias=True, exclude_none=True)
        payload["query"] = self._normalize_query(payload.get("query"))
        payload["sorts"] = self._normalize_sorts(payload.get("sorts"))
        data = await self.client.post(f"/v1/projects/{project_id}/search-logs", json=payload)
        return self._as_text(data)

    async def search_logs_default(
        self,
        project_id: str = Field(..., description="Log project ID to search in"),
        body: LogSearchDto = Field(
            ..., description="LogSearchDto body (same shape as search_logs)."
        ),
    ) -> str:
        """Run the default (user) log search variant over a project's data.

        Same query DSL as `search_logs` but a narrower clause set — `match`,
        `range` and `bool` only (this variant rejects `exists`; use `search_logs`
        for an exists clause). Omit the query to match every log (`match_all` is
        not accepted). ES shorthands and `{field, order}` sorts are translated
        automatically.
        """
        validate_id(project_id, "project_id")
        payload = body.model_dump(by_alias=True, exclude_none=True)
        payload["query"] = self._normalize_query(payload.get("query"))
        payload["sorts"] = self._normalize_sorts(payload.get("sorts"))
        data = await self.client.post(
            f"/v1/projects/{project_id}/search-logs/default", json=payload
        )
        return self._as_text(data)

    async def get_project_log_data_exists(
        self,
        project_id: str = Field(..., description="Log project ID to check"),
    ) -> bool:
        """Check whether a log project has any ingested data yet."""
        validate_id(project_id, "project_id")
        data = await self.client.get(f"/v1/projects/{project_id}/exists-log-data")
        return bool(data)

    async def get_log_export(
        self,
        project_id: str = Field(..., description="Log project ID"),
        export_id: str = Field(..., description="Export job ID to check"),
    ) -> LogResource:
        """Get the status/result of a prepared log export."""
        validate_id(project_id, "project_id")
        validate_id(export_id, "export_id")
        data = await self.client.get(f"/v1/projects/{project_id}/log-exports/{export_id}")
        return LogResource.from_api(data)

    async def create_log_export(
        self,
        project_id: str = Field(..., description="Log project ID to export from"),
        body: ExportLogDto = Field(
            ...,
            description="ExportLogDto body. Required: query (same DSL as search_logs). Optional: sorts.",
        ),
    ) -> LogResource:
        """Prepare an asynchronous export of a project's search results.

        Poll get_log_export with the returned id to track completion. `query` uses
        the same `{type, value}` DSL as `search_logs` (match/range/exists/bool);
        an empty/`match_all` query exports everything. ES shorthands and
        `{field, order}` sorts are translated automatically.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(project_id, "project_id")
        payload = body.model_dump(exclude_none=True)
        payload["query"] = self._normalize_query(payload.get("query"))
        payload["sorts"] = self._normalize_sorts(payload.get("sorts"))
        data = await self.client.post(f"/v1/projects/{project_id}/log-exports", json=payload)
        return LogResource.from_api(data)
