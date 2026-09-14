"""Kafka user tools.

A Kafka user is a client principal with per-topic permissions and its own
issued credentials. Two things here exist nowhere else in vDB:

* **Permissions come as a list-or-flag pair**, four times over. When an
  ``*All`` flag is set the platform ignores the matching list, so a stored
  permission can be far wider than the list beside it suggests. The DTOs refuse
  to send both.
* **Credentials download as a ZIP archive of private keys**, not as the
  ``array of string`` the OpenAPI document describes. That is why
  ``get_kafka_user_credentials`` writes a file and reports its contents rather
  than returning anything, and why it is gated behind
  ``--allow-sensitive-data-access``.
"""

from __future__ import annotations

import io
import zipfile
from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import VDB_KAFKA_SERVICE, VdbConfig
from greennode.vdb_mcp_server.guards import require_sensitive_access, require_write
from greennode.vdb_mcp_server.models import (
    CreateKafkaUserDto,
    KafkaActionData,
    KafkaCredentialBundleData,
    KafkaUser,
    KafkaUserListData,
    UpdateKafkaUserDto,
)
from greennode.vdb_mcp_server.paging import as_list, unwrap_wrapped
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pathlib import Path
from pydantic import Field
from typing import Any


BASE = "/clusters"

CREDENTIAL_WARNING = (
    "This archive holds private keys, certificates and their passwords. Tell the user where "
    "the file is and what it contains; never read its contents back into the conversation, "
    "and never paste a password or key into chat."
)

USER_NEXT_STEP = (
    "The request was accepted, not applied -- measured, this endpoint answers with an EMPTY "
    "body. Poll get_kafka_user until status_kind is settled, and read "
    "list_kafka_cluster_histories if nothing changed."
)

CREDENTIAL_NEXT_STEP = (
    "New credentials have been issued and the previous ones no longer work. Download the new "
    "archive with get_kafka_user_credentials and give it to whoever runs the client."
)

DELETE_NEXT_STEP = (
    "The deletion was accepted. Confirm with list_kafka_users. Any client still using this "
    "user's credentials stops being able to connect."
)


