#!/usr/bin/env python3
"""Automated MCP smoke test for the VKS MCP server.

Starts a LOCAL server in read-only mode (no --allow-write), drives the real MCP
protocol over streamable-http (initialize -> initialized -> tools/list ->
tools/call), exercises the read-only tools against the live VKS API using the
credentials in ~/.greennode, and prints a PASS/FAIL summary.

Safe: never calls write tools, never prints access tokens or kubeconfig content.

Usage (from src/vks-mcp-server):
    uv run python scripts/smoke_test.py
    uv run python scripts/smoke_test.py --port 8769 --region HCM-3

Exit code 0 if all executed tools pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import httpx
import json
import re
import subprocess
import sys
import time


# Tools whose output may contain secrets — verify success but never print content.
_SENSITIVE = {"get_access_token", "cluster_get_kubeconfig"}


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
        self._client = httpx.Client(timeout=30.0)

    def _post(self, body: dict) -> httpx.Response:
        headers = dict(self._headers)
        if self._sid:
            headers["Mcp-Session-Id"] = self._sid
        return self._client.post(self._mcp, headers=headers, json=body)

    def initialize(self) -> dict:
        """Send initialize + notifications/initialized; capture the session id."""
        r = self._post(
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
        self._sid = r.headers.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return _parse_sse(r.text) or {}

    def list_tools(self) -> list[dict]:
        """Return the tools advertised by the server."""
        r = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return (_parse_sse(r.text) or {}).get("result", {}).get("tools", [])

    def call(self, name: str, arguments: dict) -> dict:
        """Invoke a tool by name and return the parsed JSON-RPC response."""
        r = self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return _parse_sse(r.text) or {
            "error": {"message": f"unparseable response (HTTP {r.status_code})"}
        }


def _result_text(resp: dict) -> str:
    blocks = resp.get("result", {}).get("content", [])
    return " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _ok(resp: dict) -> tuple[bool, str]:
    """Return (passed, short_note). A JSON-RPC error or isError result fails."""
    if "error" in resp:
        return False, str(resp["error"].get("message", resp["error"]))[:120]
    result = resp.get("result", {})
    if result.get("isError"):
        return False, _result_text(resp)[:120]
    return True, ""


def _fill_args(tool: dict, ctx: dict) -> dict | None:
    """Best-effort fill required args from context (cluster_id/region). None = can't."""
    schema = tool.get("inputSchema", {}) or {}
    required = schema.get("required", []) or []
    args: dict = {}
    for prop in required:
        low = prop.lower()
        if "region" in low:
            args[prop] = ctx["region"]
        elif "nodegroup" in low or "node_group" in low or low in ("node_group_id", "nodegroupid"):
            if not ctx.get("nodegroup_id"):
                return None
            args[prop] = ctx["nodegroup_id"]
        elif "cluster" in low or low in ("id", "cluster_id", "clusterid"):
            if not ctx.get("cluster_id"):
                return None
            args[prop] = ctx["cluster_id"]
        else:
            return None  # unknown required arg -> skip rather than guess
    return args


def main() -> int:
    """Start a local server, drive read-only tools, and print a PASS/FAIL summary."""
    ap = argparse.ArgumentParser(description="VKS MCP server smoke test")
    ap.add_argument("--port", type=int, default=8769)
    ap.add_argument("--region", default="HCM-3")
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    print(f"==> Starting local server (read-only) on {base}")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "greennode.vks_mcp_server.server",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        # Wait for readiness via /health.
        ready = False
        with httpx.Client(timeout=5.0) as hc:
            for _ in range(30):
                try:
                    if hc.get(f"{base}/health").status_code == 200:
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
        server_info = init.get("result", {}).get("serverInfo", {})
        print(f"==> initialize OK: {server_info.get('name')} v{server_info.get('version')}")

        tools = client.list_tools()
        by_name = {t["name"]: t for t in tools}
        print(f"==> tools/list: {len(tools)} tools")

        # Read-only tools to exercise (in order; cluster-scoped ones get a cluster id).
        no_arg = [
            "get_access_token",
            "cluster_versions_list",
            "vpc_list",
            "subnet_list",
            "flavor_list",
            "sshkey_list",
            "secgroup_list",
            "cluster_list",
        ]
        scoped = ["cluster_get", "cluster_get_events", "nodegroup_list"]

        ctx = {"region": args.region, "cluster_id": None, "nodegroup_id": None}
        results: list[tuple[str, str, str]] = []  # (tool, status, note)

        def run(name: str) -> dict:
            tool = by_name.get(name)
            if tool is None:
                results.append((name, "SKIP", "not registered"))
                return {}
            filled = _fill_args(tool, ctx)
            if filled is None:
                results.append((name, "SKIP", "missing required arg in context"))
                return {}
            resp = client.call(name, filled)
            passed, note = _ok(resp)
            if passed and name not in _SENSITIVE:
                sc = resp.get("result", {}).get("structuredContent")
                if sc is not None:
                    sc_note = f"[structuredContent: {len(sc)} key(s)]"
                    note = f"{note} {sc_note}" if note else sc_note
            results.append((name, "PASS" if passed else "FAIL", note))
            return resp

        for name in no_arg:
            resp = run(name)
            if name == "cluster_list" and resp:
                m = re.search(r"k8s-[0-9a-f-]{8,}", _result_text(resp))
                if m:
                    ctx["cluster_id"] = m.group(0)

        if ctx["cluster_id"]:
            print(f"==> drilling into cluster {ctx['cluster_id']}")
            for name in scoped:
                run(name)
        else:
            for name in scoped:
                results.append((name, "SKIP", "no cluster found to drill into"))

        # Report
        print("\n==================== SMOKE TEST RESULTS ====================")
        width = max(len(n) for n, _, _ in results)
        passed = failed = skipped = 0
        for name, status, note in results:
            mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "–"}[status]
            extra = (
                "" if name in _SENSITIVE and status == "PASS" else (f"  {note}" if note else "")
            )
            print(f"  {mark} {name.ljust(width)}  {status}{extra}")
            passed += status == "PASS"
            failed += status == "FAIL"
            skipped += status == "SKIP"
        print("-----------------------------------------------------------")
        print(f"  {passed} passed, {failed} failed, {skipped} skipped")
        print("===========================================================")
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
