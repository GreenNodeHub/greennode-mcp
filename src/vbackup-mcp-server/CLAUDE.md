# CLAUDE.md — vBackup MCP Server

Product-specific guidance for `src/vbackup-mcp-server`. Monorepo-wide
conventions (tool naming, DTOs, TDD, branch/release flow, security rules) live
in the **repo-root CLAUDE.md** — read that first.

## Product overview

MCP server for GreenNode **vBackup** — scheduled, policy-driven backups of
vServer instances and their volumes and of vDB databases, stored in a
destination vault, plus the restore points they produce and the history of
backup and restore runs.

- **68 tools** with `--allow-write`, **41** in the default read-only mode, plus
  **9 MCP prompts** and a `get_feature_guide` tool serving the same guidance.
- **Every operation the gateway publishes is covered**, in both regions, plus
  two reads against the vServer gateway, two against the vDB gateway and two
  dashboards against vMonitor.
- vBackup protects **two** products. `backup_server_handler` covers vServer
  instances (`bk-ins-`), `database_handler` covers vDB databases (`bk-db-`).
  They share policies, destinations and the history service and nothing else.
- vBackup grew out of vServer and is normally used to protect it, so this
  package is modelled on `src/vserver-mcp-server`: the same region split, the
  same handler/model/paging layering, the same auth. It is nonetheless a
  **separate product** from vServer's block-level snapshots — see *Overlap with
  vserver-mcp-server*.
- **Every tool has been exercised against the live gateway over the real MCP
  protocol** — reads in both regions, and every write round-tripped on a
  throwaway resource or restored afterwards. See *Before shipping a write*.

## Domain model

| Object | What it is | Id prefix |
|---|---|---|
| Backend | The region-local service storing backups; `backendId` on nearly every object | plain UUID |
| Backup destination | Where backups land (a vault or vStorage container), with quota, soft-delete and vault-lock. The console calls it a **Backup Location** and its tags say `BACKUP_LOCATION` | `bk-des-` |
| Backup region | The storage site a destination is placed in (HCM04, HAN02) — not an API gateway | plain UUID in `regionId` |
| Product | What a destination or policy protects: `vServer` or `vDB` | `prd-` |
| Backup policy | The schedule: hourly/daily/weekly/monthly toggles, retention, backup type | `bk-pol-` |
| Backup server | One protected instance — the join of a server, a policy and a destination | `bk-ins-` |
| Backup server point | One restore point produced by a run | `bk-ins-pt-` |
| Backup volume point | The per-volume slice of a server point | `bk-vol-pt-` |
| Backup database | One protected vDB instance — the join of a database, a policy and a destination | `bk-db-` |
| Backup database point | One restore point of a protected database | `bk-db-pt-` |

**Naming**: the API tag and the console say "Backup Server", the path says
`backup-instances`, the ids say `bk-ins-`. Tools use **`backup_server`**,
because that is the word a user says. Keep new tools on that spelling and let
the docstring name the endpoint.

## vBackup API quirks

Every statement in this section was **observed on the live gateway**. Add to it
as you extend the server, and add only what you have actually seen the API do.

### Probe before modelling

Model a payload from a response you have in front of you, never from an
expectation of what it should contain. Three habits follow from that, and each
of them exists because the opposite once shipped a bug:

- **Read the types off the wire.** Numeric policy fields arrive as floats
  (`hour: 12.0`, `retention: 1.0`); a model that insists on integers rejects a
  perfectly valid policy. `models._common.as_int` coerces.
- **Do not assume two endpoints share a shape because they describe the same
  thing.** The `/v1/vserver/**` family renames nearly every field of the
  generic one — see *The `/v1/vserver/**` projection renames everything*.
- **An endpoint that is absent stays absent.** Restore, in particular, has no
  path on this gateway; see *Known API limitations*.

### Endpoints and scoping

- Two region gateways: `https://hcm-3.api.vngcloud.vn/vbackup-gateway` and
  `https://han-1.api.vngcloud.vn/vbackup-gateway` — the same
  `<region>.api.vngcloud.vn` prefix vServer uses, with `vbackup-gateway` in
  place of `vserver/vserver-gateway`.
- **Every path is `/v1/**` and none carry a project id**, unlike vServer's
  `/v2/{projectId}/...`. Scoping is implicit in the caller's token;
  `backendId` and `projectId` are *response fields* and optional query filters,
  never path segments. There is no `require_project_id` equivalent to port.
- The console host `https://<region>.console.greennode.ai/vserver/vbackup-gateway`
  serves the identical API. `vserver-mcp-server` reaches this gateway through
  that spelling; this package uses the `api.vngcloud.vn` host.
- **The two gateways do not answer with the same set of backends.** The HAN
  gateway returns both the HAN and the HCM backend while the HCM gateway
  returns only its own, so a tool must not assume "one gateway = one backend"
  and must never infer a region from a `backendId`.

### One envelope, and the exceptions

Collections answer with a single shape:

```
{"items": [...], "page": null, "pageSize": null, "totalPages": 1, "totalItems": 22}
```

Note the **plural** `totalItems` / `totalPages`. vServer's main collections
spell it `totalItem` / `totalPage` singular, so a helper copied across from
that package reads the count as absent and treats a truncated page as complete.
`paging.total_items` reads the plural spelling this API uses.

Everything that breaks the pattern:

| Endpoint | Shape |
|---|---|
| `GET /v1/backup-instances/{id}/volumes` | a **bare array** of volume objects |
| `GET /v1/backup-instances/protected-servers` | `{"ids": [...]}` — a bare id list under its own key |
| `GET /v1/configurations` | `{"configs": {...}}` — an object of platform limits |
| every `/v1/vserver/**` list | a **bare array**, never the envelope |
| `POST /v1/volume-usage` | a **bare array** |

