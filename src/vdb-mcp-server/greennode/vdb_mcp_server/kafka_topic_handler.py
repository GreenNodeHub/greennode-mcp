"""Kafka topic tools.

Topics exist in no other vDB family: they hold the data, they are created on a
running cluster rather than with it, and they are the unit a Kafka user's
permissions are granted over.

Two bounds are enforced from different places, which is why both matter:
`get_kafka_limits` publishes the partition, retention and name rules, while the
**replication factor** is bounded by the cluster's own broker count — a number
the limits catalogue does not carry. Read it from `get_kafka_cluster`.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import VDB_KAFKA_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    CreateKafkaTopicDto,
    KafkaActionData,
    KafkaTopic,
    KafkaTopicListData,
    UpdateKafkaTopicDto,
)
from greennode.vdb_mcp_server.paging import as_list, unwrap_wrapped
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


BASE = "/clusters"

TOPIC_NEXT_STEP = (
    "The request was accepted, not applied -- measured, this endpoint answers with an EMPTY "
    "body. Poll get_kafka_topic until status_kind is settled, and read "
    "list_kafka_cluster_histories if nothing changed: a failure is recorded there and "
    "nowhere else."
)

DELETE_NEXT_STEP = (
    "The deletion was accepted. Confirm with list_kafka_topics. Every message the topic held "
    "is gone -- Kafka has no backup in vDB, so there is nothing to restore from."
)


class KafkaTopicHandler:
    """Register and serve the Kafka topic tools."""

    def __init__(self, mcp, config: VdbConfig, client: VdbClient, allow_write: bool):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_kafka_topics", annotations=READ)(self.list_kafka_topics)
        self.mcp.tool(name="get_kafka_topic", annotations=READ)(self.get_kafka_topic)

        if self.allow_write:
            self.mcp.tool(name="create_kafka_topic", annotations=WRITE)(self.create_kafka_topic)
            self.mcp.tool(name="update_kafka_topic", annotations=WRITE)(self.update_kafka_topic)
            self.mcp.tool(name="delete_kafka_topic", annotations=DESTRUCTIVE)(
                self.delete_kafka_topic
            )

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.client.call(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    async def _call_scalar(self, method: str, path: str, **kwargs: Any) -> Any:
        """For the endpoints whose success response is a bare word, not a document."""
        return await self.client.call_scalar(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_kafka_topics(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
    ) -> KafkaTopicListData:
        """List the topics on a Kafka cluster.

        Unpaginated and unfiltered — the whole array comes back. An empty
        result means the cluster genuinely holds no topics; this endpoint is
        scoped to one cluster, so it cannot be hiding another's.

        Read this before shrinking a cluster: a topic whose `replicas` equals
        the current broker count cannot keep its replicas afterwards.

        A topic reports `WAITING_CREATING` while it is being made and settles
        to `ACTIVE`; `status_kind` says which. Nothing else on the cluster can
        be written while one is in flight.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self._call("GET", f"{BASE}/{cluster_id}/topics")
        items = [KafkaTopic.from_api(row) for row in as_list(raw)]
        return KafkaTopicListData(cluster_id=cluster_id, count=len(items), items=items)

    async def get_kafka_topic(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        topic_id: str = Field(..., description="Topic ID from list_kafka_topics"),
    ) -> KafkaTopic:
        """Get one Kafka topic.

        Takes the topic's **id**, not its name — the name is what a user's
        permissions are granted over, but the id is what addresses the topic
        here.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(topic_id, "topic_id")
        raw = await self._call("GET", f"{BASE}/{cluster_id}/topics/{topic_id}")
        return KafkaTopic.from_api(unwrap_wrapped(raw) or {})

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_kafka_topic(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        spec: CreateKafkaTopicDto = Field(..., description="The topic to create"),
    ) -> KafkaTopic:
        """Create a topic on a Kafka cluster. Free, but asynchronous.

        ## Requirements

        - `--allow-write` must be enabled.
        - **`replicas` must not exceed the cluster's broker count.** That
          ceiling is not in `get_kafka_limits` — read `broker_count` from
          `get_kafka_cluster`. A replication factor of 1 means the topic is
          unavailable whenever its broker restarts, which is what happens
          during any cluster resize or config-group change.
        - Partitions, retention and the name rule are bounded by
          `get_kafka_limits`; the DTO enforces them, so a rejection here names
          the real problem.
        - A cluster holds at most the `max_topics` that catalogue reports.
        - Omitting `partitions`/`replicas` takes the Kafka version's defaults,
          which `get_kafka_limits` publishes per version.
        - `retentionBytes` takes exactly `-1` for unlimited; 0 is not that.

        ## Workflow

        1. `get_kafka_cluster` → `broker_count`, to bound `replicas`.
        2. `get_kafka_limits` → the partition and retention ranges.
        3. `list_kafka_topics` → check the name is free and the count is under
           the cap.
        4. This tool. It returns the created topic directly, with no
           envelope — but with `status: WAITING_CREATING`, **not** ready.
        5. Poll `get_kafka_topic` until `status_kind` is `settled`. Measured,
           that takes under a minute; until then the topic cannot be updated
           and **no other write on the cluster will be accepted** (see below).

        ## The cluster runs one operation at a time

        Measured: while this topic was still `WAITING_CREATING`, updating it
        answered `400 Bad request: Topic … is not active`, and creating a user
        on the same cluster was rejected too. Kafka reports this as a plain
        `400`, not as the "Cannot perform action" the other families use — so a
        rejection right after another write is a **timing** problem, not a bad
        payload. Wait for the resource to settle and send it again.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        raw = await self._call(
            "POST", f"{BASE}/{cluster_id}/topics", json=spec.model_dump(exclude_none=True)
        )
        return KafkaTopic.from_api(unwrap_wrapped(raw) or {})

    async def update_kafka_topic(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        topic_id: str = Field(..., description="Topic ID from list_kafka_topics"),
        spec: UpdateKafkaTopicDto = Field(..., description="The settings to change"),
    ) -> KafkaActionData:
        """Change a topic's partitions, replicas or retention.

        ## Requirements

        - `--allow-write` must be enabled.
        - **A topic's name cannot be changed**, and **partitions cannot be
          reduced** — that is a Kafka limitation, not a platform one. Growing
          partitions also re-keys message distribution, so consumers relying
          on per-key ordering see it change.
        - **The topic must be `ACTIVE`.** A topic still `WAITING_CREATING` is
          rejected with `400 Bad request: Topic … is not active` — measured.
          Check `status_kind` with `get_kafka_topic` first.
        - `replicas` must still not exceed the cluster's broker count.
        - Reducing retention **deletes messages older than the new window**,
          immediately and irreversibly. Say so before sending it.
        - **This replaces the whole set.** `partitions` and `replicas` are
          required even when they are not changing: measured, omitting
          `replicas` answers `400 Can't update replicas for topic` and omitting
          `partitions` answers `400 Partition count needs to be between 1 and
          2048` — neither of which says a field is missing. Read the topic and
          send its current values back.

        ## Workflow

        1. `get_kafka_topic` → the current settings, which are also what you
           send back for whatever is not changing.
        2. `get_kafka_cluster` → `broker_count`, if changing `replicas`.
        3. Confirm with the user, naming what is lost if retention shrinks.
        4. This tool, then poll `get_kafka_topic` — the response here is an
           **empty body**, not the updated topic, and the topic goes back
           through a transitional status before the change is live.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(topic_id, "topic_id")
        raw = await self._call_scalar(
            "PUT",
            f"{BASE}/{cluster_id}/topics/{topic_id}",
            json=spec.model_dump(exclude_none=True),
        )
        payload = unwrap_wrapped(raw)
        return KafkaActionData(
            cluster_id=cluster_id,
            resource_id=topic_id,
            message="" if payload is None else str(payload),
            accepted=True,
            next_step=TOPIC_NEXT_STEP,
        )

    async def delete_kafka_topic(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        topic_id: str = Field(..., description="Topic ID from list_kafka_topics"),
    ) -> KafkaActionData:
        """Delete a topic and every message in it. Irreversible.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm the topic **by name** with the user. vDB keeps no Kafka
          backups, so the messages cannot be recovered.
        - Producers and consumers bound to it start failing at once.
        - Users holding permissions on the topic keep them as dangling names;
          review with `list_kafka_users`.

        ## Workflow

        1. `get_kafka_topic` → confirm which topic, and its retention.
        2. Confirm with the user, naming the topic.
        3. This tool, then `list_kafka_topics` to confirm it is gone.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(topic_id, "topic_id")
        raw = await self._call_scalar("DELETE", f"{BASE}/{cluster_id}/topics/{topic_id}")
        payload = unwrap_wrapped(raw)
        return KafkaActionData(
            cluster_id=cluster_id,
            resource_id=topic_id,
            message="" if payload is None else str(payload),
            accepted=True,
            next_step=DELETE_NEXT_STEP,
        )
