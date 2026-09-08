# CLAUDE.md — vLB MCP Server

Product-specific guidance for `src/vlb-mcp-server`. Monorepo-wide conventions
(tool naming, DTOs, TDD, branch/release flow) live in the **repo-root
CLAUDE.md** — read that first.

## Product overview

MCP server for GreenNode vLB (load balancing). **47 tools** in write mode, 25
read-only, plus 6 MCP prompts. Region-scoped (`HCM-3`, `HAN`).

Built from `openapi-vlb.json` at the repo root (OpenAPI 3.0.1, 24 paths / 37
operations / 57 schemas). **That spec contradicts itself in several places and
declares no error responses and no status enums** — the sections below record
what was corrected and what is still assumed. Where this file says ASSUMED,
nothing has been verified against the live gateway yet.

## vLB API quirks

### Endpoints and scoping

- Two gateways, both region-scoped:
  - vLB: `https://{hcm-3|han-1}.api.vngcloud.vn/vserver/vlb-gateway`
  - vServer: `https://{hcm-3|han-1}.api.vngcloud.vn/vserver/vserver-gateway`
- Every vLB path is `/v2/{projectId}/...`. **vLB has no project-listing
  endpoint**, so `project.require_project_id` resolves it from
  `GET /v1/projects` on the **vServer** gateway via `VlbClient.get_vserver`.
  The same gateway serves the zone/VPC/subnet/server discovery tools.
- Use the `/vserver/vserver-gateway` spelling. The shorter `/vserver-gateway`
  answers in HCM-3 but 404s in HAN (recorded by `vbackup-mcp-server`).
- Auth is declared as an apiKey header, so the client prepends `Bearer ` itself
  — which `mcp_core.http.BaseClient` already does.

### Envelope families

| Shape | Used by |
|-------|---------|
| `{"data": <object>}` | Single-object GETs, `importCA` |
| `{"data": [...]}` | Sub-resource lists: listeners, pools, members, L7 policies, headers |
| `{"listData", "page", "pageSize", "totalPage", "totalItem"}` | Top-level collections: load balancers, certificates, packages |
| bare array | `scale-history`, `loadBalancers/subnet/{id}` |
| bare object | `cas/{caId}`, `cloneMetadata`, the bandwidth update |
| untyped `Map<String,String>` | **All five creates** — see below |

All of it goes through `paging.as_list` / `unwrap` / `unwrap_one`; no handler
indexes a raw response.

### A create response does not name the new resource

`createLoadBalancer`, `createListener`, `createPool`, `createPolicy` and
`cloneLoadBalancer` all declare `{"type":"object","additionalProperties":
{"type":"string"}}` and the spec never says which key holds the id.

`paging.created_id` searches a preference list (`uuid`, `id`, `<resource>Id`,
any `*Id`/`*uuid` key, then a single-entry map) and **returns `""` rather than
raising**. `CreatedResourceData` carries `id`, the `raw` body and a `hint`.

This is not defensive padding. A model that hard-requires `id: str` turns a
*successful* create into a `ValidationError`, an agent reads that as failure and
retries, and the account is billed for two load balancers. Every create
docstring says: an empty `id` still means success — confirm with `list_…`, never
retry.

### Request and response spell the same concept differently

Confined entirely to the `from_api` classmethods. Model fields always take the
**request** name, so a caller reads back the vocabulary it must write.

| Concept | Request | Response | Model field |
|---|---|---|---|
| id (every resource) | — | `uuid` | `id` |
| LB scheme | `scheme` | `loadBalancerSchema` | `scheme` |
| LB subnet | `subnetId` | `privateSubnetId` | `subnet_id` |
| Pool name / protocol / algorithm | `poolName`, `poolProtocol`, `algorithm` | `name`, `protocol`, `loadBalanceMethod` | `name`, `protocol`, `algorithm` |
| Member IP / port | `ipAddress`, `port` | `address`, `protocolPort` | `ip_address`, `port` |
| Listener name / protocol / port | `listenerName`, `listenerProtocol`, `listenerProtocolPort` | `name`, `protocol`, `protocolPort` | `name`, `protocol`, `port` |
| Client cert | `clientCertificate` | `clientCertificateAuthentication` | `client_certificate` |
| Bandwidth package | `bandWidthPackageId` (capital W) | `packageId` | `bandwidth_package_id` |
| CA type | `type` | `certificateType` | `type` |

Two more traps in the same area:

- `ListenerDto.headers` and `ListenerDto.insertHeaders` are **different
  things** — the names a listener may insert versus the injections configured.
  Modelled as `available_headers` and `insert_headers`.
