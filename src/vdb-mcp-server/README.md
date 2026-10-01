# GreenNode vDB MCP Server

An MCP (Model Context Protocol) server for **vDB** on VNG Cloud — managed
databases: relational engines (MySQL, MariaDB, standalone PostgreSQL),
MemoryStore (Redis), PostgreSQL Clusters and Kafka.

> **All four families are implemented** — relational, MemoryStore,
> PostgreSQL Cluster and Kafka. The build plan, progress checklist and session
> log live in
> [`docs/superpowers/vdb-mcp-server-plan.md`](../../docs/superpowers/vdb-mcp-server-plan.md).

## Configuration

Credentials are read from `~/.greennode/credentials` and `~/.greennode/config`
(INI format, shared with greennode-cli; `GRN_*` env vars override — see the
repo-root CLAUDE.md).

**No region parameter.** Unlike vServer and vBackup, vDB serves the whole
account from one gateway (`https://vdb-gateway.vngcloud.vn`) and resolves the
project from the token, so no tool takes a `region` argument.

## Running

```bash
# Read-only mode (default)
uv run vdb-mcp-server

# Enable create/update/delete operations
uv run vdb-mcp-server --allow-write

# HTTP transport
uv run vdb-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

## Tools

Tools are prefixed by product family — `relational`, `memory`, `postgresql`,
`kafka` — and are **not** interchangeable: the same operation uses a different
path, and often a different HTTP method, in each family, and some exist in only
one.

### Catalogue — what the platform offers

Every tool is cached (TTL per tool) and takes `refresh: bool` to bypass the
cache.

| Tool | Access | Description |
|------|--------|-------------|
| `list_relational_datastores` | read | Relational engine/version pairs — **start here**; `type` and `version` feed the flavour and create tools |
| `list_relational_engines` | read | Relational engine families and licences (no versions) |
| `list_relational_instance_families` | read | Instance families; rows with `group == "family_custom"` describe a zone group, not a family |
| `list_relational_flavors` | read | Instance sizes for one engine + version (**both required**) and optionally one zone |
| `list_relational_flavor_codes` | read | Platform codes behind the families (`code-s`, `code-a`, …) |
| `list_relational_volume_types` | read | Storage types with the size range each accepts |
| `list_relational_zones` | read | Availability zones |
| `list_relational_networks` | read | Networks (VPCs) — coarser than subnets |
| `list_relational_subnets` | read | Subnets, flattened out of their networks; a create needs `subnet_id` |
| `list_memory_datastores` | read | MemoryStore (Redis) engine/version pairs — **start here** |
| `list_memory_engines` | read | MemoryStore engine families and licences |
| `list_memory_instance_families` | read | MemoryStore instance families |
| `list_memory_flavors` | read | MemoryStore instance sizes for one engine + version |
| `list_memory_flavor_codes` | read | MemoryStore platform codes |
| `list_memory_volume_types` | read | MemoryStore storage types |
| `list_memory_networks` | read | Networks available to MemoryStore instances |
| `list_memory_subnets` | read | Subnets available to MemoryStore instances |
| `list_postgresql_datastores` | read | PostgreSQL Cluster versions — a different set from the relational family's PostgreSQL |
| `list_postgresql_flavors` | read | Cluster node sizes for one zone; **no engine or version argument** — the family has one engine. `multi_zone=true` lists what suits a Multi-AZ cluster (answered for the Multi-AZ default zone, HCM03-1A); not combinable with `zone_id` |
| `list_postgresql_volume_types` | read | Cluster storage types for one zone; a create takes the `id`, not the name. Same `multi_zone` flag |
| `get_kafka_limits` | read | **Start here for Kafka** — the bounds, ports and name regexes the platform enforces |
| `list_kafka_flavors` | read | Broker sizes; `type`/`version` optional, no zone |
| `list_kafka_volume_types` | read | Broker storage types |
| `list_kafka_flavor_codes` | read | Kafka platform codes |
| `list_kafka_instance_families` | read | Kafka instance families |

The memory family has no zones endpoint; use `list_relational_zones` or the
`zone_id` on a flavour row. The PostgreSQL Cluster family has neither zones nor
subnets of its own and uses the relational ones — zones and subnets are
account-wide.

**The zones do not offer the same things.** Measured 2026-09-14: the cluster
flavour catalogue lists 28 sizes in HCM03-1A and HCM03-1B but only **15** in
HCM03-1C, and 5 storage types against 4. Ids differ per zone even where the
size is identical, so a row from one zone is never reusable in another.

### Two catalogue behaviours worth knowing

**`list_*_flavors` requires `datastore_type` and `version`.** The API answers
`400` without them, and answers an *unrecognised pair* with an **empty list**
rather than an error — so a wrong version looks like "this engine has no
sizes". Take both values from `list_*_datastores`; when the result is empty the
response carries a `note` saying so.

**Omitting `zone_id` means the default zone, not every zone.** Both flavour
and volume-type catalogues fall back to the platform default zone, so an
unzoned answer silently describes one zone. The response echoes the `zone_id`
it describes (`null` = default zone). Measured on MySQL 8.0: 84 flavours in
`HCM03-1A`, 47 in `HCM03-1B`, 34 in `HCM03-1C`.

### Usage example

```
"What can I run a PostgreSQL 15 database on in HCM03-1B?"

  list_relational_datastores          -> ('postgresql', '15') is offered
  list_relational_zones               -> HCM03-1A (default), HCM03-1B, HCM03-1C
  list_relational_flavors(
      datastore_type='postgresql',
      version='15',
      zone_id='HCM03-1B')             -> the sizes that exist in that zone
  list_relational_volume_types(
      zone_id='HCM03-1B')             -> storage types and their size ranges
  list_relational_subnets             -> the subnet_id to place it in
