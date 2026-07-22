"""The User-Agent this server sends with every outbound API request.

One string, one source of truth — the VKS/vServer HTTP client and the
kubernetes client all use it, so the platform can attribute and count
requests originating from the VKS MCP server.
"""

from __future__ import annotations

from importlib import metadata


def _version() -> str:
    try:
        return metadata.version("greennode-vks-mcp-server")
    except metadata.PackageNotFoundError:  # running from a raw checkout
        return "dev"


USER_AGENT = f"vks-mcp-server/{_version()}"
