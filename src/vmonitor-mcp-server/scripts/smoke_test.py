#!/usr/bin/env python3
"""Automated MCP smoke test for the vMonitor MCP server.

Starts a LOCAL server in read-only mode (no --allow-write), drives the real MCP
protocol over streamable-http (initialize -> initialized -> tools/list ->
tools/call), exercises the read-only tools against the live vMonitor API using
the credentials in ~/.greennode, and prints a PASS/FAIL summary.

Safe by construction: the server is started without --allow-write, so no write
tool is even registered. Access tokens are never printed.

The run is staged: global listings first, then drill-downs that reuse the ids
those listings returned. The dashboard stage walks the "default dashboard ->
list_widgets -> get_statistics_v2" chain, i.e. reading a resource's metrics
straight off its auto-generated dashboard without enabling detailed monitoring.

Usage (from src/vmonitor-mcp-server):
    uv run python scripts/smoke_test.py
    uv run python scripts/smoke_test.py --port 8771

Exit code 0 if every executed tool passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import httpx
import json
import subprocess
import sys
import time


def _parse_sse(text: str) -> dict | None:
    """Extract the first JSON object from an SSE 'data:' line."""
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                continue
    return None


class McpClient:
    """Minimal MCP streamable-http client for smoke testing."""

    def __init__(self, base: str) -> None:
        self._mcp = f"{base}/mcp"
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._sid: str | None = None
        self._client = httpx.Client(timeout=60.0)

    def _post(self, body: dict) -> httpx.Response:
        headers = dict(self._headers)
        if self._sid:
            headers["Mcp-Session-Id"] = self._sid
        return self._client.post(self._mcp, headers=headers, json=body)

    def initialize(self) -> dict:
        """Send initialize + notifications/initialized; capture the session id."""
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke-test", "version": "0"},
                },
            }
        )
        self._sid = response.headers.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return _parse_sse(response.text) or {}

    def list_tools(self) -> list[dict]:
        """Return the tools advertised by the server."""
        response = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return (_parse_sse(response.text) or {}).get("result", {}).get("tools", [])

    def call(self, name: str, arguments: dict) -> dict:
        """Invoke a tool by name and return the parsed JSON-RPC response."""
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return _parse_sse(response.text) or {
            "error": {"message": f"unparseable response (HTTP {response.status_code})"}
        }


def _result_text(response: dict) -> str:
    blocks = response.get("result", {}).get("content", [])
    return " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _structured(response: dict) -> dict:
    """Return the tool's structured result, or an empty dict."""
    value = response.get("result", {}).get("structuredContent")
    return value if isinstance(value, dict) else {}


def _ok(response: dict) -> tuple[bool, str]:
    """Return (passed, short_note). A JSON-RPC error or isError result fails."""
    if "error" in response:
        return False, str(response["error"].get("message", response["error"]))[:120]
    result = response.get("result", {})
    if result.get("isError"):
        return False, _result_text(response)[:120]
    return True, ""


def _first_item(response: dict) -> dict:
    """Return the first entry of a list tool's `items`, or an empty dict."""
    items = _structured(response).get("items")
    return items[0] if isinstance(items, list) and items else {}


def _first_metric_name(response: dict) -> str | None:
    """Return the first metric name from get_metric_names (items are bare strings)."""
    for item in _structured(response).get("items") or []:
        if isinstance(item, str) and item:
            return item
        if isinstance(item, dict) and item.get("name"):
            return item["name"]
    return None


def _window(hours: int = 1) -> tuple[str, str]:
    """Return an (start, end) epoch-millis window ending now, as strings."""
    now = int(time.time() * 1000)
    return str(now - hours * 3600_000), str(now)


class SmokeRun:
    """Drive the read-only tool surface and collect a PASS/FAIL/SKIP result row per tool."""

    def __init__(self, client: McpClient, tool_names: set[str]) -> None:
        self.client = client
        self.tool_names = tool_names
        self.results: list[tuple[str, str, str]] = []

    def run(self, name: str, arguments: dict | None = None) -> dict:
        """Call one tool, record its outcome, and return the raw response."""
        if name not in self.tool_names:
            self.results.append((name, "SKIP", "not registered"))
            return {}
        response = self.client.call(name, arguments or {})
        passed, note = _ok(response)
        if passed:
            structured = _structured(response)
            if "total_item" in structured:
                note = f"{structured['total_item']} item(s)"
            elif structured:
                note = f"[{len(structured)} key(s)]"
        self.results.append((name, "PASS" if passed else "FAIL", note))
        return response

    def skip(self, name: str, reason: str) -> None:
        """Record a tool as skipped because its input could not be discovered."""
        self.results.append((name, "SKIP", reason))

    def report(self) -> int:
        """Print the summary table; return 0 when nothing failed."""
        print("\n==================== SMOKE TEST RESULTS ====================")
        width = max(len(n) for n, _, _ in self.results)
        passed = failed = skipped = 0
        for name, status, note in self.results:
            mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "–"}[status]
            print(f"  {mark} {name.ljust(width)}  {status}{f'  {note}' if note else ''}")
            passed += status == "PASS"
            failed += status == "FAIL"
            skipped += status == "SKIP"
        print("-----------------------------------------------------------")
        print(f"  {passed} passed, {failed} failed, {skipped} skipped")
        print("===========================================================")
        print(
            "\nNote: a tool may FAIL with IAM_PERMISSION_DENIED when the calling\n"
            "identity's policy does not cover that endpoint, or when the account\n"
            "has no quota for that feature. That is an account state, not a server\n"
            "defect — see CLAUDE.md."
        )
        return 0 if failed == 0 else 1


