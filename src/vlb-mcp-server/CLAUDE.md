# CLAUDE.md — vLB MCP Server

Product-specific guidance for `src/vlb-mcp-server`. Monorepo-wide conventions
(tool naming, DTOs, TDD, branch/release flow) live in the **repo-root
CLAUDE.md** — read that first.

## Product overview

MCP server for GreenNode vLB (load balancing). **47 tools** in write mode, 25
read-only, plus 6 MCP prompts. Region-scoped (`HCM-3`, `HAN`).

### Sources of truth, in order

1. **The gateway source**, `../vlb-gateway` — Spring Boot, so its DTO
   annotations are the request contract, `validator/load_balancer/*.java` are
   the cross-field rules, and `helper/LBGatewayStatus.java` is the status
   vocabulary. This wins every disagreement.
2. **The live HCM-3 gateway**, including its dry-run validator
   `POST /v2/{projectId}/validation` — `@Hidden`, used by the billing service.
   It runs the real `CreateLoadBalancerValidator` and creates nothing, so a
   create body can be checked against the actual rules for free. This is how
   the rules below were confirmed rather than inferred.
3. `openapi-vlb.json` at the repo root (OpenAPI 3.0.1, 24 paths / 37
   operations / 57 schemas). **It contradicts itself in several places and
   declares no error responses and no status enums.** Useful for the path list;
   trust nothing else in it that the two sources above cover.

Where this file still says ASSUMED, it has not been checked against any of the
three.

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
  answers in HCM-3 but **404s in HAN** — measured here, not inherited:
  `/vserver/vserver-gateway/v1/projects` → 200, `/vserver-gateway/v1/projects`
  → 404 on `han-1`.
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

- `ListenerDto.headers` reads like "the names a listener may insert" and is
  **not**: it holds the names of the listener's own configured `insertHeaders`.
  Two live observations — an HTTPS listener reporting its three configured
  names rather than the eleven `list_listener_headers` returns, and both
  fields emptying together on one write. Modelled as `inserted_header_names`,
  kept only because the API sends it; `insert_headers` carries the same
  information with the values attached, and `list_listener_headers` is the
  only source of the available set.
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

`status.IN_FLIGHT` is transcribed from `LBGatewayStatus.LoadBalancer`. Settled
means `CREATED`, `ACTIVE`, `ERROR` or `DELETED`; every other value the gateway
can report is a transition. `is_settled` still **fails open** — an unrecognised
status counts as settled and the raw string is returned — because blocking an
agent forever on a status nobody anticipated is worse than starting one write a
few seconds early.

The set it replaced was a guess, and the guess was wrong in both directions: it
omitted `MIGRATING`, `CREATING-BILLING`, `SCALING_IN`, `SCALING_OUT` and
`UNDETERMINED`, so a load balancer mid-scale read as settled and the next write
got `400 LoadBalancerNotReady`; and it invented `PENDING_DELETE`, `RESIZING`,
`IN_PROGRESS` and `PROCESSING`, which the gateway cannot emit.
`test_gateway_contract.py` parametrises the full list so it cannot drift back.

**Measured on a real load balancer** (create → pool → member → listener →
delete):

| Phase | `displayStatus` / `progressStatus` | Took |
|---|---|---|
| create | `CREATING` / `CREATING` → `ACTIVE` / `CREATED` | ~105 s |
| any sub-resource write | `UPDATING` / `UPDATING` → `ACTIVE` / `CREATED` | 10-20 s |
| delete | `DELETING` / `DELETING` | async; the load balancer is still listed immediately after the 202 |

Two things that table settles:

- **The two fields are different vocabularies.** `displayStatus` says `ACTIVE`
  where `progressStatus` says `CREATED`. Neither alone is the lifecycle.
- **Every sub-resource write moves the load balancer itself to `UPDATING`.**
  Creating a pool, changing members, adding a listener and deleting any of them
  all do it — which is why polling `get_load_balancer_status` is enough, and
  why a write issued 2 s after another really does come back
  `400 LoadBalancerNotReady`.

