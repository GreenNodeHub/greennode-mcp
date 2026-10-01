# CLAUDE.md — vDB MCP Server

Product-specific guidance for `src/vdb-mcp-server`. Monorepo-wide conventions
(tool naming, DTOs, TDD, branch/release flow) live in the **repo-root
CLAUDE.md** — read that first.

The living build plan, its progress checklist and the session log are in
**`docs/superpowers/vdb-mcp-server-plan.md`** — read that before starting work
on this package. This file records what the API does; the plan records what is
built, what is left, and why the decisions were made.

## Product overview

MCP server for GreenNode vDB — managed databases. vDB is **four APIs behind
one gateway**, sharing nothing but the IAM token:

| Family | Service constant | Path style | Operations |
|---|---|---|---|
| Relational (MySQL, MariaDB, standalone PostgreSQL) | `VDB_RELATIONAL_SERVICE` | `/v1/**` | 45 |
| MemoryStore (Redis) | `VDB_MEMORY_SERVICE` | `/v1/**` | 43 |
| PostgreSQL Cluster | `VDB_POSTGRESQL_SERVICE` | `/v1/**` | 14 |
| Kafka | `VDB_KAFKA_SERVICE` | unversioned (`/clusters`) | 36 |

Tools are prefixed by family and are **not** interchangeable — see "Do not
generalise across families".

## Configuration

One gateway, `https://vdb-gateway.vngcloud.vn`, **not region-scoped**: it
serves every region and resolves the project from the token. `REGIONS` maps
both `HCM-3` and `HAN` to the same URLs so `BaseClient.get_base_url` keeps
working, and **no tool exposes a `region` argument** (a test enforces this).

The family prefix lives in the base URL, so handler paths omit it:
`GET /vdb-relational/v1/database-instances` is called as
`/v1/database-instances` against `VDB_RELATIONAL_SERVICE`.

## vDB API quirks

### Envelopes and list shapes — there is no single unwrapper

Relational, memory and postgresql wrap every response in
`{code, message, data}`; Kafka wraps only 9 of its 36 operations. Inside
`data` the shape still varies, so `paging.py` has four helpers and reaching
for the wrong one **fails silently**:

| Helper | Endpoints | Shape |
|---|---|---|
| `unwrap_wrapped` | all wrapped | `{code,message,data}` → `data`; an unwrapped payload passes through |
| `as_list` | 81 | `data` is a plain array |
| `unwrap_nested_list` | 5 | items at `data.data[]` |
| `unwrap_content_list` | 6 | items at `data.content[]` |

`unwrap_nested_list` covers two unrelated groups: the two
`GET /database-instances` listings (with `pageObject`) **and the three
`volume-types` catalogues** (no `pageObject`, just `{projectId, data[]}`).
Using `as_list` on a volume-types response returns an empty list with no
error — an empty catalogue that looks like a valid answer.

`unwrap_wrapped` requires **all three** of `code`, `message` and `data`.
Testing for `data` alone would unwrap any resource that happens to have a
field of that name, and vDB has them.

### Pagination

`pageNumber` + `pageSize`, and **`pageNumber` is 1-based** (like vServer,
unlike vks). `pageObject.number` echoes the requested page, so it is 1-based
too — never use it as a zero-based offset. `pageObject.maxSize` is 100, the
ceiling for `pageSize`. Only 8 endpoints paginate.

### Filters are flat query parameters

The spec models `filterRequest` as a nested required object; that is a
springdoc artifact. On the wire the fields are plain optional query
parameters — `?name=my-db&status=ACTIVE&status=BUILDING` — never JSON-encoded,
and multiple statuses repeat the key. `name` is a substring match.

### The relational instance listing is a MIXED listing

`GET /vdb-relational/v1/database-instances` returns relational instances
(`db-`) **and** PostgreSQL Clusters (`pg-`), because the PostgreSQL Cluster
family has no list and no get-by-id endpoint of its own. Two consequences:

- Tools must filter client-side by id prefix.
- The API's `name`/`status` filters are **not applied to the `pg-` rows**: a
  query for a name that exists nowhere still returns them. Never describe a
  filtered result as exact.

Second discriminator, for cross-checking: `pg-` rows have `dbBackendId = null`,
`deployType = "cluster"`, `numberOfNodes = 3` and populated
`privateRwIp`/`publicRwIp`/`portRo`/`domainName`; `db-` rows have a numeric
`dbBackendId`, `deployType = "single_node"`, `numberOfNodes = 1` and nulls in
the RW/RO fields.

### Do not generalise across families

The same business operation differs in path *and* method:

| Operation | relational | memory |
|---|---|---|
| Delete instance | `POST /database-instances/{instanceId}/delete` | `POST /database-instances/{dbInstanceId}/delete` |
| Delete backup | `DELETE /backups/{backupId}/delete` | `POST /backups/delete` |
| Delete config | `DELETE /configurations/delete` | `POST /configurations/delete` |
| Get instance | `GET /database-instances/id/{dbInstanceId}` | `GET /database-instances/{dbInstanceId}` |
| Get backup | `GET /backups/detail/{backupId}` | `GET /backups/{backupId}/detail` |
| Backups of an instance | `GET /backups/insId/{instanceId}` | `GET /database-instances/{dbInstanceId}/backups` |
| Resize storage | `POST /database-instances/{id}/resize-storage` | *(does not exist)* |

Path parameter names differ too (`instanceId` / `dbInstanceId` /
`replicaSourceId`). Share helpers (unwrap, paging, projection); never share a
path builder.

**An instance ID does NOT identify its family, and the two get-by-id endpoints
are not equally strict.** Verified live 2026-09-10:

| | `GET /vdb-relational/.../id/{id}` | `GET /vdb-memory/.../{id}` |
|---|---|---|
| a relational `db-` id | returns it | `404 not_found` |
| a MemoryStore `db-` id | **returns it** | returns it |
| a PostgreSQL Cluster `pg-` id | **returns it** | `404 not_found` |

MemoryStore instances carry the **same `db-` prefix** as relational ones
(`db-5a4d26f1-…` is Redis), so the prefix discriminates nothing except `pg-`.
The relational get-by-id is effectively family-agnostic and hands back a Redis
instance or a cluster without complaint, which is how a relational-only
operation such as `resize-storage` ends up aimed at a resource that has none.
`get_relational_instance`'s docstring says so, and `get_memory_instance` is the
lookup that actually proves a family. Read `datastore_type`, never the prefix.

The *memory* instance listing, by contrast, is single-family and its filters
**are** exact — a name that matches nothing returns zero rows. Only the
relational listing has the mixed-listing caveat.

### `user-type`: the two flows are not variants, and `IAM_USER` is the only useful one

19 endpoints accept an optional `user-type` header. Verified against the live
API — the two values behave completely differently:

| | `ROOT_USER` (Checkout) | `IAM_USER` (Auto Payment) |
|---|---|---|
| HTTP status | `200` | `200` |
| `orderUrl` | payment console order page | vDB console |
| `orderId` | **null** | populated |
| `resourceId` | **null** | **populated** (`db-...`) |
| Resource provisioned | **no** — the order waits for a human to pay | **yes** — goes straight to `BUILDING` |

`ROOT_USER` answers `200` and creates nothing, so a caller that trusts the
status code waits forever for a resource that will never appear. That is why
`DEFAULT_USER_TYPE` is **`IAM_USER`** even though `ROOT_USER` is the API's own
default, and why every billable tool defaults to it. `ROOT_USER` stays
reachable for the rare case someone wants a reviewable order.

Because `IAM_USER` returns `resourceId`, a create does **not** need to poll by
name to find what it made.

`VdbClient.call` omits the header entirely when `user_type` is `None`, so a
non-billable call never opts into a flow by accident.

This is why `mcp_core.http.BaseClient._request` grew a `headers` parameter. It
is applied in **both** places a request is built — the first attempt and the
retry after a token refresh — and can never override `Authorization` or
`User-Agent`.

### Lifecycle actions: `action` and `resType` are NOT derivable from the path