Detail endpoints (`GET /v1/backup-instances/{id}`, `/v1/backup-policies/{id}`)
return the resource object **directly**, with no `data` envelope.
`paging.unwrap` tolerates both.

### Paging is opt-in, and the parameter name is a trap

- **Omitting the paging params returns the whole collection** in one response,
  with `page` and `pageSize` reported as `null` and `totalPages: 1`.
- The request parameter is **`size`**. `page=1&size=1` paginates correctly.
- **`pageSize` as a request parameter is ignored silently**: the call answers
  `200` with the entire collection and `pageSize: null`, exactly as if no
  paging had been asked for. Since `pageSize` *is* the name in the response,
  this is easy to get wrong and impossible to notice from the status code.
  Always send `size`. `tests/test_paging.py` pins this.

`paging.fetch_all_items` takes the unpaged fast path and only re-pages when a
response reports more items than it returned. `list_backup_history` caps its
result with an explicit `limit` instead — the full history runs to thousands of
records and would flood an agent's context.

### JSON-as-string fields

A history record embeds `policySnapshot` and `destinationSnapshot` as **escaped
JSON strings**, not objects, and a destination's `config` is a dict on some
endpoints and a string on others. `models._common.as_dict` parses either and
returns `{}` for anything unparseable, so one malformed snapshot cannot sink a
whole history read.

Those two snapshots are the policy and destination **as they were at run time**,
so a record stays explainable after the policy is renamed, edited or deleted.
Report them rather than re-reading the live objects.

### Sizes are bytes

Every size the API reports — `volumeSize`, `volumeUsage`, a point's `size` and
`usage`, a destination's `maxQuota`, a vault's `used` — is a **byte count**.
Models expose both units (`size_gb` and `size_bytes`), so a caller never has to
guess. `used_gb` is the number that matters for cost: it is what a run
transfers and what the vault bills.

### Policy config

```
{"hour": 12.0, "minute": 0.0, "timeZone": "Asia/Ho_Chi_Minh",
 "hourlyEnabled": true,  "hourlyConfig": {"interval": 4.0, "retention": 1.0,
                                          "backupType": "INCREMENTAL",
                                          "incrementalQuantity": 3.0},
 "dailyEnabled": true,   "dailyConfig": {"retention": 1.0, "backupType": "FULL",
                                         "incrementalQuantity": 0.0},
 "weeklyEnabled": false, "weeklyConfig": {},
 "monthlyEnabled": false, "monthlyConfig": {},
 "isProtectedServer": true, "statusSendEmail": ["ERROR"]}
```

- The cadence is **four independent enable flags**, each with its own config
  object — not one frequency enum. A disabled cadence carries an **empty**
  config object rather than a null, so "has a daily schedule" is
  `dailyEnabled`, never `bool(dailyConfig)`. `models.policy` flattens the whole
  thing into `schedule.summary` so an agent cannot misread a disabled cadence
  as active; an empty summary means the policy never runs.
- `backupType` is `FULL` or `INCREMENTAL`, and two cadences of one policy can
  differ.
- **A policy with no cadence enabled is accepted by the API and never runs.**
  Nothing in the payload marks it as broken. The create docstring and the
  `manage_policy` guide both refuse to let that happen silently.

### `GET /v1/configurations` is the authority for policy bounds

It reports the platform's own limits, and they differ between **backup**
policies and vServer **snapshot** policies — validating one against the other
is the easy mistake:

| Key | Backup | Snapshot |
|---|---|---|
| hourly interval | `[4, 6, 8, 12]` | `[1, 2, 4, 6, 8, 12]` |
| retention limit per cadence | 30000 | 64 |

It also reports `allowed_backup_server_status` (which vServer instance states
can be protected) and `backup_policy_time_ranges` — a per-hour list where some
hours carry `enable: false`. `get_configuration` returns only the open hours,
because an existing policy can sit on a disabled hour while a new one must not
pick it. DTO bounds in `models/requests.py` mirror these values but
`get_configuration` stays the authority: it can change without the API contract
changing.

### The `/v1/vserver/**` projection renames everything

The projection is **not the generic family under another URL**:

| Generic family | vServer projection |
|---|---|
| `id` | `backupInstanceId` |
| `name` | `backupInstanceName` |
| `destination` | `backupDestination` |
| `volumes` (a list) | `protectedVolumes` (a **count**) |
| `status`, `backupEnabled`, `policy`, `serverDeleted` | absent |

Reusing the generic models here yields **empty ids** and a `backup_enabled` of
false on servers whose schedule is running, because a missing field reads as
"paused". `models/vserver.py` holds dedicated models; keep the two families
apart.

The projection earns its place by reporting two things the generic family does
not: a restore point's per-disk detail (`bootable`, `bootIndex`,
`volumeTypeId`) and `serverInfo` — the image the captured instance was built
from, which is what tells a user whether a point still matches the OS they run.

**`projectId` is effectively required** on `GET /v1/vserver/backup-instances`:
without it the API answers `200` with an **empty array** rather than an error,
so a tool that omits it reports "nothing here" on an account full of backups.

### Filters

| Endpoint | Filters |
|---|---|
| `/v1/backup-instances` | `id`, `name`, `serverId`, `backendId`, `projectId`, `page`, `size` |
| `/v1/backup-policies` | `name`, `backendId`, `projectId`, `page`, `size` |
| `/v1/backup-destinations` | `name`, `type`, `backendId`, `projectId`, `page`, `size` |
| `/v1/histories/**` | `id`, `serverId`, `backupInstanceId`, `backendId`, `projectId`, `page`, `size` |
| `/v1/backends` | **`backend`** — the one place the name filter is not `backendId` |