Listener and pool `displayStatus` are a **third** vocabulary, and the health
values in it appear nowhere in `LBGatewayStatus`: live responses gave
`CREATING`, `DELETING`, `HEALTHY`, `UNHEALTHY` and `OFFLINE`.
`LoadBalancerStatusData.build` folds every child status into `settled`, so the
distinction matters — `CREATING`/`DELETING` must block a write, while
`UNHEALTHY` and `OFFLINE` must not. An unhealthy pool is settled; it is just
unhealthy, and treating it as in-flight would leave an agent polling forever on
backends that are simply down.

### Errors: three shapes, and "busy" arrives as 400

`controller/ApiExceptionHandler.java` emits:

| Shape | When |
|---|---|
| `{"message": "..."}` | Most failures, including **quota exhaustion** |
| `{"message": ..., "errorCode": ..., "statusCode": 400}` | The three not-ready codes below |
| `{"message": ..., "code": 452}` with HTTP 404 | Project not found |

`helper/ErrorCodeConstants.java` defines exactly three `errorCode` values:
**`LoadBalancerNotReady`**, **`ListenerNotReady`**, **`PoolNotReady`**. All
three are HTTP **400**, and so is a lost Redis lock
(`"LOAD-BALANCER <id> is in-process"` / `"... is timeout"`). `BaseClient`
renders a 400 as `Bad request: ...`, which tells an agent to change the body —
the one thing that cannot help, because the body was accepted and the resource
was merely busy. `VlbClient._raise_error` relabels those and names
`get_load_balancer_status`.

Two corrections to the monorepo-level assumption:

- **Quota exhaustion is 400 here, not 409** (`"Exceeded {0} quota. Current
  used: {1}, max: {2}."`). It is terminal, so it deliberately keeps the generic
  wording — a "poll and retry" hint would send an agent into a loop that can
  never succeed.
- vLB's 409s are unrelated to quotas: an autoscale group mid-scale, and DNS
  lookup failures.

### Pagination: two conventions, one of them undocumented

- `/cas` uses flat `name` / `page` / `size`, `page` 1-based.
- `/loadBalancers` is flat Spring `@ModelAttribute` too, `page` 1-based
  (`PagingHelper.checkInput` requires `page > 0`). `paging.lb_list_params` is
  the single change point (`_LIST_PARAM_STYLE`).
- **Paging on `/loadBalancers` only works when `name` is also set.** The
  controller declares two mappings and the paged one is
  `@GetMapping(params = {"name", "page", "size"})`; without a name the plain
  mapping wins, `listAllByFilter` returns the whole project in one response,
  and `page` / `pageSize` / `totalPage` come back as **0**. Measured live: 48
  load balancers returned for `page=1&size=1`.
- **Only `name` and `zoneId` are filters the gateway acts on.**
  `VlbGatewayHelper.createSearchCriteria` forwards `projectId`, `name`,
  `zoneId`, `tagIds` and `dhcpOptionIds` and drops the rest, so `status`,
  `subnetIds` and `vpcIds` were accepted and silently ignored — a bogus value
  for any of them provably returns the full list. `lb_list_params` no longer
  sends them (`paging.SERVER_SIDE_FILTERS`); `list_load_balancers` applies
  them to the results instead and names them in `filtered_locally`, and
  `total_item` then counts the filtered list rather than repeating the
  gateway's unfiltered total.
- `name` is a substring match, not equality — `name=vks` matched 46 of 48.
  `filter_applied` uses `in`, so that is not mistaken for an ignored filter.
- `paging.fetch_list` walks the following pages whenever the envelope reports
  more than it returned, so a long list is never silently truncated even if the
  paging params fail to land.

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
  (`csv_to_list`). The comma is confirmed — `AclCidrValidator` splits on it and
  validates each entry as strict IPv4 `a.b.c.d/0-32` — and blank or null means
  no restriction rather than deny-all. Also note the **ACL quota counts the
  summed entries across every listener of the load balancer**, so a long
  `blockedCidrs` on one listener can exhaust it for the others.
- **`LbPool.members` is typed as a single object** while
  `CreatePoolRequest.members` is an array of the same thing. Treated as a spec
  bug: `EmbeddedPoolDto.members` is a list. Telling an agent a pool can have one
  backend would be worse than being wrong about the wire shape.
- **`MemberRequest.backup` is required** even when false, so `MemberDto.backup`
  has a default and is never pruned by `exclude_none`.
