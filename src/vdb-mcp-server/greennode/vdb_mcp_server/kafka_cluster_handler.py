"""Kafka cluster tools.

Kafka disagrees with the rest of vDB about almost every convention, and the
disagreements are the reason this handler shares nothing but the HTTP client
with its siblings:

* **Paths carry no ``/v1``** -- ``/clusters``, not ``/v1/clusters``.
* **Most responses carry no envelope.** Of the operations here, only the four
  order flows answer ``{code, message, data}``; the rest return a bare object,
  a bare array, or a bare string. ``unwrap_wrapped`` passes an unwrapped
  payload straight through, so the helpers still apply.
* **Several writes take query parameters, not a body** -- authentication,
  public access, the config-group attach and all three resizes. A body would
  be ignored.
* **Delete is a real ``DELETE``**, not a ``POST /delete`` with an action
  envelope. There is no ``action``/``resType`` vocabulary anywhere in Kafka.
* **There is no zone.** A Kafka cluster is placed by network and subnet alone.

Security rules are a fourth oddity: they are created and deleted one at a
time, and there is no endpoint that lists them -- ``get_kafka_cluster`` is the
only way to see them, and the cluster *listing* omits the field entirely.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import DEFAULT_USER_TYPE, UserType, VdbClient
from greennode.vdb_mcp_server.config import VDB_KAFKA_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    CreateKafkaClusterDto,
    CreateKafkaSecurityRuleDto,
    DryRunData,
    KafkaActionData,
    KafkaCluster,
    KafkaClusterListData,
    KafkaHistoryEntry,
    KafkaHistoryListData,
    KafkaSecurityRule,
    OrderData,
    OrderResult,
)
from greennode.vdb_mcp_server.paging import as_list, unwrap_wrapped
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


BASE = "/clusters"
"""No ``/v1``. Kafka is the only vDB family with unversioned paths."""

KAFKA_PREFIX = "clus-"
"""Cluster ids. Distinctive -- no other vDB family uses it."""

ORDER_NEXT_STEP = (
    "An order has been raised, not a cluster. Under the default IAM_USER flow the order "
    "carries the new resourceId, but the cluster still provisions asynchronously -- a Kafka "
    "cluster builds at least three brokers, so expect it to take longer than a single "
    "instance. Poll get_kafka_cluster, or find it with list_kafka_clusters by name."
)

RESIZE_NEXT_STEP = (
    "A resize order has been raised. Confirm with get_kafka_cluster rather than assuming the "
    "new shape: the change is applied asynchronously across every broker in turn."
)

ACTION_NEXT_STEP = (
    "The request was accepted, not applied -- measured, this endpoint answers with an EMPTY "
    "body: no id, no word, nothing to poll. Confirm with get_kafka_cluster, and read "
    "list_kafka_cluster_histories if nothing changed: a failure is recorded there and "
    "nowhere else."
)

SECRULE_NEXT_STEP = (
    "Confirm with get_kafka_cluster -- its security_group_rules field is the only place rules "
    "are listed; there is no endpoint for them on their own."
)

DELETE_NEXT_STEP = (
    "The deletion was accepted, not completed. Confirm with list_kafka_clusters that the "
    "cluster is gone. Its topics, users and credentials go with it and cannot be recovered."
)


class KafkaClusterHandler:
    """Register and serve the Kafka cluster tools."""

    def __init__(self, mcp, config: VdbConfig, client: VdbClient, allow_write: bool):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_kafka_clusters", annotations=READ)(self.list_kafka_clusters)
        self.mcp.tool(name="get_kafka_cluster", annotations=READ)(self.get_kafka_cluster)
        self.mcp.tool(name="list_kafka_cluster_histories", annotations=READ)(
            self.list_kafka_cluster_histories
        )
        self.mcp.tool(name="create_kafka_cluster_dryrun", annotations=READ)(
            self.create_kafka_cluster_dryrun
        )

        if self.allow_write:
            self.mcp.tool(name="create_kafka_cluster", annotations=WRITE)(
                self.create_kafka_cluster
            )
            self.mcp.tool(name="resize_kafka_cluster_brokers", annotations=DESTRUCTIVE)(
                self.resize_kafka_cluster_brokers
            )
            self.mcp.tool(name="resize_kafka_cluster_storage", annotations=WRITE)(
                self.resize_kafka_cluster_storage
            )
            self.mcp.tool(name="update_kafka_cluster_storage_type", annotations=WRITE)(
                self.update_kafka_cluster_storage_type
            )
            self.mcp.tool(name="update_kafka_cluster_authentication", annotations=WRITE)(
                self.update_kafka_cluster_authentication
            )
            self.mcp.tool(name="update_kafka_cluster_public_access", annotations=WRITE)(
                self.update_kafka_cluster_public_access
            )
            self.mcp.tool(name="update_kafka_cluster_config_group", annotations=WRITE)(
                self.update_kafka_cluster_config_group
            )
            self.mcp.tool(name="create_kafka_cluster_secrule", annotations=WRITE)(
                self.create_kafka_cluster_secrule
            )
            self.mcp.tool(name="delete_kafka_cluster_secrule", annotations=DESTRUCTIVE)(
                self.delete_kafka_cluster_secrule
            )
            self.mcp.tool(name="delete_kafka_cluster", annotations=DESTRUCTIVE)(
                self.delete_kafka_cluster
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.client.call(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    async def _call_scalar(self, method: str, path: str, **kwargs: Any) -> Any:
        """For the endpoints whose success response is a bare word, not a document."""
        return await self.client.call_scalar(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    async def _order(self, method: str, path: str, user_type: str, **kwargs: Any):
        raw = await self._call(method, path, user_type=user_type, **kwargs)
        return [OrderResult.from_api(row) for row in as_list(raw)]

    def _action(self, raw: Any, cluster_id: str, next_step: str, resource_id: str = ""):
        """Wrap a scalar response.

        Measured, every Kafka write here answers with an **empty** body -- the
        schema's ``type: string`` never materialises as an actual word. The
        message is reported verbatim anyway, so a platform that starts sending
        one is surfaced rather than swallowed.
        """
        payload = unwrap_wrapped(raw)
        return KafkaActionData(
            cluster_id=cluster_id,
            resource_id=resource_id,
            message="" if payload is None else str(payload),
            accepted=True,
            next_step=next_step,
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_kafka_clusters(self) -> KafkaClusterListData:
        """List the Kafka clusters in this project.

        Unpaginated, unfiltered and unenveloped: the endpoint answers with the
        whole array and takes no query parameters at all, so there is nothing
        to narrow it by. Filter on `name` or `status` from the rows.

        Rows do **not** carry the security-group rules — that field exists only
        on `get_kafka_cluster`.
        """
        raw = await self._call("GET", BASE)
        items = [KafkaCluster.from_api(row) for row in as_list(raw)]
        return KafkaClusterListData(count=len(items), items=items)

    async def get_kafka_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
    ) -> KafkaCluster:
        """Get one Kafka cluster in full.

        Prefer this over a listing row for anything that matters: it is the
        **only** source of `security_group_rules`, since this family has no
        endpoint that lists rules on their own.

        Read `config_group_version_id` here too — it names a configuration
        group *version* (`cgroupver-…`), not a group, and an empty string means
        none is attached.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self._call("GET", f"{BASE}/{cluster_id}")
        return KafkaCluster.from_api(unwrap_wrapped(raw) or {})

    async def list_kafka_cluster_histories(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
    ) -> KafkaHistoryListData:
        """List what has been done to a cluster, and how it turned out.

        **This is the only record of an asynchronous failure.** Most Kafka
        writes answer with a bare string and no id to poll, so when a change
        appears not to have landed, this is where the platform says why:
        `action` names the operation, `description` names the resource, and
        `error_message` carries the failure.

        Read it whenever a write looks like it did nothing — before retrying,
        and before reporting success.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self._call("GET", f"{BASE}/{cluster_id}/history")
        items = [KafkaHistoryEntry.from_api(row) for row in as_list(raw)]
        return KafkaHistoryListData(cluster_id=cluster_id, count=len(items), items=items)

    # ------------------------------------------------------------------
    # dry run
    # ------------------------------------------------------------------

    async def create_kafka_cluster_dryrun(
        self,
        spec: CreateKafkaClusterDto = Field(..., description="The cluster to order"),
    ) -> DryRunData:
        """Show the exact order that create_kafka_cluster would place.

        Validates the whole body locally — the name rule, the broker range and
        the storage range, all of them published by `get_kafka_limits` — and
        sends nothing.
        """
        return DryRunData(
            tool="create_kafka_cluster",
            method="POST",
            path=BASE,
            service=VDB_KAFKA_SERVICE,
            user_type=DEFAULT_USER_TYPE,
            body=spec.model_dump(exclude_none=True),
            warnings=[
                "Raises a BILLABLE order, and Kafka bills per broker: the minimum cluster is "
                f"{spec.kafkaBrokerCount} brokers of this flavour, each with "
                f"{spec.kafkaStorageSize} GB.",
                "serverFlavorId must be a flavour's 'flav-…' id and kafkaStorageType a volume "
                "type's 'vtype-…' id -- neither catalogue lists those first.",
                "configGroupVersionId must be a VERSION ('cgroupver-…'), never a group.",
            ],
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_kafka_cluster(
        self,
        spec: CreateKafkaClusterDto = Field(..., description="The cluster to order"),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE,
            description=(
                "Billing flow. IAM_USER (Auto Payment) is the default and the one to use: "
                "ROOT_USER routes the order through manual Checkout, where it sits unpaid "
                "until someone completes it in the payment console."
            ),
        ),
    ) -> OrderData:
        """Order a new Kafka cluster. Costs money, per broker.

        ## Requirements

        - `--allow-write` must be enabled.
        - Run `create_kafka_cluster_dryrun` first and have the user confirm the
          flavour, broker count and storage. The floor is **3 brokers**, so
          this is the most expensive create in vDB.
        - `serverFlavorId` takes a flavour's **`flav-…`** id from
          `list_kafka_flavors`, not its integer `id`.
        - `kafkaStorageType` takes a volume type's **`vtype-…`** id from
          `list_kafka_volume_types`, not its integer `id` and not its `type`
          name.
        - `configGroupVersionId`, if given, is a **version** (`cgroupver-…`)
          from `list_kafka_config_groups`, never a group id.
        - `kafkaVersion` must be one of the versions `get_kafka_limits`
          reports.
        - Kafka has **no zone**: placement is the network and subnet alone.
        - `vserverProjectId` is filled from the configured profile when left
          out; pass it only to override.

        ## Workflow

        1. `get_kafka_limits` → the version list and the bounds this order
           must satisfy.
        2. `list_kafka_flavors` → `serverFlavorId` (the `flav-…` one).
        3. `list_kafka_volume_types` → `kafkaStorageType` (the `vtype-…` one).
        4. `list_relational_subnets` → the network and subnet.
        5. Optional: `list_kafka_config_groups` → a `cgroupver-…` version.
        6. `create_kafka_cluster_dryrun` → confirm with the user.
        7. This tool, then poll `get_kafka_cluster` with the `resourceId` it
           returns. Topics and users are created afterwards, on the running
           cluster.
        """
        require_write(self.allow_write)
        body = spec.model_dump(exclude_none=True)
        orders = await self._order("POST", BASE, user_type, json=body)
        return OrderData(orders=orders, instance_name=spec.name, next_step=ORDER_NEXT_STEP)

    async def resize_kafka_cluster_brokers(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        count: int = Field(..., ge=3, le=10, description="New broker count; 3-10"),
        rebalance: bool = Field(
            ...,
            description=(
                "Whether to rebalance partitions across the new broker set. Decide this "
                "explicitly: without it new brokers stay empty, and removing brokers without "
                "it can strand partitions."
            ),
        ),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE, description="Billing flow; leave at IAM_USER (Auto Payment)"
        ),
    ) -> OrderData:
        """Change a Kafka cluster's broker count. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first: this is a billable order and it changes
          a running cluster.
        - **Reducing the count destroys brokers.** A topic whose replication
          factor equals the old broker count cannot keep its replicas — check
          `list_kafka_topics` before shrinking, and say which topics are at
          risk.
        - `rebalance` has no default here on purpose. Ask.
        - The parameters go on the **query string**; this endpoint takes no
          body.

        ## Workflow

        1. `get_kafka_cluster` → the current count and `status_kind`.
        2. `list_kafka_topics` → replication factors, if shrinking.
        3. Confirm with the user, including `rebalance`.
        4. This tool, then poll `get_kafka_cluster`.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        orders = await self._order(
            "PUT",
            f"{BASE}/{cluster_id}/kafka-broker-count",
            user_type,
            params={"count": count, "rebalance": rebalance},
        )
        return OrderData(orders=orders, instance_name=cluster_id, next_step=RESIZE_NEXT_STEP)

    async def resize_kafka_cluster_storage(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        size: int = Field(
            ..., ge=20, le=5000, description="New storage per broker in GB; 20-5000"
        ),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE, description="Billing flow; leave at IAM_USER (Auto Payment)"
        ),
    ) -> OrderData:
        """Change a Kafka cluster's storage size. Costs money, per broker.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user: the new size applies to **every** broker, so
          the bill moves by the size difference times the broker count.
        - Storage grows only.
        - The size goes on the **query string**; this endpoint takes no body.

        ## Workflow

        1. `get_kafka_cluster` → `storage_size_gb` and `storage_used_bytes`
           (one entry per broker, so compare against the busiest).
        2. Confirm with the user.
        3. This tool, then poll `get_kafka_cluster`.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        orders = await self._order(
            "PUT",
            f"{BASE}/{cluster_id}/kafka-storage-size",
            user_type,
            params={"size": size},
        )
        return OrderData(orders=orders, instance_name=cluster_id, next_step=RESIZE_NEXT_STEP)

    async def update_kafka_cluster_storage_type(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        storage_type: str = Field(
            ..., description="New storage type 'vtype-…' id from list_kafka_volume_types"
        ),
        user_type: UserType = Field(
            DEFAULT_USER_TYPE, description="Billing flow; leave at IAM_USER (Auto Payment)"
        ),
    ) -> OrderData:
        """Move a Kafka cluster to a different storage type. Costs money.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user: this is a billable order across every broker,
          and a faster storage type is a recurring cost, not a one-off.
        - `storage_type` is the volume type's **`vtype-…`** id, not its
          integer `id` and not its `type` name — `get_kafka_cluster` reports
          the one in use as `storage_type_id`.
        - The value goes on the **query string**; this endpoint takes no body.

        ## Workflow

        1. `list_kafka_volume_types` → the `vtype-…` id and its IOPS.
        2. `get_kafka_cluster` → what is in use now.
        3. Confirm with the user.
        4. This tool, then poll `get_kafka_cluster`.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(storage_type, "storage_type")
        orders = await self._order(
            "PUT",
            f"{BASE}/{cluster_id}/kafka-storage-type",
            user_type,
            params={"storageType": storage_type},
        )
        return OrderData(orders=orders, instance_name=cluster_id, next_step=RESIZE_NEXT_STEP)

    async def update_kafka_cluster_authentication(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        mtls_authen: bool = Field(..., description="Whether mTLS authentication is enabled"),
        sasl_authen: bool = Field(..., description="Whether SASL authentication is enabled"),
    ) -> KafkaActionData:
        """Turn a Kafka cluster's authentication mechanisms on or off.

        ## Requirements

        - `--allow-write` must be enabled.
        - **Both flags are sent every time**, so this replaces the whole
          setting rather than amending it. Read `get_kafka_cluster` first and
          pass back the one you do not mean to change.
        - Turning a mechanism **off breaks every client using it**, and a user
          issued credentials for that mechanism can no longer connect. Confirm
          with the user, naming the mechanism.
        - Turning both off leaves the cluster with no authentication at all.
        - The flags go on the **query string**; this endpoint takes no body.

        ## Workflow

        1. `get_kafka_cluster` → the current `mtls_authen` / `sasl_authen`.
        2. `list_kafka_users` → who authenticates how.
        3. Confirm with the user.
        4. This tool, then `get_kafka_cluster` to confirm.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        raw = await self._call_scalar(
            "PUT",
            f"{BASE}/{cluster_id}/authentication",
            params={"mtlsAuthen": mtls_authen, "saslAuthen": sasl_authen},
        )
        return self._action(raw, cluster_id, ACTION_NEXT_STEP)

    async def update_kafka_cluster_public_access(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        enable: bool = Field(..., description="Whether the cluster is reachable publicly"),
    ) -> KafkaActionData:
        """Turn public access to a Kafka cluster on or off.

        ## Requirements

        - `--allow-write` must be enabled.
        - Enabling it gives every broker a floating IP. It does **not** open
          the firewall: reaching the cluster still needs a security-group rule,
          and a rule allowing `0.0.0.0/0` on a broker port exposes Kafka to the
          internet. Say both parts out loud before enabling.
        - Disabling it drops the floating IPs, so every client connecting over
          them breaks.
        - The flag goes on the **query string**; this endpoint takes no body.

        ## Workflow

        1. `get_kafka_cluster` → `public_access` and the existing rules.
        2. Confirm with the user.
        3. This tool, then `get_kafka_cluster` to see the floating IPs appear.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        raw = await self._call_scalar(
            "PUT", f"{BASE}/{cluster_id}/public-access", params={"enable": enable}
        )
        return self._action(raw, cluster_id, ACTION_NEXT_STEP)

    async def update_kafka_cluster_config_group(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        config_group_version_id: str = Field(
            ...,
            description=(
                "Configuration group VERSION id ('cgroupver-…') from "
                "list_kafka_config_groups. Not a group id ('cgroup-…')."
            ),
        ),
    ) -> KafkaActionData:
        """Apply a configuration group version to a Kafka cluster.

        ## Requirements

        - `--allow-write` must be enabled.
        - The id is a **version**, `cgroupver-…`. A group id (`cgroup-…`) names
          a container of versions and is not what this endpoint wants.
        - Applying broker settings **restarts the brokers**, one after another.
          Say so before doing it: producers and consumers reconnect, and a
          cluster with a topic whose replication factor is 1 loses
          availability for that topic while its broker is down.
        - There is no detach: this endpoint only moves a cluster from one
          version to another.

        ## Workflow

        1. `list_kafka_config_groups` → the group, then the version inside it.
        2. `get_kafka_config_group` → the version's `properties`, so the user
           sees what is about to change.
        3. `get_kafka_cluster` → `config_group_version_id` in use now.
        4. Confirm with the user.
        5. This tool, then `get_kafka_cluster`, and
           `list_kafka_cluster_histories` if it looks unchanged.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(config_group_version_id, "config_group_version_id")
        raw = await self._call_scalar(
            "PUT",
            f"{BASE}/{cluster_id}/config-group",
            params={"configGroupVersionId": config_group_version_id},
        )
        return self._action(raw, cluster_id, ACTION_NEXT_STEP)

    async def create_kafka_cluster_secrule(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        spec: CreateKafkaSecurityRuleDto = Field(..., description="The rule to add"),
    ) -> KafkaSecurityRule:
        """Add one firewall rule to a Kafka cluster.

        ## Requirements

        - `--allow-write` must be enabled.
        - Rules are added **one at a time** here, unlike the other families'
          replace-the-whole-set endpoints — so this never silently drops an
          existing rule.
        - `port` must be one of the five the platform allows (9092, 9094,
          9096, 9194, 9196); `get_kafka_limits` publishes the list.
        - A rule allowing `0.0.0.0/0` exposes the broker port to the internet.
          Get explicit confirmation, and prefer the narrowest CIDR that works.

        ## Workflow

        1. `get_kafka_cluster` → the rules already in place (the only listing
           of them).
        2. `get_kafka_limits` → the allowed ports, if unsure.
        3. Confirm the CIDR with the user.
        4. This tool, then `get_kafka_cluster` to confirm.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        raw = await self._call(
            "POST",
            f"{BASE}/{cluster_id}/security-group-rules",
            json=spec.model_dump(exclude_none=True),
        )
        return KafkaSecurityRule.from_api(unwrap_wrapped(raw) or {})

    async def delete_kafka_cluster_secrule(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        secrule_id: str = Field(..., description="Rule ID from get_kafka_cluster"),
    ) -> KafkaActionData:
        """Remove one firewall rule from a Kafka cluster.

        ## Requirements

        - `--allow-write` must be enabled.
        - Every client connecting from that CIDR loses access the moment this
          applies. Name the rule's CIDR and port to the user before removing
          it.
        - Removing the last rule leaves the cluster unreachable from outside
          its subnet.

        ## Workflow

        1. `get_kafka_cluster` → the rule id, CIDR and port.
        2. Confirm with the user.
        3. This tool, then `get_kafka_cluster` to confirm.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(secrule_id, "secrule_id")
        raw = await self._call("DELETE", f"{BASE}/{cluster_id}/security-group-rules/{secrule_id}")
        return self._action(raw, cluster_id, SECRULE_NEXT_STEP, resource_id=secrule_id)

    async def delete_kafka_cluster(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
    ) -> KafkaActionData:
        """Delete a Kafka cluster and every broker. Irreversible.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm the cluster **by name and id** with the user first. There is
          no dry run and no undo.
        - Everything inside goes with it: topics and the messages they hold,
          users, their issued credentials, and the firewall rules. Kafka has
          no backups in vDB, so there is nothing to restore from.
        - A real `DELETE`, not a `POST /delete` — no action envelope anywhere
          in this family.

        ## Workflow

        1. `get_kafka_cluster` → confirm which cluster this is.
        2. `list_kafka_topics` → show the user what data is about to go.
        3. Confirm with the user, naming the cluster.
        4. This tool, then `list_kafka_clusters` to confirm it is gone.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        raw = await self._call_scalar("DELETE", f"{BASE}/{cluster_id}")
        return self._action(raw, cluster_id, DELETE_NEXT_STEP)