def _stage_global(run: SmokeRun) -> dict:
    """Run the argument-free listings; return the ids later stages drill into."""
    print("==> stage 1: global listings")
    start, end = _window()
    paged = {"page": 1, "size": 5}

    dashboards = run.run("list_dashboards", {"page": 1, "size": 5})
    hosts = run.run("list_vserver_hosts", paged)
    run.run("list_hosts", paged)
    metrics = run.run("get_metric_names", {"start_time": start, "end_time": end})
    run.run("list_metric_dimension_names", {"start_time": start, "end_time": end})
    run.run("list_metric_units")
    run.run("list_metric_unit_mappings")
    alarms = run.run("list_alarms", paged)
    run.run("list_notifications", paged)
    run.run("list_notification_types")
    run.run("list_integrations", paged)
    run.run("list_metric_api_keys")
    projects = run.run("list_projects", paged)
    run.run("list_uptimes")
    run.run("list_locations")
    run.run("list_pipelines", paged)
    run.run("get_current_quota", {"category": "metric"})
    run.run("get_quota_usage", {"category": "metric"})

    return {
        "dashboard": _first_item(dashboards),
        "host": _first_item(hosts),
        "alarm": _first_item(alarms),
        "project": _first_item(projects),
        "metric_name": _first_metric_name(metrics),
    }


def _stage_dashboard(run: SmokeRun, ctx: dict) -> None:
    """Walk dashboard -> widgets -> the widget's own metric query."""
    dashboard_id = ctx["dashboard"].get("id")
    if not dashboard_id:
        for name in ("get_dashboard", "list_widgets", "get_widget", "get_statistics_v2"):
            run.skip(name, "no dashboard found to drill into")
        return

    print(f"==> stage 2: dashboard {dashboard_id}")
    run.run("get_dashboard", {"dashboard_id": dashboard_id})
    run.run("list_dashboard_variables", {"dashboard_id": dashboard_id})
    run.run("list_dashboard_views", {"dashboard_id": dashboard_id})
    widgets = run.run("list_widgets", {"dashboard_id": dashboard_id})

    items = _structured(widgets).get("items") or []
    widget = next((w for w in items if w.get("metric_queries")), items[0] if items else None)
    if not widget:
        run.skip("get_widget", "dashboard has no widgets")
        run.skip("get_statistics_v2", "dashboard has no widgets")
        return

    run.run("get_widget", {"dashboard_id": dashboard_id, "widget_id": widget["id"]})

    queries = widget.get("metric_queries") or []
    if not queries:
        run.skip("get_statistics_v2", "widget plots log data, not metrics")
        return

    # The point of list_widgets: this query needs no discovery and no detailed
    # monitoring — it is exactly what the dashboard already plots.
    query = queries[0]
    start, end = _window()
    print(f"    replaying widget query {query['metric_name']} via get_statistics_v2")
    run.run(
        "get_statistics_v2",
        {
            "body": {
                "type": "SIMPLE",
                "data": {
                    "graph": {
                        "name": query["metric_name"],
                        "statistics": query["statistic"] or "avg",
                        "dimensions": query["dimensions"],
                        "group_by": query["group_by"] or "none",
                        "offset": 0,
                        "limit": "",
                        "rollup": "",
                        "rate": 0,
                    },
                    "start_time": int(start),
                    "end_time": int(end),
                    "period": widget.get("period") or 60,
                    "alarm": False,
                },
            }
        },
    )


def _stage_drilldowns(run: SmokeRun, ctx: dict) -> None:
    """Drill into the first host, metric, alarm and log project discovered."""
    print("==> stage 3: per-resource drill-downs")
    start, end = _window()

    host_id = ctx["host"].get("id")
    if host_id:
        run.run("get_vserver_host_metrics", {"host_id": host_id})
    else:
        run.skip("get_vserver_host_metrics", "no vserver host found")

    metric_name = ctx["metric_name"]
    if metric_name:
        run.run(
            "get_metric_dimensions",
            {"name": metric_name, "start_time": start, "end_time": end},
        )
        run.run("get_statistics", {"name": metric_name, "statistics": "avg"})
    else:
        run.skip("get_metric_dimensions", "metric catalogue empty in this window")
        run.skip("get_statistics", "metric catalogue empty in this window")

    alarm_id = ctx["alarm"].get("id")
    if alarm_id:
        run.run("get_alarm", {"alarm_id": alarm_id})
    else:
        run.skip("get_alarm", "no alarm to read")

    project_id = ctx["project"].get("id")
    if project_id:
        run.run("get_project", {"project_id": project_id})
        run.run("get_project_log_data_exists", {"project_id": project_id})
    else:
        run.skip("get_project", "no log project to read")
        run.skip("get_project_log_data_exists", "no log project to read")