- **Both `monitorPort` and `weight` are primitive `int`, and both must be
  sent.** An absent field arrives as 0, and the two fail differently:
  - `monitorPort` has `@Range(min = 1, …)`, so 0 is rejected outright with
    "Invalid port number". `MemberDto` fills it from `port`.
  - `weight` has `@Range(min = 0, …)`, so 0 is **accepted and stored** — the
    member sits in the pool taking no traffic. A member created without a
    weight came back `weight=0` on a live run. `MemberDto` defaults it to 1.
    This file previously called omitting `weight` "safe"; it is the worse of
    the two, because nothing fails.
- **`weight` spans 0-256**, and an explicit 0 is meaningful: it drains a member
  without removing it. The DTO declared `ge=1` and rejected that.
- **All four health-monitor timings are `@NotNull`**, and `timeout` must be
  strictly below `interval`. The DTO had them optional, so four required fields
  could be omitted and the timing relationship went unchecked.
- **`healthMonitor` is required on a pool**, embedded or standalone
  (`pool.healthMonitor: must not be null`).
- **An HTTP pool must carry `stickiness` and `tlsEncryption`; any other
  protocol must not.** Checked before protocol compatibility, so a Layer 7 body
  with a TCP pool is rejected for the stickiness mismatch first.
- **`maxSize` is capped at 15** (`ScaleValidationHelper.MAX_ALLOWED_SIZE`), and
  `minSize` must be at least one node per availability zone the `subnets` span.
  Only the cap and `minSize <= maxSize` are checkable locally; the zone count
  needs the subnets resolved, so it stays in the docstring.
- **Inserted headers**: total `headerName + headerValue` length across all of
  them must be under 1024; every value must be `"true"` unless the name starts
  with `Access`; a name containing `SSL` is rejected on an HTTP listener; and
  the name must be one of `list_listener_headers` (11 names live, all `X-*`).
- **Non-HTTPS listeners must not carry** `clientCertificate`,
  `certificateAuthorities`, `defaultCertificateAuthority`, `tlsSecurityPolicy`
  or `alpnProtocols`; TCP/UDP must not carry `insertHeaders` at all.
- **Protocol compatibility** is three-way. Layer 4: listener TCP/UDP, pool
  TCP/UDP/PROXY, probe TCP/PING-UDP/HTTP/HTTPS. Layer 7: listener HTTP/HTTPS,
  pool **HTTP only**, probe TCP/HTTP. On top of that a TCP listener needs a
  TCP or PROXY pool, a UDP listener needs a UDP pool, and a UDP pool needs a
  PING-UDP probe (and only a UDP pool may have one).
- **Re-adding a member that is already present** is refused with
  `"The members provided are identical to the existing members in the pool"`.
  `attach_pool_members` detects the no-op and skips the write.
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
  `volumeTypes`, `encryptionTypes`, `dhcpOptionIds` and friends, which the
  gateway ignores. Of the five kept, only `name` and `zone_id` are sent to the
  API — the other three are applied locally, because the gateway accepts and
  ignores them (see Pagination above).

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
| `test_tool_schemas.py` | The registered input schemas: enums, `@Range` bounds, name patterns, required fields — the layer the direct-call tests skip |
| `test_tool_annotations.py` | Every tool declares `readOnlyHint`/`destructiveHint`, and the labels match what the tool does |
| `test_gateway_contract.py` | Rules transcribed from the gateway source: the status vocabulary, which list filters are real, the not-ready/lock/quota error surface |
| `test_write_rules.py` | Every cross-field create rule, with the gateway's own messages |
| `test_http_transport.py` | Identity resolution on the HTTP transport, `/health` exemption, and what the `--auth-debug` summary may reveal |

All `respx`-mocked. Handlers are called directly, so **every parameter must be
passed explicitly** — a `Field(...)` default arrives as a `FieldInfo` object
when MCP is not resolving it.

## Verified against the live gateway (HCM-3 and HAN, 2026-09-08/09)

Read with a service account against a project holding 48 load balancers, plus
the gateway source and its dry-run validator. All 29 create-body rules were run
through both the local DTOs and `POST /validation`; 28 agree, and the one
exception is noted below.

