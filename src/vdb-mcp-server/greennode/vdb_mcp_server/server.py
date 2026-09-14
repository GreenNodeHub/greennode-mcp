"""MCPServer entry point for the vDB MCP server."""

from __future__ import annotations

import argparse
from greennode.mcp_core.auth import TokenManager
from greennode.mcp_core.config import resolve_config_dir
from greennode.vdb_mcp_server.catalog_handler import CatalogHandler
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import load_config
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.kafka_cluster_handler import KafkaClusterHandler
from greennode.vdb_mcp_server.kafka_config_handler import KafkaConfigHandler
from greennode.vdb_mcp_server.kafka_topic_handler import KafkaTopicHandler
from greennode.vdb_mcp_server.kafka_user_handler import KafkaUserHandler
from greennode.vdb_mcp_server.memory_backup_handler import MemoryBackupHandler
from greennode.vdb_mcp_server.memory_config_handler import MemoryConfigHandler
from greennode.vdb_mcp_server.memory_instance_handler import MemoryInstanceHandler
from greennode.vdb_mcp_server.memory_storage_handler import MemoryStorageHandler
from greennode.vdb_mcp_server.postgresql_backup_handler import PostgresqlBackupHandler
from greennode.vdb_mcp_server.postgresql_cluster_handler import PostgresqlClusterHandler
from greennode.vdb_mcp_server.prompts_handler import PromptsHandler
from greennode.vdb_mcp_server.relational_backup_handler import RelationalBackupHandler
from greennode.vdb_mcp_server.relational_config_handler import RelationalConfigHandler
from greennode.vdb_mcp_server.relational_instance_handler import RelationalInstanceHandler
from greennode.vdb_mcp_server.relational_storage_handler import RelationalStorageHandler
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


# Prefer ~/.greennode; fall back to the legacy ~/.greenode when only it exists.
CONFIG_PATH = resolve_config_dir()