- `L7RuleDto` is the only DTO that does not use the
  `displayStatus`/`progressStatus` pair; it reports `provisioningStatus` and
  `operatingStatus`. Both are surfaced under their own names.

### Static path segments collide with `{loadBalancerId}`

`/loadBalancers/packages`, `/all_packages`, `/headers` and `/subnet/{id}` sit
where an id would. `mcp_core.validators.validate_id` **accepts** `packages`,
`headers` and `subnet`, so without a guard
`get_load_balancer(load_balancer_id="packages")` fetches the package catalogue
and parses it as an empty load balancer — no error, wrong answer.

`validators.validate_lb_id` checks `RESERVED_LB_SEGMENTS` **before** delegating
to `validate_id` (so `all_packages` gets the useful message rather than the
"alphanumeric only" one) and names the tool that actually serves each word.
Every `load_balancer_id` uses it; other ids use plain `validate_id`.

### Writes are asynchronous, and there are no status enums

Deletes and `rebalancing` answer `202` with no body; `updateListener`,
`updatePool`, `changesMembers`, `updateL7Policy` and `reorderPolicies` answer
`200` with no content. `BaseClient` turns all of those into `None`, so no
handler indexes a write result.

The gateway rejects a second mutation while one is in flight, so
`get_load_balancer_status` fans out to the load balancer plus its listeners and
pools and computes `settled`. Every write docstring routes there.

`status.IN_FLIGHT` is a **guessed** vocabulary — the spec declares no enums at
all — so `is_settled` **fails open**: an unrecognised status counts as settled
and the raw string is returned. Blocking an agent forever on a status nobody
anticipated is worse than starting one write a few seconds early.

### Pagination: two conventions, one of them undocumented

- `/cas` uses flat `name` / `page` / `size` with `page` documented as 1-based.
- `/loadBalancers` declares `filter` and `pageRequest` as `type: object` with no
  `style`/`explode`, so the spec does not actually say how they are encoded.
  `paging.lb_list_params` emits the flat Spring `@ModelAttribute` form and is
  the single change point (`_LIST_PARAM_STYLE`); the two alternatives are named
  in a comment there. **ASSUMED, including that `page` is 1-based.**
- `paging.fetch_list` walks the following pages whenever the envelope reports
  more than it returned, so a long list is never silently truncated even if the
  paging params fail to land.
- `LoadBalancerListData.filter_applied` is `False` when a `name` filter was
  passed but non-matching entries came back. Client-side filtering as a
  substitute is deliberately **not** done — it would make an ignored server
  filter look like it worked.

### Write-body traps

- **`PUT .../pools/{poolId}/members` replaces the entire list.** `GET` spells
  the fields `address`/`protocolPort`, `PUT` wants `ipAddress`/`port`.
  `PoolMemberItem.to_member_dto()` is the single conversion point;
  `attach_pool_members` / `detach_pool_members` do the read-modify-write, and
  the raw `update_pool_members` is DESTRUCTIVE with the warning first in its
  docstring.
- **There is no health-monitor write endpoint.** The only path is
  `PUT .../pools/{poolId}`, whose `algorithm` is required and whose
  `UpdateHMonitorRequest` requires all four thresholds — so a partial body
  resets the load balancing method and the thresholds at once.
  `update_pool_health_monitor` reads the pool and carries everything untouched
  forward. It returns the configuration that was **sent**, not a fresh read: the
  write is asynchronous, so reading back immediately reports the old values.

### Unconfirmed state is typed differently from a read

An asynchronous write cannot report the state it produced, but echoing the
request is still useful — provided it is not dressed up as a read.

- `update_pool_health_monitor` returns **`HealthMonitorUpdateData`**, not
  `HealthMonitorItem`. It carries only the requested settings plus the
  immutable probe protocol and a `hint`. Returning the read-shaped model meant
  mixing pre-update `id`/`display_status`/`progress_status` with post-update
  settings in one object, with nothing marking which was which — worse than
  either pure option.
- `PoolMemberListData` carries **`confirmed`**: `True` from
  `list_pool_members`, `False` from `attach_pool_members` /
  `detach_pool_members` / `update_pool_members`, where the list is what was
  requested.

Both point at the same rule: never hand back a value that looks like live state
when it is a pending intention.
- **`healthCheckProtocol` is immutable.** `CreateHMonitorRequest` has it,
  `UpdateHMonitorRequest` does not. `update_pool_health_monitor` therefore has
  no such parameter at all.