| Question | Answer |
|---|---|
| Create-response id key | `{"uuid": "<prefix>-<uuid4>"}` + 202. Prefixes per `UuidPattern`: `lb-`, `lis-`, `pool-`, `policy-`, `secret-`. `created_id` already looked for `uuid` first. |
| `/loadBalancers` query encoding | Flat `@ModelAttribute`. Confirmed. |
| `page` base | 1-based. Confirmed. |
| CIDR separator, and `""` | Comma (`AclCidrValidator`); blank and null are both accepted and mean no restriction. |
| Nested `pool.members` | An array (`List<MemberRequest>`). The spec's single-object typing was a bug. |
| `PUT /pools/{poolId}` without `healthMonitor` | **Only safe for a TCP probe.** The field is optional in the DTO, but the gateway re-validates the pool's stored probe against whatever the body carries: an HTTP monitor is rejected with "If healthCheckProtocol field is HTTP, following fields must be specified: …". So every pool update has to resend the current monitor, and the carry-forward is load-bearing after all. (This row previously said "preserves it" — inferred from the DTO being optional, and wrong.) |
| `GET /v1/projects` with a vLB token | **Works.** So does `/v1/{projectId}/zones`. `/v2/{projectId}/networks` returned `403 IAM_PERMISSION_DENIED` for the account tested — a policy limit on that account, not a code path. |
| `portal-user-id` header | Not needed. Every public route declares it `@RequestHeader(required)`, but the public API gateway injects it from the IAM token — which is why `SwaggerConfiguration` strips it from the published spec. Every read and the dry-run validator work without it. |
| Real status values | `LBGatewayStatus.LoadBalancer`, now transcribed into `status.IN_FLIGHT`. All 48 live load balancers read `progressStatus=CREATED` / `displayStatus=ACTIVE`. |
| Error body shape | Three shapes — see *Errors* above. |
| `zoneId` on `/packages` | Optional, and **not** a widening filter — it defaults to `${zone.default.id}`, so omitting it answers for one zone (HCM03-1A / HAN01-1A). See *Packages are per-zone* below. `/all_packages` takes no `zoneId`. |
| Capital-W `bandWidthPackageId` | Still only confirmed from the spec and the Java DTO; no live write was made. |

### Write paths, exercised live

One throwaway `SINGLE_NLB_Small` was created, driven through every write tool
and deleted, on 2026-09-08. What it established beyond the reads:

| Checked | Result |
|---|---|
| Create 202 body | `{"uuid": "lb-<uuid4>"}` and nothing else. Pool gave `pool-…`, listener `lis-…`. `created_id` hits it on the first key; `hint` stays empty. **No job id** — polling is the only option. |
| `monitorPort` filled from `port` | Accepted, and read back as the port. Without it the create is rejected, so this is load-bearing. |
| `weight=0` | Accepted **and stored as 0**. The old `ge=1` was blocking a valid drain. |
| Re-attaching an unchanged member | Short-circuits, no PUT, `confirmed=True`. |
| `detach_pool_members` emptying a pool | Refused locally before any request. |
| A write 2 s after another | `400` + `errorCode: LoadBalancerNotReady`, relabelled as transient with the poll instruction. |
| L7 policy on a Layer 4 load balancer | `400 "Load balancer <id> is Network Load Balancer"` — clear enough that no local pre-flight GET is worth the round trip. |
| Resize to the current package | `400 "The package id: <id> is the same as the current package."` No `errorCode`. |
| Delete | 202, and the load balancer is **still listed as `DELETING`** right after. Confirm with `list_load_balancers`, not by the absence of an error. |

### An ALB, its certificates and its L7 policies

A second throwaway load balancer — Layer 7, `SINGLE_ALB_Small`, created with
its listener **and** pool nested in the one create body — carried the rest:

