# GreenNode VKS MCP Server

An MCP (Model Context Protocol) server that gives AI assistants (Claude, Cursor,
Gemini, etc.) tools to manage **VKS — GreenNode Kubernetes Service** clusters and the
Kubernetes resources inside them.

- **39 tools** across 7 handlers: Auth, Cluster, NodeGroup, Version, Discovery, K8s, Guidance
- Fully **async** (httpx) on the **FastMCP** framework
- Read-only by default; write and sensitive-data access are opt-in via flags — and the server instructions tell the agent which mode **this** session runs in
- Every tool declares MCP **ToolAnnotations** (`readOnlyHint`/`destructiveHint`), so clients can auto-approve reads and warn before destructive calls
- Import package: `greennode.vks_mcp_server`
- Structured (JSON) output for data tools; FastMCP emits `outputSchema` + `structuredContent`. Region is `Literal["HCM-3", "HAN"]`; list outputs echo the region they were fetched from.

## Installation

Requires Python ≥ 3.11. From the repository root (uv workspace):

```bash
uv sync
```

Or from this project directory:

```bash
cd src/vks-mcp-server
uv sync
```

## Configuration

Credentials and region are read from `~/.greenode/credentials` and
`~/.greenode/config` (INI format, shared with greennode-cli).

Environment variables override the config files (highest priority):

| Variable | Purpose |
|----------|---------|
| `GRN_CLIENT_ID` | Override client_id |
| `GRN_CLIENT_SECRET` | Override client_secret |
| `GRN_PROFILE` | Select profile (default: `default`) |
| `GRN_DEFAULT_REGION` | Override region (`HCM-3` or `HAN`) |
| `GRN_PROJECT_ID` | Override project_id (auto-discovered from vServer when unset) |

HTTP-transport / auth variables (all optional):

| Variable | Purpose |
|----------|---------|
| `GRN_MCP_API_KEY` | Static bearer token for `--auth-mode api-key` |
| `GRN_MCP_JWT_ISSUER` / `GRN_MCP_JWT_JWKS_URI` / `GRN_MCP_JWT_AUDIENCE` | JWT verification for `--auth-mode jwt` |
| `GRN_MCP_RESOURCE_URL` | This server's resource URL (Protected Resource Metadata) |
| `GRN_MCP_AUTH_DEBUG` | `1` = redacted inbound-auth diagnostics + `GET /whoami` (never in production) |

## Running

```bash
# Read-only mode (default)
uv run vks-mcp-server

# Enable create/update/delete operations
uv run vks-mcp-server --allow-write

# Enable reading Kubernetes Secrets / logs / events
uv run vks-mcp-server --allow-sensitive-data-access
```

The server speaks MCP over **stdio** by default. Example Claude Desktop /
Cursor entry:

```json
{
  "mcpServers": {
    "vks": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/greennode-mcp", "vks-mcp-server", "--allow-write"]
    }
  }
}
```

### HTTP transport