class KafkaUserHandler:
    """Register and serve the Kafka user tools."""

    def __init__(
        self,
        mcp,
        config: VdbConfig,
        client: VdbClient,
        allow_write: bool,
        allow_sensitive_data_access: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write
        self.allow_sensitive_data_access = allow_sensitive_data_access

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_kafka_users", annotations=READ)(self.list_kafka_users)
        self.mcp.tool(name="get_kafka_user", annotations=READ)(self.get_kafka_user)

        # Registered only with the sensitive flag: the tool writes private keys
        # to disk, so a server that cannot do that should not advertise it.
        if self.allow_sensitive_data_access:
            self.mcp.tool(name="get_kafka_user_credentials", annotations=READ)(
                self.get_kafka_user_credentials
            )

        if self.allow_write:
            self.mcp.tool(name="create_kafka_user", annotations=WRITE)(self.create_kafka_user)
            self.mcp.tool(name="update_kafka_user", annotations=WRITE)(self.update_kafka_user)
            self.mcp.tool(name="update_kafka_user_credentials", annotations=DESTRUCTIVE)(
                self.update_kafka_user_credentials
            )
            self.mcp.tool(name="delete_kafka_user", annotations=DESTRUCTIVE)(
                self.delete_kafka_user
            )

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.client.call(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    async def _call_scalar(self, method: str, path: str, **kwargs: Any) -> Any:
        """For the endpoints whose success response is a bare word, not a document."""
        return await self.client.call_scalar(method, path, service=VDB_KAFKA_SERVICE, **kwargs)

    def _string_action(self, raw: Any, cluster_id: str, user_id: str, next_step: str):
        payload = unwrap_wrapped(raw)
        return KafkaActionData(
            cluster_id=cluster_id,
            resource_id=user_id,
            message="" if payload is None else str(payload),
            accepted=True,
            next_step=next_step,
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_kafka_users(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
    ) -> KafkaUserListData:
        """List the users on a Kafka cluster, with their permissions.

        Unpaginated and scoped to one cluster.

        **Read the `*_all` flags before the topic lists.** Each permission is a
        list *or* a flag, and when the flag is set the platform ignores the
        list — so a user showing `produce_all: true` next to two topic names
        can produce to every topic, not those two.

        A user reports `CREATING` while it is being made and settles to
        `ACTIVE`; `status_kind` says which.
        """
        validate_id(cluster_id, "cluster_id")
        raw = await self._call("GET", f"{BASE}/{cluster_id}/users")
        items = [KafkaUser.from_api(row) for row in as_list(raw)]
        return KafkaUserListData(cluster_id=cluster_id, count=len(items), items=items)

    async def get_kafka_user(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        user_id: str = Field(..., description="User ID ('user-…') from list_kafka_users"),
    ) -> KafkaUser:
        """Get one Kafka user and its permissions.

        Call this before `update_kafka_user`: that endpoint **replaces** the
        whole permission set, so anything not sent back is revoked.
        """
        validate_id(cluster_id, "cluster_id")
        validate_id(user_id, "user_id")
        raw = await self._call("GET", f"{BASE}/{cluster_id}/users/{user_id}")
        return KafkaUser.from_api(unwrap_wrapped(raw) or {})

    async def get_kafka_user_credentials(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        user_id: str = Field(..., description="User ID ('user-…') from list_kafka_users"),
        save_to: str = Field(
            ...,
            description=(
                "Absolute path of the .zip file to write. The archive is never returned "
                "through the conversation, only saved."
            ),
        ),
    ) -> KafkaCredentialBundleData:
        """Download a Kafka user's credentials to a file. Sensitive.

        Requires `--allow-sensitive-data-access`, because what comes back is a
        **ZIP archive of private keys, certificates and their passwords** — not
        the list of strings the OpenAPI document describes. The archive is
        written to `save_to` and this tool reports only its size and the names
        inside it.

        Hand the user the path and say what it contains. Do not open the file,
        do not summarise its contents, and never put a key or password into the
        conversation. If the user asks for the contents, explain that the file
        is on disk and that pasting it into a chat would expose the cluster.

        A user only has credentials for the mechanisms it was created with:
        `get_kafka_user` shows `mtls_authen` / `sasl_authen`.
        """
        require_sensitive_access(self.allow_sensitive_data_access)
        validate_id(cluster_id, "cluster_id")
        validate_id(user_id, "user_id")
        raw = await self.client.call_bytes(
            "GET",
            f"{BASE}/{cluster_id}/users/{user_id}/authen-creds",
            service=VDB_KAFKA_SERVICE,
        )
        destination = Path(save_to).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)

        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = [name for name in archive.namelist() if not name.endswith("/")]
        except zipfile.BadZipFile:
            # Reported rather than raised: the bytes are already on disk, and
            # the caller needs to know they are not the archive they expected.
            entries = []

        return KafkaCredentialBundleData(
            cluster_id=cluster_id,
            user_id=user_id,
            saved_to=str(destination),
            size_bytes=len(raw),
            entries=entries,
            warning=CREDENTIAL_WARNING,
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_kafka_user(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        spec: CreateKafkaUserDto = Field(..., description="The user to create"),
    ) -> KafkaUser:
        """Create a Kafka user with per-topic permissions. Free, but asynchronous.

        ## Requirements

        - `--allow-write` must be enabled.
        - **The user's authentication must match the cluster's.** Enabling
          `saslAuthen` on a cluster with SASL turned off issues credentials
          that cannot be used; read `get_kafka_cluster` first.
        - Grant the narrowest permission that works. Each kind is a list of
          topic names **or** an `*All` flag, never both — the DTO rejects the
          combination, because the platform would silently ignore the list and
          grant everything.
        - `adminAll` makes the user an administrator of every topic, including
          ones created later. Treat it as a decision for the user to make
          explicitly.
        - Topic names here are **names**, not ids, and a name that does not
          exist is not rejected — it simply grants nothing until a topic takes
          that name.
        - A cluster holds at most the `max_users` `get_kafka_limits` reports.

        ## Workflow

        1. `get_kafka_cluster` → which authentication mechanisms are on.
        2. `list_kafka_topics` → the exact topic names to grant over.
        3. Ask the user which permissions this client actually needs.
        4. This tool. It returns the created user directly, but with
           `status: CREATING` — **not** ready yet. Poll `get_kafka_user` until
           `status_kind` is `settled`.
        5. `get_kafka_user_credentials` to hand the client its keys — that
           needs `--allow-sensitive-data-access`.

        ## The cluster runs one operation at a time

        Measured: this call was rejected with a plain `400` while a topic on
        the same cluster was still being created. Kafka does not use the
        "Cannot perform action" wording the other families do, so a `400`
        immediately after another write is a **timing** problem rather than a
        bad payload — wait for the other resource to settle and retry.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        raw = await self._call(
            "POST", f"{BASE}/{cluster_id}/users", json=spec.model_dump(exclude_none=True)
        )
        return KafkaUser.from_api(unwrap_wrapped(raw) or {})

    async def update_kafka_user(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        user_id: str = Field(..., description="User ID ('user-…') from list_kafka_users"),
        spec: UpdateKafkaUserDto = Field(..., description="The COMPLETE desired permission set"),
    ) -> KafkaActionData:
        """Replace a Kafka user's permissions.

        ## Requirements

        - `--allow-write` must be enabled.
        - **This replaces the whole set.** Every field left at its default is
          sent as an empty list or false, so a permission not included is
          revoked. Read `get_kafka_user` first and send back everything that
          should survive.
        - The list-or-flag rule still applies: sending both for one kind is
          rejected here rather than silently widened by the platform.
        - Changing `mtlsAuthen` / `saslAuthen` changes which credentials the
          user can use — a client authenticating with a mechanism that is
          turned off here stops connecting.

        ## Workflow

        1. `get_kafka_user` → the current permissions, verbatim.
        2. Decide the full new set with the user.
        3. This tool, then poll `get_kafka_user` — the response here is an
           **empty body**, not the updated user, and the user passes through a
           transitional status before the new permissions are live.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(user_id, "user_id")
        raw = await self._call_scalar(
            "PUT",
            f"{BASE}/{cluster_id}/users/{user_id}",
            json=spec.model_dump(exclude_none=True),
        )
        return self._string_action(raw, cluster_id, user_id, USER_NEXT_STEP)

    async def update_kafka_user_credentials(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        user_id: str = Field(..., description="User ID ('user-…') from list_kafka_users"),
    ) -> KafkaActionData:
        """Re-issue a Kafka user's credentials, invalidating the old ones.

        ## Requirements

        - `--allow-write` must be enabled.
        - **Every client using the previous credentials stops connecting the
          moment this applies.** There is no grace period and no rollback.
          Confirm with the user, and plan who distributes the new archive
          before sending it.
        - Use it when credentials may have leaked, or on a rotation schedule —
          not to "refresh" a working client.

        ## Workflow

        1. `get_kafka_user` → confirm which user, and who uses it.
        2. Confirm with the user that the outage is acceptable.
        3. This tool.
        4. `get_kafka_user_credentials` to download the new archive (needs
           `--allow-sensitive-data-access`) and hand it over.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(user_id, "user_id")
        raw = await self._call_scalar(
            "PUT", f"{BASE}/{cluster_id}/users/{user_id}/regenerate-creds"
        )
        return self._string_action(raw, cluster_id, user_id, CREDENTIAL_NEXT_STEP)

    async def delete_kafka_user(
        self,
        cluster_id: str = Field(..., description="Cluster ID ('clus-…')"),
        user_id: str = Field(..., description="User ID ('user-…') from list_kafka_users"),
    ) -> KafkaActionData:
        """Delete a Kafka user and its credentials. Irreversible.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm the user **by name** first, and check who is connecting with
          it: every client using its credentials stops working immediately.
        - The topics it could reach are untouched — only the principal goes.

        ## Workflow

        1. `get_kafka_user` → confirm which user and what it could reach.
        2. Confirm with the user.
        3. This tool, then `list_kafka_users` to confirm it is gone.
        """
        require_write(self.allow_write)
        validate_id(cluster_id, "cluster_id")
        validate_id(user_id, "user_id")
        raw = await self._call_scalar("DELETE", f"{BASE}/{cluster_id}/users/{user_id}")
        return self._string_action(raw, cluster_id, user_id, DELETE_NEXT_STEP)