Every lifecycle endpoint takes the same envelope, and the values inside it are
what the spec's `description` and `example` say — not what the path says:

```jsonc
{"databaseInstances": [{"instancesId": "db-..."}], "action": "stop", "resType": "dbaas"}
```

| Endpoint | `action` |
|---|---|
| `/start` | `start` |
| `/shutdown` | **`stop`** — not "shutdown" |
| `/reboot` | `reboot` |
| `/detach-replica` | **`detach_replica`** — underscore, not hyphen |
| `/delete` | `delete` |
| `/resize-instance`, `/resize-storage` | `resize`, and the key is `resourceType` not `resType` |

`resType` / `resourceType` is always the literal **`dbaas`** — *except on a
backup restore*, which is the one endpoint that changes both halves at once:
`action: "restore_backup"` and `resourceType: "dbaas-backup"`. Neither is
derivable from the instance envelope, and the failure mode below applies to it
too.

**A wrong `action` or `resType` returns HTTP 200 with `data: []`, applies
nothing, and reports no error.** That is indistinguishable from "accepted, in
progress", which is why `ActionData` sets `accepted=False` and a `warning` when
the result array comes back empty — never present that as success.

The values live in `ActionDbInstanceRequest.action.description`
(`"Allowed values: start, stop, reboot, detach_replica"`) and its `example`.
There is no `enum`. **Read the raw schema in `docs/api-docs-local/vdb-api-docs.json`
when building any write payload** — `vdb-api-map.md` is a lookup table, and its
generator only started preserving `description`/`example` after this bit me.

### Backups: the listing and the detail describe the same row differently

Verified live by reading one backup through both endpoints. `GET /backups`
returns `storageType`, `storageSize`, `ram`, `vcpu`, `packageId`, `username`,
`configId`, `netIds`, `backupDuration` and `projectId` as **`null`**, all of
which `GET /backups/detail/{id}` fills in — and the listing reports
`datastoreType` as `mysql` where the detail says `MySQL`, for the same backup.

So a list row is an index entry, not a description. A restore planned from one
sends nulls for the fields it needs and comes back as a bare `in_valid`.
`RelationalBackupListData` sets `rows_are_summaries` to state this rather than
leaving it to be discovered.

Two more traps in the same shape:

- **`netIds` on a backup holds NETWORK ids (`net-…`)**, while `netIds` on a
  create or restore wants a **subnet** id (`sub-…`). Same upstream name,
  incompatible values — which is why the model calls it `network_ids`.
- **`pageObject.maxSize` is 50 here**, not the 100 the instance listings
  report. A larger `pageSize` is still honoured, so it reads as advisory, but
  do not assume one ceiling across the API.

`isRestoring` is the reverse of the usual pattern: populated (`false`) in the
listing and `null` in the detail.

**The two listings null *different* fields, and not the ones you would guess.**
Measured on the memory family 2026-09-10: the **project-wide** `GET /backups`
does carry `dbInstanceId` and `instanceName`, while the **per-instance**
listing (`/database-instances/{id}/backups`, and its relational twin
`/backups/insId/{id}`) returns both as `null`. So "the listing is the thin one"
is too coarse a rule — the per-instance listing cannot even tell you which
instance a row belongs to, which is why both list models carry
`rows_are_summaries` and the instance id is echoed from the request.

**Neither `get` backup endpoint is family-scoped.** `GET /vdb-memory/v1/backups/{id}/detail`
returns a MySQL backup and `GET /vdb-relational/v1/backups/detail/{id}` returns
a Redis backup, both without complaint — measured both directions. A result
therefore proves nothing about which family the backup belongs to; read
`datastore_type`. This matters because restoring needs the *matching* family's
restore tool and flavour catalogue, and the two restore bodies differ. Both
tools' docstrings say so, and neither "not found" message blames a family
mismatch any more — an id that fails here really does not exist.

**A successful backup deletion reports `success: null`.** Measured on the
memory endpoint: deleting a real backup returns `status: PROCESSING` with
`success: null`, while deleting an id that does not exist returns
`success: false, code: 404`. So `null` is the normal case and only an explicit
`false` is a failure — treating "not true" as failure would report every real
deletion as broken. (Same shape as backup storage deletion.)

### The free backup allowance is not a constant

`GET /backups/free-backup` reads like a fixed per-family quota — 150 GB for
relational, 100 GB for memory on this account — and it is not. It is the sum of
what the project's **instances** grant: each flavour's catalogue row carries a
`backupSize`, and each instance row a `freeBackupSize`.

Measured live: restoring one extra Redis instance on flavour `139`, whose
catalogue row says `backupSize: 5`, moved the memory allowance from 100 GB to
**105**; deleting that instance moved it back to 100.

Two consequences: read the allowance when you need it rather than remembering
a number, and note that **deleting an instance shrinks the allowance**, which
can put backups that were previously free over the line.

### `backupDuration` is retention in DAYS, not a window length

All seven schemas that carry it say the same thing: "how many days your backup
will be retained. Minimum 2 days and maximum 14 days". The name and its
position next to `backupTime` ("02:00") make it read as the length of the
backup window; it is not, and the obvious `1` is rejected. `models/requests.py`
bounds it `ge=2, le=14` on every write DTO through
`BACKUP_RETENTION_DESCRIPTION`.

Response models are **not** bounded: rows created before the rule exist and
report `1`.

### `backupType` is one of the few genuinely closed value sets

`FULL` or `INCREMENTAL`, named in the field's `description`. An `INCREMENTAL`
backup requires `parentId` — another backup of the same instance — and a `FULL`
one must not have it; both are checked in `CreateRelationalBackupDto` because
the API reports either mistake as `in_valid`. An incremental backup is only as
restorable as its chain of parents, so deleting a parent silently invalidates
its children.

A backup also carries `type` (`MANUAL` / `AUTO_DAILY`, projected as `origin`)
and `backupTier` (`FREE`, projected as `tier`). Those two *are* populated in
the listing.

### Creating a backup: two rules the spec does not state, both failing async

Both were found by live A/B with the instance idle between attempts, and both
share the worst failure shape in this API: **HTTP 200 with `success: true` and
a real `backupId`, then the backup fails and disappears.** It never reaches any
listing, and `get_relational_backup` reports it missing. The *only* record is
`list_relational_instance_histories`.

**1. `description` is required, despite being optional in the spec.**

| `description` sent | Outcome |
|---|---|
| field absent | `Failed` — "An error occurred when communicating with system" |
| `""` | `Failed` — same message |
| `"Manual created"` | `Finished` |

The Portal never exposes this field to the user; it generates the text itself
("Manual created" for a hand-made backup). `CreateRelationalBackupDto`
therefore defaults to that string and sets `min_length=1`, and the handler
never sends the key as null.

**2. One backup action per instance at a time.** A second `create` while one is
running is accepted, then fails with `Cannot perform action CREATE_BACKUP,
current database action is CREATE_BACKUP`. Wait for the previous backup to
leave its transitional status.

These two produce *different* error strings, which is the only way to tell them
apart — and the generic one belongs to the missing description, not to a
platform outage. Do not read it as a platform fault before checking the body.

### Backup storage

**The two `GET` paths mean OPPOSITE things in the two families.** The
operationIds read as swapped in both, so the paths are what to trust — but the
same path is not the same answer:

| Path | Returns |
|---|---|
| `GET /vdb-relational/v1/backup-storages` | the **price list** |
| `GET /vdb-relational/v1/backup-storages/information` | what the project **holds** |
| `GET /vdb-memory/v1/backup-storages` | what the project **holds** |
| `GET /vdb-memory/v1/backup-storages/packages` | the **price list** |

Measured side by side on one token: the memory bare path returned 0 rows (the
project held nothing) while the relational bare path returned 3 rows keyed
`{engineGroup, packages}`. The memory family has no `/information` path at all,
and its delete is `/actions/delete` where the relational one is
`/actions/deletions`. **Never carry a backup-storage path across families**,
however identical it looks; `HELD_PATH` and `PACKAGES_PATH` in
`memory_storage_handler.py` exist to keep that visible in the code.