```bash
uv run vks-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

`GET /health` is always unauthenticated (liveness/readiness). The Docker image
serves streamable-http on port 8080.

### Inbound authentication (HTTP transport)

`--auth-mode` selects how clients authenticate to the HTTP endpoint:

- `none` (default) — no auth (use only on a trusted/private network)
- `api-key` — static Bearer token (`--api-key` / `GRN_MCP_API_KEY`)
- `jwt` — OAuth 2.1 Resource Server: verifies Bearer JWTs against a JWKS and
  advertises Protected Resource Metadata. Requires `--jwt-issuer`,
  `--jwt-jwks-uri`, `--jwt-audience`, `--resource-url` (or the matching
  `GRN_MCP_JWT_*` / `GRN_MCP_RESOURCE_URL` env vars); optional
  `--jwt-required-scopes`.

Behind the GreenNode MCP Gateway: use `api-key` when the Gateway's outbound auth
is API Key, or `jwt` when it is OAuth 2.0. `/health` is always unauthenticated.
(Per-user VKS access is a future phase.)

## Tools

Tool names follow the EKS-style `verb_noun` convention and map 1:1 to
greennode-cli command names (`list-clusters` ↔ `list_clusters`).

### Cluster (12)

| Tool | Access | Description |
|------|--------|-------------|
| `list_clusters` | read | List clusters (structured summaries; every page fetched automatically) |
| `get_cluster` | read | Full cluster detail (structured) |
| `get_cluster_kubeconfig` | read | Kubeconfig YAML for a cluster |
| `get_cluster_events` | read | Cluster events table |
| `list_cluster_versions` | read | Available Kubernetes versions (cached 30 min) |
| `validate_cluster_create` | read | Validate a create body without creating |
| `delete_cluster_dryrun` | read | Preview a cluster deletion (incl. node groups) |
| `create_cluster` | **write** | Create a cluster (control plane only); add workers via `create_nodegroup` |
| `update_cluster` | **write** | Change version + whitelistNodeCIDRs (+ LB/CSI plugin toggles) |
| `delete_cluster` | **write** | Delete a cluster (IRREVERSIBLE; dry-run first) |
| `configure_auto_upgrade` / `delete_auto_upgrade` | **write** | Manage the auto-upgrade schedule |
| `configure_auto_healing` | **write** | Configure node auto-healing |

### Node group (9)

| Tool | Access | Description |
|------|--------|-------------|
| `list_nodegroups` | read | Node groups of a cluster |
| `get_nodegroup` | read | Full node-group detail (incl. subnet, encryption, placement) |
| `list_nodes` | read | Nodes of a node group (floating/fixed IP, ready, poc) |
| `delete_nodegroup_dryrun` | read | Preview a node-group deletion |
| `create_nodegroup` | **write** | Create a node group (full CLI parity: os, labels/taints/tags, autoscale, placement, encryption, private subnet) |
| `update_nodegroup` | **write** | Update numNodes / securityGroups / autoScaleConfig / upgradeConfig |
| `update_nodegroup_metadata` | **write** | Update labels, tags, taints (`PATCH .../metadata`) |
| `delete_nodegroup` | **write** | Delete a node group (IRREVERSIBLE; dry-run first) |
| `upgrade_nodegroup_version` | **write** | Upgrade a node group's Kubernetes version |

### Discovery (8) — resolve names → IDs for create bodies

| Tool | Feeds | Cache TTL |
|------|-------|-----------|
| `list_vpcs` | `vpcId` (ACTIVE only) | 2 min |
| `list_subnets` | `subnetId` / `listSubnetIds`, `secondarySubnets` + each subnet's availability zone (ACTIVE only) | 2 min |
| `list_flavors` | `flavorId` (tagged by deployment-need group; sold-out excluded) | 30 min |
| `list_ssh_keys` | `sshKeyId` | 30 s |
| `list_security_groups` | `securityGroups` (ACTIVE only) | 2 min |
| `list_volume_types` | `diskType` (a volume-type **ID**, not "SSD"; NVME tiers picked by IOPS) | 30 min |
| `list_placement_groups` | `placementGroupId` (type=EXISTING) | 2 min |
| `get_quota` | pre-create quota check (max/used clusters, node groups, nodes) | none |

All discovery tools accept `refresh: true` to bypass the cache (e.g. right
after creating a resource in the console). `list_flavors` and
`list_volume_types` take `cluster_id` + the chosen `subnet_id` — the server
locates the cluster (any region) and derives the availability zone itself, so
a region/zone mismatch is impossible.

### Guidance (1)

| Tool | Description |
|------|-------------|
| `get_creation_guide` | On-demand creation choreography for `resource="cluster" \| "nodegroup"`: the pinned question order, one-setting-per-question rules, defaults, and the confirm-gate protocol. Agents call it FIRST in any create flow; failed creates point back at it. |

### Kubernetes (7)

`list_k8s_resources`, `manage_k8s_resource` (CRUD one resource), `apply_yaml`,
`generate_app_manifest`, `get_pod_logs`, `get_k8s_events`, `list_api_versions`.
Always registered; mutating operations require `--allow-write`, and reading
Secrets/logs/events requires `--allow-sensitive-data-access`.

### Auth (1)

`get_access_token` — current IAM bearer token + endpoint URLs (auto-refreshed).

## Prompts

Three portable prompts (Vietnamese) work in any MCP client and are always
available (no `--allow-write` needed):

| Prompt | Purpose |
|--------|---------|
| `vks_getting_started` | Onboarding: concepts, auth setup, regions, naming rules, tool routing |
| `vks_create_cluster` | Guided cluster creation: quota check → pinned question order → validate → confirm gate → create → poll |
| `vks_create_nodegroup` | Guided node-group creation (optional `cluster_id` argument): pinned question order → discovery → confirm gate → create → poll |

The two create guides are also served as the `get_creation_guide` **tool**
(same text, one source of truth) — prompts must be loaded by the user, while
agents call tools on their own.

## Development

### Unit tests

```bash
cd src/vks-mcp-server
uv run pytest tests/ -v

# One handler / one test
uv run pytest tests/test_cluster_tools.py -v
uv run pytest tests/test_nodegroup_tools.py -v -k "create"

# Lint + format (what CI runs)
uv run ruff check . && uv run ruff format --check .
```

Tests use `respx` for async HTTP mocking and `pytest-asyncio` — no real API
calls, no credentials needed. See the repo-root `CLAUDE.md` for conventions on
adding new tools.

### Manual testing with MCP Inspector (stdio)

Interactive UI to browse tools/prompts, inspect input/output schemas, and call
tools against the **real** VKS API (credentials are read from `~/.greenode/`,
same as the CLI — run `grn configure` once if you haven't):

```bash
cd src/vks-mcp-server

# Read-only (28 tools)
npx @modelcontextprotocol/inspector uv run vks-mcp-server

# All 39 tools (write + sensitive data)
npx @modelcontextprotocol/inspector \
  uv run vks-mcp-server --allow-write --allow-sensitive-data-access
```

In the UI: Transport Type = `STDIO` → **Connect** → **Tools** → **List Tools**
→ pick a tool → fill parameters → **Run**. Call `get_access_token` first to
confirm authentication works.

Notes:

- Inspector v0.14+ requires a **proxy session token**. Open the URL the
  terminal prints (`http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=...`) instead
  of a bare `localhost:6274`, or disable it for local dev:
  `DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector ...`
- `uv run mcp dev` does **not** work here: the FastMCP instance is built by
  `create_server()` inside `main()`, so there is no module-level `mcp` object.
  Use the Inspector command above instead.
- To test another account/region without touching `~/.greenode/`, prefix env
  overrides: `GRN_DEFAULT_REGION=HAN GRN_PROFILE=staging npx ...`

### Scripted smoke test over stdio (no UI)

The server speaks JSON-RPC on stdin/stdout, so a pipe is enough to smoke-test
it (useful in CI or a quick sanity check):

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | uv run vks-mcp-server 2>/dev/null
```

Replace the last message with a `tools/call` to invoke a tool, e.g.
`{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_clusters","arguments":{}}}`.
For multi-step flows, use the Python MCP client (`mcp.client.stdio`) instead of
a pipe — the process exits when stdin closes.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