`serverId` on `/v1/backup-instances` is the useful one: it answers "is this
instance backed up?" directly, and an empty result means *unprotected*, not an
error.

### Backup destinations: what the API requires

All of this was established by round-tripping a throwaway location.

**`maxQuota` is an object, not a byte count.** It arrives as
`{"unlimited": bool, "maxQuota": <number>}`, and the number is **GB** — the
API's own history text spells it `{max-quota: 150GB}`. Every other size this
API reports is a byte count, so this one field breaks the rule in *Sizes are
bytes*; reading it with `as_gib` reports every quota as 0.

**`config` holds `vault` OR `vstorage`, never both.** A destination of type
`VAULT` fills `config.vault`, one of type `VSTORAGE` fills `config.vstorage`,
and the other key is `null`. Reading only `vault` reports a VSTORAGE
destination as an empty, unused store. `models.catalogue.VaultInfo` tries both.

**Three create fields are required even when the feature is off.** Omitting
`maxQuota`, `softDeleteConfig` or `vaultLock` is a `400 missing_required_field`
— send them with `enable: false` rather than leaving them out. The DTO defaults
do this.

**An absent `isDefault` is read as `true`.** Omitting it makes the create try to
claim the default, which is then refused with "Limit the number of default
backup locations to 1 for product vServer". Always send it explicitly.

**The vault lock has bounds that are not obvious, and one of them is a trap:**

| Rule | Violation answers |
|---|---|
| `changeDuration` must be 0-7 days | `The value '30' of the field 'changeDuration' must be between 0 and 7.` |
| `minRetention` must be <= `maxRetention` (equal is fine) | `vault_locked_invalid` — names neither field |
| **`changeDuration: 0` makes the lock permanent immediately** | every later edit, including disabling it, answers `Cannot edit vault lock` |

The zero case is the dangerous one: it is a perfectly ordinary-looking value
that silently removes the ability to ever change or disable the lock.
`VaultLockDto` therefore makes `changeDuration` **required** (no default that
could be 0 by accident), caps it at 7, and validates the retention ordering
client-side so the opaque `vault_locked_invalid` never reaches the agent.

An **empty** destination can still be deleted under a permanent lock — the lock
protects stored backups, not the destination object.

**The regions lookup returns two different ids.** `GET /v1/regions?product=X`
answers a bare array of `{id, name, regionId, product}`; a create takes
**`regionId`**, and the `vst-cf...` `id` is rejected. Each product publishes its
own list with different `regionId` values, and `product` defaults to `vServer`
when the parameter is omitted.

**Destination change history is a separate trail.** `GET
/v1/histories/backup-destinations/{id}` records configuration edits — including
failed ones, with `errorMessage` such as `backup_location_is_being_used` for a
delete refused while resources were stored there. Its `description` carries the
values that were used in the API's own words, so quota history is readable even
though the destination stores only the current value. Actions seen live:
`CREATE`, `DELETE`, `RENAME`, `EDIT_MAX_QUOTA`, `ENABLE_SOFT_DELETED`,
`EDIT_SOFT_DELETED`, `SOFT_DELETE`, `ENABLE_VAULT_LOCK`, `DISABLE_VAULT_LOCK`,
`SWITCH_DEFAULT`.

**Tags live on the account-wide tag service.** `GET /v1/tags/{destinationId}`,
not a sub-path of the destination. No endpoint adds or removes one.

**`/v1/backup-destinations/{id}/backup-databases` returns the full backup
database with `policy` and `backupDestination` nulled out.** The destination is
implied by the call, so the two nested refs are dropped rather than repeated;
`BackupDatabaseItem.from_api` falls back to empty refs instead of assuming they
are present. Everything else on the item is identical to
`/v1/backup-databases`.

### Backup databases: the vDB half, and where it differs

**The create body is flat, and that is not a style choice.** A backup server
create nests its instances so each can carry a disk selection; a database is
captured whole, so the API takes a single top-level `databaseId`. The
alternatives all fail — `databaseIds: [...]`, `databaseConfig: [{databaseId}]`
and `serverConfig: [{serverId}]` each answer `The field databaseId cannot be
null or empty`. The field is read only at the top level, and only in the
singular; there is no multi-database create.

**`databaseType` is required and is a cluster spelling, not an engine name:**
`PostgresCluster` or `RedisCluster`. It is validated immediately after
`databaseId`, and the failure message transposes its value and field
(`The value 'databaseType' of the field 'null' is invalid`). The enum is
**static**: it is parsed before the database behind `databaseId` is resolved,
so the value cannot be inferred from the instance and an unknown spelling fails
identically whether or not the database exists.

**`/v1/protected-resources/databases` fails open.** It answers `200 {"ids":
[]}` for a `databaseType` it does not recognise *and* for a missing one —
exactly what "nothing is protected" looks like. A typo therefore reads as a
clean bill of health on an account that is fully protected. `DatabaseType` is a
`Literal` in the DTO and in every tool parameter so the ambiguity cannot be
reached; do not relax it to `str`.

**Only cluster deployments can be backed up**, and vDB spells the topology
differently per engine: PostgreSQL is `cluster` / `single`, Redis is
`sharding` / `non-sharding`. Single-node PostgreSQL is ineligible; both Redis
topologies are accepted (a `non-sharding` Redis is a 3-node replica set, not a
standalone). `DatabaseInstanceItem` resolves eligibility once and reports the
reason, so a tool never has to re-derive the rule.