- **`allowedCidrs` / `blockedCidrs` are `string`, not arrays**, in both
  directions. The DTO accepts a list or a string and joins on `","`
  (`_common.list_to_csv`); the response model splits back to a list
  (`csv_to_list`). **The separator is ASSUMED** — and getting it wrong on
  `blockedCidrs` is a security bug, so it is a blocking verification item.
- **`LbPool.members` is typed as a single object** while
  `CreatePoolRequest.members` is an array of the same thing. Treated as a spec
  bug: `EmbeddedPoolDto.members` is a list. Telling an agent a pool can have one
  backend would be worse than being wrong about the wire shape.
- **`MemberRequest.backup` is required** even when false, so `MemberDto.backup`
  has a default and is never pruned by `exclude_none`.
- **`bandWidthPackageId`** really is spelled with a capital W in the spec; a
  test asserts the literal.
- **The CA name pattern forbids underscores** (`^[a-zA-Z0-9.-]{5,50}$`) — the
  only name in this API that does.
- **`update_listener` and `update_l7_policy` are full replacements.** Omitting
  `certificateAuthorities`, `insertHeaders` or `rules` may clear them; a policy
  left without rules matches every request.
- **Nothing can be renamed.** There is no `PUT /loadBalancers/{id}`, and the
  listener, pool and policy updates all omit the name field.

### Duplicate L7 policy path template

The spec declares `.../l7policies/{l7PolicyId}` for the GET and
`.../l7policies/{policyId}` for PUT/DELETE. These are one endpoint, not two —
a spec-generation artefact. All four tools take a single `policy_id` and build
the identical path, and `test_l7policies.py` asserts the GET and PUT URLs match.
Do not "fix" this by adding a second tool.

## Deliberate scope limits

All 37 operations are exposed. Two narrowings, both to stop an agent believing
something untrue:

- `/all_packages` is `scope="all"` on `list_load_balancer_packages`, not its own
  tool. Two tools differing only in "includes SKUs you cannot buy" invites
  picking one that fails the create.
- `list_load_balancers` exposes only `name`, `status`, `subnet_id`, `vpc_id`,
  `zone_id`. The spec's `FilterRequest` is a shared vServer grab-bag carrying
  `volumeTypes`, `encryptionTypes`, `dhcpOptionIds` and friends; the gateway
  ignores them here, so offering them would let an agent report a filter that
  never happened.

There is deliberately **no** blocking `wait_for_*` tool: `wait` is not an
allowed verb, and a tool that blocks for the minutes a provision takes hits
client timeouts while giving no intermediate signal. `get_load_balancer_status`
is the single-shot rollup to poll instead.

## Tool naming decisions

`clone` and `reorder` are not in `ALLOWED_VERBS` (repo-root
`tests/test_conventions.py`). The tools were **renamed** rather than the verb
list extended:

| API operation | Tool |
|---|---|
| `cloneLoadBalancer` | `create_load_balancer_clone` |
| `getCloneInfo` | `get_load_balancer_clone_metadata` |
| `reorderPolicies` | `update_l7_policy_order` |
| `haConfiguration` | `configure_load_balancer_scaling` |

The reason is not only taste: `ALLOWED_VERBS` and `WRITE_PREFIXES` are two
coupled lists, and adding a verb to the first without the second would make
those tools silently exempt from the `## Requirements` docstring rule. The new
names are also more accurate — the clone endpoint POSTs a new load balancer, and
reordering rewrites `position` on existing policies.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | Instructions, middlewares, argparse; `create_server` + `register_handlers` |
| `config.py` | `Region`, the two services, `REGIONS`, `VlbConfig.get_base_url` |
| `client.py` | `VlbClient` + `get_vserver()` for the cross-gateway reads |
| `project.py` | `require_project_id` — resolves via the vServer gateway |
| `paging.py` | Envelope normalisation, `created_id`, `lb_list_params`, `fetch_list` |
| `status.py` | `IN_FLIGHT` vocabulary and the fail-open `is_settled` |
| `validators.py` | `validate_lb_id` + `RESERVED_LB_SEGMENTS` |
| `discovery_cache.py` | `TTL_CONFIG`; identity-scoped keys |
| `*_handler.py` | One per domain: loadbalancer, pool, listener, l7policy, certificate, catalogue, discovery, prompts, auth |

`register_handlers` exists separately from `create_server` so the complete tool
surface can be built without a running process — the guidance tests assert that
every tool a guide, the server instructions, or a cache TTL names actually
exists.

### The models/ package

