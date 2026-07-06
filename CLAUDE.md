# CLAUDE.md — GreenNode MCP Server

## Project overview

GreenNode MCP Server provides AI assistants (Claude, Cursor, Gemini, etc.) with tools to manage VKS (VNG Kubernetes Service) clusters and Kubernetes resources via the Model Context Protocol.

- **38 tools** across 6 handlers: Auth, Cluster, NodeGroup, Version, Discovery, K8s
- **3 MCP prompts** (`vks_getting_started`, `vks_create_cluster`, `vks_create_nodegroup`) — portable Vietnamese guidance for any MCP client (auth/regions/naming/tool-routing, guided cluster creation, and node-group creation flow); always available, no `--allow-write` needed
- **Async architecture** — fully async/await with httpx.AsyncClient
- **FastMCP framework** — uses `mcp` library for tool registration
- **Structured output** — data tools return Pydantic models; FastMCP emits `outputSchema` + `structuredContent` (JSON). Blob tools (`get_access_token`, `get_cluster_kubeconfig`) stay `str`. Region is a fixed `Literal["HCM-3", "HAN"]`.

## Repository layout

Monorepo organized as a **uv workspace** (root `pyproject.toml`, `members = ["src/*"]`), mirroring the AWS Labs MCP layout. Each product is an independent project under `src/` sharing the `greennode` namespace.

- Product project: `src/vks-mcp-server/` (own `pyproject.toml`, `tests/`, `README.md`, `Dockerfile`, …)
- Import package: `greennode.vks_mcp_server` (source under `src/vks-mcp-server/greennode/vks_mcp_server/`)
- CLI entry point: `vks-mcp-server` → `greennode.vks_mcp_server.server:main`
- Future products: add as `src/<name>-mcp-server/` siblings under the same `greennode` namespace

## Branch & release flow (trunk-based)

`main` is the only long-lived branch. **All work goes through a PR to `main`**
(feature branch → squash merge). The PR **title must follow Conventional
Commits** (`feat:`, `fix:`, `feat!:` …) — with squash merge it becomes the
commit message on `main` and drives release automation; `pr-title.yml` enforces
it. The old `develop` branch is legacy (no common ancestor with the squashed
`main`) — do not base new work on it.

## CI/CD

GitHub Actions live in `.github/workflows/`:

- `ci.yml` — runs on pull requests and pushes. Installs the workspace with `uv sync --all-packages --all-groups`, then runs ruff lint (`ruff check .`), ruff format check (`ruff format --check .`), and pytest in `src/vks-mcp-server`, followed by a `build` job that builds the Docker image (`src/vks-mcp-server/Dockerfile`, build context = repo root).
- `pr-title.yml` — enforces Conventional Commits on PR titles (semantic-pull-request action).
- `deploy.yml` — builds and pushes the image to a registry, using **GitHub Environments** so dev and production can have different registry config (environment names `develop`/`production` are decoupled from branch names). Triggers: push to `main` touching `src/vks-mcp-server/**` (path filter — the `[build]` magic string is gone) → `develop` environment (image tag = commit sha); push tag `v*` → `production` environment (image tag = git tag; path filters do not apply to tag pushes). In each environment (Settings → Environments) set the `IMAGE_REGISTRY` variable (e.g. `vcr.vngcloud.vn/<namespace>`) and the `REGISTRY_USERNAME` / `REGISTRY_PASSWORD` secrets. If an environment restricts "Deployment branches and tags", it must allow `main` (develop env) / `v*` tags (production env).
- `dependabot.yml` — weekly grouped updates for uv deps and GitHub Actions pins.
- `.github/CODEOWNERS` — per-product ownership: each team owns its `src/<product>-mcp-server/`.

## Code conventions

- All source code text must be in **English** — error messages, descriptions, comments, docstrings
- Async/await throughout — all handlers and client methods are async
- Use `from __future__ import annotations` in all files
- Follow existing handler pattern: class with `__init__` registering tools via `self.mcp.tool()`
- **Tool naming**: EKS-style `verb_noun` (`list_clusters`, `get_nodegroup`, `create_cluster`), matching the AWS Labs MCP convention and mapping 1:1 to greennode-cli command names (`list-clusters` → `list_clusters`). Never `noun_verb`.

## VNG Cloud API quirks

- **IAM API uses camelCase**: `grantType`, `accessToken`, `expiresIn` (not snake_case OAuth2 standard)
- **VKS API pagination is 0-based**: page 0 = first page
- **API returns 202** for most successful operations (not 200)

## Configuration

Reads from `~/.greenode/credentials` and `~/.greenode/config` (INI format, shared with greennode-cli).

**Environment variable overrides** (highest priority):

| Variable | Purpose |
|----------|---------|
| `GRN_CLIENT_ID` | Override client_id |
| `GRN_CLIENT_SECRET` | Override client_secret |
| `GRN_PROFILE` | Select profile (default: "default") |
| `GRN_DEFAULT_REGION` | Override region |
| `GRN_PROJECT_ID` | Override project_id (auto-discovered from vServer `GET /v1/projects` when unset; needed for discovery tools) |