**Resize and delete use different keys for the same thing.** Both carry
`dbaas-backup-storage`, but resize spells the key `resourceType` and delete
spells it `resType`:

```jsonc
// POST /backup-storages/actions/resize
{"databaseInstances": [{"instancesId": "db-bk-storage-...", "config": {"backupPackageId": "4"}}],
 "action": "resize", "resourceType": "dbaas-backup-storage"}

// POST /backup-storages/actions/deletions
{"databaseInstances": [{"instancesId": "db-bk-storage-..."}],
 "action": "delete", "resType": "dbaas-backup-storage"}
```

That makes **three** resource-type values across this API — `dbaas`,
`dbaas-backup`, `dbaas-backup-storage` — under **two** key spellings, in five
combinations, none derivable from the path. `_resize_envelope` and
`_delete_envelope` are deliberately two functions rather than one with a flag.

Note also that both envelopes call backup storage `databaseInstances` /
`instancesId`. Those are `db-bk-storage-` ids, not instance ids.

**The price list is repeated per engine group, identically.** Live: groups 1
and 2 return the same eight packages (ids 1-8, 100 GB to 10 TB, byte-identical
rows); group 3 returns none. Same in the memory family. So a `package_id` is
unambiguous and there is nothing to match against the group.
`list_relational_backup_storage_packages` returns a deduplicated `packages`
list plus the raw `groups`, and sets `groups_are_identical` so a future
divergence is visible rather than silently flattened.

**Creating backup storage is a RECURRING charge**, unlike an instance order.
Deleting the quota is how it stops.

⚠️ **Paid quota does NOT add to `freeBackupStorage`.** While the project held
100 GB and then 200 GB of paid memory quota, `get_memory_free_backup_usage`
kept reporting `100` — the free allowance and the paid quota are separate
pools. Never add them together when telling a user how much room is left.

The whole buy → resize → release cycle has now been run live in **both**
families, and both behave the same way: create and resize settle within
seconds with no transitional status, and delete answers
`status: PROCESSING` with `success: null` before taking effect about a minute
later.

**Verified live** by buying, resizing and releasing a quota:

```jsonc
{"id": "db-bk-storage-32b911d6-...", "name": "relational_backup_storage",
 "quota": 100, "usage": 0.0, "status": "ACTIVE", "engineGroup": 1,
 "backupPackageId": "1", "backupPackageName": ""}
```

Three things that came out of it:

- **`backupPackageName` comes back empty** even though the package has a name
  in the catalogue. Match `package_id` against
  `list_relational_backup_storage_packages` to name it.
- **`name` is a fixed label, and it differs per family**: every relational
  purchase is called `relational_backup_storage` and every memory one
  `memory_store_backup_storage`. Create takes no name field.
- **`engineGroup` is 1 for relational and 2 for memory** on this account.
- **Both create and resize are effectively synchronous.** The order returned a
  populated `resourceId`, and the quota appeared and changed within seconds
  (100 GB → 200 GB) rather than passing through a transitional status. The
  delete answered `status: PROCESSING` with `success: null` and had taken
  effect within ~15 seconds. `success: null` is normal here, so it is not
  treated as a failure -- only an explicit `false` is.

### Configuration groups

**`get` takes its id as a QUERY parameter.** `GET /v1/configurations/id?id=cfg-...`
— the literal word `id` is the path segment. The memory family uses
`/configurations/{id}/detail`.

**`deployType` is required on create, though the spec says otherwise.** The
spec marks it optional, scopes it to PostgreSQL, and names `single_node` as its
default. Omitting it returns `400 bad_request` — verified live for MySQL, with
and without a description. `CreateRelationalConfigurationDto` therefore
defaults it rather than omitting it.

**In the memory family it is required and the schema does not have the field at
all.** `CreateMemConfigGroupRequest` declares exactly four fields — name,
description, datastoreType, datastoreVersion — and a body containing those four
is rejected as `400 bad_request`; adding `deployType: "single_node"`, which the
schema never mentions, succeeds. A DTO built faithfully from that schema cannot
create anything, which is how `CreateMemoryConfigurationDto` came to be wrong
until its first live call. **Reading the schema is not enough for a write
payload — the only proof is a live call.**

Two more differences on that same endpoint, both measured:

- **`datastoreType` is case-sensitive here.** `"redis"` is rejected as
  `bad_request` where `"Redis"` succeeds — unlike the flavour and parameter
  catalogues, which match case-insensitively. The DTO normalises it.
- **`description` really is optional** in the memory family, unlike the backup
  `description` the spec also calls optional.

So this is the **fourth** field in this package whose documented behaviour the
server does not implement, after the backup `description`, the relational
`deployType`, and `configId: ""`. Treat "optional, default X" in this spec as a
claim to test.

**A brand-new configuration group cannot be deleted for the first several
seconds.** Measured: create, then delete immediately → `success: false,
code: 404, "Resource not found"`; the same id 45 seconds later → `success:
true`. The group is in the listing and readable the whole time; only the delete
path lags. Do not read that 404 as a wrong id.

While isolating that, three delete body shapes were compared. The array
element's field is **`id`**, as the spec says — even though the *response*
calls it `configId`, and sending `configId` is accepted and does nothing. A
bare string array is a `400`. A two-id batch works.

**The memory create response carries only `id` and `deployType`**, and
`GET /configurations/{id}/detail` answers an **empty payload** for a few
seconds after a create — so the re-read that normally fills in a thin echo can
fail too. `create_memory_configuration` then fills from the request it just
sent rather than returning a group with no name and no engine.

**The create response omits `name`.** The group is named correctly and a later
GET proves it, but the immediate answer carries an empty `name`, so
`create_relational_configuration` re-reads the group before returning it.

**Changing a group's values changes every attached instance.** For MySQL 8.0,
13 of 67 parameters report `restartRequired`. Verified live: changing
`innodb_buffer_pool_size` on an attached group moved the instance to
`RESTART_REQUIRED` within 30 seconds, and **the instance keeps serving the old
value until it is rebooted** — so a change can look applied and do nothing.
`update_relational_configuration` cross-references the submitted keys against
the parameter catalogue and reports `restart_required_parameters` plus
`affected_instances`. That lookup is best-effort: if it fails, the update still
succeeds and the tool claims nothing rather than guessing.

**The update response is too thin to answer that question, so the group is
re-read.** Measured live, `PUT /configurations/update` echoes `values: {}` and
no `instances` — it does not even say which engine the group is for. An earlier
version of the handler trusted that echo and reported *"no instance uses this
group yet, so nothing restarted"* at the exact moment it had pushed a running
database into `RESTART_REQUIRED`. The re-read exists to prevent that specific
lie. The re-read's own `values` can still lag a moment behind the write;
`datastore_*` and `instances` are reliable.

**Polling right after an async action returns the pre-action status.** A
`get_relational_instance` issued seconds after a reboot still reports the old
status, so `settled`/`attention` is not a "done" signal there — wait for the
transition to appear before treating a status as the outcome.

**A numeric parameter's `values` is `[min, max]`, not an enum.** Upstream, one
field carries two different things depending on `type`: a genuine enum for
string parameters (39 character sets, ~500 time zones) and the two range
endpoints for numeric ones. `innodb_buffer_pool_size` arrives as
`["5242880", "2147483647"]`, which presented as an enum would claim those are
the only two legal sizes. `ConfigurationParam` exposes `allowed_values` only
for non-numeric parameters and `minimum`/`maximum` for numeric ones.

**The parameter catalogue needs an engine pair, like flavours.**
`datastoreType` and `datastoreVersion` are both required, and an unrecognised
pair returns an **empty list rather than an error** — so the result sets
`unknown_engine` to distinguish "wrong pair" from "no settable parameters".

It is also **not family-scoped**: the memory endpoint returns MySQL 8.0's 67
parameters if asked for them. It is a shared catalogue behind two paths, so the
`datastore_type` default on each tool is a convenience, not a constraint.

**Parameter counts differ sharply between Redis versions** — 4.0 exposes 24
(one restart-required, `tcp-backlog`), while 6.2 and 7.2 expose 8 and none.
Always pass the version of the group being changed.

