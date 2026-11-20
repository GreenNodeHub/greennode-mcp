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

`get_access_token` over HTTP returns the caller's own token, and refuses when
the request carries none. It never returns the service-account token there;
only the stdio transport, which is local and single-user, does.

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
| `get_access_token` | read | Current IAM token, region and endpoint (over HTTP: the caller's own token only) |

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
| `list_load_balancers` | read | List; `name`/`zone_id` filter server-side, the rest locally |
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
| `update_listener` | destructive | **Full replacement** — omitted fields are cleared, including the default pool |
| `delete_listener` | destructive | Delete it and its L7 policies |

### Pools, members and health monitor

| Tool | Access | Description |
|------|--------|-------------|
| `list_pools` | read | Pools of a load balancer |
| `get_pool` | read | One pool, with its health monitor |
| `list_pool_members` | read | Backend members |
| `get_pool_health_monitor` | read | The pool's health check |
| `create_pool` | write | Add a pool (health monitor mandatory) |
| `update_pool` | write | Algorithm, stickiness, TLS (an HTTP pool keeps its current flags when unset) |
| `delete_pool` | destructive | Delete it, its members and its monitor |
| `attach_pool_members` | write | Add backends, keeping the existing ones |
| `detach_pool_members` | destructive | Remove named backends, keeping the rest |
| `update_pool_members` | destructive | **Replace the whole member list** |
| `update_pool_health_monitor` | write | Change the health check, carrying the rest forward (`''` clears a text field) |

### L7 routing policies

| Tool | Access | Description |
|------|--------|-------------|
| `list_l7_policies` | read | Policies of a listener, with `position` |
| `get_l7_policy` | read | One policy with its rules |
| `create_l7_policy` | write | Add a path/host routing rule |
| `update_l7_policy` | write | **Full replacement**; `rules` required (at least one) |
| `delete_l7_policy` | destructive | Delete a policy |
| `update_l7_policy_order` | write | Change evaluation order (positions are 1-based) |

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
  ignores here. Of the five, only `name` and `zone_id` are sent to the API —
  the gateway accepts `status`, `subnet_id` and `vpc_id` and then drops them, so
  this server applies those itself and names them in `filtered_locally`.
  Paging likewise only takes effect alongside `name`; without it the whole
  project comes back in one response.

## Testing status

406 tests, all `respx`-mocked — no network, no credentials.

**Verified against the live HCM-3 gateway (2026-09-08).** Every read tool was
run against a project holding 48 load balancers, and every create-body rule was
checked against the gateway's own dry-run validator
(`POST /v2/{projectId}/validation`, which runs the real validators and creates
nothing): of 29 rules, 28 now give the same verdict locally as the API does.
That closed the questions the OpenAPI spec left ambiguous — the create-response
id key, the query encoding, the page base, the CIDR separator, the nested
member array — and corrected several rules the spec had wrong, including the
status vocabulary, the member `weight` range, the required health-monitor
timings, and three list filters the gateway accepts and silently ignores.

**Write paths were exercised live too**: one throwaway load balancer was
created, given a pool, a member and a listener, then deleted. That confirmed the
202 body shape (`{"uuid": ...}`, no job id), that a member without
`monitorPort` now succeeds, that `weight=0` is stored, that re-adding an
unchanged member performs no write, and that a write issued while another is in
flight really answers `LoadBalancerNotReady` — which the client now reports as
transient rather than as a bad request. It also produced the real status
sequence, including three listener/pool health values that appear nowhere in the
gateway's own status constants.

A second run built an ALB with an HTTPS listener and the full L7 policy
surface. That settled the last spec ambiguity — `position` is **1-based** — and
turned up four things worth knowing before you use the certificate and clone
tools: a listener's `certificate_authorities` lists only the *extra* SNI certs
so an empty list does not mean "no certificate"; the gateway injects three
`X-Forwarded-*` headers into any L7 listener created without them;
`configure_load_balancer_scaling` on a load balancer that is not autoscalable
answers a bare 500; and **cloning holds the source load balancer in `UPDATING`
for about 15 minutes**, refusing every other write to it including delete.

A third run drove every remaining tool against an ALB with two pools, members,
HTTP and HTTPS listeners and three L7 policies. It found **two tools that could
never have worked**: `update_pool_health_monitor` read the probe from a field
the API declares and never fills in, so it failed for every pool; and
`update_pool` omitted the monitor, which the gateway re-validates regardless
and rejects for any HTTP probe. Both are fixed and verified. It also showed
that `update_listener` clears `defaultPoolId` and `insertHeaders` when they are
omitted — it is now marked destructive.

**HAN was checked too.** Every read tool works there, and asking for a region
other than the configured one makes the server resolve that region's own
project rather than reusing the configured id. The HTTP transport, its identity
middleware and the `--auth-debug` redaction were driven against a locally
running server.

One finding worth carrying into your own use: **package ids are per-zone and no
two zones share one.** HCM03-1A offers 58 packages and HCM03-1B offers 16, with
zero ids in common, and calling `list_load_balancer_packages` without a
`zone_id` answers for the platform's default zone rather than for all of them.
Pass the zone you will actually create in, or the create fails with "Invalid
package id".

Limits worth knowing:

- **Three write paths were never exercised**: resize to a different package,
  autoscale configuration and bandwidth attachment. Each changes what the
  account is billed, so only their refusal paths are known.

`CLAUDE.md` records what was verified, and what remains open.

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