```

### Instance tools (relational family)

Write tools are registered only with `--allow-write`; the `*_dryrun` previews
are always available and always read-only.

| Tool | Access | Description |
|------|--------|-------------|
| `list_relational_instances` | read | List instances. Filters `pg-` rows out of a **mixed listing** and reports how many it dropped |
| `get_relational_instance` | read | One instance by ID |
| `list_relational_instance_histories` | read | Operation history — where an asynchronous action reports what happened |
| `list_relational_instance_replicas` | read | Read replicas of an instance |
| `list_relational_instance_secrules` | read | Security-group rules guarding an instance |
| `create_relational_instance_dryrun` | read | Preview the order a create would place |
| `resize_relational_instance_dryrun` | read | Preview a flavour change |
| `resize_relational_instance_storage_dryrun` | read | Preview a storage change |
| `create_relational_instance_replicas_dryrun` | read | Preview a replica order |
| `delete_relational_instance_dryrun` | read | Preview a delete, including the backup options |
| `create_relational_instance` | **write** | Order a new instance. Billable, asynchronous |
| `start_relational_instance` | **write** | Start a stopped instance |
| `stop_relational_instance` | **write** | Stop a running instance |
| `reboot_relational_instance` | **write** | Reboot an instance |
| `resize_relational_instance` | **write** | Change flavour. Billable, restarts the instance |
| `resize_relational_instance_storage` | **write** | Grow storage. Billable, cannot be shrunk back |
| `create_relational_instance_replicas` | **write** | Order a read replica. Billable |
| `detach_relational_instance_replica` | **write** | Promote a replica to standalone |
| `update_relational_instance_setting` | **write** | Password, public access, automatic backup |
| `update_relational_instance_config_group` | **write** | Attach a configuration group, or `""` to detach |
| `update_relational_instance_secrules` | **write** | Replace the whole security rule set |
| `delete_relational_instance` | **destructive** | Delete an instance. Irreversible |

### Instance tools (memory family — Redis)

The same shape as the relational table above, minus the storage operations: a
Redis flavour bundles its disk, so `resize_memory_instance` is the only sizing
tool and there is no `resize_memory_instance_storage`.

| Tool | Access | Description |
|------|--------|-------------|
| `list_memory_instances` | read | List Redis instances. One family, and the filters are **exact** — unlike the relational listing |
| `get_memory_instance` | read | One instance by ID. **Family-strict**: answers "not found" for anything that is not Redis |
| `list_memory_instance_histories` | read | Operation history — where an asynchronous action reports what actually happened |
| `list_memory_instance_replicas` | read | Read replicas of an instance |
| `list_memory_instance_secrules` | read | Security-group rules guarding an instance |
| `list_memory_instance_backups` | read | Every backup of one instance, unpaginated. Rows are summaries |
| `create_memory_instance_dryrun` | read | Preview the order a create would place |
| `resize_memory_instance_dryrun` | read | Preview a flavour change |
| `create_memory_instance_replicas_dryrun` | read | Preview a replica order |
| `delete_memory_instance_dryrun` | read | Preview a delete, including the backup options |
| `create_memory_instance` | **write** | Order a new Redis instance. Billable, asynchronous |
| `start_memory_instance` | **write** | Start a stopped instance |
| `stop_memory_instance` | **write** | Stop a running instance |
| `reboot_memory_instance` | **write** | Reboot an instance |
| `resize_memory_instance` | **write** | Change flavour. Billable, restarts the instance |
| `create_memory_instance_replicas` | **write** | Order a read replica. Billable |
| `detach_memory_instance_replica` | **write** | Promote a replica to standalone |
| `update_memory_instance_setting` | **write** | Master password, public access, automatic backup |
| `update_memory_instance_config_group` | **write** | Attach a configuration group, or `""` to detach |
| `update_memory_instance_secrules` | **write** | Replace the whole security rule set |
| `delete_memory_instance` | **destructive** | Delete an instance. Irreversible |

Four things this family does differently:

**An instance ID does not identify its family.** Redis instances are prefixed
`db-` exactly like relational ones. Only `pg-` (PostgreSQL Cluster) is
distinctive. Worse, `get_relational_instance` will happily return a Redis
instance or a `pg-` cluster, while `get_memory_instance` rejects anything that
is not Redis — so the memory tool is the one that actually proves a family.
Check `datastore_type` on whatever comes back.

**There is nothing to size but the flavour.** The create body has no
`volumeType`, `volumeSize`, `databases` or `user` — and if you send them anyway
the API answers `200` and silently ignores them, so `extra="forbid"` on the DTO
is what turns a copied relational payload into a named error instead of an
instance sized differently from what was asked for.

**The password rule is 16-128 characters**, four times the relational minimum,
so a password that works for MySQL is usually too short here. It must also be
enabled whenever `publicAccess` is — both rules are checked locally, because
the API reports either as a bare `in_valid`.

**`update_memory_instance_setting` needs `editRedisPassword`.** Changing
`redisPassword` or `redisPasswordEnabled` without that flag is accepted and
then leaves the password untouched, reporting nothing. The DTO rejects the
combination rather than guessing.

### Backup tools (relational family)

| Tool | Access | Description |
|------|--------|-------------|
| `list_relational_backups` | read | Every backup in the project. Paginated, **no filters**, and the rows are summaries |
| `get_relational_backup` | read | One backup by ID — the only call that returns sizing, flavour and network |
| `list_relational_instance_backups` | read | Every backup of one instance, unpaginated |
| `get_relational_free_backup_usage` | read | Free backup allowance and how much of it is used |
| `restore_relational_backup_dryrun` | read | Preview the order a restore would place |
| `create_relational_backup` | **write** | Take a backup. `FULL`, or `INCREMENTAL` with a `parentId` |
| `restore_relational_backup` | **write** | Build a **new** instance from a backup. Billable, asynchronous |
| `delete_relational_backup` | **destructive** | Delete a backup. Irreversible |

Three things about backups differ from everything else in this server:

**A restore is a create, not a rollback.** It orders a **second** instance
built from the backup and leaves both the backup and its source instance
untouched. It is priced like a create, so it needs the same discovery chain —
flavour, volume type, subnet and zone, all from the same zone — and it has a
`*_dryrun` companion for that reason.

**`list_relational_backups` returns summaries.** Verified live by reading one
backup through both endpoints: the listing returns `null` for storage size,
storage type, flavour, network, config group, username and retention, and
reports the engine as `mysql` where the detail says `MySQL`. The list result
sets `rows_are_summaries` to say so. Plan a restore from
`get_relational_backup`, never from a list row.

**A backup can be accepted and still never exist.** `create_relational_backup`
answers `200` with `success: true` and a real id, then builds the backup
asynchronously — and two things make it fail silently after that, both verified
live:

- **`description` must be non-empty.** The spec marks it optional; sending no
  field, or `""`, fails the backup. The Portal generates the text itself
  ("Manual created"), so the DTO defaults to that and rejects an empty string.
- **Only one backup at a time per instance.** A second request while one is
  running fails with `current database action is CREATE_BACKUP`.

In both cases the backup vanishes from every listing and
`get_relational_backup` reports it missing. `list_relational_instance_histories`
is the only place the reason is recorded — check it before concluding the
platform is at fault.

**A backup's `network_ids` cannot be passed to a restore.** Upstream both are
called `netIds`, but a backup reports **network** ids (`net-…`) while a restore
wants a **subnet** id (`sub-…`) from `list_relational_subnets`. The field is
renamed here so the two are not confused.

The restore envelope also breaks the pattern the lifecycle actions follow: its
action is `restore_backup` and the key is `resourceType` holding
`dbaas-backup`, not `resType` holding `dbaas`.

### Backup tools (memory family — Redis)

| Tool | Access | Description |
|------|--------|-------------|
| `list_memory_backups` | read | Every backup in the project. Paginated, **no filters**, and the rows are summaries |
| `get_memory_backup` | read | One backup by ID, with the fields the listing leaves null |
| `get_memory_free_backup_usage` | read | Free backup allowance and how much is used |
| `restore_memory_backup_dryrun` | read | Preview the order a restore would place |
| `create_memory_backup` | **write** | Take a backup. Synchronous id, asynchronous data |
| `restore_memory_backup` | **write** | Restore into a **new** instance. Billable |
| `delete_memory_backups` | **destructive** | Delete one or more backups. Irreversible |

Note `list_memory_instance_backups` lives with the instance tools above — it
hangs off `/database-instances/{id}/backups` rather than `/backups`.

Three things to know here:

**`delete_memory_backups` is plural for a reason.** The endpoint is a `POST`
with no id in the path, so its array body is the whole instruction: every id
passed is deleted, in one irreversible action. The relational endpoint repeats
a single id in its path and can only ever remove one.

**Neither `get_*_backup` endpoint is family-scoped.** `get_memory_backup`
returns a MySQL backup and `get_relational_backup` returns a Redis backup, both
without complaint — so a result proves nothing about the family. Read
`datastore_type` before choosing which restore tool to use; the two restore
bodies differ (the memory one takes the Redis password pair, the relational one
takes volume fields).

**The free allowance moves.** It is the sum of what the project's instances
grant, not a fixed quota: restoring one instance on a flavour granting 5 GB
took the memory allowance from 100 GB to 105, and deleting it took it back. So
deleting an instance can push previously-free backups over the line.

### Configuration tools (relational family)

A configuration group is a named set of engine parameter overrides that
instances attach to.

| Tool | Access | Description |
|------|--------|-------------|
| `list_relational_configurations` | read | All groups, each with the instances attached to it |
| `get_relational_configuration` | read | One group by ID (the id goes in a **query** parameter) |
| `list_relational_configuration_params` | read | What an engine version lets you set, with bounds and `restart_required` |
| `create_relational_configuration` | **write** | Create an empty group, bound to one engine + version for life |
| `update_relational_configuration` | **write** | Set values. **Affects every attached instance** |
| `delete_relational_configurations` | **destructive** | Delete groups. Refused while one is in use |

**Changing a group can leave attached instances needing a reboot.** For
MySQL 8.0, 13 of 67 parameters are marked `restart_required` — changing one of
those puts every attached instance into `RESTART_REQUIRED`, and the instance
**keeps serving the old value** until someone reboots it. So the change can
appear to have been applied while having no effect at all.

`update_relational_configuration` therefore returns the parameters it changed,
which of them force a restart, and the instances affected. Check each one with
`get_relational_instance` afterwards, and leave the reboot decision to the user
— `reboot_relational_instance` drops every connection.

`list_relational_configuration_params` takes `restart_required=true` to show
exactly which parameters carry that cost, and a `name` substring filter because
MySQL 8.0 alone exposes 67 parameters (one with ~500 allowed values).

**A numeric parameter's `allowed_values` is empty on purpose.** Upstream, a
numeric parameter echoes its two range endpoints into the same field a string
parameter uses for its enum — so `innodb_buffer_pool_size` arrives as
`["5242880", "2147483647"]`, which read as an enum would claim those are the
only two legal sizes. Numeric parameters expose `minimum`/`maximum` instead.

#### These are also the PostgreSQL **Cluster** configuration tools

There is no separate set. A cluster's group is created, listed, read, updated
and deleted with the tools above — only `update_postgresql_cluster_config_group`
(attach/detach) lives in the cluster family. Three things differ:

- **`deployType` must be `cluster` at creation**, and it is fixed for life. Such
  a group's id comes back prefixed **`pg-cfg-`** rather than `cfg-`, and
  `list_relational_configurations` returns it alongside the single-node ones
  with `deploy_type: "cluster"` — that field is how you tell them apart.
- **`list_relational_configuration_params` needs the same `deploy_type`.**
  Omitting it means `single_node`, *not* "any", so a cluster question answered
  without it comes back empty. Measured 2026-09-14:

  | PostgreSQL | omitted / `single_node` | `cluster` |
  |---|---|---|
  | 17, 16 | **0** | 25 |
  | 15 | 41 | 25 |
  | 14, 13, 12 | 40, 40, 73 | **0** |

  So 17 and 16 exist only as clusters, 14 and below only as single nodes, and 15
  is the one version in both — with a **different** set of parameters in each.
  An empty result sets `unknown_engine` **and** `empty_result_hint`, which names
  `deploy_type` as the thing to change; it never means "this engine has no
  settable parameters".
- **`datastore_type` stays `PostgreSQL`.** There is no cluster engine name.

A cluster group attaches and detaches through
`update_postgresql_cluster_config_group`, and — measured — that left the cluster
`ACTIVE` rather than `RESTART_REQUIRED`. Do not generalise from the Redis case;
read the cluster's own status afterwards.

### Configuration tools (memory family — Redis)

| Tool | Access | Description |
|------|--------|-------------|
| `list_memory_configurations` | read | Configuration groups, each with the instances attached to it |
| `get_memory_configuration` | read | One group by ID — a plain path lookup here |
| `list_memory_configuration_params` | read | The Redis parameters a group can set, with bounds and `restart_required` |
| `create_memory_configuration` | **write** | Create an empty group. Free and immediate |
| `update_memory_configuration` | **write** | Set parameter values. **Affects every attached instance** |
| `delete_memory_configurations` | **destructive** | Delete one or more groups |

Four things measured on this family:

**`deployType` is required and is not in the endpoint's schema.** A body with
only the four declared fields is rejected as `400 bad_request`; the tool sends
`deployType: single_node` for you. `datastoreType` is also case-sensitive here
(`"redis"` is rejected), so let the DTO normalise it.

**A brand-new group cannot be deleted for the first several seconds** — the
call answers "Resource not found" while the group is plainly listed and
readable. That is a timing window, not a wrong id.

**Parameters differ sharply by Redis version**: 4.0 exposes 24 (one
restart-required), 6.2 and 7.2 expose 8 and none. And `maximum` is usually
absent, so don't report an upper bound that isn't there.

**Detaching a group leaves the instance in `RESTART_REQUIRED`**, even with zero
restart-required parameters. A count of zero is not a promise that no reboot is
needed — the instance status is what decides.

### Backup-storage tools (relational family)

Backup storage is the quota backups are written into. A project starts with a
free allowance and buys quota beyond it.

| Tool | Access | Description |
|------|--------|-------------|
| `list_relational_backup_storage_packages` | read | The price list of quota packages |
| `get_relational_backup_storage` | read | What the project holds, and how full it is |
| `create_relational_backup_storage_dryrun` | read | Preview a purchase |
| `resize_relational_backup_storage_dryrun` | read | Preview a quota change |
| `create_relational_backup_storage` | **write** | Buy quota. Billable and **recurring** |
| `resize_relational_backup_storage` | **write** | Move to another package. Billable |
| `delete_relational_backup_storage` | **destructive** | Release quota |

**Trust the paths, not the operation names.** The spec calls
`GET /backup-storages` `getListQuotaPackage` and
`GET /backup-storages/information` `getListBackupStorage` — which read as
though they were swapped. The first returns the price list, the second returns
what is held.

**Resize and delete disagree about one key.** Resize sends `resourceType`,
delete sends `resType`, and both carry `dbaas-backup-storage` — a third
resource-type value alongside `dbaas` (lifecycle, instance resize) and
`dbaas-backup` (restore). None of the three follows from the path, and a wrong
one returns `200` having done nothing. Both envelopes also address backup
storage under a field called `databaseInstances`.

**Buying is quick, and the quota carries no name of your own.** Verified live:
create returns a populated `resourceId`, and both the purchase and a resize
take effect within seconds rather than passing through a transitional status.
Every purchase is labelled `relational_backup_storage`, and
`backupPackageName` comes back empty — match `package_id` against the price
list to name it.

**The price list is repeated per engine group.** Measured live, engine groups
1 and 2 return byte-identical package lists (ids 1–8, 100 GB to 10 TB) and
group 3 returns none — so a package id is unambiguous. The tool returns a
deduplicated `packages` list to choose from, keeps the raw `groups`, and sets
`groups_are_identical` in case that ever stops being true.

### Backup-storage tools (memory family — Redis)

| Tool | Access | Description |
|------|--------|-------------|
| `list_memory_backup_storage_packages` | read | The quota packages available to buy (price list) |
| `get_memory_backup_storage` | read | The paid quota this project holds, and how full it is |
| `create_memory_backup_storage_dryrun` | read | Preview the order a purchase would place |
| `resize_memory_backup_storage_dryrun` | read | Preview a quota change |
| `create_memory_backup_storage` | **write** | Buy quota. **Recurring** monthly charge |
| `resize_memory_backup_storage` | **write** | Change to a different package. Billable |
| `delete_memory_backup_storage` | **destructive** | Release quota. Cannot be undone |

**The two `GET` paths mean the opposite of what they mean in the relational
family.** `GET /backup-storages` returns what the project holds here and the
price list there; the memory family has no `/information` path. The tools sort
that out, but never assume a backup-storage path carries across families.

**Paid quota does not add to the free allowance.** Holding 200 GB of paid quota
left `get_memory_free_backup_usage` still reporting its own 100 GB — two
separate pools, so don't sum them when answering "how much room is left".

### Cluster tools (PostgreSQL Cluster family)

| Tool | Access | Description |
|------|--------|-------------|
| `list_postgresql_clusters` | read | The project's clusters. **Derived** — see below |
| `get_postgresql_cluster` | read | One cluster in full, with per-zone placement (`zones`) for a Multi-AZ cluster; rejects a non-`pg-` id locally |
| `get_postgresql_cluster_volume_used` | read | Disk actually used. The **only** source — the detail reports `volume_used` as null |
| `list_postgresql_cluster_secrules` | read | Security rules guarding the cluster |
| `list_postgresql_cluster_histories` | read | What has been done, and what the platform made of it |
| `create_postgresql_cluster_dryrun` | read | Preview the order a create would place |
| `resize_postgresql_cluster_dryrun` | read | Preview a resize order |
| `create_postgresql_cluster` | **write** | Order a cluster, in one zone or spread across several (Multi-AZ). Billable, **per node** |
| `resize_postgresql_cluster` | **destructive** | Resize one axis. Billable; shrinking nodes destroys them |
| `update_postgresql_cluster_setting` | **write** | Master password and/or public access |
| `update_postgresql_cluster_config_group` | **write** | Attach or detach a `cluster` configuration group (`pg-cfg-…`); `""` detaches |
| `update_postgresql_cluster_secrules` | **write** | Replace the whole rule set |
| `reboot_postgresql_cluster` | **write** | Reboot. The family's only lifecycle action |
| `delete_postgresql_cluster` | **destructive** | Delete the cluster and every node |

**This family has no listing and no get-by-id endpoint.** `GET /v1/cluster`
answers `400` and `GET /v1/cluster/{id}` answers `403`, so both reads are
**derived** from the relational instance listing, which returns clusters mixed
in with relational instances and is filtered here by the `pg-` prefix.
Consequences the results carry explicitly:

- `filters_are_approximate` — the API ignores `name`/`status` for cluster rows
  entirely (a name matching nothing still returns every cluster), so the
  filtering happens locally, across the pages scanned.
- `pages_scanned` / `more_pages_exist` — relational rows share the paging, so a
  project with many instances can push clusters onto later pages. `max_pages`
  raises the scan limit.

**Five operations have no cluster endpoint at all** and are served by the
*relational* ones, which is what the GreenNode Portal itself calls: security
rules (read and write), history, reboot and delete. Not everything carries
across, though — `/replicas/{id}` answers `403` for a cluster and
`/backups/insId/{id}` answers an empty array, because cluster backups live in
vBackup. The wrapper is per-operation, measured, not a blanket rule.

**Configuration groups for a cluster are relational-family tools.** Create,
list, read, update and delete them with `create_relational_configuration` (with
`deployType: "cluster"`), `list_relational_configurations` and friends — only
the attach/detach lives here. See *Configuration tools (relational family)*
above, and note that `list_relational_configuration_params` needs
`deploy_type="cluster"` or it answers empty for PostgreSQL 17 and 16.

**Multi-AZ is chosen by `netIds` alone.** One subnet puts every node in one
zone; several subnets — one per zone, e.g. a HCM03-1A and a HCM03-1B subnet of
the same network (`list_relational_subnets` reports each subnet's `zone_id`) —
spread the nodes evenly across those zones. There is no separate flag on the
create. For a Multi-AZ order:

- take `packageId`, `volumeTypeId` and `locateZoneId` from
  `list_postgresql_flavors` / `list_postgresql_volume_types` called with
  `multi_zone=true` — they answer for the Multi-AZ default zone, currently
  HCM03-1A. `zone_id` cannot be combined with it (the API would replace the
  zone), so the tool refuses the pair;
- each zone builds its nodes from **its own copy** of that flavour and volume
  type, so both must be listed in every chosen zone. **HCM03-1C has no NVMe
  volume type** (as of 2026-10-01) and therefore cannot be part of a Multi-AZ
  cluster.

The DTO rejects a repeated subnet and more subnets than nodes; the dry-run adds
a Multi-AZ warning, since it cannot see which zone a subnet is in. Once
created, `get_postgresql_cluster` reports `multi_zone: true` and a `zones`
list — each zone's subnet, status, addresses and ports — while the top-level
`zone_id` / `subnet_id` name only the order's own zone.

**A cluster has two ports.** `port` (5432) reaches the primary and `port_ro`
(15432) the replicas; on a single-subnet cluster both resolve to the same
address, so the port is what routes a connection. A new cluster comes up with a
**single** ingress rule — `0.0.0.0/0` on the read/write port only.

### Backup tools (PostgreSQL Cluster family)

Cluster backups are **vBackup** resources, with a vocabulary of their own:

| Tool | Access | Description |
|------|--------|-------------|
| `list_postgresql_backups` | read | Every backup-**configuration** in the project (`bk-db-…`), not copies of data |
| `get_postgresql_cluster_backup` | read | One cluster's backup configuration. Keyed by **cluster** id despite the `/detail` path |
| `list_postgresql_restore_points` | read | The actual copies (`bk-db-pt-…`); a create's `backupPointId` |
| `list_postgresql_backup_locations` | read | vBackup destinations (`bk-des-…`) |
| `list_postgresql_backup_policies` | read | vBackup schedules (`bk-pol-…`) |
| `create_postgresql_cluster_backup` | **write** | Take a backup now, outside the schedule |

**A backup location holds exactly one cluster.** Passing a `backupLocationId`
that already has one is rejected with
`Please select a backup location that does not have any backup instances` —
so on an account where every location is in use, the create must omit the
backup fields entirely. Doing that is not "no backups": the platform assigns
the default policy and creates a fresh location itself, verified on a cluster
created with neither field.

There is no delete, no restore and no "create backup configuration" endpoint
here. Restoring is a `create_postgresql_cluster` carrying a `backupPointId`,
which builds a **second** cluster and is priced as a new one.

### Kafka tools

Kafka disagrees with the rest of vDB about nearly every convention, so nothing
here transfers from another family:

- **Paths carry no `/v1`.**
- **27 of 36 operations carry no envelope** — a bare object, array or string.
- **Several writes take query parameters, not a body**: authentication, public
  access, the config-group attach, and all three resizes.
- **Delete is a real `DELETE`**, with no `{action, resType}` envelope anywhere.
- **There is no zone.** Placement is the network and subnet alone.

| Tool | Access | Description |
|------|--------|-------------|
| `list_kafka_clusters` | read | The project's clusters. Unpaginated, unfiltered, no security rules |
| `get_kafka_cluster` | read | One cluster — the **only** source of its security rules |
| `list_kafka_cluster_histories` | read | What was done and how it turned out; the only record of a failure |
| `create_kafka_cluster_dryrun` | read | Preview the order a create would place |
| `create_kafka_cluster` | **write** | Order a cluster. Billable, **per broker**, minimum 3 |
| `resize_kafka_cluster_brokers` | **destructive** | Change broker count; shrinking destroys brokers |
| `resize_kafka_cluster_storage` | **write** | Change storage per broker. Billable across every broker |
| `update_kafka_cluster_storage_type` | **write** | Move to a different storage type. Billable |
| `update_kafka_cluster_authentication` | **write** | mTLS / SASL on or off — **both flags sent every time** |
| `update_kafka_cluster_public_access` | **write** | Floating IPs on or off; does **not** open the firewall |
| `update_kafka_cluster_config_group` | **write** | Apply a config group **version**; restarts brokers |
| `create_kafka_cluster_secrule` | **write** | Add one firewall rule |
| `delete_kafka_cluster_secrule` | **destructive** | Remove one firewall rule |
| `delete_kafka_cluster` | **destructive** | Delete the cluster, its topics, users and credentials |
| `list_kafka_topics` / `get_kafka_topic` | read | Topics on a cluster |
| `create_kafka_topic` | **write** | Create a topic. Free, but asynchronous |
| `update_kafka_topic` | **write** | **Replaces** the topic: `partitions` and `replicas` are required even when unchanged |
| `delete_kafka_topic` | **destructive** | Delete a topic and every message in it |
| `list_kafka_users` / `get_kafka_user` | read | Users and their per-topic permissions |
| `get_kafka_user_credentials` | read | Download the credential archive. **Needs `--allow-sensitive-data-access`** |
| `create_kafka_user` | **write** | Create a user with permissions. Free, but asynchronous |
| `update_kafka_user` | **write** | **Replace** the whole permission set |
| `update_kafka_user_credentials` | **destructive** | Re-issue credentials; the old ones stop working at once |
| `delete_kafka_user` | **destructive** | Delete a user and its credentials |
| `list_kafka_config_groups` | read | Configuration groups with their versions |
| `get_kafka_config_group` | read | One group; adds the attached clusters but **not** the settings |
| `get_kafka_config_group_version` | read | The settings a version carries — the **only** source of them |
| `create_kafka_config_group` | **write** | Create a group and its version 1. Free, touches no cluster |
| `create_kafka_config_group_version` | **write** | Add a version. A complete set, not a patch |
| `delete_kafka_config_group` | **destructive** | Delete a group and all its versions |

**`get_kafka_limits` is unique in vDB: the platform publishes its own rules.**
Broker count 3-10, storage 20-5000 GB per broker, at most 50 topics and 50
users per cluster, and the **only** ports a firewall rule may open —
`9092, 9094, 9096, 9194, 9196`. It also carries the name regexes, which differ
per resource: a configuration group name may contain spaces, a topic name may
contain dots, a cluster name may contain neither. Every value arrives as a
string and six are JSON encoded inside that string; the tool decodes both.

One bound is **not** published there: a topic's replication factor may not
exceed the cluster's broker count. Read `broker_count` from
`get_kafka_cluster`.

**A `create` needs `tags`, which the schema does not mention at all.** Kafka
clusters were designed to carry user tags; the feature was dropped but the
backend still calls `getTags().size()` while validating, so a body without the
field is a null dereference and the gateway answers a bare `500` with no
message. The DTO always sends `tags: {}`, as the Portal does.

**A user's permission is a list *or* a flag, never both.** Four kinds —
produce, consume, produce+consume, admin — each a list of topic names or an
`*_all` flag. When the flag is set the platform **ignores the list**, so the
DTOs refuse to send both rather than let a stored permission end up wider than
the list beside it suggests.

**Configuration groups are versioned and immutable.** A cluster attaches to a
`cgroupver-…`, never to the `cgroup-…`. Creating a group or a version touches
no running cluster; only `update_kafka_cluster_config_group` does, and that
restarts brokers one by one. Measured quirks worth knowing: the create answers
with `versions: []` even though version 1 exists (so the tool re-reads the
group), versions come back **newest first**, and only
`get_kafka_config_group_version` returns a version's settings — the group
detail leaves them empty.

**Topics and users are created asynchronously, and only one operation runs
per cluster at a time.** A new topic reports `WAITING_CREATING` and a new user
`CREATING`, and both settle to `ACTIVE` within about a minute. Those come from
**separate status vocabularies**: Kafka has three — the cluster's 48, the
topic's 11 and the user's 9 — and a user has no `WAITING_*` state at all, plus
two of its own for credential rotation. While one is in flight, Kafka
refuses the next write with a plain `400` (`Topic … is not active`, or no
explanation at all), so a rejection straight after another write is a **timing**
problem rather than a bad payload.

**Every Kafka write answers with an empty body.** No id, no word — deleting a
user answers `204 No Content`. Confirm a change by polling the resource, and
read `list_kafka_cluster_histories` when nothing seems to have happened.

**What each write costs in time, measured on a three-broker cluster:** create
~9 min, brokers 3→4 with rebalance ~6.5 min, enable SASL ~2.5 min, storage
resize ~2 min, public access ~1.5 min, config-group attach ~1 min. Kafka names
each phase rather than reporting a single `BUILDING`, so the status says how far
along it is — and `rebalance=true` is visible as its own
`WAITING_REBALANCING_OUT → REBALANCING_OUT` pair.

**Credentials are a ZIP of private keys, not a list of strings.** Despite what
the OpenAPI document says, `authen-creds` answers with
`application/octet-stream`: an archive of mTLS keystores, certificates and
their passwords. `get_kafka_user_credentials` therefore writes it to a path you
give and reports only its size and the names inside, and it is registered only
when the server runs with `--allow-sensitive-data-access`.

### Reading a status

Every instance response carries `status_kind` and `status_guidance` next to the
raw `status`, because the raw value alone does not say what to do with it:

| `status_kind` | Examples | What it means |
|---|---|---|
| `settled` | `ACTIVE`, `SHUTDOWN`, `DELETED` | Nothing in flight |
| `transitional` | `BUILDING`, `REBOOT`, `stopping`, `resizing` | Poll again before reporting |
| `failed` | `ERROR`, `INTERNAL_ERROR`, `FAILED`, `BILLING_ERROR` | A **platform** fault — report it, don't retry |
| `attention` | `RESTART_REQUIRED`, `BLOCKED`, `EXPIRED`, `WAIT_BILLING` | Stable, but needs a user decision |

Backups use the same four kinds over their own six statuses: `COMPLETED` is
settled, `NEW`/`BUILDING`/`SAVING` are transitional, `ERROR`/`FAILED` are
platform faults. Do not restore from a backup that has not settled.

Casing is not the signal: `BUILDING` and `REBOOT` are uppercase and in
progress, `SHUTDOWN` is uppercase and settled. A status the platform adds later
reports as `unknown`, which means "cannot tell" rather than "failed".

### Five things to know before creating anything

**Billable operations use Auto Payment by default.** `user_type` defaults to
`IAM_USER`, which settles the order and provisions the resource, returning its
`resourceId`. The alternative, `ROOT_USER`, also answers `200` — but it creates
**nothing** until someone completes the order in the payment console, so the
resource never appears. Only pass it when a reviewable order is what's wanted.

**Every validation failure is reported as `in_valid`, with no field name.** So
the tools validate locally instead: DTOs set `extra="forbid"`, and the password
rule (letters, digits and `$ ^ _ < >` only, start with a letter, end
alphanumeric, **8-32** for relational and **16-128** for MemoryStore) is
checked before the call. When `in_valid` does come back, the cause is almost
always a zone mismatch — `packageId`, `volumeType` and `locateZoneId` must all
come from the same zone.

**A wrong `action` or `resType` is accepted silently.** Lifecycle endpoints
take `{databaseInstances, action, resType}` and the values are not the path
segment: `/shutdown` wants `action: "stop"`, `/detach-replica` wants
`"detach_replica"`, and `resType` is always the literal `"dbaas"`. Send
anything else and the API answers `200` with an empty result, applies nothing
and reports no error — `data: []` in the relational family and `data: null` in
the memory one, for the same mistake. The tools use the documented values, and
when a response does come back empty they set `accepted: false` with a
`warning` rather than implying success.

**HTTP 200 is not the end of the story, and history is the only record.** An
edit can be accepted with `200` and then fail asynchronously — most commonly
because the instance was still busy with the previous one ("Cannot perform
action EDIT"). Nothing in any listing or detail records that; only
`list_*_instance_histories` does, and its `description` names what the platform
understood the request to be. After any write that looks like it did nothing,
read the history before concluding anything.

**The relational listing is shared with PostgreSQL Cluster.** It is the only
endpoint that can enumerate those, so `list_relational_instances` filters them
out and sets `postgresql_clusters_excluded`. The API does not apply the
`name`/`status` filters to those rows, so a filtered result also sets
`filters_are_approximate` — never present the count as exact. The PostgreSQL
Cluster tools work the other way round from the same endpoint; see below.

### Guidance prompts

| Prompt / topic | What it covers |
|---|---|
| `getting_started` | The four families, why an instance id does not identify one, tool routing |
| `create_instance` | Question order, the same-zone rule, per-family password rules, confirm gate |
| `restore_backup` | Why a restore builds a **second** instance rather than rolling anything back |
| `configuration_group` | What a change does to attached instances, and `RESTART_REQUIRED` |
| `backups_and_storage` | Taking and deleting backups; the free allowance that is not a constant |
| `troubleshooting` | **Why HTTP 200 is not success**, and where async failures are recorded |

Each is an MCP prompt (`vdb_getting_started`, `vdb_create_instance`, …) and is
also returned by the read-only `get_vdb_guide` tool, so an agent that never
loads prompts can still fetch the flow it needs.

The guide bodies are written in **Vietnamese**, matching `vks-mcp-server`: they
are conversation guidance aimed at the end user, not source code text. They
instruct the agent to reply in whatever language the user writes in, so an
English-speaking user still gets English answers.

## Development

```bash
cd src/vdb-mcp-server
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format --check .
```

Tests use `respx` — no real API calls and no credentials required.

Manual testing with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run vdb-mcp-server
```

Against the real API, `docs/superpowers/vdb_probe.py` performs a **read-only**
probe of every family through `VdbClient`:

```bash
uv run python docs/superpowers/vdb_probe.py
```

See [`CLAUDE.md`](CLAUDE.md) for the API quirks this package has to work
around — response envelopes, the mixed relational listing, the order flow, and
the catalogue traps above.