**`maximum` is usually absent for Redis.** Most integer parameters come back
with `max: null` and only a `min` (`repl-backlog-size` has a floor of 16384 and
no ceiling). When `max` is null, `values` is a **one**-element list, not the
`[min, max]` pair the relational family shows — so never tell a user a
parameter has an upper bound unless `maximum` is set.

**`instanceCount` can disagree with `instances`.** A freshly attached instance
appeared in `instances` while `instanceCount` still read 0 — confirmed in both
families. Trust the list, not the count, when deciding whether a group is in
use.

**Changing a group's `values` writes no instance history entry.** Only
attach/detach does. So for a parameter change the history is not where to
confirm it; re-read the group — and its `values` lag a moment, returning the
previous set immediately after the update and the new one within a minute.

⚠️ **Detaching a configuration group also leaves the instance in
`RESTART_REQUIRED`** — measured on Redis 7.2, which reports **zero**
restart-required parameters. Attach left the instance `ACTIVE`, changing two
parameters left it `ACTIVE`, and the detach took it through `BUILDING` to
`RESTART_REQUIRED`. (The status read `ACTIVE` immediately before the detach, so
the detach is the likely cause; a delayed effect of the parameter change cannot
be ruled out entirely.) The lesson for the tools: a restart-required count of
zero is **not** a guarantee that no reboot is needed, which is why the
update tool's "no restart expected" message still tells the caller that the
instance status is what decides.

**Page ceilings differ per endpoint.** `pageObject.maxSize` is 100 for instance
listings, 50 for backups and **30** for configurations. A larger `pageSize` is
still honoured, so it reads as advisory.

### Deleting a backup: HTTP 200 is not deletion

Deleting an id that does not exist returns **200** carrying
`{"success": false, "code": 404, "errorMsg": "Resource not found"}`. The HTTP
status describes the call, not the deletion, so `BackupDeleteData` sets
`accepted=False` and surfaces the per-row error whenever any result reports
`success: false`.

Similarly, `GET /backups/detail/{id}` answers **200 with an empty payload** for
a backup that does not exist rather than a 404. `get_relational_backup` raises
instead of returning a model whose every field is `""` — which an agent cannot
distinguish from a real backup.

### Creating a backup is not an order flow

Unlike create instance / resize / restore, `POST /backups/create` takes no
`user-type` header and raises no order. It answers synchronously with the
`backupId` it just started — the id exists before the data does, so poll
`get_relational_backup` until the status settles before restoring from it.

Restoring, by contrast, **is** an order flow and builds a *new* instance. It
never overwrites the source instance and never rolls anything back, so it costs
the same as a create and needs the same zone-consistent discovery chain.

### Status vocabulary — complete, and NOT classifiable by casing

`statuses.py` holds all four published vocabularies, supplied by the GreenNode
team and kept verbatim in `docs/api-docs-local/vdb-status-notes.txt`: 21 values for relational/MemoryStore instances, 6 for backups, 11 for
PostgreSQL Cluster, 48 for Kafka.

Casing looks like a signal and is not. The transitional relational states are
lowercase (`stopping`, `deleting`) and the settled ones uppercase (`ACTIVE`) —
but `BUILDING`, `BACKUP`, `REBOOT` and `RESIZE` are uppercase and describe work
in progress, while `SHUTDOWN` is uppercase and settled. Use `classify()`, never
the case.

`REBOOT` and `rebooting` are two different states, not two spellings of one;
same for `SHUTDOWN` (stopped) and `stopping` (stopping).

`classify()` returns one of four actionable kinds:

| Kind | Means | What a tool should do |
|---|---|---|
| `failed` (`ERROR`, `INTERNAL_ERROR`, `FAILED`, `BILLING_ERROR`) | Platform-side failure | Report the id, zone and request so the platform team can look. Do **not** retry as if the payload were wrong. |
| `transitional` | An operation is running | Poll again; do not report a result or start another operation |
| `settled` (`ACTIVE`, `COMPLETED`, `DELETED`, `SHUTDOWN`) | Nothing in flight | Safe to report |
| `attention` (`RESTART_REQUIRED`, `BLOCKED`, `EXPIRED`, `EXPIRING`, `WAIT_BILLING`, `WARNING_CONFIG_GROUP`) | Stable but unusable as-is | Say what decision the user has to make; do not act for them |

An unlisted status returns `unknown`, which means "cannot tell" — never a
failure. The platform can add a state before this file learns of it, so the
vocabularies stay documentation rather than a closed `Literal[...]`.

Instance responses carry `status_kind` and `status_guidance` alongside the raw
`status`, so a caller does not have to infer meaning from a bare string.

### Six endpoints take a JSON **array** as the request body

Not an object — the DTO must be a `list[...]`:
`DELETE /vdb-relational/v1/backups/{backupId}/delete`,
`POST /vdb-memory/v1/backups/delete` (**no id in the path, so the array is the
whole instruction and it deletes every entry — the relational twin repeats one
id in its path and can only ever delete one**),
`DELETE /vdb-relational/v1/configurations/delete`,
`POST /vdb-memory/v1/configurations/delete` (**a `POST` here, a `DELETE` with a
body there; the element field is `id` in both**),
`PUT /vdb-relational/v1/database-instances/{instanceId}/secrules`,
`PUT /vdb-memory/v1/database-instances/{dbInstanceId}/secrules`.

`BaseClient.delete()` accepts `json` for the two DELETE cases.

### Creating and resizing is an order flow

`POST /payment/database-instances` (both families) and the Kafka/PostgreSQL
cluster creates raise a **billable order** and return **asynchronously**. Every
`create_*` / `resize_*` / `restore_*` tool needs a `*_dryrun` companion
registered with the `READ` annotation.

### No `projectId` in any URL

It appears only in response bodies. Unlike vServer (`/v2/{projectID}/...`)
there is nothing to inject, so `VdbClient` has no `project_id()` helper.

### No `enum` anywhere in the spec

None of the 158 schemas declares an enum: status, datastore type, volume type
and flavour are free strings. Use discovery tools plus `DiscoveryCache`, not
hard-coded `Literal[...]`.

`GET /vdb-memory/v1/database/status` (`listDatabaseInstanceStatus`) is **dead
and removed from the spec** — never call it. There is therefore no dynamic
source for status values; `status` parameters are plain `str`, with the
observed values (`ACTIVE`, `BUILDING`, `WAIT_BILLING`) named in the docstring
as hints only.

### Write-flow quirks the spec does not mention

**Password rules differ by family, and are absent from the spec.** Same
character set — only `a-z A-Z 0-9 $ ^ _ < >` — but different lengths:
**8-32** for relational, **16-128** for MemoryStore. Both must start with a
letter and end with a letter or digit. A password valid for a relational
instance is usually too short for Redis, so `models/requests.py` has
`validate_relational_password` and `validate_memory_password` rather than one
shared validator. Confirmed live: a 12-character password — perfectly legal for
MySQL — is rejected by the memory create as a bare `in_valid`.

**`in_valid` is the message for every validation failure, including an empty
body.** Confirmed: `POST` with `{}` returns `400 Bad request: in_valid`, the
same as a bad password or a mismatched zone. It names no field, so a payload
cannot be bisected by reading the error. When it appears, check the password
first, then that `packageId`, `volumeType` and `locateZoneId` all belong to the
same zone. This is also why every DTO sets `extra="forbid"` and validates as
much as possible locally.

**Volume types are zone-suffixed and flavour ids are per-zone.** `HCM03-1A` has
`Gen2-NVMe2-IOPS3000`, `HCM03-1B` has `Gen2-NVMe2-IOPS3000-HCM03-1B`, and
`HCM03-1C` offers a different set again (`ssd-iops3000-HCM03-1C`). The flavour
named `db.s1-highcpu-2x2` is id 224 in 1A, 192 in 1B and 208 in 1C. A create
must take `packageId` and `volumeType` from the **same zone** as
`locateZoneId`; mixing them yields `in_valid`.

**`databases` is required in practice.** The spec marks it optional, but
creating with `databases: []` is rejected as `in_valid`.