SERVER_INSTRUCTIONS = """
# GreenNode vDB MCP Server

MCP Server for GreenNode vDB — managed databases: relational engines (MySQL,
MariaDB, standalone PostgreSQL), MemoryStore (Redis), PostgreSQL Clusters and
Kafka.

## IMPORTANT: Operating mode

By default the server runs in **read-only** mode. Use the `--allow-write` flag
to enable write operations (create, update, restore, delete).

## Start with the guide

`get_vdb_guide` returns the flow for a task: the question order, the platform
rules that the OpenAPI document gets wrong, and the confirm-gate protocol. It
is read-only and calls no API. Fetch the topic **before** asking the user
anything:

- `getting_started`, `create_instance`, `restore_backup`,
  `configuration_group`, `backups_and_storage`, `troubleshooting`.

Read `troubleshooting` whenever a call looks like it did nothing — HTTP 200 is
not success in this API. The same text is available as MCP prompts
(`vdb_getting_started`, `vdb_create_instance`, …) for users who load prompts.

## Four product families, four separate tool sets

vDB is four APIs behind one gateway. Tools are prefixed by family —
`*_relational_*`, `*_memory_*`, `*_postgresql_*`, `*_kafka_*` — and they are
NOT interchangeable: the same business operation uses a different path and
often a different HTTP method in each family, and some operations exist in only
one of them. Establish which product the user means before searching. "Not
found" in one family is not evidence of absence in another.

## An instance ID does NOT tell you its family

`db-` is used by **both** relational instances and MemoryStore (Redis) ones —
verified live. Only `pg-` (PostgreSQL Cluster) and Kafka's own ids are
distinctive. So never infer the family from an id, and never conclude a
resource is missing because one family's tool did not find it.

Worse, the two get-by-id endpoints disagree about how strict they are:

- `get_memory_instance` is **family-strict**: it answers "not found" for a
  relational or cluster id, so a result from it proves the instance is Redis.
- `get_relational_instance` is **not**: it resolves ids from every family and
  will hand back a Redis instance or a PostgreSQL Cluster without complaint.
  Always check `datastore_type` on what comes back before treating it as a
  relational instance — relational-only operations such as
  `resize_relational_instance_storage` do not apply to the others.

Note that `datastore_type` is not normalised either: the same engine appears as
`mysql` or `MySQL` depending on how the instance was created. Compare it
case-insensitively.

## The relational listing is a MIXED listing

`list_relational_instances` is backed by the only endpoint that can enumerate
PostgreSQL Clusters, so the raw API response contains both `db-` and `pg-`
rows. The tool filters to `db-` for you. Two consequences:

- The API's `name`/`status` filters are **not applied to `pg-` rows** at all.
  Never describe a filtered count as exact.
- PostgreSQL Cluster has no list or get endpoint of its own; its tools are
  derived from this same listing. `list_postgresql_clusters` is the other half
  of the same filter, and it carries `filters_are_approximate` and
  `more_pages_exist` for exactly this reason.

`list_memory_instances` is the opposite and can be trusted as it reads: one
family, and filters that are applied exactly. An empty result there really
does mean no such Redis instance exists.

## Kafka shares nothing but the gateway

Do not carry a convention into Kafka from another family. Measured: its paths
carry **no `/v1`**, 27 of its 36 operations return **no envelope** (a bare
object, array or string), several writes take **query parameters instead of a
body** (authentication, public access, the config-group attach, all three
resizes), delete is a real `DELETE` with no action envelope, and there is **no
zone**.

Start with `get_kafka_limits`: it is the only endpoint in vDB where the
platform publishes its own validation rules — broker range (3-10; a quorum, so
the floor is 3), storage range per broker, the topic and user caps, the name
regexes, and the five ports a security rule may open. One bound is missing from
it: a topic's replication factor may not exceed the cluster's broker count,
which `get_kafka_cluster` reports.

Three Kafka shapes mislead if taken at face value:

- **A user's permission is a list OR an `*_all` flag.** When the flag is set the
  platform ignores the list, so read the flags first.
- **Configuration groups are versioned.** A cluster attaches to a
  `cgroupver-…`, never a `cgroup-…`; only `get_kafka_config_group_version`
  returns a version's settings; and applying one **restarts brokers**.
- **`get_kafka_user_credentials` downloads a ZIP of private keys**, not a list
  of strings. It writes a file and reports only its name list. Never read that
  file back into the conversation.

## The PostgreSQL Cluster family borrows five relational endpoints

Beyond list and get, this family has no endpoint for security rules, history,
reboot or delete either — the relational ones serve a `pg-` id, which is what
the GreenNode Portal itself calls, and `list_postgresql_cluster_secrules`,
`update_postgresql_cluster_secrules`, `list_postgresql_cluster_histories`,
`reboot_postgresql_cluster` and `delete_postgresql_cluster` wrap them.

Do not generalise that into "the relational API works for clusters": measured,
`/replicas/{id}` answers `403` for a cluster and `/backups/insId/{id}` answers
an empty array, because cluster backups are vBackup resources with their own
tools (`list_postgresql_restore_points`, and the locations and policies).
Reboot is the family's only lifecycle action — there is no start and no stop.

## No region parameter

Unlike vServer and vBackup, vDB has **one gateway for the whole account** and
resolves the project from the token. There is no `region` argument on any tool
and no region to get wrong.

## Creating and resizing costs real money

`create_*`, `resize_*` and `restore_*` go through an order flow: they raise a
billable order and provision **asynchronously**, so the resource is not ready
when the call returns — expect `BUILDING` before `ACTIVE`. Every one of them has
a `*_dryrun` companion that shows the exact payload without sending it. Use the
dry run first and confirm with the user before the real call. Never pick a
flavour, volume size or billing period on the user's behalf.

These tools default to the **Auto Payment** billing flow (`user_type`
`IAM_USER`), which settles the order and provisions the resource. Do not switch
to `ROOT_USER` unless the user explicitly asks for a reviewable order:
`ROOT_USER` also answers `200`, but it creates **nothing** until a human
completes the order in the payment console, so the resource never appears.

## When a create is rejected

The API answers every validation failure — bad password, wrong zone
combination, missing field, even an empty body — with the single message
`in_valid`, naming no field. Do not try to bisect the payload. Check, in order:

1. The password rule: only letters, digits and `$ ^ _ < >`, starting with a
   letter, ending alphanumeric, **8-32** characters for relational and
   **16-128** for MemoryStore. The tools validate this locally, so a rejection
   here is reported properly.
2. That `packageId`, `volumeType` and `locateZoneId` all come from the **same
   zone**. Volume types are zone-suffixed outside `HCM03-1A`
   (`Gen2-NVMe2-IOPS3000-HCM03-1B`) and flavour ids differ per zone.
3. For a relational create, that at least one database is requested — an empty
   `databases` is rejected. The memory create has no `databases` field at all,
   nor any volume field: a Redis flavour bundles its disk.
4. For a memory create, that `redisPasswordEnabled` is on whenever
   `publicAccess` is — the platform requires it.

## A resource in ERROR or INTERNAL_ERROR is a platform fault

Those statuses mean the platform failed, not that the request was wrong. Report
the resource id, its zone and what was asked for, rather than retrying the same
call or quietly trying a different shape.

## Discovery before creating

The catalogue tools answer what the platform offers. Two of them behave
unusually:

- `list_*_flavors` **requires** `datastore_type` and `version`, and returns an
  **empty list** — not an error — when the pair is not one the platform
  offers. Always take both values from `list_*_datastores` first.
- `list_*_subnets` is what a create needs (it wants a subnet, not a network).

## Presenting results

When rendering any resource list or detail (tables, bullet lists), ALWAYS put
each item's `id` and `name` first — follow-up calls and confirmations need
them. Never drop the id column to save space and never truncate an id.

Instance rows carry 68 fields upstream, including billing fields; the tools
return a projection. Ask for the detail tool if the user needs a field the
listing does not show.
"""


