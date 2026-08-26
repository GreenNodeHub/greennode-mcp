#!/usr/bin/env python3
"""Automated MCP smoke test for the vServer MCP server.

Starts a LOCAL server in read-only mode (no --allow-write), drives the real MCP
protocol over streamable-http (initialize -> initialized -> tools/list ->
tools/call), exercises the read-only tools against the live vServer API using
the credentials in ~/.greennode, and prints a PASS/FAIL summary.

Safe by construction: the server is started without --allow-write, so no write
tool is even registered. Access tokens are never printed.

Usage (from src/vserver-mcp-server):
    uv run python scripts/smoke_test.py
    uv run python scripts/smoke_test.py --port 8770 --region HAN

Exit code 0 if every executed tool passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import httpx
import json
import re
import subprocess
import sys
import time


_SENSITIVE = {"get_access_token", "get_server_console_url"}

_PROJECT_SCOPED = [
    "get_access_token",
    "list_zones",
    "list_flavor_families",
    "list_flavor_codes",
    "list_images",
    "get_quota",
    "list_vpcs",
    "list_security_groups",
    "list_servers",
    "list_volumes",
    "list_floating_ips",
    "list_network_interfaces",
    "list_ssh_keys",
    "list_placement_groups",
    "list_user_images",
    "list_dhcp_options",
    "list_route_tables",
    "list_network_acls",
    "list_peerings",
    "list_virtual_ips",
    "list_interconnects",
    "list_interconnect_packages",
    "list_persistent_volumes",
    "list_tags",
    "get_tag_quota",
]

_SERVER_SCOPED = [
    "get_server",
    "list_server_interfaces",
    "list_server_security_groups",
    "list_server_volumes",
    "list_server_actions",
    "get_server_boot_volume",
    "list_server_snapshots",
    "get_server_snapshot_policy",
]

_VPC_SCOPED = ["get_vpc", "list_subnets"]


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


def _ok(response: dict) -> tuple[bool, str]:
    """Return (passed, short_note). A JSON-RPC error or isError result fails."""
    if "error" in response:
        return False, str(response["error"].get("message", response["error"]))[:120]
    result = response.get("result", {})
    if result.get("isError"):
        return False, _result_text(response)[:120]
    return True, ""


def _fill_args(tool: dict, ctx: dict) -> dict | None:
    """Fill a tool's required args from context. None means "cannot, skip it"."""
    schema = tool.get("inputSchema", {}) or {}
    args: dict = {}
    for prop in schema.get("required", []) or []:
        low = prop.lower()
        if "region" in low:
            args[prop] = ctx["region"]
        elif "server" in low:
            if not ctx.get("server_id"):
                return None
            args[prop] = ctx["server_id"]
        elif "vpc" in low or "network_id" in low:
            if not ctx.get("vpc_id"):
                return None
            args[prop] = ctx["vpc_id"]
        else:
            return None
    args.setdefault("region", ctx["region"])
    return args


def main() -> int:
    """Start a local server, drive read-only tools, and print a PASS/FAIL summary."""
    parser = argparse.ArgumentParser(description="vServer MCP server smoke test")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--region", default="HCM-3", choices=["HCM-3", "HAN"])
    options = parser.parse_args()

    base = f"http://127.0.0.1:{options.port}"
    print(f"==> Starting local server (read-only) on {base}")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "greennode.vserver_mcp_server.server",
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
        by_name = {t["name"]: t for t in tools}
        print(f"==> tools/list: {len(tools)} read-only tools")

        ctx = {"region": options.region, "server_id": None, "vpc_id": None}
        results: list[tuple[str, str, str]] = []

        def run(name: str) -> dict:
            tool = by_name.get(name)
            if tool is None:
                results.append((name, "SKIP", "not registered"))
                return {}
            filled = _fill_args(tool, ctx)
            if filled is None:
                results.append((name, "SKIP", "missing required arg in context"))
                return {}
            response = client.call(name, filled)
            passed, note = _ok(response)
            if passed and name not in _SENSITIVE:
                structured = response.get("result", {}).get("structuredContent")
                if structured is not None:
                    shape = f"[structuredContent: {len(structured)} key(s)]"
                    note = f"{note} {shape}" if note else shape
            results.append((name, "PASS" if passed else "FAIL", note))
            return response

        for name in _PROJECT_SCOPED:
            response = run(name)
            if name == "list_servers" and response:
                match = re.search(r"ins-[0-9a-f-]{8,}", _result_text(response))
                if match:
                    ctx["server_id"] = match.group(0)
            if name == "list_vpcs" and response:
                match = re.search(r"net-[0-9a-f-]{8,}", _result_text(response))
                if match:
                    ctx["vpc_id"] = match.group(0)

        if ctx["vpc_id"]:
            print(f"==> drilling into VPC {ctx['vpc_id']}")
            for name in _VPC_SCOPED:
                run(name)
        else:
            for name in _VPC_SCOPED:
                results.append((name, "SKIP", "no VPC found to drill into"))

        if ctx["server_id"]:
            print(f"==> drilling into server {ctx['server_id']}")
            for name in _SERVER_SCOPED:
                run(name)
        else:
            for name in _SERVER_SCOPED:
                results.append((name, "SKIP", "no server found to drill into"))

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
        print(
            "\nNote: a tool may FAIL with IAM_PERMISSION_DENIED when the calling\n"
            "identity's policy does not cover that endpoint. That is an account\n"
            "permission, not a server defect — see CLAUDE.md."
        )
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