**A vDB destination holds at most ONE backup database.** Creating a second one
into an occupied destination answers `Bad request: The backup destination
already contains resources.` — a rule with no equivalent on the vServer side,
where one destination holds many backup servers. `list_databases` cannot see
it, so the create flow has to check
`list_backup_destination_databases` per candidate destination first.

**Two different 409s block a delete, and only one is worth retrying.** The
vServer family has one; the database family has two:

| Message | Cause | Retry? |
|---|---|---|
| `Your resource is being processed.` | The point is still uploading | Yes, after a wait |
| `Your resource is being managed by Vault.` | The destination has a **vault lock** whose retention still covers the point | **Never** — it succeeds only once the retention expires or the lock is lifted |

The second blocks `delete_backup_database` as well as
`delete_backup_database_point`: the resource cannot go while its points are
retention-locked. Lifting the lock — where the destination's change-duration
window still allows it — releases both deletes immediately.

**A database point reports two sizes**, `compressedSize` and
`uncompressedSize`, where a server point reports `size` and `usage`. The
compressed number is what is stored and billed; quoting the uncompressed one
overstates cost by whatever the engine compressed away.

**`backupName` on a point is an identifier, not a timestamp**, and its format
is engine-specific: Redis fills it with a bare number that looks like an epoch,
PostgreSQL with a WAL base name (`base_000000010000000000000009`). Neither is
the run time — `time` is.

Sizes on a fresh vDB backup are genuinely tiny (an empty Redis point is under a
kilobyte), so `size_gb` rounds to `0.0` while `size_bytes` is non-zero. Report
bytes when the GiB figure rounds away, or a real backup reads as an empty one.

### The vDB gateway: a second product, on a host of its own

vBackup knows which databases are protected but not which exist, so offering a
choice at create time means leaving the product. `VbackupClient.get_vdb` reads
`https://vdb-gateway.vngcloud.vn/vdb-{relational,memory}/v1/database-instances`
with the same IAM bearer token.

- **Two path prefixes, split by engine family, not one gateway with a filter**:
  `vdb-relational` serves PostgreSQL, `vdb-memory` serves Redis. There is no
  combined listing, so `list_databases` takes the family as a parameter.
- **Not region-scoped.** Like vMonitor and unlike everything else here, one
  host answers for the whole account and resolves the project from the token,
  so both `REGIONS` entries map to the same URL. **The consequence is a real
  trap**: the instances come back identical for both regions while the
  *protection* check is per region, so the same database shows as eligible in
  HAN purely because HAN has no backup for it. `list_databases` says so in its
  docstring; never conclude "unprotected" from one region.
- **Its envelope is doubly nested** — `{code, message, data: {data: [...],
  pageObject, projectId}}` — where vBackup uses `{items, totalItems, ...}`.
  `paging.as_list` does not reach the inner list; the handler unwraps `data.data`
  explicitly.
- **Paging is `pageNumber` / `pageSize`**, a third spelling: vBackup takes
  `page` / `size` and silently ignores `pageSize`. `pageObject.maxSize` reports
  100 but 1000 is honoured and returns the whole estate in one call.
- **Reads only.** There is no write counterpart and there must not be —
  creating or deleting a database belongs to vDB's own product surface.

### History: three trails, one silent date window

`/v1/histories/**` is not one collection. There are three independent trails and
mixing them up produces confident wrong answers:

| Trail | Endpoint | Tool |
|---|---|---|
| vServer backup runs | `/v1/histories/backup-instances` | `list_backup_history` |
| vServer restores | `/v1/histories/restoration` | `list_restore_history` |
| vDB backup runs | `/v1/histories/backup-databases` | `list_database_backup_history` |
| vDB restores | `/v1/histories/restoration/databases` | `list_database_restore_history` |
| Destination config changes | `/v1/histories/backup-destinations[/{id}]` | `list_backup_destination_history` |
| Server migrations (vServer gateway) | `/v1/{projectId}/histories/server-migration` | `list_server_migration_history` |

A vDB run never appears in the vServer trail; the call answers `200` with an
empty list rather than an error, so "no results" is indistinguishable from
"wrong product" without knowing this.

**`/v1/histories/backup-instances` applies a silent 180-day window.** Measured
live: an unfiltered call returned records whose oldest was exactly 180 days
old, while the same call with `from_date` set to the epoch returned several
times as many, reaching back more than a year. Nothing in the response marks
the cut. **An empty or short history is therefore never
proof that a backup did not run**, and `list_backup_history` says so in its
docstring and exposes `from_date` to defeat it.

The date parameter is **`from_date`, snake_case**, in epoch milliseconds — the
lone snake_case parameter in an otherwise camelCase API. `fromDate` is accepted
and **silently ignored**, exactly like `pageSize`: the call returns the default
window while looking filtered. `history_handler.to_epoch_millis` converts an
ISO-8601 string so no caller has to compute milliseconds.

`to_date` is accepted and ignored too — there is no upper bound filter. The
same goes for `backendId` and `projectId` on the history endpoints: the console
sends them, but the returned counts are identical with and without, so this
server deliberately does not expose them rather than offering filters that do
nothing.

`/v1/histories/backup-destinations` takes **no date filter at all** and, called
without an id, returns the account-wide configuration log — tens of thousands of
records on an active account, and the only way to see what happened to a
destination that has since been deleted.

vDB runs size differently: `compressedSize` / `uncompressedSize` where a vServer
run reports `size` / `usage`. The compressed number is what the vault bills.

### The server-migration trail breaks three package-wide assumptions

