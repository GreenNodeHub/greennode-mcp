"""Listeners — the front door: what port the load balancer accepts on, and its TLS.

The update is a **full replacement**, not a patch: ``UpdateListenerRequest``
requires the three timeouts, and omitting ``certificateAuthorities`` or
``insertHeaders`` may clear them. ``update_listener`` therefore takes the whole
body and its docstring sends the caller to ``get_listener`` first, rather than
pretending individual fields can be poked.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import Region, VlbConfig
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.guards import require_write
from greennode.vlb_mcp_server.models import (
    CreatedResourceData,
    CreateListenerDto,
    ListenerItem,
    ListenerListData,
    UpdateListenerDto,
)
from greennode.vlb_mcp_server.paging import as_list, unwrap_one
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vlb_mcp_server.validators import validate_id, validate_lb_id
from pydantic import Field


class ListenerHandler:
    """Register and serve the listener MCP tools."""

    def __init__(
        self,
        mcp,
        config: VlbConfig,
        client: VlbClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_listeners", annotations=READ)(self.list_listeners)
        self.mcp.tool(name="get_listener", annotations=READ)(self.get_listener)

        if self.allow_write:
            self.mcp.tool(name="create_listener", annotations=WRITE)(self.create_listener)
            # DESTRUCTIVE, not WRITE: PUT .../listeners/{id} is a full
            # replacement, so a body that omits defaultPoolId and insertHeaders
            # clears both. Measured live — a listener serving a pool was left
            # with no pool at all by a body carrying only its three timeouts.
            self.mcp.tool(name="update_listener", annotations=DESTRUCTIVE)(self.update_listener)
            self.mcp.tool(name="delete_listener", annotations=DESTRUCTIVE)(self.delete_listener)

    async def _listeners_path(self, load_balancer_id: str, region: str | None) -> str:
        """Validate the load balancer id and build the listeners collection path."""
        validate_lb_id(load_balancer_id)
        pid = await require_project_id(self.config, self.client, region)
        return f"/v2/{pid}/loadBalancers/{load_balancer_id}/listeners"

    async def list_listeners(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ListenerListData:
        """List the listeners of a load balancer.

        Returns {load_balancer_id, listeners[{id, name, protocol, port,
        default_pool_id, display_status, ...}]}. Not cached: listeners are read
        while polling a write, where a stale answer is worse than a request.
        """
        path = await self._listeners_path(load_balancer_id, region)
        raw = await self.client.get(path, region=region)
        return ListenerListData(
            load_balancer_id=load_balancer_id,
            listeners=[ListenerItem.from_api(x) for x in as_list(raw) if isinstance(x, dict)],
        )

    async def get_listener(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ListenerItem:
        """Get one listener with its full configuration.

        Returns {id, name, protocol, port, timeouts, allowed_cidrs,
        blocked_cidrs, certificate_authorities, insert_headers,
        inserted_header_names, tls_security_policy, ...}.

        Read this before `update_listener`: that call replaces the whole
        configuration, so anything you do not resend may be cleared.
        """
        path = await self._listeners_path(load_balancer_id, region)
        validate_id(listener_id, "listener_id")
        data = await self.client.get(f"{path}/{listener_id}", region=region)
        return ListenerItem.from_api(unwrap_one(data))

    async def create_listener(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        body: CreateListenerDto = Field(..., description="Listener definition."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CreatedResourceData:
        """Create a listener on a load balancer.

        ## Requirements
        - Requires `--allow-write`.
        - The protocol must match the load balancer's type: HTTP/HTTPS need
          'Layer 7', TCP/UDP need 'Layer 4'.
        - An HTTPS listener needs `defaultCertificateAuthority` — import or pick
          one with `import_certificate` / `list_certificates` first.
        - `defaultPoolId` must already exist; create the pool with `create_pool`
          before the listener that forwards to it.
        - `insertHeaders[].headerName` must be one of `list_listener_headers`.
        - Name, protocol and port are permanent — `update_listener` changes none
          of them.

        ## Workflow
        - Poll `get_load_balancer_status` until `settled` before the next write.
        - If the returned `id` is empty the create still SUCCEEDED — confirm
          with `list_listeners` and never retry.
        """
        require_write(self.allow_write)
        path = await self._listeners_path(load_balancer_id, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(path, region=region, json=payload)
        return CreatedResourceData.from_api(data, "list_listeners")

    async def update_listener(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        body: UpdateListenerDto = Field(
            ...,
            description=(
                "The COMPLETE desired configuration. Fields you leave out may be "
                "cleared — read the current listener with get_listener first."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Replace a listener's configuration (timeouts, CIDRs, certificates, headers).

        ## Requirements
        - Requires `--allow-write`.
        - This is a **full replacement**, not a patch. Call `get_listener`
          first and resend every value you want to keep — especially
          `certificateAuthorities` and `insertHeaders`, which may be cleared by
          omission.
        - The three timeouts are required by the API.
        - Name, protocol and port cannot be changed; recreate the listener for
          that.
        - Changing `blockedCidrs` or `allowedCidrs` changes who can reach the
          service — confirm the intended list with the user.

        ## Workflow
        - Poll `get_load_balancer_status` until `settled`, then confirm with
          `get_listener`.
        """
        require_write(self.allow_write)
        path = await self._listeners_path(load_balancer_id, region)
        validate_id(listener_id, "listener_id")
        payload = body.model_dump(exclude_none=True)
        await self.client.put(f"{path}/{listener_id}", region=region, json=payload)
        return (
            f"Listener {listener_id} updated. Poll get_load_balancer_status until settled, "
            "then confirm with get_listener."
        )

    async def delete_listener(
        self,
        load_balancer_id: str = Field(..., description="Load balancer ID."),
        listener_id: str = Field(..., description="Listener ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a listener and its L7 policies.

        ## Requirements
        - Requires `--allow-write`.
        - Traffic on this port stops immediately. Confirm the port and name with
          the user.
        - The L7 policies attached to it go too; the pools it forwarded to
          remain.

        ## Workflow
        - The API answers 202; poll `list_listeners` to confirm it is gone.
        """
        require_write(self.allow_write)
        path = await self._listeners_path(load_balancer_id, region)
        validate_id(listener_id, "listener_id")
        await self.client.delete(f"{path}/{listener_id}", region=region)
        return (
            f"Listener {listener_id} deletion accepted. It is removed asynchronously — "
            "poll list_listeners to confirm."
        )
