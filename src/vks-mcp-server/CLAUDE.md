# CLAUDE.md — VKS MCP Server

Product-specific guidance for `src/vks-mcp-server`. Monorepo-wide conventions
(tool naming, DTOs, TDD, branch/release flow, security rules) live in the
**repo-root CLAUDE.md** — read that first.

## Product overview

MCP server for VKS (GreenNode Kubernetes Service) clusters and the Kubernetes
resources inside them.

- **38 tools** across 6 handlers: Auth, Cluster, NodeGroup, Version, Discovery, K8s
- **3 MCP prompts** (`vks_getting_started`, `vks_create_cluster`, `vks_create_nodegroup`) — portable Vietnamese guidance for any MCP client; always available, no `--allow-write` needed
- **Structured output** — data tools return Pydantic models; FastMCP emits `outputSchema` + `structuredContent` (JSON). Blob tools (`get_access_token`, `get_cluster_kubeconfig`) stay `str`. Region is a fixed `Literal["HCM-3", "HAN"]`.

## VKS API quirks

- **Pagination is 0-based**: page 0 = first page
- **API returns 202** for most successful operations (not 200)
- **The greennode-cli is the source of truth for the current API** — the bundled `~/.greenode/mcp-specs/vks.json` OpenAPI file is stale
- Discovery (vpc/subnet/flavor/sshkey/secgroup/volume-type/placement-group) goes to the **vServer API** (token-only auth); `project_id` is auto-discovered from `GET /v1/projects` when unset
- **vServer list pagination is effectively a no-op**: `page`/`size` query params are ignored and every list endpoint returns the full set in one response (envelope reports `page=0 / pageSize=0 / totalPage=0`, `len(listData) == totalItem`). Discovery fetchers go through `_fetch_all_items`, which uses that single-call fast path but pages explicitly as a safety net if a response ever reports `totalItem > len(listData)` — so results never truncate silently as an account grows.

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

## Write DTO field scope

All write DTOs (`CreateClusterComboDto`, `UpdateClusterDto`, `CreateNodeGroupDto`, `UpdateNodeGroupDto`, `UpdateNodeGroupMetadataDto`, `NodeGroupSpec`, `UpgradeConfig`, and the nested `NodeGroupTaint`, `AutoScaleConfig`, `PlacementGroupConfig`, `AutoUpgradeConfig`, `AutoHealingConfig`) are configured with `extra="forbid"`. They mirror the greennode-cli field set.

Cluster write DTOs:

- **Create** (`CreateClusterComboDto`, `POST /v1/clusters`): required `name`, `version`, `networkType`, `vpcId`. Creates the **control plane only** (matching the CLI) — add workers via `create_nodegroup`. The API's `nodeGroups` array is **deprecated** and rejected (`extra="forbid"`). Optional: `enablePrivateCluster`, `releaseChannel`, `enabled{LoadBalancer,BlockStoreCsi,ServiceEndpoint}Plugin`, `azStrategy`, `description`, `subnetId`, `cidr`, `secondarySubnets`, `listSubnetIds`, `nodeNetmaskSize`, `autoUpgradeConfig`, `autoHealingConfig`.
- **Update** (`UpdateClusterDto`, `PUT /v1/clusters/{id}`): required `version` + `whitelistNodeCIDRs`; optional plugin toggles `enabledLoadBalancerPlugin`, `enabledBlockStoreCsiPlugin`. Name, description, and release channel are **not** editable via this endpoint.

Node-group write DTOs:

- **Create** (`CreateNodeGroupDto`/`NodeGroupSpec`): `name`, `flavorId`, `diskType` (a volume-type **ID** from `list_volume_types`), `sshKeyId`, `diskSize`, `numNodes`, `os` (ubuntu/linux/rocky, top level — NOT inside `upgradeConfig`), `enablePrivateNodes`, `enabledEncryptionVolume`, `securityGroups`, `upgradeConfig`, `subnetId`, `secondarySubnets`, `labels`, `taints`, `tags`, `autoScaleConfig`, `placementGroupConfigDto`.
- **Update** (`UpdateNodeGroupDto`, `PUT .../node-groups/{id}`): `numNodes`, `securityGroups`, `autoScaleConfig`, `upgradeConfig`.
- **Metadata** (`UpdateNodeGroupMetadataDto`, `PATCH .../node-groups/{id}/metadata`): `labels`, `tags`, `taints` — updated **only** through this endpoint, never via `update_nodegroup`.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | FastMCP entry point, handler registration, CLI flags, auth modes |
| `config.py` | VksConfig + REGIONS endpoints; credential/profile loading delegates to `mcp_core.config.load_profile` |
| `auth.py` | Re-export of `mcp_core.auth.TokenManager` (IAM client credentials, auto-refresh) |
| `client.py` | VksClient extends `mcp_core.http.BaseClient` — adds the vServer service and default service `vks` |
| `validators.py` | Re-export of `mcp_core.validators.validate_id` |
| `cluster_handler.py` | 12 cluster tools (CRUD + kubeconfig + events + auto-upgrade + validation + auto-healing) |
| `nodegroup_handler.py` | 9 nodegroup tools (CRUD + metadata + nodes + dry-run + version upgrade) |
| `k8s_handler.py` | 7 K8s tools (list/manage resources + logs + events + apply YAML + generate app manifest) |
| `k8s_apis.py` | K8s API client wrapper using kubernetes library |
| `k8s_client_cache.py` | TTL cache for K8s clients (840s) |
| `version_handler.py` | 1 tool (cluster versions) |
| `discovery_handler.py` | 8 discovery tools (vpc/subnet/flavor/sshkey/secgroup/volumetype/placementgroup lists + quota) — vServer + VKS quota, name→ID resolution for create bodies |
| `discovery_cache.py` | Package TTL config on top of `mcp_core.cache.DiscoveryCache` |
| `prompts_handler.py` | 3 MCP prompts (getting-started, create-cluster, create-nodegroup) |
| `models.py` | Pydantic models + markdown formatters for responses |

## Testing

```bash
cd src/vks-mcp-server && uv run pytest tests/ -v
```

- Tests cover all handlers (incl. tool-schema introspection and outputSchema assertions); `respx` mocks all HTTP — no credentials needed
- **Manual testing** (real API, credentials from `~/.greenode/`): MCP Inspector over stdio or piped JSON-RPC — full walkthrough in `README.md` → Development. Do NOT use `uv run mcp dev` (FastMCP is built inside `create_server()`/`main()`, no module-level `mcp` object). Verify auth first with the `get_access_token` tool.

## Relationship with greennode-cli

Both projects share config files (`~/.greenode/`), the REGIONS endpoints, the
IAM auth flow, and the `GRN_*` env var names. Tool names map 1:1 to CLI
command names. Key differences: the MCP server is **async** (CLI is sync),
returns **structured JSON** for data tools, and adds **K8s resource
management** which the CLI does not have.