**Unknown fields are ignored, not rejected — so `extra="forbid"` is doing real
work.** Verified live on the memory create: a body carrying the relational-only
`volumeType` and `volumeSize` answered `200`, raised an order and provisioned
an instance whose disk was the flavour's default (8 GB), not the 20 GB
requested. Nothing in the response or the history mentions the dropped fields.
A caller who copies a relational payload into a memory create therefore gets a
working instance sized differently from what it asked for, silently. The DTOs
are the only thing that turns that into a named error.

**The MemoryStore create has no volume, database or user fields at all.** A
Redis flavour bundles its disk (`volumeType` / `volumeSize` appear on the
*response* but are not choosable), Redis has no per-database concept, and it
authenticates with a single master password (`redisPasswordEnabled` +
`redisPassword`) rather than a user. There is also no `resize-storage` in this
family, so changing the flavour is the only sizing operation.

**`publicAccess` requires `redisPasswordEnabled`.** The spec says so
("Required ENABLED if `publicAccess` is true") and the platform enforces it
with a bare `in_valid`. `CreateMemoryInstanceDto` checks it locally and
defaults `redisPasswordEnabled` to true.

**`update-setting` in the memory family needs `editRedisPassword`.** The spec:
"Set to 'true' if you make any change of 'redisPasswordEnabled' or
'redisPassword'". Without it the password half of the body is ignored — the
call succeeds and changes nothing. `UpdateMemorySettingDto` rejects that
combination rather than deriving the flag, because deriving it would mean
guessing whether re-sending an unchanged `redisPasswordEnabled` counts as a
change, and guessing wrong could reset a password nobody asked to reset.

**`datastoreType` in a listing echoes what was sent at create time.** An
instance created with `datastoreType: "mysql"` reports `mysql`; one created
through the Portal reports `MySQL`. So when *reading*, never match with `==` —
compare case-insensitively. When *writing*, the DTOs normalise through
`canonical_datastore_type` to the platform's spelling (`MySQL`, `PostgreSQL`,
`MariaDB`, `Kafka`, `Redis`) whatever case the caller used, so this server never
adds to the inconsistency. An unrecognised engine passes through untouched —
there is no enum to validate against.

### Catalogue quirks

**`flavors` is not a bare lookup.** `GET /v1/database-instances/flavors`
(relational) and `GET /v1/database/flavors` (memory) **require** `type` and
`version` and answer `400` without them. An unrecognised pair is **not** an
error — it returns an empty array — so a wrong version reads as "this engine
has no sizes". Always source the pair from the datastore catalogue. `type` is
matched case-insensitively but the *pair* must exist. An empty `version`
string produces a `500`, so it is rejected before the call.

**Omitting `zoneId` means the DEFAULT zone, not all zones.** True for both
flavour endpoints and all three volume-type endpoints. Measured: mysql 8.0
returns 84 flavours unzoned and for `HCM03-1A`, 47 for `HCM03-1B`, 34 for
`HCM03-1C`. An unzoned result silently describes one zone, so the response
models echo the `zone_id` they describe.

**Three flavour fields look like the zone and only one is.** `locateZoneId` is
the availability zone (`HCM03-1A`) that matches the zones endpoint and the
`zoneId` parameter. `zoneId` is an internal integer index; `zoneUUID`
identifies a zone *group*. Project `locateZoneId`.

