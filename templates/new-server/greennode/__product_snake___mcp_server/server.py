"""FastMCP entry point for the {{PRODUCT}} MCP server."""

from __future__ import annotations

import argparse

from greennode.mcp_core.auth import TokenManager
from greennode.mcp_core.config import resolve_config_dir
from greennode.{{product_snake}}_mcp_server.client import {{Product}}Client
from greennode.{{product_snake}}_mcp_server.config import load_config
from greennode.{{product_snake}}_mcp_server.example_handler import ExampleHandler
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


# Prefer ~/.greennode; fall back to the legacy ~/.greenode when only it exists.
CONFIG_PATH = resolve_config_dir()

SERVER_INSTRUCTIONS = """\
GreenNode {{PRODUCT}} MCP Server.

By default the server runs in **read-only** mode. Use the `--allow-write`
flag to enable write operations.
"""


def create_server() -> FastMCP:
    """Create and return a FastMCP server instance (handlers wired in main)."""
    server = FastMCP("{{product}}-mcp-server", instructions=SERVER_INSTRUCTIONS)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        """Liveness/readiness probe endpoint (no authentication required)."""
        return JSONResponse({"status": "ok"})

    return server


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="GreenNode MCP Server -- manage {{PRODUCT}} via MCP"
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Enable create/update/delete operations (default: read-only)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode: stdio (default) or streamable-http",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP transport")
    parser.add_argument("--port", type=int, default=8080, help="Bind port for HTTP transport")
    return parser


def main() -> None:
    """Run the MCP server with CLI argument support."""
    args = _build_parser().parse_args()

    config = load_config(CONFIG_PATH)
    token_manager = TokenManager(config)
    client = {{Product}}Client(config, token_manager)

    mcp = create_server()
    ExampleHandler(mcp, config, client, allow_write=args.allow_write)

    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
