"""Kafka configuration-group tools.

Kafka's configuration groups are **versioned**, and that single fact makes them
behave unlike the configuration groups of every other vDB family:

* A cluster attaches to a **version** (``cgroupver-…``), never to the group
  (``cgroup-…``).
* A version is **immutable**. There is no update endpoint: changing a setting
  means creating another version and moving the cluster onto it.
* Because of that, editing settings is safe in a way it is not elsewhere --
  creating a version touches no running cluster at all. The risk sits entirely
  in ``update_kafka_cluster_config_group``, which restarts brokers.

There is also no parameter catalogue here: nothing lists the broker settings a
version may carry, the way ``list_relational_configuration_params`` does. The
keys are Kafka's own broker configuration names, and the platform publishes only
the ones it forces (``configsForcedValues`` in ``get_kafka_limits``).
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import VDB_KAFKA_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    CreateKafkaConfigGroupDto,
    CreateKafkaConfigGroupVersionDto,
    KafkaActionData,
    KafkaConfigGroup,
    KafkaConfigGroupListData,
    KafkaConfigGroupVersion,
)
from greennode.vdb_mcp_server.paging import as_list, unwrap_wrapped
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


BASE = "/config-groups"

DELETE_NEXT_STEP = (
    "The deletion was accepted. Confirm with list_kafka_config_groups. A cluster still "
    "attached to one of this group's versions keeps running the settings it already has."
)


class KafkaConfigHandler:
    """Register and serve the Kafka configuration-group tools."""

    def __init__(self, mcp, config: VdbConfig, client: VdbClient, allow_write: bool):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_kafka_config_groups", annotations=READ)(
            self.list_kafka_config_groups
        )
        self.mcp.tool(name="get_kafka_config_group", annotations=READ)(self.get_kafka_config_group)
        self.mcp.tool(name="get_kafka_config_group_version", annotations=READ)(
            self.get_kafka_config_group_version
        )

        if self.allow_write:
            self.mcp.tool(name="create_kafka_config_group", annotations=WRITE)(
                self.create_kafka_config_group
            )
            self.mcp.tool(name="create_kafka_config_group_version", annotations=WRITE)(
                self.create_kafka_config_group_version
            )
            self.mcp.tool(name="delete_kafka_config_group", annotations=DESTRUCTIVE)(
                self.delete_kafka_config_group
            )

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.client.call(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    async def _call_scalar(self, method: str, path: str, **kwargs: Any) -> Any:
        """For the endpoints whose success response is a bare word, not a document."""
        return await self.client.call_scalar(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_kafka_config_groups(self) -> KafkaConfigGroupListData:
        """List the Kafka configuration groups in this project, with their versions.

        Unpaginated and unfiltered.

        **The rows are summaries, and so is the group detail.** A nested
        version reports `properties` as empty here *and* in
        `get_kafka_config_group` — measured. Only
        `get_kafka_config_group_version` returns the settings a version
        actually carries, so a group that looks as if it sets nothing has
        simply not been read deeply enough.

        What a cluster attaches to is a **version** id (`cgroupver-…`) from
        inside a row, not the group id.
        """
        raw = await self._call("GET", BASE)
        items = [KafkaConfigGroup.from_api(row) for row in as_list(raw)]
        return KafkaConfigGroupListData(count=len(items), items=items)

    async def get_kafka_config_group(
        self,
        config_group_id: str = Field(..., description="Group ID ('cgroup-…')"),
    ) -> KafkaConfigGroup:
        """Get one Kafka configuration group and every version of it.

        Use it to enumerate a group's versions. **It does not return their
        settings**: measured, `properties` comes back empty here just as it
        does in the listing, and only `get_kafka_config_group_version` fills
        it in. `associated_cluster_ids` is the one field this endpoint does
        add, coming back as a real list rather than null.

        Versions come back **newest first** — measured, a group with two
        versions lists version 2 before version 1 — so `versions[0]` is the
        latest, not the original. The number in `version` counts from 1. A
        cluster runs exactly one of them.
        """
        validate_id(config_group_id, "config_group_id")
        raw = await self._call("GET", f"{BASE}/{config_group_id}")
        return KafkaConfigGroup.from_api(unwrap_wrapped(raw) or {})

    async def get_kafka_config_group_version(
        self,
        config_group_id: str = Field(..., description="Group ID ('cgroup-…')"),
        config_group_version_id: str = Field(..., description="Version ID ('cgroupver-…')"),
    ) -> KafkaConfigGroupVersion:
        """Get one version of a Kafka configuration group.

        This is the settings a cluster actually runs. Read it before
        `update_kafka_cluster_config_group` so the user can see what is about
        to change on the brokers, and read the version the cluster is on now
        alongside it — `get_kafka_cluster` reports that as
        `config_group_version_id`.
        """
        validate_id(config_group_id, "config_group_id")
        validate_id(config_group_version_id, "config_group_version_id")
        raw = await self._call(
            "GET", f"{BASE}/{config_group_id}/versions/{config_group_version_id}"
        )
        return KafkaConfigGroupVersion.from_api(unwrap_wrapped(raw) or {})

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_kafka_config_group(
        self,
        spec: CreateKafkaConfigGroupDto = Field(..., description="The group to create"),
    ) -> KafkaConfigGroup:
        """Create a Kafka configuration group and its first version. Free.

        ## Requirements

        - `--allow-write` must be enabled.
        - Creating a group also creates its **version 1** from `properties`.
          There is no way to edit that version afterwards — a change means a
          new version.
        - This affects **no running cluster**. Nothing happens to brokers until
          `update_kafka_cluster_config_group` points one at a version.
        - `properties` are Kafka broker settings as `{key, value}` pairs, with
          values always as strings. Nothing in this API lists the valid keys;
          `get_kafka_limits` reports only `configs_forced_values`, the settings
          the platform overrides whatever a group says.
        - The name rule is its own: 5-50 characters starting with a letter, and
          **spaces are allowed here and nowhere else in this family**.
        - An account holds at most the `max_config_groups` `get_kafka_limits`
          reports.

        ## Workflow

        1. `get_kafka_limits` → the name rule and the forced values.
        2. `list_kafka_config_groups` → check the name is free.
        3. This tool. Take `versions[0].id` from what it returns — that is
           version 1, and it is what an attach needs.
        4. `update_kafka_cluster_config_group` when the user is ready for the
           brokers to restart.

        Note the result is **re-read** rather than taken from the create
        response: measured, the create answers with `versions: []` even though
        version 1 exists, so reporting it verbatim would hand back a group that
        looks unusable.
        """
        require_write(self.allow_write)
        raw = await self._call("POST", BASE, json=spec.model_dump(exclude_none=True))
        created = KafkaConfigGroup.from_api(unwrap_wrapped(raw) or {})
        if not created.id:
            return created
        # The create response omits the version it just made. Read the group
        # back so the caller gets the id an attach needs, and fall back to the
        # thin response if that read fails -- the group exists either way.
        try:
            return await self.get_kafka_config_group(created.id)
        except Exception:  # noqa: BLE001 - a failed re-read must not lose the id
            return created

    async def create_kafka_config_group_version(
        self,
        config_group_id: str = Field(..., description="Group ID ('cgroup-…')"),
        spec: CreateKafkaConfigGroupVersionDto = Field(
            ..., description="The COMPLETE set of settings for the new version"
        ),
    ) -> KafkaConfigGroupVersion:
        """Add a version to a Kafka configuration group. Free.

        ## Requirements

        - `--allow-write` must be enabled.
        - **A version is a complete set, not a patch.** Nothing is inherited
          from the version before it, so read the current version first and
          send back every setting that should survive.
        - This changes **no running cluster**. Clusters stay on the version
          they are attached to until someone moves them.
        - Versions are immutable once created; a mistake means another
          version, not an edit.

        ## Workflow

        1. `get_kafka_config_group` → the versions that exist and which
           clusters use them.
        2. `get_kafka_config_group_version` → the current settings, which this
           is the **only** endpoint that returns. Build the full new set from
           them.
        3. This tool. It answers with the new version's id and number but
           **not** its settings — measured, `properties` comes back empty — so
           confirm with `get_kafka_config_group_version` if the values matter.
        4. `update_kafka_cluster_config_group` to put a cluster on the new
           version — that is the step that restarts brokers.
        """
        require_write(self.allow_write)
        validate_id(config_group_id, "config_group_id")
        raw = await self._call(
            "POST",
            f"{BASE}/{config_group_id}/versions",
            json=spec.model_dump(exclude_none=True),
        )
        return KafkaConfigGroupVersion.from_api(unwrap_wrapped(raw) or {})

    async def delete_kafka_config_group(
        self,
        config_group_id: str = Field(..., description="Group ID ('cgroup-…')"),
    ) -> KafkaActionData:
        """Delete a Kafka configuration group and all its versions. Irreversible.

        ## Requirements

        - `--allow-write` must be enabled.
        - Check `get_kafka_config_group` for versions with
          `associated_cluster_ids`: those clusters are running settings from
          this group. Deleting it does not revert them, but it does remove the
          record of what they are running and any way back to it.
        - Every version goes at once — there is no delete for a single version.

        ## Workflow

        1. `get_kafka_config_group` → the versions and the clusters using them.
        2. Confirm with the user, naming any cluster still attached.
        3. This tool, then `list_kafka_config_groups` to confirm — measured,
           this endpoint answers with an **empty body**, so `message` is empty
           and the listing is the only evidence the group is gone.
        """
        require_write(self.allow_write)
        validate_id(config_group_id, "config_group_id")
        raw = await self._call_scalar("DELETE", f"{BASE}/{config_group_id}")
        payload = unwrap_wrapped(raw)
        return KafkaActionData(
            cluster_id="",
            resource_id=config_group_id,
            message="" if payload is None else str(payload),
            accepted=True,
            next_step=DELETE_NEXT_STEP,
        )
