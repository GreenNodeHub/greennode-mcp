#!/usr/bin/env python3
"""Drive every read-only vBackup tool against the live gateway over real MCP.

Unlike the unit tests (respx-mocked, no credentials), this speaks the MCP
protocol to a server started over stdio and calls each read tool for real. It
is the check that catches what mocks cannot: an endpoint that moved, an
envelope that changed shape, a field the account actually returns as null, or a
permission the caller's IAM policy does not grant.

Ids for the by-id tools are discovered from the listings, so the script adapts
to whatever the account holds. Requires credentials in ``~/.greennode`` (or
GRN_* env vars). Read-only — it never registers or calls a write tool.

Usage:
    uv run python scripts/smoke_test.py                  # HCM-3
    uv run python scripts/smoke_test.py --region HAN
    uv run python scripts/smoke_test.py --region both
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typing import Any


LIST_KEYS = (
    "backends",
    "destinations",
    "policies",
    "backup_servers",
    "server_ids",
    "volumes",
    "points",
    "volume_points",
    "runs",
    "restores",
)


def summarize(payload: Any, limit: int = 3) -> str:
    """Render a short, id-first preview of a tool result."""
    if isinstance(payload, dict):
        for key in LIST_KEYS:
            items = payload.get(key)
            if not isinstance(items, list):
                continue
            if items and isinstance(items[0], str):
                return f"{len(items)} {key}: " + ", ".join(items[:limit])
            head = [
                f"{i.get('id') or i.get('volume_id') or '?'}"
                f"{' (' + i['name'] + ')' if isinstance(i, dict) and i.get('name') else ''}"
                for i in items[:limit]
                if isinstance(i, dict)
            ]
            more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
            return f"{len(items)} {key}: " + ", ".join(head) + more
        if payload.get("id"):
            return f"{payload['id']} {payload.get('name') or ''}".strip()
    text = json.dumps(payload) if not isinstance(payload, str) else payload
    return text[:160]


class Runner:
    """Call tools on a session and record pass/fail per tool."""

    def __init__(self, session: ClientSession, registered: set[str]) -> None:
        self.session = session
        self.registered = registered
        self.failures: list[str] = []
        self.denied: list[str] = []

    async def call(self, name: str, args: dict) -> Any:
        """Invoke one tool, print a one-line result, return its payload."""
        if name not in self.registered:
            print(f"  SKIP {name}: not registered")
            return None
        try:
            result = await self.session.call_tool(name, args)
        except Exception as exc:
            self.failures.append(name)
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
            return None
        if result.isError:
            text = str(result.content)
            if "IAM_PERMISSION_DENIED" in text or "403" in text:
                self.denied.append(name)
                print(f"  DENY {name}: IAM policy does not grant this endpoint")
            else:
                self.failures.append(name)
                print(f"  FAIL {name}: {text[:200]}")
            return None
        payload = result.structuredContent or (result.content[0].text if result.content else "")
        print(f"  OK   {name}: {summarize(payload)}")
        return payload


async def exercise(runner: Runner, region: str) -> None:
    """Call every read tool for one region, discovering ids as it goes."""
    print(f"\n-- catalogue ({region})")
    await runner.call("get_access_token", {})
    await runner.call("get_feature_guide", {"feature": "getting_started"})
    await runner.call("list_backends", {"region": region})
    await runner.call("get_configuration", {"region": region})
    await runner.call("list_backup_destinations", {"region": region})
    await runner.call("list_protected_servers", {"region": region})

    print(f"\n-- policies ({region})")
    policies = await runner.call("list_backup_policies", {"region": region})
    policy_id = _first_id(policies, "policies")
    if policy_id:
        await runner.call("get_backup_policy", {"policy_id": policy_id, "region": region})

    print(f"\n-- backup servers ({region})")
    servers = await runner.call("list_backup_servers", {"region": region})
    server = _first(servers, "backup_servers")
    if server:
        bs_id = server.get("id")
        await runner.call("get_backup_server", {"backup_server_id": bs_id, "region": region})
        await runner.call(
            "list_backup_server_volumes", {"backup_server_id": bs_id, "region": region}
        )
        points = await runner.call(
            "list_backup_server_points", {"backup_server_id": bs_id, "region": region}
        )
        point_id = _first_id(points, "points")

        print(f"\n-- vserver projection ({region})")
        project_id = server.get("project_id")
        if project_id:
            await runner.call(
                "list_vserver_backup_servers",
                {"project_id": project_id, "region": region},
            )
        await runner.call(
            "get_vserver_backup_server", {"backup_server_id": bs_id, "region": region}
        )
        await runner.call(
            "list_vserver_backup_server_points",
            {"backup_server_id": bs_id, "region": region},
        )
        if point_id:
            await runner.call(
                "get_vserver_backup_server_point", {"point_id": point_id, "region": region}
            )
            slices = await runner.call(
                "list_vserver_backup_volume_points",
                {"point_id": point_id, "region": region},
            )
            slice_id = _first_id(slices, "volume_points")
            if slice_id:
                await runner.call(
                    "get_vserver_backup_volume_point",
                    {"volume_point_id": slice_id, "region": region},
                )

        print(f"\n-- volume usage ({region})")
        live = _first_live_server(servers)
        if live and live.get("volumes"):
            await runner.call(
                "list_volume_usage",
                {
                    "region": region,
                    "body": {
                        "backendId": live.get("backend_id"),
                        "projectId": live.get("project_id"),
                        "volumeIds": [v["volume_id"] for v in live["volumes"][:2]],
                    },
                },
            )
        else:
            print("  SKIP list_volume_usage: no live server with volumes in this region")

    print(f"\n-- history ({region})")
    await runner.call("list_backup_history", {"region": region, "limit": 5})
    await runner.call("list_restore_history", {"region": region, "limit": 5})


def _first(payload: Any, key: str) -> dict | None:
    """Return the first item of a list field, if any."""
    if isinstance(payload, dict) and isinstance(payload.get(key), list) and payload[key]:
        item = payload[key][0]
        return item if isinstance(item, dict) else None
    return None


def _first_id(payload: Any, key: str) -> str | None:
    """Return the id of the first item of a list field, if any."""
    item = _first(payload, key)
    return item.get("id") if item else None


def _first_live_server(payload: Any) -> dict | None:
    """Return a backup server whose source instance still exists.

    A deleted instance's volumes no longer exist in vServer, and volume-usage
    answers 404 for the whole request when one is included.
    """
    if not isinstance(payload, dict):
        return None
    for item in payload.get("backup_servers") or []:
        if isinstance(item, dict) and not item.get("server_deleted") and item.get("volumes"):
            return item
    return None


async def run(regions: list[str]) -> int:
    """Start the server over stdio and exercise every read tool."""
    params = StdioServerParameters(command="vbackup-mcp-server", args=[])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"Registered tools ({len(names)}):")
            for name in names:
                print(f"  - {name}")

            runner = Runner(session, set(names))
            for region in regions:
                print(f"\n{'=' * 66}\n### {region}\n{'=' * 66}")
                await exercise(runner, region)

    print(f"\n{'=' * 66}")
    if runner.denied:
        print(f"IAM-denied ({len(runner.denied)}): {', '.join(sorted(set(runner.denied)))}")
        print("  These are permission grants on the caller, not defects.")
    if runner.failures:
        print(f"FAILED ({len(runner.failures)}): {', '.join(sorted(set(runner.failures)))}")
        return 1
    print("All read tools passed.")
    return 0


def main() -> None:
    """Parse arguments and run the smoke test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--region",
        default="HCM-3",
        choices=["HCM-3", "HAN", "both"],
        help="Region to exercise (default: HCM-3)",
    )
    args = parser.parse_args()
    regions = ["HCM-3", "HAN"] if args.region == "both" else [args.region]
    sys.exit(asyncio.run(run(regions)))


if __name__ == "__main__":
    main()