`list_server_migration_history` reads
`GET /v1/{projectId}/histories/server-migration` on the **vServer** gateway.
It is the one endpoint here that follows none of this package's conventions:

- **A third envelope spelling.** Rows arrive under `listData`, and the counters
  are **singular** — `totalItem` / `totalPage` — where vBackup uses `items`
  with plural `totalItems` / `totalPages`. `paging.as_list` needs the wrapper
  key passed explicitly and `paging.total_items` does not read this shape at
  all.
- **Paging is mandatory, not opt-in.** Omitting `page` *or* `size` answers
  `500 Internal Server Error`, so there is no unpaged fast path and
  `fetch_all_items` cannot serve this endpoint. Pages are 1-based; `page=0` is
  a clean `400 Page or size invalid`. The handler walks pages itself, driven by
  `totalPage`, with a hard stop so a mis-reported count cannot spin.
- **One of its filters is silently ignored.** `serverId` and `status` filter
  correctly — the reported `totalItem` drops with them. **`action` does not**:
  the call succeeds, the count is unchanged, and the rows are unfiltered, which
  is the same class of trap as `pageSize` and `fromDate` elsewhere in this
  package. The tool therefore never sends `action` and filters those rows
  itself, saying so in the parameter description.

**And one semantic trap worth more than the three above:** `status` reports the
phase a step reached, not what the step was. A `ROLLBACK` finishes as
`COMPLETE-MIGRATING-SUCCESS` — byte-identical to a migration that was
confirmed. Reading `status` alone reports an abandoned migration as a
successful one. `action` is the field that says what happened; the model
documents both and the docstring pairs them.

The three actions map onto the product's own flow: `START-MIGRATING` is the
cutover that brings the instance up at the new site, `COMPLETE-MIGRATING`
confirms it and releases the source (irreversible), and `ROLLBACK` abandons the
move and returns the instance. A server whose newest record is
`START-MIGRATING` is mid-migration — cut over, and still waiting on a decision.

### Backup servers: the operational quirks

Established by running a full cycle against a backup server: create, run a
backup, download and delete the point it produced, then remove the backup
server.

**A restore point walks `BACKING_UP` → `UPLOADING` → `ACTIVE`.** Only `ACTIVE`
is usable, and a 20 GB boot disk takes minutes to get there. Two calls behave
differently while a point is unsettled, and both look like something else:

| Call on an in-progress point | Answers |
|---|---|
| `GET /v1/backup-instance-points/{id}/pre-signed-url` | `200` with an **empty** `preSignedUrl` list — success, no links. Empty means "not ready", never "no data". |
| `DELETE /v1/backup-instance-points/{id}` | `409 Your resource is being processed.` — a "wait", not a failure. The same 409 blocks deleting the backup server. |

**One instance, one backup server — the API enforces it.** Creating a second
backup server on an already-protected instance is refused with
`Conflict: The backup server for server <ins-...> already exists`, and
`create_vserver_backup_servers` answers the same. This corrects an earlier note
here that claimed the opposite; re-verified against both regions.

It matters because `POST /v1/backup-instances/backup-now/{serverId}` addresses
the **instance**, not a backup server. With the uniqueness rule the two are
effectively interchangeable for `backup-now`, so the tool no longer warns about
an ambiguity that cannot arise — but it still reads the ids off the backup
server rather than assuming.

**`backup-now` takes the instance id in the path and `backendId`/`projectId` in
the body** — three values best read off the backup server itself rather than
assembled from separate lookups. It answers immediately; the outcome only shows
up in `list_backup_history` seconds later.

**Moving a destination moves only future runs.** `PUT
/v1/backup-instances/{id}/destination` leaves existing restore points in the old
destination, still restorable and still billed there, splitting the history
across two vaults. Users reliably read it as "the backups moved".

**`GET /v1/backup-statistic` needs `projectId` to be useful.** Without it the
response still succeeds but `totalServers` is **0**, so any coverage ratio built
on it is nonsense. `totalBackupServers` normally exceeds
`totalProtectedServers`; the gap is backup servers whose source instance is
gone — the fastest way to spot pure waste.

### vMonitor metrics: six fixed queries, and three ways to misread them

`metrics_handler` posts to
`https://backupcenter.console.greennode.ai/vmonitor-api/api/v1/statistics/default`
with the same IAM bearer token the rest of the package uses (no token is a
`401 IAM_UNAUTHORIZED`). It is the **only non-region-scoped host** here: one
endpoint serves both regions and labels each series with the region it came
from, so both `REGIONS` entries map to the same URL.

vMonitor publishes exactly six vBackup metrics:

| Metric | Dimensions | Meaning |
|---|---|---|
| `vbk.total_backup_servers` | `product:vbackup` | Backup servers, per region |
| `vbk.total_servers` | `product:vbackup` | vServer instances, per region |
| `vbk.total_usage` | `product:vbackup` | Storage used, **GB**, per region |
| `vbk.location.usage` | `+ backup_location_id:<id>` | Storage in one location, **GB** |
| `vbk.location.success_rate` | `+ backup_location_id:<id>` | Successful runs — a **count** |
| `vbk.location.failed_rate` | `+ backup_location_id:<id>` | Failed runs — a **count** |

Both tools ship those payloads baked in and expose only the time window. There
is deliberately **no free-form metric-query tool**: an unknown metric name
answers `200 []`, so a typo would come back as a confident "no data".

**Three traps:**

1. **Values are strings, timestamps are seconds.** The request sends epoch
   milliseconds; the response returns `[[1786943100.0, "32317"], ...]` — epoch
   *seconds* and *string* values. Arithmetic on the raw values concatenates.
   `models/metrics.py` coerces both once.
