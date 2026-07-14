"""GreenNode MCP Server entry point — follows EKS MCP Server handler pattern."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import sys
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.auth_debug import summarize_request
from greennode.vks_mcp_server.auth_handler import AuthHandler
from greennode.vks_mcp_server.auth_verifier import JwtAuthConfig, JwtTokenVerifier
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.cluster_handler import ClusterHandler
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.discovery_handler import DiscoveryHandler
from greennode.vks_mcp_server.k8s_handler import K8sHandler
from greennode.vks_mcp_server.nodegroup_handler import NodeGroupHandler
from greennode.vks_mcp_server.prompts_handler import PromptsHandler
from greennode.vks_mcp_server.version_handler import VersionHandler
from mcp.server.fastmcp import FastMCP
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


CONFIG_PATH = Path.home() / ".greenode"

SERVER_INSTRUCTIONS = """
# GreenNode MCP Server

MCP Server for GreenNode Kubernetes Service (VKS).

## IMPORTANT: Operating mode

By default the server runs in **read-only** mode. Use the `--allow-write` flag to enable write operations (create, update, delete cluster/node group).

## Regions

Every resource is region-scoped: `HCM-3` (default) or `HAN`. Clusters, SSH keys, security groups, and quotas differ per region — if a resource the user mentions isn't found, retry in the other region. Discovery outputs echo the region they were fetched from.

## Creation flows (resolve every id via discovery — never invent one)

Before starting either flow, call get_creation_guide(resource="cluster" | "nodegroup") and conduct the conversation exactly as it says (question order, one setting per question, confirm gate).

Create a cluster: get_quota -> list_vpcs (vpcId) -> list_cluster_versions -> validate_cluster_create -> create_cluster -> poll get_cluster until ACTIVE. The cluster is control-plane only; add workers next.

Add a node group: get_cluster (vpcId + region) -> get_quota -> list_subnets (user picks a subnetId; its zone scopes the next two) -> list_flavors(cluster_id, subnet_id) (flavorId) -> list_volume_types(cluster_id, subnet_id) (IOPS tier id = diskType) -> list_ssh_keys (sshKeyId) -> validate_nodegroup_create -> create_nodegroup -> poll get_nodegroup.

Present the discovered options to the user and wait for confirmation before any write call.

## Guided prompts

For full step-by-step flows (safe defaults, plan review, confirm gate) load the prompts: `vks_getting_started`, `vks_create_cluster`, `vks_create_nodegroup`.

## Available tools

### Read-only (always available):
- get_creation_guide: Step-by-step guide for the create flows — call it FIRST
- get_access_token: Get the current access token
- list_clusters, get_cluster: View clusters (get_cluster is step 1 of the node-group flow)
- get_cluster_kubeconfig: Get kubeconfig YAML — cluster-admin credentials, requires --allow-sensitive-data-access (a new cluster needs generate_kubeconfig first)
- get_cluster_events: View cluster events
- delete_cluster_dryrun: Preview information before deleting a cluster
- validate_cluster_create: Validate body before creating a cluster
- list_cluster_versions: List available Kubernetes versions
- list_nodegroups, get_nodegroup: View node groups
- list_nodes: List nodes in a node group
- delete_nodegroup_dryrun: Preview information before deleting a node group
- validate_nodegroup_create: Validate a create_nodegroup body before creating (free, non-mutating)
- list_vpcs, list_subnets, list_ssh_keys, list_security_groups, list_placement_groups: Discover vServer resources to fill cluster/node-group creation
- list_flavors, list_volume_types: Zone-scoped discovery (pass cluster_id + the chosen subnet_id; region and zone are derived server-side)
- get_quota: Check per-region limits before creating anything

