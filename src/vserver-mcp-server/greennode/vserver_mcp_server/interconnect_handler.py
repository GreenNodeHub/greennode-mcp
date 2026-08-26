"""Interconnect management for the vServer MCP server.

An interconnect is a private circuit between GreenNode and somewhere else — an
on-premises site, another cloud, or the other GreenNode region — carrying
traffic over dedicated links instead of the public internet. Each circuit then
gets one *connection* per VPC it should reach.

These are long-lived, contracted, high-cost resources: every write tool here is
gated on `--allow-write` and expects explicit user confirmation.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateInterconnectConnectionDto,
    CreateInterconnectDto,
    InterconnectCatalogueItem,
    InterconnectCatalogueListData,
    InterconnectConnectionItem,
    InterconnectConnectionListData,
    InterconnectItem,
    InterconnectListData,
    PingResultData,
    UpdateInterconnectConnectionDto,
    UpdateInterconnectDto,
    UpdateInterconnectPackageDto,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field
from typing import Literal


class InterconnectHandler:
    """Register and serve interconnect MCP tools."""

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

        self.mcp.tool(name="list_interconnects", annotations=READ)(self.list_interconnects)
        self.mcp.tool(name="get_interconnect", annotations=READ)(self.get_interconnect)
        self.mcp.tool(name="list_interconnect_packages", annotations=READ)(
            self.list_interconnect_packages
        )
        self.mcp.tool(name="list_interconnect_circuit_types", annotations=READ)(
            self.list_interconnect_circuit_types
        )
        self.mcp.tool(name="list_interconnect_connections", annotations=READ)(
            self.list_interconnect_connections
        )
        self.mcp.tool(name="get_interconnect_connection", annotations=READ)(
            self.get_interconnect_connection
        )

        if self.allow_write:
            self.mcp.tool(name="create_interconnect", annotations=WRITE)(self.create_interconnect)
            self.mcp.tool(name="update_interconnect", annotations=WRITE)(self.update_interconnect)
            self.mcp.tool(name="update_interconnect_package", annotations=WRITE)(
                self.update_interconnect_package
            )
            self.mcp.tool(name="create_interconnect_connection", annotations=WRITE)(
                self.create_interconnect_connection
            )
            self.mcp.tool(name="update_interconnect_connection", annotations=WRITE)(
                self.update_interconnect_connection
            )
            self.mcp.tool(name="ping_interconnect", annotations=WRITE)(self.ping_interconnect)
            self.mcp.tool(name="delete_interconnect_connection", annotations=DESTRUCTIVE)(
                self.delete_interconnect_connection
            )
            self.mcp.tool(name="delete_interconnect", annotations=DESTRUCTIVE)(
                self.delete_interconnect
            )

    async def list_interconnects(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> InterconnectListData:
        """List the interconnect circuits in the project.

        Returns {region, interconnects[{id, name, status, type_id, type_name,
        package_id, circuit_id, enable_gw2, gw1_ip, gw2_ip, gw_vip,
        remote_gw1_ip, remote_gw2_ip, created_at}]}.

        `gw*_ip` are the GreenNode-side gateway addresses and `remote_gw*_ip`
        the customer-side ones — the pair the BGP/IPsec session runs between.
        `gw2_ip` is only populated when `enable_gw2` is on.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[InterconnectItem]:
            raw = await fetch_all_items(self.client, f"/v2/{pid}/interconnects", region=region)
            return [InterconnectItem.from_api(i) for i in raw]

        key = ("list_interconnects", resolved_region, pid)
        items = await self.cache.get_or_fetch("list_interconnects", key, fetch, refresh)
        return InterconnectListData(region=resolved_region, interconnects=items)

    async def get_interconnect(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectItem:
        """Get one interconnect circuit by id.

        Read `package_id` here before update_interconnect_package, and
        `enable_gw2` before update_interconnect.
        """
        validate_id(interconnect_id, "interconnect_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/interconnects/{interconnect_id}", region=region)
        return InterconnectItem.from_api(unwrap(data) or {})

    async def list_interconnect_packages(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> InterconnectCatalogueListData:
        """List the bandwidth packages an interconnect can be ordered with.

        Returns {region, items[{id, name, status, description}]} — ids look like
        `itp-1Gbps`. Pass the chosen `id` as `packageId` to create_interconnect
        or update_interconnect_package.

        ## Workflow
        - Step 1 of the create_interconnect flow. The package sets the circuit's
          committed bandwidth and therefore its price — always let the user pick.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[InterconnectCatalogueItem]:
            data = await self.client.get(f"/v2/{pid}/interconnects/packages", region=region)
            return [InterconnectCatalogueItem.from_api(p) for p in as_list(data)]

        key = ("list_interconnect_packages", resolved_region, pid)
        items = await self.cache.get_or_fetch("list_interconnect_packages", key, fetch, refresh)
        return InterconnectCatalogueListData(region=resolved_region, items=items)

    async def list_interconnect_circuit_types(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> InterconnectCatalogueListData:
        """List the circuit types an interconnect can be created with.

        Returns {region, items[{id, name, status, description}]}. Pass the
        chosen `id` as `typeId` to create_interconnect. Types map to the
        connectivity models — hybrid-cloud (on-premises), multi-cloud (another
        provider) and VPN.

        Note: this endpoint is permission-gated. A `403 IAM_PERMISSION_DENIED`
        means the caller's IAM policy lacks interconnect read rights, not that
        the catalogue is empty.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[InterconnectCatalogueItem]:
            data = await self.client.get(f"/v2/{pid}/interconnects/circuit-types", region=region)
            return [InterconnectCatalogueItem.from_api(t) for t in as_list(data)]

        key = ("list_interconnect_circuit_types", resolved_region, pid)
        items = await self.cache.get_or_fetch(
            "list_interconnect_circuit_types", key, fetch, refresh
        )
        return InterconnectCatalogueListData(region=resolved_region, items=items)

    async def list_interconnect_connections(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectConnectionListData:
        """List the VPC attachments on one interconnect circuit.

        Returns {region, interconnect_id, connections[{id, name, status,
        vpc_id, remote_subnets, created_at}]}. `remote_subnets` are the
        customer-side CIDRs routed over the circuit into that VPC.
        """
        validate_id(interconnect_id, "interconnect_id")
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/interconnects/{interconnect_id}/connections", region=region
        )
        return InterconnectConnectionListData(
            region=region or self.config.default_region,
            interconnect_id=interconnect_id,
            connections=[InterconnectConnectionItem.from_api(c) for c in raw],
        )

    async def get_interconnect_connection(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        connection_id: str = Field(
            ..., description="Connection ID from list_interconnect_connections."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectConnectionItem:
        """Get one VPC attachment of an interconnect by id.

        Read `remote_subnets` here before update_interconnect_connection, which
        replaces the whole list rather than adding to it.
        """
        validate_id(interconnect_id, "interconnect_id")
        validate_id(connection_id, "connection_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/interconnects/{interconnect_id}/connections/{connection_id}",
            region=region,
        )
        return InterconnectConnectionItem.from_api(unwrap(data) or {})

    async def create_interconnect(
        self,
        body: CreateInterconnectDto = Field(..., description="Interconnect circuit to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectItem:
        """Create an interconnect circuit.

        ## Requirements
        - Requires `--allow-write`. An interconnect is a **contracted, monthly
          billed** circuit — never create one without explicit user approval of
          the package and the redundancy option.
        - `typeId` comes from list_interconnect_circuit_types and `packageId`
          from list_interconnect_packages.
        - `enableGw2` provisions a second gateway for redundancy and **raises
          the cost**; leave it off unless the user asks.
        - Provisioning also needs physical cross-connect work at a GreenNode
          Direct Connect site — the circuit does not carry traffic on creation.

        ## Workflow
        - List packages and circuit types, present both with their prices/limits,
          and let the user choose. Do not default either.
        - After creation, attach VPCs with create_interconnect_connection, then
          add routes for the remote CIDRs with update_route_table_routes.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/interconnects", region=region, json=payload)
        self.cache.invalidate("list_interconnects")
        return InterconnectItem.from_api(unwrap(data) or {})

    async def update_interconnect(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        body: UpdateInterconnectDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectItem:
        """Update an interconnect's description, tags or gateway-2 redundancy.

        ## Requirements
        - Requires `--allow-write`.
        - Turning `enableGw2` **on** provisions a second gateway and raises the
          monthly cost; turning it **off** removes the redundant path, so a
          failure of gateway 1 then takes the circuit down.
        - The circuit's package is changed with update_interconnect_package,
          not here.

        ## Workflow
        - Call get_interconnect first and show the user the current `enable_gw2`
          before flipping it.
        """
        require_write(self.allow_write)
        validate_id(interconnect_id, "interconnect_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v2/{pid}/interconnects/{interconnect_id}", region=region, json=payload
        )
        self.cache.invalidate("list_interconnects")
        return InterconnectItem.from_api(unwrap(data) or {})

    async def update_interconnect_package(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        body: UpdateInterconnectPackageDto = Field(..., description="New bandwidth package."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectItem:
        """Change the bandwidth package of an interconnect circuit.

        ## Requirements
        - Requires `--allow-write`. The package sets the committed bandwidth and
          the monthly price — this **changes what the circuit costs**.
        - `packageId` must come from list_interconnect_packages.
        - Downgrading caps throughput as soon as it applies; traffic above the
          new package is dropped.

        ## Workflow
        - Show the user the current package (get_interconnect) and the target
          package with its bandwidth, and get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(interconnect_id, "interconnect_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v2/{pid}/interconnects/{interconnect_id}/change-package",
            region=region,
            json=payload,
        )
        self.cache.invalidate("list_interconnects")
        return InterconnectItem.from_api(unwrap(data) or {})

    async def create_interconnect_connection(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        body: CreateInterconnectConnectionDto = Field(
            ..., description="VPC attachment to create on the circuit."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectConnectionItem:
        """Attach a VPC to an interconnect circuit.

        ## Requirements
        - Requires `--allow-write`.
        - `networkId` is a VPC id from list_vpcs.
        - `subnets` are the **customer-side** CIDRs reachable over the circuit.
          They must not overlap the VPC's own CIDR, or traffic to those
          addresses stays inside the VPC and never crosses the circuit.

        ## Workflow
        - Ask the user for the remote CIDRs; do not guess them from the VPC.
        - After the connection is ACTIVE, add matching routes with
          update_route_table_routes — attaching alone does not steer traffic.
        - Verify end-to-end with ping_interconnect.
        """
        require_write(self.allow_write)
        validate_id(interconnect_id, "interconnect_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(
            f"/v2/{pid}/interconnects/{interconnect_id}/connections", region=region, json=payload
        )
        return InterconnectConnectionItem.from_api(unwrap(data) or {})

    async def update_interconnect_connection(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        connection_id: str = Field(
            ..., description="Connection ID from list_interconnect_connections."
        ),
        body: UpdateInterconnectConnectionDto = Field(
            ..., description="The complete set of remote subnets for this connection."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> InterconnectConnectionItem:
        """Replace the remote subnets of an interconnect connection.

        ## Requirements
        - Requires `--allow-write`.
        - This is a **full replacement**: a CIDR missing from `subnets` stops
          being routed over the circuit. Call get_interconnect_connection first
          and resend the CIDRs you want to keep.
        - Removing a CIDR cuts live traffic to that remote network.

        ## Workflow
        - Show the user the current and proposed CIDR lists side by side, and
          get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(interconnect_id, "interconnect_id")
        validate_id(connection_id, "connection_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.put(
            f"/v2/{pid}/interconnects/{interconnect_id}/connections/{connection_id}",
            region=region,
            json=payload,
        )
        return InterconnectConnectionItem.from_api(unwrap(data) or {})

    async def ping_interconnect(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        gateway_number: Literal[1, 2] = Field(
            1, description="Which gateway to test — 2 only works when enable_gw2 is on."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> PingResultData:
        """Test reachability of an interconnect's remote gateway.

        ## Requirements
        - Requires `--allow-write` because the API models the test as a PUT.
          It is a **diagnostic only** — it changes no configuration.
        - `gateway_number=2` fails on a circuit whose `enable_gw2` is off.

        ## Workflow
        - Run this after create_interconnect_connection to tell a routing
          mistake apart from a circuit that is not up yet.
        """
        require_write(self.allow_write)
        validate_id(interconnect_id, "interconnect_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/interconnects/{interconnect_id}/ping",
            region=region,
            json={"gwNumber": gateway_number},
        )
        payload = unwrap(data)
        reachable = None
        detail = ""
        if isinstance(payload, bool):
            reachable = payload
        elif isinstance(payload, dict):
            for key in ("success", "reachable", "result", "status"):
                if isinstance(payload.get(key), bool):
                    reachable = payload[key]
                    break
            detail = str(payload)
        elif payload is not None:
            detail = str(payload)
        return PingResultData(
            interconnect_id=interconnect_id,
            gateway_number=gateway_number,
            reachable=reachable,
            detail=detail,
        )

    async def delete_interconnect_connection(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        connection_id: str = Field(
            ..., description="Connection ID from list_interconnect_connections."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Detach a VPC from an interconnect circuit. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Private traffic between that VPC and the remote networks stops
          immediately.
        - The circuit itself, and its billing, are unaffected — use
          delete_interconnect to remove the circuit.

        ## Workflow
        - Show the user the VPC and the remote CIDRs that lose connectivity, and
          get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(interconnect_id, "interconnect_id")
        validate_id(connection_id, "connection_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/interconnects/{interconnect_id}/connections/{connection_id}",
            region=region,
        )
        return f"Interconnect connection {connection_id} deleted."

    async def delete_interconnect(
        self,
        interconnect_id: str = Field(..., description="Interconnect ID from list_interconnects."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete an interconnect circuit. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Detach every VPC first with delete_interconnect_connection; the API
          rejects a circuit that still has connections.
        - Re-creating the circuit means new physical cross-connect work — this
          is not a same-day undo.

        ## Workflow
        - List the connections, show the user everything that loses private
          connectivity, and get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(interconnect_id, "interconnect_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/interconnects/{interconnect_id}", region=region)
        self.cache.invalidate("list_interconnects")
        return f"Interconnect {interconnect_id} deleted."