| Checked | Result |
|---|---|
| `reorderL7Policies` position base | **1-based.** Two policies came back as `position` 1 and 2, and swapping them by sending each other's position worked. Closes the last spec ambiguity. |
| `bandWidthPackageId` capital W | **Confirmed on the wire.** A non-existent id answered `"Bandwidth package not found: bandwidth-pack-…"` — so the field was parsed. A misspelling would have complained about a missing field instead. |
| `import_certificate` | Works with a plain PEM pair. `notBefore`/`notAfter` come back as **epoch milliseconds** (`1791456697000`), which `models._common.epoch_to_iso` now renders as ISO-8601 — `expiredAt` is empty on a fresh import, so `notAfter` is the only readable expiry. |
| Private key hygiene | `get_certificate` returns 15 metadata fields and no key material of any kind. |
| `delete_certificate` while attached | `400 "CA is in use."` |
| HTTPS listener with a certificate | Accepted, and `tlsSecurityPolicy` + `alpnProtocols` read back exactly as sent. |
| `certificateAuthorities` on a response | **Lists only the EXTRA SNI certificates, never the default.** Sending `certificateAuthorities: [X]` and `defaultCertificateAuthority: X` reads back as `certificateAuthorities: []` with the default set. An empty list does not mean "no certificate". |
| `insertHeaders` left unset on an L7 listener | **The gateway injects a default set** — `X-Forwarded-For`, `X-Forwarded-Port`, `X-Forwarded-Proto`, all with value `"true"`. So an L7 listener is never really header-free. |
| Clone | Copies the listeners and pools, and the copy gets its own public address. |
| `configure_load_balancer_scaling` on a non-autoscale LB | **`500 Internal Server Error`** with no detail — a gateway bug (it should be a 400). The tool's docstring now says to check `autoscalable` first, because nothing in the failure says that was the problem. |

### "Busy" arrives in more shapes than three error codes

Eight validators build their rejection as
`String.format("<Resource> id %s is %s", uuid, status.toLowerCase())`, so the
message ends in the resource's own live status: `… is updating`, `… is
creating`, `… is deleting`. None of them carries an `errorCode`.

This was found by accident. `create_load_balancer_clone` holds the **source**
load balancer in `UPDATING` for around 15 minutes after the clone itself is
already `ACTIVE` — with the source's listeners stuck `UPDATING` too — and a
`delete_load_balancer` during that window answered
`400 "Load balancer id lb-… is updating"`, which the first version of
`VlbClient._raise_error` did not recognise. It reached the caller as
`Bad request: …`: advice to fix a body that was correct.

`client._transient_reason` now matches the shape rather than a phrase list, and
is deliberately narrow — `"… is expired"`, `"… is not a leader"`, `"… is used
in listener"` and `"… is created"` are the same grammar and all **terminal**.
Only a status the gateway itself reports as in flight counts.

**Still open:**

