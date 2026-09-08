# GreenNode vLB MCP Server

MCP server for **GreenNode vLB** — load balancers, listeners, backend pools and
members, Layer 7 routing policies, and TLS certificates.

**47 tools** (25 in the default read-only mode) and **6 MCP prompts**, covering
every operation the vLB API exposes. Read-only by default; mutations require
`--allow-write`.

## Configuration

Credentials come from `~/.greennode/credentials` and `~/.greennode/config` (INI,
shared with `greennode-cli`), or from environment variables:

| Variable | Purpose |
|----------|---------|
| `GRN_CLIENT_ID` | Override client_id |
| `GRN_CLIENT_SECRET` | Override client_secret |
| `GRN_PROFILE` | Select profile (default: `default`) |
| `GRN_DEFAULT_REGION` | Override region (`HCM-3` or `HAN`) |
| `GRN_PROJECT_ID` | Override project_id |
| `GRN_MCP_AUTH_DEBUG` | Diagnostic auth logging (HTTP transport only) |

## Running

```bash
# stdio, read-only (default)
uv run vlb-mcp-server

# stdio, mutations enabled
uv run vlb-mcp-server --allow-write

# HTTP transport
uv run vlb-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

### Auth (HTTP transport)

Each request may carry the caller's IAM bearer token in `Authorization`; every
vLB call then runs as that caller, with their own project and permissions. With
no token the shared service account is used; with neither, the request is 401.
A rejected user token never falls back to the service account.

## Regions and projects

Every resource is region-scoped: `HCM-3` (default) or `HAN`. The project id is
resolved automatically per region and never asked of the user.

vLB has no project-listing endpoint of its own, so that lookup — and the zone,
VPC, subnet and server discovery tools — read the **vServer** gateway. They are
strictly reads; this server never mutates a vServer resource.

## Tools

### Guidance

| Tool | Access | Description |
|------|--------|-------------|
| `get_feature_guide` | read | Step-by-step flow for a composite capability |
| `get_access_token` | read | Current IAM token, region and endpoint |

Prompts: `vlb_getting_started`, `vlb_create_load_balancer`, `vlb_manage_members`,
`vlb_route_http_traffic`, `vlb_terminate_tls`, `vlb_high_availability`.

### Catalogue and discovery

| Tool | Access | Description |
|------|--------|-------------|
| `list_load_balancer_packages` | read | Buyable packages (`scope="all"` includes retired ones) |
| `list_listener_headers` | read | Header names a listener may insert |
| `list_zones` | read | Availability zones (via vServer) |
| `list_vpcs` | read | VPCs (via vServer) |
| `list_subnets` | read | Subnets of a VPC — where a load balancer is placed (via vServer) |
| `list_servers` | read | Servers and their private IPs — pool member candidates (via vServer) |

### Load balancers

| Tool | Access | Description |
|------|--------|-------------|
| `list_load_balancers` | read | List, with best-effort server-side filtering |
| `get_load_balancer` | read | One load balancer in full |
| `list_load_balancers_by_subnet` | read | Load balancers on a subnet |
| `list_load_balancer_scale_history` | read | Autoscaling events |
| `get_load_balancer_clone_metadata` | read | What a clone needs supplied |
| `get_load_balancer_status` | read | **Poll this after every write** — rolls up the whole tree, reports `settled` |
| `validate_load_balancer_create` | read | Check a create body locally, no API call |
| `create_load_balancer` | write | Create, optionally with a listener and pool inline |
| `delete_load_balancer` | destructive | Delete it and everything under it |
| `resize_load_balancer` | write | Move to a different package |
| `configure_load_balancer_scaling` | write | Multi-AZ node bounds and subnets |
| `update_load_balancer_bandwidth` | write | Attach/change the bandwidth package |
| `create_load_balancer_clone` | write | Create a copy as a new load balancer |

### Listeners

| Tool | Access | Description |
|------|--------|-------------|
| `list_listeners` | read | Listeners of a load balancer |
| `get_listener` | read | One listener in full |
| `create_listener` | write | Add a listener |
| `update_listener` | write | **Full replacement** of the configuration |
| `delete_listener` | destructive | Delete it and its L7 policies |

### Pools, members and health monitor

| Tool | Access | Description |
|------|--------|-------------|
| `list_pools` | read | Pools of a load balancer |
| `get_pool` | read | One pool, with its health monitor |
| `list_pool_members` | read | Backend members |
| `get_pool_health_monitor` | read | The pool's health check |
| `create_pool` | write | Add a pool (health monitor mandatory) |
| `update_pool` | write | Algorithm, stickiness, TLS |
| `delete_pool` | destructive | Delete it, its members and its monitor |
| `attach_pool_members` | write | Add backends, keeping the existing ones |
| `detach_pool_members` | destructive | Remove named backends, keeping the rest |
| `update_pool_members` | destructive | **Replace the whole member list** |
| `update_pool_health_monitor` | write | Change the health check, carrying the rest forward |

### L7 routing policies

| Tool | Access | Description |
|------|--------|-------------|
| `list_l7_policies` | read | Policies of a listener, with `position` |
| `get_l7_policy` | read | One policy with its rules |
| `create_l7_policy` | write | Add a path/host routing rule |
| `update_l7_policy` | write | **Full replacement**, including rules |
| `delete_l7_policy` | destructive | Delete a policy |
| `update_l7_policy_order` | write | Change evaluation order |

### Certificates

| Tool | Access | Description |
|------|--------|-------------|
| `list_certificates` | read | Certificates in the project |
| `get_certificate` | read | One certificate |
| `import_certificate` | write | Import PEM material |
| `delete_certificate` | destructive | Delete permanently |

## Two behaviours worth knowing before you use this

**Writes are asynchronous.** Nearly every mutation returns before the work is
done, and the gateway rejects a second change while one is in flight. After any
write, poll `get_load_balancer_status` until `settled` is true. Where a write
echoes back what it asked for, the result says so — `PoolMemberListData` sets
`confirmed=false`, and `update_pool_health_monitor` returns a distinct
`HealthMonitorUpdateData` rather than a read-shaped monitor.

**`update_pool_members` replaces the entire member list.** Use
`attach_pool_members` / `detach_pool_members` to add or remove individual
backends; they read the current list first.

## Not covered

Nothing from the vLB API is left out — all 37 operations are exposed. Two
deliberate narrowings:

- `/loadBalancers/all_packages` is `scope="all"` on
  `list_load_balancer_packages` rather than its own tool, so listing packages
  that cannot be bought is a deliberate choice.
- `list_load_balancers` exposes only the filter fields that mean anything for a
  load balancer (`name`, `status`, `subnet_id`, `vpc_id`, `zone_id`). The API's
  `FilterRequest` is a shared vServer grab-bag whose other fields the gateway
  ignores here.

## Testing status

181 tests, all `respx`-mocked — no network, no credentials. Several API details
the OpenAPI spec leaves ambiguous are still marked ASSUMED; see the "Known open
questions" section of `CLAUDE.md`.

Develop-environment auto-deploy is not wired for this package yet (the shared
`deploy.yml` push path is hardcoded to another package); production deploys work
through the release tag.

## Development

```bash
cd src/vlb-mcp-server
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format --check .

# MCP Inspector over stdio
npx @modelcontextprotocol/inspector uv run vlb-mcp-server
```

## License

Apache-2.0.