### Write (requires --allow-write):
- create_cluster, update_cluster, delete_cluster: Create, update, delete cluster
- configure_auto_upgrade, delete_auto_upgrade: Configure auto-upgrade
- configure_auto_healing: Configure cluster auto-healing
- generate_kubeconfig: Mint a kubeconfig for a cluster (async; required once for a new cluster)
- create_nodegroup, update_nodegroup, delete_nodegroup: Create, update, delete node group
- update_nodegroup_metadata: Update a node group's labels/tags/taints
- upgrade_nodegroup_version: Upgrade a node group's Kubernetes version (irreversible)

### Kubernetes Resource Management (requires kubeconfig from VKS API):
Prefer these tools over raw `kubectl` — they fetch and cache the cluster's kubeconfig from the VKS API automatically, no manual setup.
- list_k8s_resources: List K8s resources (Pods, Services, Deployments...)
- get_pod_logs: View pod logs
- get_k8s_events: View resource events
- list_api_versions: List API versions
- generate_app_manifest: Generate a deployment manifest for an app
- manage_k8s_resource: CRUD single K8s resource (requires --allow-write for write ops, --allow-sensitive-data-access for Secrets)
- apply_yaml: Apply YAML manifest file (requires --allow-write)
"""

mcp = None


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Validate Authorization: Bearer <token> on every HTTP request."""

    def __init__(self, app, api_key: str) -> None:
        super().__init__(app)
        self._expected = f"Bearer {api_key}".encode()

    async def dispatch(self, request: Request, call_next):
        """Reject requests lacking the expected Bearer token, else forward them."""
        # Health probes are unauthenticated so liveness/readiness checks work.
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(auth, self._expected):
            return Response(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


class AuthDebugMiddleware(BaseHTTPMiddleware):
    """DIAGNOSTIC: log a redacted summary of every inbound request, then pass it through unchanged.

    Never blocks a request; never logs the full bearer token.
    """

    async def dispatch(self, request: Request, call_next):
        """Log the request's redacted auth summary, then forward it untouched."""
        summary = summarize_request(request.method, request.url.path, request.headers)
        # Emit a single plain line to stdout rather than via the logging framework:
        # the uvicorn/rich log handler wraps long messages across several lines,
        # which splits the JSON and makes it ungreppable in collected runtime logs.
        print(f"AUTH-DEBUG {json.dumps(summary, default=str)}", flush=True)
        return await call_next(request)


def _env_truthy(val: str | None) -> bool:
    """True for common truthy env-var spellings (1/true/yes/on)."""
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_auth(args) -> tuple[str, JwtAuthConfig | None, str | None]:
    """Resolve inbound-auth config from CLI args + env. Returns (mode, jwt_config, api_key)."""
    mode = args.auth_mode or os.environ.get("GRN_MCP_AUTH_MODE") or "none"
    api_key = args.api_key or os.environ.get("GRN_MCP_API_KEY")
    if mode == "api-key" and not api_key:
        raise SystemExit("--auth-mode api-key requires --api-key or GRN_MCP_API_KEY")
    jwt_config: JwtAuthConfig | None = None
    if mode == "jwt":
        issuer = args.jwt_issuer or os.environ.get("GRN_MCP_JWT_ISSUER")
        jwks_uri = args.jwt_jwks_uri or os.environ.get("GRN_MCP_JWT_JWKS_URI")
        audience = args.jwt_audience or os.environ.get("GRN_MCP_JWT_AUDIENCE")
        resource_url = args.resource_url or os.environ.get("GRN_MCP_RESOURCE_URL")
        missing = [
            name
            for name, val in [
                ("--jwt-issuer", issuer),
                ("--jwt-jwks-uri", jwks_uri),
                ("--jwt-audience", audience),
                ("--resource-url", resource_url),
            ]
            if not val
        ]
        if missing:
            raise SystemExit(f"--auth-mode jwt requires: {', '.join(missing)}")
        scopes_raw = args.jwt_required_scopes or os.environ.get("GRN_MCP_JWT_REQUIRED_SCOPES")
        required_scopes = (
            [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else None
        )
        jwt_config = JwtAuthConfig(
            issuer=issuer,
            jwks_uri=jwks_uri,
            audience=audience,
            resource_url=resource_url,
            required_scopes=required_scopes,
        )
    return mode, jwt_config, api_key


def _mode_addendum(allow_write: bool, allow_sensitive_data_access: bool) -> str:
    """Runtime-mode addendum for SERVER_INSTRUCTIONS (EKS pattern).

    The server knows this session's mode at startup — telling the agent up
    front turns "the create fails after the whole guided conversation" into
    "the agent refuses the flow in its first reply".
    """
    if allow_write:
        write = (
            "- Write: ENABLED — create/update/delete/scale tools are available. "
            "Every write still goes through the plan review + explicit user "
            "confirmation gate."
        )
    else:
        write = (
            "- Write: OFF — this session is read-only; create/update/delete/scale "
            "tools are NOT registered. If the user asks for one, do NOT start the "
            "creation flow or ask any configuration question: tell them immediately "
            "to restart the server with --allow-write."
        )
    if allow_sensitive_data_access:
        sensitive = (
            "- Sensitive data: ENABLED — Kubernetes Secrets and the cluster "
            "kubeconfig can be read."
        )
    else:
        sensitive = (
            "- Sensitive data: OFF — reading Kubernetes Secrets or the cluster "
            "kubeconfig requires restarting with --allow-sensitive-data-access."
        )
    return f"\n## This session (runtime mode)\n\n{write}\n{sensitive}\n"


def create_server(
    jwt_config: JwtAuthConfig | None = None,
    auth_debug: bool = False,
    allow_write: bool = False,
    allow_sensitive_data_access: bool = False,
) -> FastMCP:
    """Create and return a FastMCP server instance.

    When jwt_config is provided, the server runs as an OAuth 2.1 Resource Server
    (verify Bearer JWT + emit 401/WWW-Authenticate + Protected Resource Metadata).
    """
    instructions = SERVER_INSTRUCTIONS + _mode_addendum(allow_write, allow_sensitive_data_access)
    if jwt_config is not None:
        from mcp.server.auth.settings import AuthSettings

        server = FastMCP(
            "vks-mcp-server",
            instructions=instructions,
            token_verifier=JwtTokenVerifier(jwt_config),
            auth=AuthSettings(
                issuer_url=jwt_config.issuer,
                resource_server_url=jwt_config.resource_url,
                required_scopes=jwt_config.required_scopes or None,
            ),
        )
    else:
        server = FastMCP("vks-mcp-server", instructions=instructions)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        """Liveness/readiness probe endpoint (no authentication required)."""
        return JSONResponse({"status": "ok"})

    if auth_debug:
        # Intentionally unauthenticated and registered ahead of auth middleware:
        # it must observe the raw inbound request even under --auth-mode jwt/api-key.

        @server.custom_route("/whoami", methods=["GET"])
        async def whoami(request: Request) -> Response:
            """DIAGNOSTIC: echo the request's redacted auth summary (no auth, no verify)."""
            return JSONResponse(
                summarize_request(request.method, request.url.path, request.headers)
            )

    # Prompts are always available (read-only guidance, no --allow-write needed).
    PromptsHandler(server)

    return server


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="GreenNode MCP Server -- manage GreenNode Kubernetes Service via MCP"
    )
    parser.add_argument(
        "--allow-write",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable write mode (allow create, update, delete cluster/node group)",
    )
    parser.add_argument(
        "--allow-sensitive-data-access",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable access to sensitive data (required for reading Kubernetes Secrets)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode: stdio (default) or streamable-http",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for HTTP transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port for HTTP transport (default: 8000)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Bearer token to protect the HTTP endpoint (env: GRN_MCP_API_KEY)",
    )
    parser.add_argument(
        "--auth-mode",
        choices=["none", "api-key", "jwt"],
        default=None,
        help="Inbound auth for HTTP transport: none (default), api-key, or jwt "
        "(env: GRN_MCP_AUTH_MODE)",
    )
    parser.add_argument("--jwt-issuer", default=None, help="JWT issuer (env: GRN_MCP_JWT_ISSUER)")
    parser.add_argument(
        "--jwt-jwks-uri", default=None, help="JWKS URI (env: GRN_MCP_JWT_JWKS_URI)"
    )
    parser.add_argument(
        "--jwt-audience", default=None, help="Expected JWT audience (env: GRN_MCP_JWT_AUDIENCE)"
    )
    parser.add_argument(
        "--jwt-required-scopes",
        default=None,
        help="Comma-separated required scopes (env: GRN_MCP_JWT_REQUIRED_SCOPES)",
    )
    parser.add_argument(
        "--resource-url",
        default=None,
        help="This server's public URL for PRM 'resource' (env: GRN_MCP_RESOURCE_URL)",
    )
    parser.add_argument(
        "--auth-debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="DIAGNOSTIC: log redacted inbound auth summary and expose /whoami "
        "(HTTP only, off by default; env: GRN_MCP_AUTH_DEBUG). Do NOT use in production.",
    )
    return parser


def main() -> None:
    """Load config, create handlers, and run the MCP server."""
    global mcp

    args = _build_parser().parse_args()
    auth_mode, jwt_config, api_key = _resolve_auth(args)
    auth_debug = args.auth_debug or _env_truthy(os.environ.get("GRN_MCP_AUTH_DEBUG"))

    config = load_config(CONFIG_PATH)
    token_manager = TokenManager(config)
    client = VksClient(config, token_manager)

    mcp = create_server(
        jwt_config,
        auth_debug=auth_debug,
        allow_write=args.allow_write,
        allow_sensitive_data_access=args.allow_sensitive_data_access,
    )

    AuthHandler(mcp, config, token_manager)
    ClusterHandler(
        mcp,
        config,
        client,
        allow_write=args.allow_write,
        allow_sensitive_data_access=args.allow_sensitive_data_access,
    )
    discovery_cache = DiscoveryCache()
    NodeGroupHandler(mcp, config, client, allow_write=args.allow_write, cache=discovery_cache)
    DiscoveryHandler(mcp, config, client, discovery_cache)
    VersionHandler(mcp, config, client, discovery_cache)
    K8sHandler(
        mcp,
        config,
        client,
        allow_write=args.allow_write,
        allow_sensitive_data_access=args.allow_sensitive_data_access,
    )

    if args.transport == "stdio":
        if auth_debug:
            print(
                "Note: --auth-debug has no effect with stdio transport (HTTP only); ignoring.",
                file=sys.stderr,
            )
        mcp.run()
    else:
        # streamable-http mode
        import uvicorn  # optional dep; only required for streamable-http mode

        if auth_mode == "none":
            print(
                "Warning: --auth-mode is 'none'. The HTTP endpoint is unauthenticated. "
                "Use api-key or jwt, or run only on a trusted network.",
                file=sys.stderr,
            )

        mcp.settings.host = args.host
        mcp.settings.port = args.port

        loopback = {"127.0.0.1", "localhost", "::1"}
        if args.host not in loopback:
            from mcp.server.fastmcp.server import TransportSecuritySettings

            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )

        starlette_app = mcp.streamable_http_app()
        if auth_mode == "api-key" and api_key:
            starlette_app.add_middleware(BearerTokenMiddleware, api_key=api_key)

        if auth_debug:
            print(
                "Warning: --auth-debug is ON. Redacted request auth metadata is logged "
                "and /whoami is exposed. Diagnostic only -- do NOT enable in production.",
                file=sys.stderr,
            )
            starlette_app.add_middleware(AuthDebugMiddleware)

        uv_config = uvicorn.Config(
            starlette_app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
        server = uvicorn.Server(uv_config)
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
