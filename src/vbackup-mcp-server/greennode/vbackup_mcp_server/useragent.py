"""The User-Agent this server sends with every outbound API request.

One string, one source of truth, so the platform can attribute and count
requests originating from the vBackup MCP server.
"""

from __future__ import annotations

from importlib import metadata


def _version() -> str:
    try:
        return metadata.version("greennode-vbackup-mcp-server")
    except metadata.PackageNotFoundError:
        return "dev"


USER_AGENT = f"vbackup-mcp-server/{_version()}"