def create_server() -> MCPServer:
    """Create and return an MCPServer instance (handlers wired in main)."""
    server = MCPServer("vdb-mcp-server", instructions=SERVER_INSTRUCTIONS)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        """Liveness/readiness probe endpoint (no authentication required)."""
        return JSONResponse({"status": "ok"})

    return server


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="GreenNode MCP Server -- manage vDB via MCP")
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Enable create/update/delete operations (default: read-only)",
    )
    parser.add_argument(
        "--allow-sensitive-data-access",
        action="store_true",
        help=(
            "Enable tools that return credential material (default: off). Currently one: "
            "downloading a Kafka user's mTLS/SASL archive, which contains private keys."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode: stdio (default) or streamable-http",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP transport")
    parser.add_argument("--port", type=int, default=8080, help="Bind port for HTTP transport")
    return parser


def register_handlers(
    mcp: MCPServer,
    config,
    client: VdbClient,
    allow_write: bool,
    allow_sensitive_data_access: bool = False,
) -> None:
    """Register every handler on *mcp*.

    Kept separate from ``main`` so tests can build a fully wired server without
    parsing arguments or reading a profile from disk.
    """
    cache = DiscoveryCache()
    CatalogHandler(mcp, config, client, cache)
    RelationalInstanceHandler(mcp, config, client, allow_write=allow_write)
    RelationalBackupHandler(mcp, config, client, allow_write=allow_write)
    RelationalConfigHandler(mcp, config, client, cache, allow_write=allow_write)
    RelationalStorageHandler(mcp, config, client, cache, allow_write=allow_write)
    MemoryInstanceHandler(mcp, config, client, allow_write=allow_write)
    MemoryBackupHandler(mcp, config, client, allow_write=allow_write)
    MemoryConfigHandler(mcp, config, client, cache, allow_write=allow_write)
    MemoryStorageHandler(mcp, config, client, cache, allow_write=allow_write)
    PostgresqlClusterHandler(mcp, config, client, allow_write=allow_write)
    PostgresqlBackupHandler(mcp, config, client, cache, allow_write=allow_write)
    KafkaClusterHandler(mcp, config, client, allow_write=allow_write)
    KafkaTopicHandler(mcp, config, client, allow_write=allow_write)
    KafkaUserHandler(
        mcp,
        config,
        client,
        allow_write=allow_write,
        allow_sensitive_data_access=allow_sensitive_data_access,
    )
    KafkaConfigHandler(mcp, config, client, allow_write=allow_write)
    PromptsHandler(mcp)


def main() -> None:
    """Run the MCP server with CLI argument support."""
    args = _build_parser().parse_args()

    config = load_config(CONFIG_PATH)
    token_manager = TokenManager(config)
    client = VdbClient(config, token_manager)

    mcp = create_server()
    register_handlers(
        mcp,
        config,
        client,
        allow_write=args.allow_write,
        allow_sensitive_data_access=args.allow_sensitive_data_access,
    )

    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