2. **The region label differs.** Series say `HCM`; everything else in this
   package says `HCM-3`. `REGION_LABELS` normalises it, so a tool never reports
   two spellings for one region.
3. **`period` is SECONDS, and it is a floor, not a bucket size.** The API
   rejects anything outside 60-86400 or not divisible by 60
   (`The field period must be 60 to 86400 and divisible by 60`), and these
   metrics are **stored hourly**: every period from 60 to 3600 returns one point
   per hour. Only a larger period aggregates — 21600 gives 6-hour buckets, 86400
   gives daily. `MetricsWindow.bucket_seconds` reports what the answer really
   used, because the requested period is usually not it.

**Units do not match the rest of the server, and that is expected.**
`vbk.total_usage` and `vbk.location.usage` are decimal **GB**, while
`vault.used_gb` is **GiB** — roughly a 7% gap on the same underlying bytes. Say
"different unit", never "discrepancy".

**Despite the names, `success_rate` and `failed_rate` are counts.** Values in
the hundreds are normal and mean hundreds of runs. Any percentage has to be
computed from the pair, and said to be computed.

An unknown `backup_location_id` also answers `200 []` for all three location
metrics, which is indistinguishable from a silent location — `empty_metrics`
surfaces it so the agent can check the id rather than report "no activity".

### The calls that leave this product

Two tools reach the **vServer** gateway rather than vBackup:
`get_vserver_instance` (`GET /v2/{projectId}/servers/{serverId}`) and
`list_server_migration_history` (`GET /v1/{projectId}/histories/server-migration`
— note the different version). vBackup stores a bare `serverId` and nothing
about the machine, so naming a protected server, checking whether its state
allows a backup, identifying its boot volume, or explaining that the machine
moved all require vServer.

- The base URL is **`https://<region>.api.vngcloud.vn/vserver/vserver-gateway`**.
  The shorter `/vserver-gateway` spelling answers in HCM-3 and **404s in HAN**,
  so it must not be used. This is the same spelling `vserver-mcp-server` routes
  to.
- Unlike every vBackup path, it is `/v2` and carries the **project id in the
  path**.
- The response is wrapped in `{"data": {...}}`; `paging.unwrap` handles it.
- The raw payload is very wide (flavour zone lists, interface internals, image
  metadata). `models/instance.py` keeps a deliberately narrow view — anything
  that does not help explain or decide a backup is dropped rather than passed
  through to flood an agent's context.
- **Reads only.** `VbackupClient.get_vserver` is the only accessor and there is
  no write counterpart. Creating, resizing or deleting an instance belongs to
  `vserver-mcp-server`; this is a lookup, not a second front door to vServer.