ORDER_TOOLS = (
    "create_log_project",
    "resize_log_project",
    "delete_log_project",
    "resize_metric_quota",
    "resize_sms_quota",
    "resize_email_quota",
)


def _active_retention(response: dict, class_name: str) -> dict:
    """Return the first retention entry of a named quota class, or an empty dict."""
    for item in _structured(response).get("items") or []:
        if item.get("name") == class_name and item.get("status") == "ACTIVE":
            retentions = (item.get("config") or {}).get("retentions") or []
            if retentions:
                return retentions[0]
    return {}


def _stage_billing(run: SmokeRun) -> None:
    """Price every order the write tools can place — without placing one.

    The v2 price endpoints take the same body as the order tools, so a green
    stage here says the discovery chain (quota class -> retention -> packageId
    -> quantity) produces payloads the billing API accepts.
    """
    print("==> stage 4: billing pre-flight (quotes only, no order)")

    log_classes = run.run("list_quota_classes", {"category": "log"})
    retention = _active_retention(log_classes, "Pro")
    if retention.get("packageId") and retention.get("minSize") and retention.get("amount"):
        quantity = int(retention["minSize"]) * int(retention["amount"])
        print(
            f"    pricing a new log project: {retention['minSize']} GB/day x "
            f"{retention['amount']} d = {quantity} GB-days"
        )
        run.run(
            "get_creation_price",
            {"category": "log", "package_id": retention["packageId"], "quantity": quantity},
        )
    else:
        run.skip("get_creation_price", "no active Pro log retention to price")

    quotas = _structured(run.run("list_log_quotas")).get("items") or []
    priced = False
    for quota in quotas:
        quota_id = quota.get("id")
        if not quota_id:
            continue
        current = (
            _structured(
                run.run("get_quota_detail", {"category": "log", "resource_id": quota_id})
            ).get("data")
            or {}
        )
        if not (current.get("packageId") and current.get("size")):
            continue
        run.run(
            "get_resize_price",
            {
                "category": "log",
                "package_id": current["packageId"],
                "resource_id": quota_id,
                "quantity": int(current["size"]),
            },
        )
        priced = True
        break
    if not priced:
        run.skip("get_resize_price", "no log quota with a packageId + size to price")

    metric_quota = _structured(run.run("get_current_quota", {"category": "metric"}))
    metric_id = metric_quota.get("id")
    metric_detail = (
        _structured(
            run.run("get_quota_detail", {"category": "metric", "resource_id": metric_id})
        ).get("data")
        or {}
        if metric_id
        else {}
    )
    if metric_detail.get("packageId") and metric_detail.get("host"):
        run.run(
            "get_resize_price",
            {
                "category": "metric",
                "package_id": metric_detail["packageId"],
                "resource_id": metric_id,
                "quantity": int(metric_detail["host"]),
            },
        )
    else:
        run.skip("get_resize_price", "no metric quota detail to price")

    for category in ("sms", "email"):
        packages = run.run("list_packages", {"category": category})
        package = _first_item(packages)
        current = _structured(run.run("get_current_quota", {"category": category}))
        if package.get("id") and current.get("id"):
            run.run(
                "get_resize_price",
                {
                    "category": category,
                    "package_id": package["id"],
                    "resource_id": current["id"],
                    "quantity": 1,
                },
            )
        else:
            run.skip("get_resize_price", f"no {category} quota/package to price")

    missing = [name for name in ORDER_TOOLS if name in run.tool_names]
    status = "FAIL" if missing else "PASS"
    run.results.append(
        (
            "order tools withheld (read-only)",
            status,
            f"exposed: {missing}" if missing else f"{len(ORDER_TOOLS)} tools need --allow-write",
        )
    )


def main() -> int:
    """Start a local server, drive read-only tools, and print a PASS/FAIL summary."""
    parser = argparse.ArgumentParser(description="vMonitor MCP server smoke test")
    parser.add_argument("--port", type=int, default=8771)
    options = parser.parse_args()

    base = f"http://127.0.0.1:{options.port}"
    print(f"==> Starting local server (read-only) on {base}")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "greennode.vmonitor_mcp_server.server",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(options.port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        ready = False
        with httpx.Client(timeout=5.0) as probe:
            for _ in range(30):
                try:
                    if probe.get(f"{base}/health").status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        if not ready:
            print("ERROR: server did not become ready", file=sys.stderr)
            return 1

        client = McpClient(base)
        init = client.initialize()
        info = init.get("result", {}).get("serverInfo", {})
        print(f"==> initialize OK: {info.get('name')} v{info.get('version')}")

        tools = client.list_tools()
        print(f"==> tools/list: {len(tools)} read-only tools")

        run = SmokeRun(client, {t["name"] for t in tools})
        ctx = _stage_global(run)
        _stage_dashboard(run, ctx)
        _stage_drilldowns(run, ctx)
        _stage_billing(run)
        return run.report()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