| Module | Holds |
|--------|-------|
| `_common.py` | `_resource_id`, `_zone_id`, `csv_to_list`/`list_to_csv`, `TagDto` |
| `catalogue.py` | Packages, header names, and the vServer projections (zone, VPC, subnet, server) |
| `loadbalancer.py` | Load balancer, scale history, clone metadata, bandwidth, status rollup, `CreatedResourceData`, `ValidationResult` |
| `listener.py` | Listener |
| `pool.py` | Pool, member, health monitor, `to_member_dto` |
| `l7policy.py` | Policy and rule |
| `certificate.py` | Certificate |
| `requests.py` | Every write DTO |

Handlers import from `greennode.vlb_mcp_server.models`, never a submodule.

### Caching policy

`TTL_CONFIG` caches the catalogue (1h), the vServer infrastructure lookups
(1-2 min) and the top-level vLB collections (30-60s). What is **absent** matters
more: listeners, pools, members, L7 policies, health monitors and every `get_*`
are never cached, because they are read inside read-modify-write cycles and
while polling a write. A 30-second-stale read there means writing back a stale
member list.

## Guidance placement policy

| Layer | Carries |
|-------|---------|
| Tool docstring | The per-tool contract: `## Requirements`, `## Workflow`, what the tool returns |
| `get_feature_guide` / prompts | Multi-endpoint choreography, which questions to ask, confirm gates (Vietnamese) |
| `SERVER_INSTRUCTIONS` | Session principles: modes, regions, the async-write and member-replace rules |
| Error messages | The next step — every raise names the tool to call instead |

## Testing

| File | Covers |
|------|--------|
| `helpers.py` | Constants, IAM/project mocks, envelope builders, payloads in **response** spellings |
| `test_server.py` | Config, region endpoints, project resolution, identity isolation |
| `test_paging.py` | Envelopes, `created_id` across every shape, `lb_list_params`, the page loop, `validate_lb_id` |
| `test_catalogue.py` | Package scopes, header names, the vServer proxies, cache behaviour |
| `test_loadbalancers.py` | CRUD, RPC actions, status rollup, local validation, the empty-id create |
| `test_pools.py` | Pools, the member RMW regressions, health-monitor carry-forward |
| `test_listeners.py` | CRUD, CIDR string handling, name drift |
| `test_l7policies.py` | CRUD, the duplicate-template URL, action/rule validation, reordering |
| `test_certificates.py` | Flat pagination, bare-object get, private-key hygiene |
| `test_guidance.py` | Guides exist, prompts resolve, and no guide or TTL names a tool that does not exist |

All `respx`-mocked. Handlers are called directly, so **every parameter must be
passed explicitly** — a `Field(...)` default arrives as a `FieldInfo` object
when MCP is not resolving it.

## Known open questions

Nothing below has been verified against the live gateway. Each is written so it
can be answered with one call; record answers here under a *Verified against the
live HCM-3 gateway* heading with the date.

**Blocking — these shape a write body:**

1. Which key does each of the five create responses use for the new id?
2. How is the `/loadBalancers` query actually encoded — flat, `filter.`
   dot-notation, or a JSON blob?
3. Is that endpoint's `page` 0- or 1-based?
4. What separates `allowedCidrs` / `blockedCidrs` entries, and does `""` mean
   allow-all or deny-all? (A security bug if wrong on `blockedCidrs`.)
5. Does the nested `pool.members` in `create_load_balancer` want an array or a
   single object?
6. Does `PUT /pools/{poolId}` without `healthMonitor` preserve or delete the
   monitor?
7. Does `GET /v1/projects` on the vServer gateway work with a vLB-only token?
   If it 403s, `project.py` and the whole discovery handler need
   `GRN_PROJECT_ID` as a hard requirement. **Check this first.**

**Non-blocking:**

8. The real `displayStatus` / `progressStatus` value sets, to replace the
   guessed `status.IN_FLIGHT`.
9. Do the 202 endpoints ever return a job id? That would beat polling.
10. Does `/loadBalancers/packages` require `zoneId`, and does `/all_packages`
    reject it?
11. What shape is an error body? The spec declares none, and
    `BaseClient._raise_error` reads `{"message": …}`.
12. Region parity — does HAN expose the same packages and paths?
13. Is `bandWidthPackageId` really capital-W on the wire?
14. Is `reorderL7Policies` `position` 0- or 1-based? The spec says both.
15. Are the two L7 policy path templates genuinely one endpoint?

## Relationship with greennode-cli

No vLB commands exist in `greennode-cli` at the time of writing, so
`openapi-vlb.json` is the only source. Where the CLI later disagrees with this
package, the CLI wins — it is generated against the running service.
