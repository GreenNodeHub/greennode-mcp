"""SSH key management for the vServer MCP server.

An SSH key is injected into a server at creation and is the usual way to log
in. Mirrors the `grn vserver sshkey` command group.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreatedSshKeyData,
    CreateSshKeyDto,
    ImportSshKeyDto,
    SshKeyItem,
    SshKeyListData,
)
from greennode.vserver_mcp_server.paging import fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


PUBLIC_KEY_PREFIXES = (
    "ssh-rsa",
    "ssh-ed25519",
    "ssh-dss",
    "ecdsa-sha2-",
    "sk-ssh-",
    "sk-ecdsa-",
)


class SshKeyHandler:
    """Register and serve SSH key MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_ssh_keys", annotations=READ)(self.list_ssh_keys)
        self.mcp.tool(name="get_ssh_key", annotations=READ)(self.get_ssh_key)

        if self.allow_write:
            self.mcp.tool(name="create_ssh_key", annotations=WRITE)(self.create_ssh_key)
            self.mcp.tool(name="import_ssh_key", annotations=WRITE)(self.import_ssh_key)
            self.mcp.tool(name="delete_ssh_key", annotations=DESTRUCTIVE)(self.delete_ssh_key)

    async def list_ssh_keys(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the key name."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> SshKeyListData:
        """List the SSH keys registered in the project.

        Returns {region, ssh_keys[{id, name, public_key, status, created_at}]}.
        Only public keys are stored; a private key is never retrievable.

        ## Workflow
        - Part of the create_server flow: present the list and let the user
          choose. IMPORTANT: do NOT pick a key silently.
        - If the list is empty, the user has no key yet — offer create_ssh_key
          (server generates a new pair) or import_ssh_key (they already have
          one). Do not invent an id.
        - Use the chosen `id` as `sshKeyId` in create_server.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[SshKeyItem]:
            params = {"name": name_filter} if name_filter else None
            raw = await fetch_all_items(
                self.client, f"/v2/{pid}/sshKeys", region=region, params=params
            )
            return [SshKeyItem.from_api(k) for k in raw]

        key = ("list_ssh_keys", resolved_region, pid, name_filter)
        keys = await self.cache.get_or_fetch("list_ssh_keys", key, fetch, refresh)
        return SshKeyListData(region=resolved_region, ssh_keys=keys)

    async def get_ssh_key(
        self,
        ssh_key_id: str = Field(..., description="SSH key ID from list_ssh_keys."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SshKeyItem:
        """Get one SSH key by id, including its public key material."""
        validate_id(ssh_key_id, "ssh_key_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/sshKeys/{ssh_key_id}", region=region)
        return SshKeyItem.from_api(unwrap(data) or {})

    async def create_ssh_key(
        self,
        body: CreateSshKeyDto = Field(..., description="SSH key to generate."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CreatedSshKeyData:
        """Generate a new SSH key pair on the server side.

        ## Requirements
        - Requires `--allow-write`.
        - The **private key is returned exactly once**, in this response, and
          can never be retrieved again. Hand it to the user immediately and tell
          them to save it to a file with `chmod 600` permissions.
        - Do not write the private key to any shared location, log or artifact.

        ## Workflow
        - Prefer import_ssh_key when the user already has a key pair — nothing
          secret then has to travel through the conversation.
        - After creating, the key id can be used as `sshKeyId` in create_server.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/sshKeys", region=region, json=body.model_dump(exclude_none=True)
        )
        self.cache.invalidate("list_ssh_keys")
        return CreatedSshKeyData.from_api(unwrap(data) or {})

    async def import_ssh_key(
        self,
        body: ImportSshKeyDto = Field(..., description="Existing public key to register."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SshKeyItem:
        """Register an SSH public key the user already owns.

        ## Requirements
        - Requires `--allow-write`.
        - `pubKey` is the **public** half only, e.g. the contents of
          `~/.ssh/id_ed25519.pub`. Never ask the user for a private key.
        - It must start with one of ssh-rsa, ssh-ed25519, ssh-dss,
          ecdsa-sha2-, sk-ssh- or sk-ecdsa-.

        ## Workflow
        - This is the preferred way to add a key: the user keeps sole custody of
          the private half.
        """
        require_write(self.allow_write)
        public_key = body.pubKey.strip()
        if not public_key.startswith(PUBLIC_KEY_PREFIXES):
            raise ValueError(
                "pubKey does not look like an SSH public key: expected it to start with "
                f"one of {', '.join(PUBLIC_KEY_PREFIXES)}. Make sure this is the .pub "
                "file and not a private key."
            )
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["pubKey"] = public_key
        data = await self.client.post(f"/v2/{pid}/sshKeys/import", region=region, json=payload)
        self.cache.invalidate("list_ssh_keys")
        return SshKeyItem.from_api(unwrap(data) or {})

    async def delete_ssh_key(
        self,
        ssh_key_id: str = Field(..., description="SSH key ID from list_ssh_keys."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete an SSH key. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Servers already created with this key keep working: the key material
          lives in their `authorized_keys`. Deleting it only stops the key being
          used for **new** servers.

        ## Workflow
        - Show the user the key's id and name and get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(ssh_key_id, "ssh_key_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/sshKeys/{ssh_key_id}", region=region)
        self.cache.invalidate("list_ssh_keys")
        return f"SSH key {ssh_key_id} deleted."