Do not confuse `models/instance.py` (the real vServer product) with
`models/vserver.py` (vBackup's own `/v1/vserver/**` projection). Different
gateway, different shapes.

## Invariants worth preserving

Each of these is enforced by a test and was learned from a defect that a mocked
unit test could not have caught — the code path was exercised, but only an
end-to-end call against the live gateway showed the result was wrong. Changing
any of them needs a deliberate decision, not a refactor.

### Sort before you slice

**The history endpoints return records in no particular order.** Every history
tool caps its result with a `limit`, so the cap must be applied to sorted
records or it keeps an arbitrary subset while presenting it as the newest runs
— the failure is silent and the output looks entirely plausible.

`models.history.newest_first` sorts on the first timestamp each family carries.
If a new list tool grows a `limit`, sort it too.

### Derived values must be serialisable

A plain `@property` is not serialised by Pydantic, so a value defined that way
reaches Python callers and unit tests but never reaches an MCP client. Anything
a docstring promises the caller must be a real field or a `computed_field`;
`BackupStatisticData` uses the latter for its two derived counters.

One consequence to know when testing: computed fields appear only in the
serialization schema, so a test that inspects the schema must ask for
`model_json_schema(mode="serialization")`. The published `outputSchema` is the
validation schema and therefore omits them, which is harmless because it sets
no `additionalProperties: false`.

### A recorded API behaviour is not verified forever

Notes in this file describe the API as it behaved when it was observed. At
least one has since reversed — an instance now accepts only a single backup
server, where it previously accepted several, and a tool docstring had been
built on the older behaviour. Before relying on a documented quirk for anything
load-bearing, re-probe it.

## Known API limitations

Both are reflected in the relevant tool docstrings, so an agent does not
mistake them for bugs in this server.

### 1. Three by-id endpoints are IAM-gated

`GET /v1/vserver/backup-instances/{id}`,
`GET /v1/vserver/backup-instance-points/{id}` and
`GET /v1/vserver/backup-volume-points/{id}` answer
**403 `IAM_PERMISSION_DENIED`** for a service account without the grant, while
their sibling *list* endpoints work for the same identity. The tools are
implemented and correct — this is a per-caller permission, exactly like the
IAM-gated endpoints documented in vserver's CLAUDE.md.

Each docstring says a 403 means "not allowed", never "does not exist", and
names the list tool to fall back to. Their exact payloads remain **unverified**
because the development account lacks the grant.

### 2. `POST /v1/volume-usage` 404s on a volume whose server was deleted

The endpoint answers `404 "Not found volumeId <id>"` for the **whole request**
when any requested volume no longer exists in vServer — which is exactly the
state of every volume belonging to a backup server with `serverDeleted=true`.
`backendId` and `projectId` are both required (`400 "Missing field"` without
them); an empty `volumeIds` returns `[]`.

`list_volume_usage` reports the ids it could not measure in
`missing_volume_ids` rather than failing silently, and the docstring points at
the restore points as the way to size a deleted server's backups.

### 3. A restore cannot be started through this API

The gateway publishes `GET /v1/histories/restoration` and **no endpoint to
trigger a restore**; restores are performed in the console. `list_restore_history`
and the `inspect_restore_point` guide both say so, so an agent stops looking for
a tool that does not exist.

Adding restore support would require an endpoint that this API does not
currently expose. If one becomes available, it belongs in a new handler
alongside the existing restore-history reads.

## Overlap with vserver-mcp-server

This gateway also serves `GET /v1/snapshot-policies`, which
`src/vserver-mcp-server` already exposes as `list_snapshot_policies` (through
its own small `VbackupClient` against the console host). Snapshots and backups
are different products sharing one gateway: snapshots are block-level, live in
vServer, and are managed by the vServer server; backups are file-level, land in
a destination vault, and survive the source server.

**Do not re-expose the snapshot endpoints from this package.** If a snapshot
tool is missing, it belongs in `vserver-mcp-server` next to the rest of the
snapshot surface. Keeping the split on product lines is what stops two servers
from offering two differently-named tools for the same call.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | MCPServer entry point, handler registration, CLI flags, auth modes, SERVER_INSTRUCTIONS + runtime-mode addendum |
| `config.py` | `VbackupConfig` + REGIONS endpoints; profile loading delegates to `mcp_core.config.load_profile` |
| `auth.py` / `validators.py` | Re-exports of the `mcp_core` TokenManager / `validate_id` |
| `client.py` | `VbackupClient` extends `mcp_core.http.BaseClient`; `get_vserver`, `get_vdb` and `post_vmonitor` are the read-only doors to the other three gateways |
| `paging.py` | `as_list` / `unwrap` / `total_items` / `fetch_all_items` — the envelope + paging normalisers |
| `discovery_cache.py` | Package TTL config on top of `mcp_core.cache.DiscoveryCache`, plus `UNCACHED_TOOLS` |
| `tool_annotations.py` | Shared `READ` / `WRITE` / `DESTRUCTIVE` ToolAnnotations |
| `guards.py` | `require_write` — the shared `--allow-write` gate |
| `useragent.py` / `context.py` | Outbound User-Agent; module-level handles set at startup |
| `auth_handler.py` | `get_access_token` |
| `catalogue_handler.py` | Backends, platform configuration, protected servers |
| `destination_handler.py` | Backup destinations: list/get, the four detail views, the full write cycle, plus the products and backup-regions lookups |
| `policy_handler.py` | Backup policies: list, get, create, update, delete |
| `backup_server_handler.py` | Protected servers: reads, volumes, restore points, the full write cycle, immediate backups, destination moves, per-point download/delete, account statistics, and the vServer instance lookup |
| `database_handler.py` | Protected vDB databases: reads, restore points, the full write cycle, immediate backups, and the vDB-gateway estate lookup |
| `metrics_handler.py` | The two vMonitor dashboards, the six fixed metric payloads, and the period bounds |
| `history_handler.py` | The run/restore audit trails for vServer and vDB plus the server-migration trail (never cached), and `to_epoch_millis` for `from_date` |
| `vserver_handler.py` | The `/v1/vserver/**` projection and volume usage |
| `prompts_handler.py` | 8 guided flows (Vietnamese), served as prompts and via `get_feature_guide` |
| `models/` | Pydantic response models and write DTOs, split by domain |
| `auth_debug.py` | Opt-in redacted inbound-auth diagnostics (HTTP only) |

### The `models/` package

Everything is re-exported from `models/__init__.py`, so
`from greennode.vbackup_mcp_server.models import X` works for every name
regardless of which file it lives in. Handlers import from the package, never
from a submodule.

| Module | Holds |
|---|---|
| `_common.py` | `as_int` / `as_gib` / `as_text` / `as_dict` / `resource_id` — the float, byte, JSON-string and id-spelling coercions |
| `catalogue.py` | Backends, destinations, vault/vstorage info, quota, soft delete, vault lock, platform configuration, protected servers |
| `destination.py` | Destination sub-resources: products, backup regions, tags, change history |
| `database.py` | Protected vDB databases, their restore points, the `databaseType` literal, and the vDB instances behind them |
| `policy.py` | Policies, cadences, the flattened schedule summary |
| `backup_server.py` | Protected servers, their volumes, and the `WriteResult` envelope |
| `points.py` | Restore points, per-volume slices, volume usage, pre-signed download links |
| `history.py` | Backup-run and restore audit trails, one model family per product |
| `vserver.py` | The `/v1/vserver/**` projection's own shapes |
| `instance.py` | The real vServer instance behind a backup server, and account backup statistics |
| `metrics.py` | vMonitor series: string→float, epoch-seconds→ISO, region relabelling |
| `requests.py` | Every `*Dto` write body (`extra="forbid"`) |

Most vBackup mutations answer `200`/`204` with **no body**, so write tools
return `WriteResult` — what was done, plus what the caller should verify next —
rather than echoing a payload that does not exist.

## Guidance placement policy

Four layers, each with one job — do not let content drift between them:

| Layer | Carries | Never carries |
|---|---|---|
| Docstring / param description | The tool CONTRACT: semantics, ranges, formats, hard API constraints, cross-tool id mapping | Conversation choreography |
| `get_feature_guide` / prompts | Choreography: question order, ask-the-user steps, confirm gates | Per-tool field detail |
| `SERVER_INSTRUCTIONS` | Session-wide principles (region/backend model, id-first rendering, snapshot-vs-backup) | Per-tool detail |
| Error messages | The next step to fix THIS failure | — |

Guides live in `prompts_handler.py` as `_<name>_guidance()` functions and are
served twice from one source: as an MCP prompt the user loads, and through the
`get_feature_guide` tool. Add one by appending the function, a
`_FEATURE_GUIDES` entry, the `Feature` literal value and a prompt method —
**only once the tools it choreographs exist**, so a guide never points at a
tool the server does not register.

## The three "off" states, and why they are all reported

Conflating these is the most common way a user is misled about their backups:

| Field | Means | Restore points |
|---|---|---|
| `backup_enabled=false` on the server | Schedule paused, no new runs | kept, still billed |
| `volumes[].backup_enabled=false` | That disk excluded from every run | missing from future points |
| `server_deleted=true` | Source instance is gone | kept, **still billed** |

`disable_backup_server` does not free storage; only `delete_backup_server`
does, and it is irreversible. Every relevant docstring and the
`reduce_backup_cost` guide say this explicitly.

## Testing

```bash
cd src/vbackup-mcp-server && uv run pytest tests/ -v
```

179 tests, `respx` for async HTTP mocking — no real API calls, no credentials.

| File | Covers |
|---|---|
| `test_server.py` | Config, regions, write guard, the complete tool surface (read-only vs write), annotations, prompts |
| `test_paging.py` | The `items` envelope, bare arrays, `{"ids": [...]}`, plural `totalItems`, `size`-not-`pageSize` |
| `test_catalogue.py` | Backends, configuration limits, protected servers |
| `test_destinations.py` | The quota object, VAULT vs VSTORAGE storage, the four edits, the required create fields, the vault-lock bounds |
| `test_policies.py` | Schedule flattening, float coercion, the write cycle, DTO bounds |
| `test_backup_servers.py` | Reads, byte→GiB conversion, excluded volumes, restore points, every write tool |
| `test_metrics.py` | The fixed payloads, string values, region relabelling, empty-200 handling, period bounds |
| `test_backup_server_extras.py` | Statistics and their ratios, the vServer-gateway lookup, split download links, immediate backups, destination moves, per-point delete |
| `test_databases.py` | The vDB item shape, nulled refs, both point sizes, eligibility and its reasons, the flat `databaseId`, the rejected engine-name spelling, every write tool |
| `test_history.py` | JSON-string snapshots, filters, failed runs, `from_date` conversion, the vDB trails, and that `limit` keeps the newest runs on an unordered response |
| `test_vserver_projection.py` | The projection's own field names, `server_info`, volume points, IAM 403, volume usage |

Fixtures live in `tests/conftest.py`; shared constants and sample payloads in
`tests/helpers.py` (test modules import `from .helpers import ...` — importing
from a conftest breaks under a different rootdir). The samples mirror the live
payloads, floats and byte sizes included, so a model that only works against
tidied-up fixtures fails.

`scripts/smoke_test.py` drives every read tool over the real MCP protocol
against a live gateway, discovering ids from the listings as it goes
(`--region both` covers HCM-3 and HAN); `scripts/auth-debug-local.sh` exercises
the `--auth-debug` diagnostic locally. Both need credentials in `~/.greennode`.

Credentials, API captures and scratch notes stay out of git — see the package
`.gitignore`. Never quote a real id, address or account name into code, tests
or docs: use placeholder ids (`bk-ins-0001`, `bk-des-0001`) and the
documentation address ranges reserved by RFC 5737.

## Before shipping a write

On this product a mistaken write costs backup data — deleting a backup server
takes its restore points with it — so before relying on a write tool:

1. Run its full cycle against a **throwaway** resource, or against one you can
   restore to its previous state afterwards.
2. Record what the response actually looked like, especially anything the API
   returns that the DTO does not model, and anything it refuses.
3. Re-check the annotation: pausing a schedule is `WRITE`, deleting a backup
   server is `DESTRUCTIVE`.

### Verification status

Every write tool has been round-tripped against the live gateway over the MCP
protocol. What each run established:

| Tools | Verified by |
|---|---|
| The six `*_backup_destination*` writes | A throwaway location created, renamed, re-quota'd, soft-delete enabled, locked and unlocked, then deleted; the change history confirmed each step. *Backup destinations: what the live API requires* came out of this run. |
| The backup-server writes | A backup server enabled and paused, its policy swapped and restored, a volume excluded and re-included, its destination moved and moved back, a backup triggered and followed to ACTIVE, the resulting point downloaded and deleted, and the backup server itself deleted and rebuilt from a captured spec. *Backup servers: the operational quirks* came out of this run. |
| The backup-database writes | A backup database created on a PostgreSQL cluster, paused and resumed, its policy swapped and restored, a backup triggered and followed to ACTIVE, then the point and the backup database deleted. *Backup databases: the vDB half* came out of this run, including the two rules that surfaced only as refusals — the one-database-per-destination limit and the vault-lock conflict. |
| The policy writes, including `update_default_backup_policy` | A policy created, fetched, replaced wholesale, promoted to default, the previous default restored, and the policy deleted. The DTO bounds were confirmed to reject invalid input before it reached the API, and a policy with no cadence enabled was confirmed to be accepted by the API and to report an empty `schedule.summary`. |

Two notes for whoever repeats this:

- **`update_default_backup_policy` cannot be tested in isolation.** Promoting a
  policy demotes whichever one currently holds the default, so the run has to
  capture the incumbent first and restore it afterwards. Do it on an account
  whose default is expendable.
- **The two create tools cannot both be exercised on one instance.** An
  instance accepts a single backup server, so proving `create_backup_server`
  and `create_vserver_backup_servers` means deleting between the two attempts.