## Server flags

```bash
# Read-only mode (default)
uv run vks-mcp-server

# Enable create/update/delete operations
uv run vks-mcp-server --allow-write

# Enable reading Kubernetes Secrets
uv run vks-mcp-server --allow-sensitive-data-access

# HTTP transport (default: stdio); Docker image serves this on port 8080
uv run vks-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

## Inbound auth (HTTP transport)

`--auth-mode none|api-key|jwt`. `jwt` runs the server as an OAuth 2.1 Resource Server
(`token_verifier` + `AuthSettings` → 401 + WWW-Authenticate + PRM), verifying Bearer
JWTs via JWKS (`--jwt-issuer/--jwt-jwks-uri/--jwt-audience/--resource-url`, or `GRN_MCP_JWT_*`/`GRN_MCP_RESOURCE_URL`).
VKS upstream still uses the global service account (per-user is a future phase). `/health` is always open.
`--auth-debug` (env `GRN_MCP_AUTH_DEBUG=1`) is an opt-in, redacted, HTTP-only diagnostic: logs a summary of inbound request auth (token scheme, JWT header, allow-listed claims, forwarding headers) and exposes `GET /whoami`. It never verifies signatures and never logs the full token; off by default; not for production.

## Adding a new tool

1. Choose the appropriate handler or create a new one in `src/vks-mcp-server/greennode/vks_mcp_server/`
2. Define async method with docstring (used as tool description)
3. Register in handler's `__init__`: `self.mcp.tool(name="tool_name")(self.method)`
4. Add `validate_id()` for any ID args used in URL construction
5. Check `self.allow_write` for mutating operations
6. Register handler in `server.py` if new handler class
7. Add tests in `tests/`
8. Use `Literal[...]` for parameters with a fixed value set, and `Field(ge=, le=)` for numeric bounds, so the schema is self-documenting
9. For create/update operations, use typed Pydantic request DTOs (e.g. `CreateClusterComboDto`, `UpdateClusterDto`, `CreateNodeGroupDto`, `UpdateNodeGroupDto` defined in `models.py`) with camelCase fields, nested specs, and Literal enums instead of `body: dict`
10. Write structured docstrings (`## Requirements`, `## Workflow`) for create/update/delete tools
11. For discovery tools (read-only vServer lookups), wrap the fetch in `DiscoveryCache.get_or_fetch`, add a per-tool TTL to `TTL_CONFIG` in `discovery_cache.py`, and expose a `refresh: bool` parameter
12. Name the tool `verb_noun`, mirroring the greennode-cli command where one exists

Example:
```python
async def my_tool(self, cluster_id: str) -> str:
    """Tool description shown to AI assistant."""
    validate_id(cluster_id, "cluster-id")
    client = self.client
    result = await client.get(f"/v1/clusters/{cluster_id}/my-endpoint")
    return format_result(result)
```

## Write DTO field scope

All write DTOs (`CreateClusterComboDto`, `UpdateClusterDto`, `CreateNodeGroupDto`, `UpdateNodeGroupDto`, `UpdateNodeGroupMetadataDto`, `NodeGroupSpec`, `UpgradeConfig`, and the nested `NodeGroupTaint`, `AutoScaleConfig`, `PlacementGroupConfig`, `AutoUpgradeConfig`, `AutoHealingConfig`) are configured with `extra="forbid"`, so passing an unsupported field raises a `ValidationError` with a clear message rather than silently dropping it. They mirror the greennode-cli field set — **the CLI is the source of truth for the current API** (the bundled `~/.greenode/mcp-specs/vks.json` OpenAPI file is stale).

Cluster write DTOs:

- **Create** (`CreateClusterComboDto`, `POST /v1/clusters`): required `name`, `version`, `networkType`, `vpcId`. `nodeGroups` is **optional** — omit for a control-plane-only cluster (CLI default) and add workers via `create_nodegroup`. Optional: `enablePrivateCluster`, `releaseChannel`, `enabled{LoadBalancer,BlockStoreCsi,ServiceEndpoint}Plugin`, `azStrategy`, `description`, `subnetId`, `cidr`, `secondarySubnets`, `listSubnetIds`, `nodeNetmaskSize`, `autoUpgradeConfig`, `autoHealingConfig`.
- **Update** (`UpdateClusterDto`, `PUT /v1/clusters/{id}`): required `version` + `whitelistNodeCIDRs`; optional plugin toggles `enabledLoadBalancerPlugin`, `enabledBlockStoreCsiPlugin`. Name, description, and release channel are **not** editable via this endpoint.

Node-group write DTOs:

- **Create** (`CreateNodeGroupDto`/`NodeGroupSpec`): `name`, `flavorId`, `diskType`, `sshKeyId`, `diskSize`, `numNodes`, `os` (ubuntu/linux/rocky, top level — NOT inside `upgradeConfig`), `enablePrivateNodes`, `enabledEncryptionVolume`, `securityGroups`, `upgradeConfig`, `subnetId`, `secondarySubnets`, `labels`, `taints`, `tags`, `autoScaleConfig`, `placementGroupConfigDto`.
- **Update** (`UpdateNodeGroupDto`, `PUT .../node-groups/{id}`): `numNodes`, `securityGroups`, `autoScaleConfig`, `upgradeConfig`.
- **Metadata** (`UpdateNodeGroupMetadataDto`, `PATCH .../node-groups/{id}/metadata`): `labels`, `tags`, `taints` — updated **only** through this endpoint, never via `update_nodegroup`.

## Security rules

- **Input validation**: All cluster-id and nodegroup-id must be validated via `validators.validate_id()` before URL construction — prevents path traversal
- **Write guard**: Mutating operations must check `self.allow_write` flag
- **Sensitive data guard**: K8s Secret reads must check `self.allow_sensitive_data_access`
- **Tokens in memory only**: Never written to disk or logged
- **Credentials not logged**: Error messages and debug logs never include tokens or secrets
- **Timeout**: All HTTP requests have 30s timeout to prevent hanging

## HTTP Client

`VksClient` in `client.py`:
- Async httpx client with Bearer token auth
- **Retry logic**: Max 3 retries with exponential backoff (1s → 2s → 4s) for 5xx and timeout errors
- **401 handling**: Auto-refresh token and retry once
- **Timeout**: 30s on all requests

## Testing

```bash
cd src/vks-mcp-server && uv run pytest tests/ -v
```

- Tests covering all handlers (incl. tool-schema introspection and outputSchema assertions)
- Uses `respx` for mocking async HTTP calls — no real API calls, no credentials needed
- Uses `pytest-asyncio` for async test support

**Manual testing** (real API, credentials from `~/.greenode/`):

```bash
# Interactive: MCP Inspector over stdio (add DANGEROUSLY_OMIT_AUTH=true to skip the proxy token)
npx @modelcontextprotocol/inspector uv run vks-mcp-server --allow-write

# Scripted: pipe JSON-RPC (initialize → initialized → tools/list | tools/call) into the stdio server
printf '...' | uv run vks-mcp-server
```

Do NOT use `uv run mcp dev` — FastMCP is built inside `create_server()`/`main()`, there is no module-level `mcp` object. Verify auth first with the `get_access_token` tool. Full walkthrough (Inspector notes, env overrides, smoke-test snippet) lives in `src/vks-mcp-server/README.md` → Development.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | FastMCP entry point, handler registration, CLI flags |
| `config.py` | Config loading from `~/.greenode/`, env var overrides, REGIONS dict |
| `auth.py` | TokenManager — async OAuth2 Client Credentials with auto-refresh |
| `client.py` | VksClient — async HTTP with retry + token refresh |
| `validators.py` | ID format validation |
| `cluster_handler.py` | 12 cluster tools (CRUD + kubeconfig + events + auto-upgrade + validation + auto-healing) |
| `nodegroup_handler.py` | 9 nodegroup tools (CRUD + metadata + nodes + dry-run + version upgrade) |
| `k8s_handler.py` | 7 K8s tools (list/manage resources + logs + events + apply YAML + generate app manifest) |
| `k8s_apis.py` | K8s API client wrapper using kubernetes library |
| `k8s_client_cache.py` | TTL cache for K8s clients (840s) |
| `version_handler.py` | 1 tool (cluster versions) |
| `discovery_handler.py` | 8 discovery tools (vpc/subnet/flavor/sshkey/secgroup/volumetype/placementgroup lists + quota) — vServer + VKS quota, name→ID resolution for create bodies |
| `discovery_cache.py` | Short-lived TTL cache for discovery results (per-tool TTLs, `refresh` bypass) |
| `prompts_handler.py` | 3 MCP prompts (getting-started, create-cluster, create-nodegroup) |
| `models.py` | Pydantic models + markdown formatters for responses |

## Documentation update rule

After completing any feature or bugfix, update ALL related documentation:

1. **README.md** — Update tool list, usage examples if changed
2. **CLAUDE.md** — Update tool count, key files if new files added
3. **Changelog** — Add entry describing the change

Code without docs is not done.

## Relationship with greennode-cli

Both projects share:
- Same config files (`~/.greenode/credentials`, `~/.greenode/config`)
- Same REGIONS dict (HCM-3, HAN endpoints)
- Same IAM auth flow (camelCase fields)
- Same env var names (`GRN_CLIENT_ID`, etc.)

Key differences:
- greennode-mcp is **async**, greennode-cli is **sync**
- greennode-mcp returns **structured JSON** (Pydantic models with `outputSchema`/`structuredContent`) for data tools and **markdown** for blob/write tools (for AI readability); greennode-cli returns **JSON/table/text**
- greennode-mcp has **K8s resource management**, greennode-cli does not