**The families endpoint returns two kinds of row.** Rows with
`group == "family_custom"` describe a **zone group**, not a family: `id` is the
UUID a flavour reports as `zoneUUID` and `name` is its label ("ENG DEV 1a"),
with `key`/`value` null. Real families carry `key` (the flavour's `familyType`)
and `condition.codes`.

**`networks/subnets` returns networks, not subnets.** Each row is a network
with a nested `subnets[]`. A create needs a `subnetId`, so `list_*_subnets`
flattens them and keeps the parent network on each row — a network with no
subnets contributes no rows.

**`backup-storages` operation ids are misleading; trust the path.**

| Path | operationId | Actually returns |
|---|---|---|
| `GET /vdb-relational/v1/backup-storages` | `getListQuotaPackage` | quota **packages** |
| `GET /vdb-relational/v1/backup-storages/information` | `getListBackupStorage` | storage **in use** |
| `GET /vdb-memory/v1/backup-storages` | `getListBackupStorage_1` | storage **in use** |
| `GET /vdb-memory/v1/backup-storages/packages` | `getListQuotaPackage_1` | quota **packages** |

**`GET /v1/database-instances/configuration` duplicates `/v1/configurations`.**
Same resource, same fields, same rows — the former is simply unpaginated. It is
deliberately **not** exposed as a second tool: two tools returning identical
rows makes a caller guess which is authoritative.

### The two `PUT` responses, and a field-swap bug in them

`update/setting` and `update/config-group` both answer:

```jsonc
{"code": 200, "message": "success",
 "data": {"status": 202,
          "dbInstanceId": "pro-2b233cab-...",   // the PROJECT id
          "projectId": "db-fa4dbb88-..."}}      // the INSTANCE id
```

**`dbInstanceId` and `projectId` hold each other's values.** Measured on both
endpoints. The GreenNode team's answer: **ignore the body, rely on the HTTP
status**. So both tools return `SettingUpdateData` (`applied=True` whenever the
call returned 2xx) and do not surface the payload at all — modelling it by
field name would hand the caller a project id labelled as an instance id.

The change is applied asynchronously either way; `config-group` in particular
takes longer to appear than a single follow-up GET.

### A new instance is open to `0.0.0.0/0` by default

Creating with `publicAccess: false` still produces a default security rule of
`0.0.0.0/0` on the database port: `publicAccess` governs floating-IP
assignment, not the firewall. This is long-standing platform behaviour and not
something to work around — but say so when advising a user, because it is not
what `publicAccess: false` sounds like. `update_*_secrules` is where it gets
narrowed, and it **replaces the whole rule set**, so send the existing rule
back with its `id` and a tighter `remoteIpPrefix`.

Confirmed for MemoryStore too (port 6379, `0.0.0.0/0`, with
`publicAccess: false`). Note that a rule's `id` does **not** survive the
update: narrowing one rule returned a rule with a different id, so an id read
before a write cannot be used to correlate afterwards — re-read the rules.

### Six endpoints whose response the spec does not describe — all settled

Both `GET .../replicas` return an array inside the envelope (empty when there
are no replicas). All four `PUT`s — the relational `update/setting` and
`update/config-group`, and the memory `update-setting` and
`update-config-group` — return the **same** shape, **including the same
field-swap bug**: `dbInstanceId` holds the project id and `projectId` holds the
instance id. Measured on the memory pair 2026-09-10:

```jsonc
{"code": 200, "message": "success",
 "data": {"status": 202,
          "dbInstanceId": "pro-2b233cab-...",   // the PROJECT id
          "projectId": "db-37ff19f7-..."}}      // the INSTANCE id
```

So all four tools return `SettingUpdateData` and surface no part of the body.
This was worth measuring rather than assuming: the point of section 4.7 was
that the spec declares these as bare `object`s, and "it probably matches the
relational one" is exactly the inference that has been wrong three times in
this package.

### An edit is accepted while busy, then fails asynchronously

Verified live on the memory family, and it is the failure mode to expect from
any `PUT`/action here: **only one edit runs per instance at a time.** A second
one is accepted with `200` and `status: 202`, then fails with
`Cannot perform action EDIT, current database action is EDIT` (or
`... database [name] status is BUILDING`).

Nothing in the instance detail or any listing records that failure. The **only**
record is `list_*_instance_histories`, and its `description` states what the
platform understood the request to be — which is how the `configId` semantics
below were established rather than guessed:

```
Update  Finished  Update database with following changes: Attach config redis72
Update  Failed    Update database with following changes: Detach config redis72
                  ERR=Cannot perform action EDIT, current database action is EDIT
```

So: wait for `status_kind == "settled"` between writes, and after any write
that looks inert, read the history before drawing a conclusion.

### `configId: ""` does NOT detach a config group — `null` does

The spec says of `UpdateDbConfigGroupRequest.configId`: "New config group ID or
set to empty string \"\" to detach current config group". That is wrong.
Measured on an idle instance with a group genuinely attached:

| Body | Result |
|---|---|
| `{"configId": ""}` | **`400 in_valid`** |
| `{"configId": null}` | `200` → history: "Detach config redis72" |
| `configId` omitted | `200` → history: "Detach config redis72", Finished |

**Both families behave the same way**, and both tools translate. The two
endpoints share the very same `UpdateDbConfigGroupRequest` schema and return
byte-identical responses (field-swap bug included), and the relational endpoint
was measured to reject `configId: ""` with `in_valid` too — a 400 mutates
nothing, so that half is safe to probe on a live instance. The `null`-detaches
half was measured on the memory endpoint only; detaching a real group on the
account's relational instances was not something to do uninvited, so treat that
half as "same schema, same response, user-confirmed" rather than
independently measured.

`update_relational_instance_config_group` and
`update_memory_instance_config_group` therefore both keep the empty string as
their *own* vocabulary — an agent expresses "none" far more reliably than a
JSON null — and translate it to `null` on the wire. This is the **third** field
in this package whose documented behaviour the server does not implement, after
the backup `description` and the configuration `deployType`.

**A listing reports an attached group's id as `null` while still carrying its
name.** Live, for the same instance: the listing says
`configuration: {"id": null, "name": "redis72"}` and the detail says
`{"id": "cfg-b634172f-...", "name": "redis72"}`. So `config_group_id` is only
trustworthy from a get, never from a list row.

### The PostgreSQL Cluster family (measured 2026-09-14)

**It has no listing and no get-by-id endpoint.** Not undocumented — absent.
`GET /v1/cluster` answers `400 Bad Request` and `GET /v1/cluster/{id}` answers
`403 IAM_PERMISSION_DENIED`. So `list_postgresql_clusters` and
`get_postgresql_cluster` are derived from the relational instance listing and
detail, filtered by the `pg-` id prefix.

**Five more operations have no cluster endpoint either**, and the relational
ones serve a `pg-` id — this is what the Portal itself calls:

| Operation | Endpoint used | Verified |
|---|---|---|
| Security rules (read + write) | `.../database-instances/{pgId}/secrules` | yes |
| History | `.../database-instances/{pgId}/histories` | yes |
| Reboot | `.../database-instances/{pgId}/reboot` | yes |
| Delete | `.../database-instances/{pgId}/delete` | yes |
| Replicas | `.../database-instances/replicas/{pgId}` | **403 — does not apply** |
| Backups | `.../backups/insId/{pgId}` | answers `[]` — cluster backups live in vBackup |

The wrapper is therefore **per operation**, not a blanket "the relational API
works for clusters". Do not extend it to another path without measuring that
path.

**`backupLocationId` must name a location that holds no cluster.** A location
is one-cluster-only: reusing one is rejected with `400 bad_request` and
`"Please select a backup location that does not have any backup instances"` —
note the `errors[].message`, which this family fills in where the relational
one answers a bare `in_valid`. On an account where every location is taken, the
create must omit `backupLocationId` **and** `backupPolicyId`. That is not "no
backups": verified on a cluster created with neither, the platform assigns the
default policy and creates a fresh location itself.

**The zones are not symmetric.** `GET /v1/cluster/flavors` returns 28 sizes for
HCM03-1A and HCM03-1B but **15** for HCM03-1C; volume types, 5 and 5 against 4.
Ids differ per zone even for an identical size, so neither an id nor the
*availability* of a size carries between zones. Unlike the relational and
memory flavour catalogues, this one takes no `type`/`version` — one engine —
and can be called bare, which then describes HCM03-1A only.

**Multi-AZ (added 2026-10-01) is chosen by `netIds`, not by a flag.** One
subnet = one zone; several subnets, one per zone, spread the nodes evenly
across those zones. Semantics as stated by the API owner, and verified live
with a two-node HCM03-1A + HCM03-1B cluster (~11 min to ACTIVE, one node per
zone):

- `?multiZone=true` on `/cluster/flavors` and `/cluster/volume-types` returns
  the catalogue that suits Multi-AZ, answered for the **Multi-AZ default zone
  (HCM03-1A)**. It **replaces `zoneId`**, so the two cannot be combined —
  `_postgresql_catalogue_params` refuses the pair rather than send a zone the
  answer will not describe.
- Each zone builds its nodes from **its own copy** of the flavour and volume
  type, so both must exist in every chosen zone. **HCM03-1C has no NVMe
  volume type**, which keeps it out of Multi-AZ today.
- The response carries `multiZoneInfos[]` (zoneId, subnetId, status, rw/ro
  IPs, `rwPort`/`roPort` **as strings**) on both the listing row and the
  detail; null for a single-zone cluster and for every relational row. The
  top-level `zoneId`/`subnetId` name only `locateZoneId`. `PostgresqlCluster`
  projects it as `multi_zone` + `zones`.
- **The API's own catalogue cache was keyed without `multiZone`** on
  2026-10-01 (`{userId, zoneId}`), so flavours and volume types came back for
  the wrong zone, non-deterministically, even on plain single-zone calls — the
  owner is fixing it. If a catalogue row's `zone_id` disagrees with what was
  asked, suspect that, not the client. Our `DiscoveryCache` key carries every
  query parameter, so it cannot repeat the bug.

**`GET /v1/cluster/volume-types` is a bare array** inside the envelope, where
the relational and memory twins wrap theirs in `{projectId, data[]}`. Same
catalogue, two shapes.

**`volume-used` returns unit-carrying strings, and not one per node.**
`["219M"]` for a three-node cluster, `["74M"]` for a two-node one — a single
element either way. It is also the only source: the detail response reports
`volumeUsed` as null for a cluster however much data it holds.

**The detail's `configId` is null while a group is attached**; the nested
`configuration.id` carries the real `pg-cfg-...`. Same trap as the relational
listing (above), one level further in.

**The resize is a `PUT`** where every other order flow in vDB is a `POST`, and
its `type` is `VOLUME-SIZE` / `VOLUME-TYPE` / `NUMBER-OF-NODES` — upper case
and hyphenated, against the lower-case-with-underscores of every other
enumerated value in this API (`single_node`, `detach_replica`). The values
appear only in the field's `description`; there is no `enum`.

**The config-group endpoint calls the field `configGroupId`**, where the
relational and memory endpoints call the same thing `configId`.

**A cluster has two ports and one address.** `port` 5432 reaches the primary,
`portRo` 15432 the replicas, and on a single-subnet cluster `privateRwIp` and
`privateRoIp` are the same address — so the port routes the connection. A new
cluster comes up with a **single** ingress rule: `0.0.0.0/0` on the read/write
port only, with nothing for the read-only one.

**A two-node cluster took about nine minutes to go from order to ACTIVE**, and
the order response carries the new `resourceId` under the default `IAM_USER`
flow (as the relational and memory creates do — an earlier docstring here
claiming otherwise was wrong and has been corrected).

**Almost every write puts the cluster into `BUILDING`, including ones that
look trivial.** Measured end to end on a two-node cluster: create ~9 min, a
security-rule change ~1.5 min, `publicAccess: true` **~5 min**,
`publicAccess: false` ~1 min, attach or detach of a configuration group ~40 s,
a `VOLUME-SIZE` resize ~1.5 min. A reboot reports `REBOOTING` (not `REBOOT`)
and takes ~45 s; a delete drops the cluster out of the listing within a
minute and reports `success: null`, which is what a *real* deletion looks like
here — only `false` is a failure. So the "wait for `settled`
between writes" rule (§ one edit at a time) is not a formality here — a second
write sent straight after the first will be rejected asynchronously.

**Detaching a configuration group works with `null`, and it was verified on
this endpoint.** `update_postgresql_cluster_config_group` translates the
caller's `""` into `null` on the wire, exactly as its relational and memory
twins do, and the history entry confirms what the platform did: *"Update config
/ Finished / Update: Detach config postgre-cluster-17"*. This is the
independent confirmation the relational half still lacks — and note that unlike
the Redis case, the detach left the cluster **ACTIVE**, not
`RESTART_REQUIRED`. The instance status decides, never the parameter
catalogue.

**`backup-now` answers the bare string `"success"`.** No id, no object. A new
restore point appearing in `list_postgresql_restore_points` is the only
evidence the backup landed; it showed up within a couple of minutes.

**Cluster configuration groups belong to the RELATIONAL config tools**, and
`deployType` is the axis that separates them — a seventh place where this API
answers a wrong-but-plausible question with an empty list instead of an error.

- A cluster group is created by `create_relational_configuration` with
  `deployType: "cluster"`, comes back with an id prefixed **`pg-cfg-`** rather
  than `cfg-`, and is returned by `list_relational_configurations` mixed in
  with the single-node ones. `deploy_type` on the row is how to tell them
  apart; `get_relational_configuration` resolves a `pg-cfg-` id fine.
- **`GET /v1/configurations/params` takes an undescribed optional `deployType`
  query parameter, and it defaults to `single_node` rather than "any".**
  Measured 2026-09-14:

  | PostgreSQL | omitted | `single_node` | `cluster` |
  |---|---|---|---|
  | 17 | 0 | 0 | **25** |
  | 16 | 0 | 0 | **25** |
  | 15 | 41 | 41 | **25** |
  | 14 / 13 / 12 | 40 / 40 / 73 | same | **0** |

  So 17 and 16 exist **only** as clusters, 14 and below only as single nodes,
  and 15 is the single version present in both — with a **different parameter
  set** in each, which is why the two are cached under separate keys. MySQL
  ignores the field entirely (67 either way), and the memory endpoint declares
  it but Redis has no clustered deployment (8/8/24 with and without), so
  `list_memory_configuration_params` deliberately does not expose it.
- The tool therefore fills `empty_result_hint` on an empty answer, naming
  `deploy_type` as the thing to change. Without that, the obvious reading of
  `unknown_engine` is "PostgreSQL 17 is not a real version", which sends the
  caller to fix something that was already right.
- `_restart_required_names` passes the group's own `deploy_type` when it
  enriches an update warning, so a cluster group's restart list is read from
  the cluster catalogue rather than the single-node one.

### The Kafka family (measured 2026-09-14)

Kafka shares the gateway and the token with the other three families and almost
nothing else.

**Paths carry no `/v1`.** `/clusters`, `/config-groups`, `/database/configs`.

**27 of 36 operations carry no envelope**: a bare object, a bare array, or a
bare string. `unwrap_wrapped` passes an unwrapped payload straight through, so
no `unwrap_kafka()` was needed after all — the earlier plan entry expecting one
was over-cautious. The nine that *are* enveloped are the five catalogue reads
and the four order flows.

**A bare-string response may be JSON or plain text and the spec does not say
which.** The schema is `type: string` and the content type is springdoc's
`*/*` placeholder. `VdbClient.call_scalar` reads the body as text and parses it
as JSON only if it is JSON, so both shapes work. Measured so far: the
config-group delete answers with an **empty body**, not a word.

**Several writes take query parameters, not a body** — authentication, public
access, the config-group attach and all three resizes. A body would be ignored.
**Delete is a real `DELETE`**: there is no `action`/`resType` vocabulary
anywhere in this family, and no `/delete` path.

**Two id fields name things the catalogue reports under three names each**, and
the obvious one is wrong both times:

| Create field | Catalogue row's… | …not its |
|---|---|---|
| `serverFlavorId` | `flavorId` (`flav-…`) | integer `id` |
| `kafkaStorageType` | `kafkaUuid` (`vtype-…`) | integer `id`, or `type` name |

`FlavorOption` carries `flavor_id` alongside `id` for exactly this reason —
before it did, `list_kafka_flavors` dropped the only id a create can use.

**`GET /database/configs` publishes the platform's own validation rules**,
which nothing else in vDB does. Brokers 3-10 (a quorum, so 3 — not the 2 a
PostgreSQL Cluster allows), storage 20-5000 GB per broker, 50 topics and 50
users per cluster, 10 clusters and 30 configuration groups per account, and the
**five ports** a security rule may open: 9092, 9094, 9096, 9194, 9196. Every
value is a string; `kafkaVersions`, `secGroupRulePorts`,
`defaultTopicSettingsByVersion`, `configsForcedValues` and two more are JSON
**inside** that string. One bound is absent: a topic's replication factor may
not exceed the cluster's broker count.

**Security rules exist only inside the cluster detail.** There is no endpoint
that lists them, and the cluster *listing* omits the field entirely. They are
also added and removed one at a time — the only family where a rule change
cannot silently drop another.

**A user's permission is a list or a flag, never both.** The platform ignores
the list when the `*All` flag is set, so `_KafkaPermissionsDto` rejects the
combination rather than letting a stored permission end up wider than it reads.

**`authen-creds` returns a ZIP archive, not an `array of string`.**
`application/octet-stream`, ~11 KB, containing `ca.p12`, `user.p12`,
`user.key`, `user.crt`, `config.properties` and two `.password` files. Putting
it through `call` raises `UnicodeDecodeError` on the first non-UTF-8 byte,
which is how the real shape was found. `call_bytes` reads it, the tool writes
it to a caller-named path, and only its size and entry names come back —
gated behind `--allow-sensitive-data-access`.

**Configuration groups are versioned and immutable**, and three measured
details contradict the obvious reading:

- the create answers `versions: []` **even though version 1 exists**, so
  `create_kafka_config_group` re-reads the group (same fix as the relational
  create's missing `name`);
- versions come back **newest first**;
- `properties` is empty on a listing row **and** on the group detail. Only
  `get_kafka_config_group_version` returns a version's settings.

**Cluster `createdAt` is formatted `Aug 12, 2026, 3:10:00 PM`** (with a narrow
no-break space before the meridiem) where every other Kafka timestamp is ISO
8601. Passed through verbatim rather than parsed.

🐛 **`POST /clusters` needs `tags` or it answers a bare `500`.** A create
without the field returns an empty Spring error page — no field named, no
message, and three unrelated payload variants produce it identically, which is
why it reads as a platform outage rather than a validation failure.

The cause, from the GreenNode Kafka team: a cluster was originally designed to
carry user tags as a `Map<String, String>`; the feature was dropped, but the
backend still calls `getTags().size()` while validating, so a missing field is
a null dereference. The Portal sends `tags: {}` on every create, which is why
it works there and a faithful reading of the OpenAPI schema does not — the
schema does not list `tags` at all.

`CreateKafkaClusterDto.tags` therefore defaults to an **empty map, not
`None`**: `model_dump(exclude_none=True)` would drop a `None` and bring the
crash straight back. A test pins the field onto the wire.

This is the **fifth** field in this package whose documented behaviour the
server does not implement, and the second whose schema omits it entirely (after
the memory configuration create's `deployType`). Treat "the schema says these
are all the fields" as a hypothesis here, exactly like "optional, default X".

**Kafka has THREE status vocabularies.** The 48 the platform team supplied are
the *cluster*'s; topics and users each have their own enum, also supplied by
the Kafka team and recorded in `../api-docs-local/vdb-status-notes.txt`:
`KAFKA_TOPIC_STATUSES` (11) and `KAFKA_USER_STATUSES` (9). Two differences are
not guessable — a **user has no `WAITING_*` state at all**, and it has two of
its own for credential rotation (`DELETING_OLD_CREDENTIALS`,
`GENERATING_NEW_CREDENTIALS`), the only place in vDB where a rotation is
visible as a status.

Both resources are created **asynchronously** (`WAITING_CREATING` for a topic,
`CREATING` for a user) and settle to `ACTIVE` within about a minute, so "create
is immediate" — which these docstrings used to say — is wrong for both.

**One operation per cluster at a time, reported as a plain `400`.** While a
topic was still `WAITING_CREATING`, updating it answered
`400 Bad request: Topic … is not active` and creating a *user* on the same
cluster was refused too. This is the same rule as the other families' "Cannot
perform action", but Kafka does not say so — a `400` immediately after another
write is a **timing** problem, not a bad payload.

🐛 **`PUT …/topics/{id}` replaces the whole topic, and both omissions blame the
wrong thing.** Measured:

| Body | Answer |
|---|---|
| `{partitions, retentionSeconds}` | `400 Can't update replicas for topic` |
| `{retentionSeconds}` | `400 Partition count needs to be between 1 and 2048` |
| `{partitions, replicas, retentionSeconds}` | 200 |

Neither error says a field is missing: one accuses the caller of changing
`replicas` they never mentioned, the other range-checks a `partitions` that was
never sent. `UpdateKafkaTopicDto` therefore makes both **required**, so the
schema says what the API will not.

**Every Kafka write answers with an empty body.** The schema's `type: string`
never materialises as an actual word — measured across add/delete secrule,
update/delete topic, update/delete user, credential re-issue, and both
config-group writes. Deleting a user answers `204 No Content` outright.
`call_scalar` copes with either, but the docstrings say "empty" because that is
what happens.

**Each Kafka write walks its own status sequence**, where the relational and
memory families report a single `BUILDING`. Measured on a three-broker cluster:

| Operation | Sequence | Time |
|---|---|---|
| Create | `INFRA_CREATING → NODES_CREATING → CLUSTER_CREATING → ACTIVE` | ~9 min |
| Attach config group | `WAITING_UPDATING_CONFIG_GROUP → UPDATING_CONFIG_GROUP → ACTIVE` | ~1 min |
| Enable SASL | `UPDATING → ACTIVE` | ~2.5 min |
| Public access on/off | `WAITING_UPDATING → UPDATING → ACTIVE` | ~1.5 min |
| Storage 20→30 GB | `UPDATING_STORAGE_SIZE → ACTIVE` | ~2 min |
| Brokers 3→4, rebalanced | `INFRA_SCALING_OUT → NODES_SCALING_OUT → WAITING_CLUSTER_SCALING_OUT → CLUSTER_SCALING_OUT → WAITING_REBALANCING_OUT → REBALANCING_OUT → ACTIVE` | ~6.5 min |

Two things that sequence is good for: the `rebalance=true` flag is **visible**
as its own `WAITING_REBALANCING_OUT → REBALANCING_OUT` pair at the end, and
enabling public access gives **each broker** a floating IP (`floating_ips` went
from `[]` to three addresses and back) — it hands out addresses, it does not
open the firewall. A resize's order carries an **empty** `resourceId`, as the
relational resizes do, so polling the cluster is the only confirmation.

### Already checked — do not re-investigate

- Response `content-type` is `*/*`; `httpx`'s `resp.json()` ignores it.
- `409` carries a `message`; `BaseClient._raise_error` surfaces it as
  `Conflict: <message>`.
- The spec's `servers[0].url` is `https:/vdb-gateway.vngcloud.vn` with one
  slash. That is a typo in the document, not an endpoint.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | MCPServer entry point, handler registration, CLI flags, `SERVER_INSTRUCTIONS` |
| `config.py` | `VdbConfig` + the four service endpoints (one host, no region axis) |
| `client.py` | `VdbClient.call(method, path, service=..., user_type=...)` |
| `paging.py` | The four unwrap helpers plus `build_page_params` / `build_filter_params` |
| `discovery_cache.py` | Per-tool TTLs, and `UNCACHED_TOOLS` recording what is deliberately never cached |
| `catalog_handler.py` | 25 catalogue tools: 9 relational, 8 memory, 3 PostgreSQL Cluster, 5 Kafka |
| `relational_instance_handler.py` | 22 tools for relational instances (5 read, 5 dry-run, 12 write) |
| `relational_backup_handler.py` | 8 tools for relational backups (4 read, 1 dry-run, 3 write) |
| `relational_config_handler.py` | 6 tools for relational configuration groups (3 read, 3 write) |
| `relational_storage_handler.py` | 7 tools for relational backup storage (2 read, 2 dry-run, 3 write) |
| `memory_instance_handler.py` | 21 tools for MemoryStore instances (6 read, 4 dry-run, 11 write) |
| `memory_backup_handler.py` | 7 tools for MemoryStore backups (3 read, 1 dry-run, 3 write) |
| `memory_config_handler.py` | 6 tools for MemoryStore configuration groups (3 read, 3 write) |
| `memory_storage_handler.py` | 7 tools for MemoryStore backup storage (2 read, 2 dry-run, 3 write) |
| `postgresql_cluster_handler.py` | 17 tools for PostgreSQL Clusters (5 read, 2 dry-run, 6 write, 2 destructive); the reads are **derived** from the relational listing; Multi-AZ via `netIds` |
| `postgresql_backup_handler.py` | 6 tools for cluster backups, which are vBackup resources rather than vDB ones |
| `kafka_cluster_handler.py` | 14 tools for Kafka clusters, including the firewall rules (added and removed one at a time) |
| `kafka_topic_handler.py` | 5 tools for topics |
| `kafka_user_handler.py` | 7 tools for users, permissions and the credential archive |
| `kafka_config_handler.py` | 6 tools for versioned configuration groups |
| `prompts_handler.py` | 6 guidance topics as MCP prompts **and** as the `get_vdb_guide` tool |
| `statuses.py` | The four status vocabularies plus `classify()` / `describe()` |
| `models/` | `relational.py`, `memory.py`, `postgresql.py`, `kafka.py`, `requests.py`, `catalog.py`, `_common.py`; `__init__.py` re-exports every name |
| `guards.py` | `require_write` and `require_sensitive_access` — second line of defence behind `--allow-write` / `--allow-sensitive-data-access` |

## Guidance lives in prompts, not in docstrings

`prompts_handler.py` carries the six flows (`getting_started`,
`create_instance`, `restore_backup`, `configuration_group`,
`backups_and_storage`, `troubleshooting`). Two things about it are deliberate:

- **Each topic is served both as an MCP prompt and through the `get_vdb_guide`
  tool.** Prompts have to be loaded by the user and agents routinely run
  promptless; a tool is something an agent calls on its own. Same text, one
  source — a test asserts the two front doors cannot drift.
- **The guide bodies are Vietnamese; the code around them is English.** That
  matches vks: a guide is end-user-facing conversation guidance, while module,
  class and method docstrings are source code text and stay English per the
  monorepo convention. The guides tell the agent to answer in the user's own
  language, so the language of the file does not decide the language of the
  conversation. A test asserts every body still carries Vietnamese diacritics,
  so a revert to an English draft fails loudly.
- **The content is the payoff of the live testing**, so its tests assert on the
  *rules*, not on length: that the restore guide leads with "does not roll
  anything back", that the configuration guide says a zero restart-required
  count is not a promise, that the triage guide leads with HTTP 200 not meaning
  success. If a quirk in this file is ever proven wrong, fix the guide and the
  test together.

When adding a tool, keep its docstring to the discovery chain and the
guardrails, and put any longer conversational choreography here.

## Testing

```bash
cd src/vdb-mcp-server && uv run pytest tests/ -v
uv run ruff check . && uv run ruff format --check .
```

Tests use `respx` for async HTTP mocking — no real API calls, no credentials.
`tests/helpers.py` mirrors the live shapes: `envelope`, `nested_list` and
`content_list` build the three response shapes.

Two conventions worth knowing before writing a handler here:

- Tool methods declare parameters with `Field(...)` defaults, so a **direct**
  call from a test must pass every argument explicitly (`refresh=False`
  included). Omitting one passes a `FieldInfo` object, which is truthy — a
  forgotten `refresh` silently disables the cache instead of failing.
- Register tools with one literal `self.mcp.tool(name="...")` call each, never
  a loop over a list of names: the monorepo `Conventions` job reads those names
  statically out of the source, and a loop makes every tool in the file
  invisible to it.

### Live probe

`docs/superpowers/vdb_probe.py` exercises the real API **read-only** through
`VdbClient`, so it checks this package's plumbing at the same time. Output
lands in `docs/superpowers/vdb-probe-out/`, gitignored because it contains real
account data.