1. **Package versus load balancer type and zone.** The gateway rejects an L4
   package on a `Layer 7` create ("Invalid package type for Application load
   balancer") and a package from the wrong zone with "Invalid package id".
   `validate_load_balancer_create` cannot see either without fetching the
   catalogue, so it is the one case out of 29 where it accepts a body the
   gateway refuses. It now says so in its warnings. Fixable by reading the
   (1h-cached) catalogue — deliberately not done, since it would turn the only
   request-free tool into one that needs credentials and a region.
2. **Still-untouched write tools**: `resize_load_balancer` and the autoscale
   and bandwidth happy paths. Each changes what the account is billed, so none
   was exercised; only their refusal paths are known.

### The update tools, and two dead code paths

A third load balancer — an ALB with two pools, members, an HTTP and an HTTPS
listener, and three L7 policies — drove every remaining tool. It found two
tools that could never have worked, both for the same reason: **the code
trusted a field the spec declares and the service never fills in.**

| Checked | Result |
|---|---|
| `update_pool_health_monitor` | **Was dead for every pool.** It read the probe from `GET /pools/{poolId}`'s nested `healthMonitor`, which the live API never populates, and so raised "Pool <id> has no health monitor" for pools that plainly had one. Now reads `GET /pools/{poolId}/healthMonitor` alongside the pool. The carry-forward itself was correct all along: changing only `interval` moved `interval` and nothing else, across all 13 observed fields. |
| `update_pool` | **Was broken for any HTTP/HTTPS probe** — see the `PUT /pools/{poolId}` row above. Now resends the current monitor. |
| `create_pool` with `members` inline | Works; the members exist as soon as the pool does. |
| `attach_pool_members` | Adds without dropping; re-sending one with a new weight replaces just that member. |
| `detach_pool_members` | Removes only the named ones; an unknown target is refused locally before any request. |
| `update_pool_members` | Replaces the whole list, as advertised. |
| `update_listener` | **A body carrying only the three timeouts cleared `defaultPoolId` and `insertHeaders`** — the listener was left with no pool at all. The tool is now DESTRUCTIVE rather than WRITE. |
| `allowedCidrs` / `blockedCidrs` | Round-trip confirmed: a list goes out as `"10.0.0.0/8,192.168.0.0/16"` and comes back as a list. `defaultAction: "drop"` sticks. |
| `delete_pool` on a listener's default pool | `400 "Pool id: <id> is used in listener"` — correctly treated as terminal, not transient. |
| `import_certificate` type `CA` + `clientCertificate` | mTLS attaches, alongside `tlsSecurityPolicy: STRICT` and `alpnProtocols: ["h2"]`. |
| L7 rule types | `HOST_NAME`/`EQUAL_TO`, `PATH`/`REGEX` and `PATH`/`ENDS_WITH` all accepted, at positions 1, 2, 3. |
| An uncompilable regex | `400 "Rule value: ^[unclosed is invalid regex pattern"`. `L7RuleDto` now compiles the pattern locally first. |
| `CREATING-BILLING` | **Seen in a live create sequence.** One of the five statuses the original guessed `IN_FLIGHT` set omitted — so on the old code a load balancer in this state would have read as settled and the next write would have failed. |

### Packages are per-zone, and the sets are disjoint

Measured across both regions:

| Zone | Active packages |
|---|---|
| HCM03-1A | 58 |
| HCM03-1B | 16 |
| HAN01-1A | 12 |
| HAN01-1B | 11 |

**No two zones share a single package id.** HCM03-1A and HCM03-1B overlap on
zero; so do HAN01-1A and HAN01-1B. A package id is therefore only meaningful
together with the zone it came from, and reusing one across zones fails the
create with "Invalid package id (<id>)" — with nothing in the listing to warn
you.

Worse, omitting `zone_id` does **not** list every package: the gateway
substitutes `${zone.default.id}`, which resolved to HCM03-1A and HAN01-1A. So
the default answer is one zone's catalogue wearing no label. The tool's
`zone_id` description and docstring now say to pass the zone you will actually
create in, and the response records which zone it answered for (empty meaning
"the platform default").

One curiosity that is *not* a problem: HAN01-1A's 12 ids are byte-identical to
12 of HCM03-1A's 58. Package rows are evidently shared across regions but
scoped per zone, so a HAN create with one of those ids is accepted — the
gateway validates against `listLoadBalancerPackageActive(projectId, zoneId)`,
which returns them. Region alone never disambiguates a package id; the zone
does.

### Region parity: HAN behaves, and resolves its own project

Every read tool was run against HAN. Findings:

- **Project resolution is per region and does not leak.** `~/.greennode/config`
  named an HCM-3 project; asking for HAN made `require_project_id` fall through
  to `GET /v1/projects` on the HAN vServer gateway, which returned a
  *different* project, cached under `('service', 'HAN')`. The configured id is
  only used for the default region — which is the behaviour that matters, since
  a project id is not valid across regions.
- Zones are `HAN01-1A` and `HAN01-1B`; the `zoneId` prefix is regional, so an
  HCM zone id in a HAN request is silently wrong rather than an error.
- `list_vpcs` answers `403 IAM_PERMISSION_DENIED` in HAN as it does in HCM-3 —
  an account policy limit, not a code path.

### The HTTP transport and the --auth-debug surface

Driven against a locally running server (three configurations, 14 assertions),
now pinned by `test_http_transport.py`. Nothing was wrong here; what matters is
that it stays that way, because this is the only code in the package that
handles somebody else's credential.

- `/health` is reachable with no credential **and** with none configured, so a
  container healthcheck never depends on auth.
- A bearer token becomes the request identity via a contextvar reset in a
  `finally`, so one caller's token cannot survive into the next request.
- With no token: the service account if one is configured, otherwise
  `401` + `WWW-Authenticate: Bearer` — never an anonymous call. An
  `Authorization` header that is empty, bare `Bearer`, or a different scheme
  counts as no token rather than as an empty one.
- `/whoami` exists only under `--auth-debug`; without it the route is a 404.
- The diagnostic summary carries a 6-character token prefix and its length,
  never the token or even its payload segment, and filters JWT claims to an
  allow-list — decoy `ssn` and `email` claims were dropped from both the
  response and the log.

## Relationship with greennode-cli

No vLB commands exist in `greennode-cli` at the time of writing. The gateway
source and the live service are the authority instead (see *Sources of truth*).
Where the CLI later disagrees with this package, check it against
`../vlb-gateway` before changing anything — both are generated from the same
service, but the CLI can lag.
