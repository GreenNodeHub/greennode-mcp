"""Server (virtual machine) management for the vServer MCP server.

Covers the instance lifecycle — create, power, resize, rename, delete — plus
the network interfaces and floating IPs attached to it, mirroring the
`grn vserver server` command group.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    AttachInternalInterfaceDto,
    ConsoleLogData,
    ConsoleUrlData,
    CreateServerDto,
    CreateServerImageDto,
    DetachInternalInterfacesDto,
    NetworkInterfaceItem,
    RenameServerDto,
    ResizeServerDto,
    SecurityGroupItem,
    SecurityGroupRuleItem,
    ServerActionItem,
    ServerActionListData,
    ServerInterfacesData,
    ServerItem,
    ServerListData,
    ServerSecurityData,
    UpdateServerSecurityGroupsDto,
    UserImageItem,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


CREATED_FROM_NEW = "NEW"


class ServerHandler:
    """Register and serve server MCP tools."""

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

        self.mcp.tool(name="list_servers", annotations=READ)(self.list_servers)
        self.mcp.tool(name="get_server", annotations=READ)(self.get_server)
        self.mcp.tool(name="list_server_interfaces", annotations=READ)(self.list_server_interfaces)
        self.mcp.tool(name="list_server_security_groups", annotations=READ)(
            self.list_server_security_groups
        )
        self.mcp.tool(name="get_server_console_url", annotations=READ)(self.get_server_console_url)
        self.mcp.tool(name="get_server_console_log", annotations=READ)(self.get_server_console_log)
        self.mcp.tool(name="list_server_actions", annotations=READ)(self.list_server_actions)
        self.mcp.tool(name="list_subnet_servers", annotations=READ)(self.list_subnet_servers)
        self.mcp.tool(name="get_server_external_interface", annotations=READ)(
            self.get_server_external_interface
        )

        if self.allow_write:
            self.mcp.tool(name="create_server", annotations=WRITE)(self.create_server)
            self.mcp.tool(name="start_server", annotations=WRITE)(self.start_server)
            self.mcp.tool(name="stop_server", annotations=WRITE)(self.stop_server)
            self.mcp.tool(name="reboot_server", annotations=WRITE)(self.reboot_server)
            self.mcp.tool(name="resize_server", annotations=WRITE)(self.resize_server)
            self.mcp.tool(name="rename_server", annotations=WRITE)(self.rename_server)
            self.mcp.tool(name="update_server_security_groups", annotations=WRITE)(
                self.update_server_security_groups
            )
            self.mcp.tool(name="create_server_image", annotations=WRITE)(self.create_server_image)
            self.mcp.tool(name="attach_server_internal_interface", annotations=WRITE)(
                self.attach_server_internal_interface
            )
            self.mcp.tool(name="attach_server_internal_interface_floating_ip", annotations=WRITE)(
                self.attach_server_internal_interface_floating_ip
            )
            self.mcp.tool(
                name="detach_server_internal_interface_floating_ip", annotations=DESTRUCTIVE
            )(self.detach_server_internal_interface_floating_ip)
            self.mcp.tool(name="detach_server_internal_interfaces", annotations=DESTRUCTIVE)(
                self.detach_server_internal_interfaces
            )
            self.mcp.tool(name="attach_server_external_interface", annotations=WRITE)(
                self.attach_server_external_interface
            )
            self.mcp.tool(name="detach_server_external_interface", annotations=DESTRUCTIVE)(
                self.detach_server_external_interface
            )
            self.mcp.tool(name="attach_server_floating_ip", annotations=WRITE)(
                self.attach_server_floating_ip
            )
            self.mcp.tool(name="detach_server_floating_ip", annotations=DESTRUCTIVE)(
                self.detach_server_floating_ip
            )
            self.mcp.tool(name="delete_server", annotations=DESTRUCTIVE)(self.delete_server)

    async def list_servers(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the server name."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerListData:
        """List the vServer instances in the project.

        Returns {region, servers[{id, name, status, private_ip, public_ip,
        zone_id, flavor_id, image_id, boot_volume_id, created_at}]}.

        Servers are not cached: status and IPs change as the user acts, so a
        stale list would mislead. `status` is ACTIVE for a running instance and
        STOPPED for a stopped one.

        The list endpoint leaves `boot_volume_id` empty for every server — only
        get_server fills it in. An empty value here means "not reported", never
        "no root disk".

        ## Workflow
        - When rendering to the user, show `id` and `name` first — every other
          server tool needs the id.
        - A server without `public_ip` is unreachable from the internet; attach
          a floating IP with attach_server_floating_ip if that is wanted.
        """
        pid = await require_project_id(self.config, self.client, region)
        params = {"name": name_filter} if name_filter else None
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/servers", region=region, params=params
        )
        return ServerListData(
            region=region or self.config.default_region,
            servers=[ServerItem.from_api(s) for s in raw],
        )

    async def get_server(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Get one server by id.

        Use it to poll `status` after create_server, start_server, stop_server
        or resize_server, and to read the server's zone before creating a
        volume that must attach to it.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/servers/{server_id}", region=region)
        return ServerItem.from_api(unwrap(data) or {})

    async def list_server_interfaces(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerInterfacesData:
        """List the network interfaces attached to a server.

        Returns {server_id, internal_interfaces[], external_interfaces[]} —
        internal ones are the private NICs on the server's subnets, external
        ones are elastic interfaces carrying public connectivity.

        ## Workflow
        - Call this before attach_server_floating_ip: that tool needs the id of
          the interface the floating IP should bind to.
        - Call it before detach_server_internal_interfaces to show the user
          exactly which NICs would be removed.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/servers/{server_id}/network-interfaces", region=region
        )
        payload = unwrap(data)
        payload = payload if isinstance(payload, dict) else {}
        return ServerInterfacesData(
            server_id=server_id,
            internal_interfaces=[
                NetworkInterfaceItem.from_api(i) for i in as_list(payload, "internalInterfaces")
            ],
            external_interfaces=[
                NetworkInterfaceItem.from_api(i) for i in as_list(payload, "externalInterfaces")
            ],
        )

    async def list_server_security_groups(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerSecurityData:
        """List the security groups and effective firewall rules of a server.

        The API answers this with the server's rules split into `inbounds` and
        `outbounds`, each tagged with the name of the group it came from, not
        with a list of groups. The owning groups are resolved here by matching
        those names against list_security_groups, so `security_groups` carries
        the ids other tools need.

        Returns {server_id, security_groups, unresolved_group_names,
        inbound_rules, outbound_rules}. A non-empty `unresolved_group_names`
        means a rule referenced a group this project cannot see.

        ## Workflow
        - Always call this before update_server_security_groups: that tool
          replaces the whole set, so you need the current ids to add or remove
          one group without dropping the others.
        - The rules tell you what is actually open; use them to answer "why can
          I not reach this server".
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/servers/{server_id}/sec-groups", region=region)
        payload = unwrap(data)
        payload = payload if isinstance(payload, dict) else {}

        inbound_raw = as_list(payload, "inbounds")
        outbound_raw = as_list(payload, "outbounds")
        names = {
            rule.get("secGroupName")
            for rule in (*inbound_raw, *outbound_raw)
            if isinstance(rule, dict) and rule.get("secGroupName")
        }

        groups: list[SecurityGroupItem] = []
        unresolved = sorted(names)
        if names:
            all_groups = await fetch_all_items(self.client, f"/v2/{pid}/secgroups", region=region)
            by_name = {g.get("name"): g for g in all_groups if isinstance(g, dict)}
            groups = [
                SecurityGroupItem.from_api(by_name[n]) for n in sorted(names) if n in by_name
            ]
            unresolved = sorted(n for n in names if n not in by_name)

        return ServerSecurityData(
            server_id=server_id,
            security_groups=groups,
            unresolved_group_names=unresolved,
            inbound_rules=[SecurityGroupRuleItem.from_api(r) for r in inbound_raw],
            outbound_rules=[SecurityGroupRuleItem.from_api(r) for r in outbound_raw],
        )

    async def get_server_console_url(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ConsoleUrlData:
        """Get a browser VNC console URL for a server.

        Returns {server_id, url}. The URL is time-limited and grants direct
        console access to the instance — hand it to the user, do not log it or
        paste it anywhere shared.

        ## Workflow
        - Useful when a server is unreachable over SSH: the console still works
          when networking or the firewall is misconfigured.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/servers/{server_id}/console-url", region=region)
        payload = data.get("data") if isinstance(data, dict) else data
        if isinstance(payload, str):
            url = payload
        elif isinstance(payload, dict):
            url = payload.get("url") or payload.get("consoleUrl") or ""
        else:
            url = ""
        return ConsoleUrlData(server_id=server_id, url=url)

    async def get_server_console_log(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        lines: int = Field(
            200,
            ge=1,
            le=5000,
            description="Keep only the last N lines. The API returns the whole log.",
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ConsoleLogData:
        """Read a server's serial-console output.

        Returns {server_id, log, truncated}. The log starts at firmware boot, so
        it shows kernel panics, filesystem errors and cloud-init failures that
        never reach SSH or any in-guest log.

        ## Workflow
        - This is the first thing to read when a server is ACTIVE but
          unreachable — it separates "the OS did not boot" from "the network is
          wrong".
        - The output can be tens of thousands of characters; `lines` keeps the
          tail, which is where boot failures land. Raise it only if the user
          needs earlier output.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/servers/{server_id}/console-log", region=region)
        payload = data.get("data") if isinstance(data, dict) else data
        text = payload if isinstance(payload, str) else ""
        all_lines = text.splitlines()
        truncated = len(all_lines) > lines
        return ConsoleLogData(
            server_id=server_id,
            log="\n".join(all_lines[-lines:]) if truncated else text,
            truncated=truncated,
        )

    async def list_subnet_servers(
        self,
        subnet_id: str = Field(..., description="Subnet ID from list_subnets."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerListData:
        """List the servers that have an interface in one subnet.

        Same shape as list_servers, narrowed to a subnet.

        ## Workflow
        - Call this before delete_subnet: the API refuses to delete a subnet
          that still has instances, and this shows exactly which ones.
        - Also the fastest way to see the blast radius of a network ACL before
          associating it with the subnet.
        """
        validate_id(subnet_id, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/servers/subnets/{subnet_id}", region=region)
        return ServerListData(
            region=region or self.config.default_region,
            servers=[ServerItem.from_api(s) for s in as_list(data)],
        )

    async def get_server_external_interface(
        self,
        network_interface_id: str = Field(
            ..., description="Elastic interface ID from list_server_interfaces."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> NetworkInterfaceItem:
        """Get one attached external (elastic) network interface by id.

        Use it to confirm which server an elastic interface is currently bound
        to before detach_server_external_interface moves it somewhere else.
        """
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/servers/external-network-interfaces/{network_interface_id}", region=region
        )
        return NetworkInterfaceItem.from_api(unwrap(data) or {})

    async def list_server_actions(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerActionListData:
        """List the recent actions performed on a server.

        Returns {server_id, actions[{action, started_at, source}]} — the audit
        trail of creates, resizes, reboots and migrations, and whether each came
        from the API or the console.

        ## Workflow
        - Use it to explain an unexpected state: a server that is STOPPED may
          simply have been stopped from the console.
        """
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/servers/{server_id}/actions", region=region)
        return ServerActionListData(
            server_id=server_id,
            actions=[ServerActionItem.from_api(a) for a in as_list(data)],
        )

    async def create_server(
        self,
        body: CreateServerDto = Field(..., description="Server to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Create a vServer instance.

        ## Requirements
        - Requires `--allow-write`. This provisions a **billable** instance.
        - Every id must come from a discovery tool, never be invented:
          `zoneId` from list_zones, `networkId` from list_vpcs, `subnetId` from
          list_subnets (must belong to that VPC), `imageId` from list_images,
          `flavorId` from list_flavors, `rootDiskTypeId` from list_volume_types.
        - The subnet's zone and `zoneId` must agree — take the zone from the
          chosen subnet rather than asking twice.
        - The image's `image_type` must appear in the flavor's
          `supported_image_types`; the API rejects a mismatched pair.
        - `rootDiskSize` is at least 20 GiB and must fit the volume type's
          min/max bounds.
        - Provide `sshKeyId`, or `userName` plus `userPassword` — without either
          there is no way to log in.
        - When `imageId` is a **user image** (from list_user_images), ask the
          user for `userData` before creating. A captured image boots as a clone
          of the machine it came from — its accounts, hostname and services come
          with it — so the first-boot script is what adapts the copy. Accept
          pasted content or read the file the user points at; never invent a
          script and never quietly send none.
        - Billing options (period, auto-renew, PoC, OS licence), backup and
          snapshot restore are not settable here by design; use the console.

        ## Workflow
        - Call `get_feature_guide(feature="create_server")` first — it carries
          the discovery chain, the questions to ask and the confirm gate.
        - `attachFloating=true` exposes the server to the internet; say so and
          make sure the security groups only open the ports the user wants.
        - `userData` is dispatched by its first line and runs as root on first
          boot: read it back to the user before creating, send it as plain text
          with `userDataBase64Encoded=false`, and set that flag only when the
          user supplies a string that is already encoded.
        - The server starts in CREATING; poll get_server until ACTIVE.
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["createdFrom"] = CREATED_FROM_NEW
        data = await self.client.post(f"/v2/{pid}/servers", region=region, json=payload)
        return ServerItem.from_api(unwrap(data) or {})

    async def start_server(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Start a stopped server.

        ## Requirements
        - Requires `--allow-write`.
        - The server must be in STOPPED; starting an already-running server is
          rejected.

        ## Workflow
        - The call returns immediately; poll get_server until `status` is ACTIVE.
        - A stopped instance is generally still billed for its storage, so
          starting it does not change the disk cost.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(f"/v2/{pid}/servers/{server_id}/start", region=region)
        return ServerItem.from_api(unwrap(data) or {})

    async def stop_server(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Stop a running server.

        ## Requirements
        - Requires `--allow-write`.
        - This is an abrupt power-off from the platform's perspective: shut the
          guest OS down cleanly first if the workload needs it.

        ## Workflow
        - Confirm with the user before stopping anything that serves traffic.
        - Poll get_server until `status` is STOPPED.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(f"/v2/{pid}/servers/{server_id}/stop", region=region)
        return ServerItem.from_api(unwrap(data) or {})

    async def reboot_server(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Reboot a running server.

        ## Requirements
        - Requires `--allow-write`.
        - The instance is unavailable for the duration of the restart.

        ## Workflow
        - Confirm with the user before rebooting anything that serves traffic.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(f"/v2/{pid}/servers/{server_id}/reboot", region=region)
        return ServerItem.from_api(unwrap(data) or {})

    async def resize_server(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: ResizeServerDto = Field(..., description="Target flavor."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Change a server's flavor (vCPU and RAM).

        ## Requirements
        - Requires `--allow-write`.
        - `flavorId` must come from list_flavors for the **server's own zone**;
          a flavor from another zone cannot be applied.
        - Resizing restarts the instance and **changes what it costs**.

        ## Workflow
        - Show the user the current and target vCPU/RAM and the fact that the
          server will restart, then get explicit confirmation.
        - Poll get_server until the status settles.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["serverId"] = server_id
        data = await self.client.put(
            f"/v2/{pid}/servers/{server_id}/resize", region=region, json=payload
        )
        return ServerItem.from_api(unwrap(data) or {})

    async def rename_server(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: RenameServerDto = Field(..., description="New name."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Rename a server.

        ## Requirements
        - Requires `--allow-write`.
        - Renaming only changes the display name; it does not touch the guest
          OS hostname.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.put(
            f"/v2/{pid}/servers/{server_id}/rename",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return ServerItem.from_api(unwrap(data) or {})

    async def update_server_security_groups(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: UpdateServerSecurityGroupsDto = Field(
            ..., description="Complete replacement set of security groups."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerItem:
        """Replace the security groups attached to a server.

        ## Requirements
        - Requires `--allow-write`.
        - `securityGroup` is a **full replacement**: any group missing from the
          list is detached. Call list_server_security_groups first and send the
          current ids plus your change, or you will silently strip the server's
          firewall rules.

        ## Workflow
        - Show the user the before and after sets and get confirmation —
          detaching the wrong group can cut off SSH.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        for group_id in body.securityGroup:
            validate_id(group_id, "security_group_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["serverId"] = server_id
        data = await self.client.put(
            f"/v2/{pid}/servers/{server_id}/update-sec-group", region=region, json=payload
        )
        return ServerItem.from_api(unwrap(data) or {})

    async def create_server_image(
        self,
        server_id: str = Field(..., description="Server to capture."),
        body: CreateServerImageDto = Field(..., description="Image to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> UserImageItem:
        """Capture a server as a reusable user image.

        ## Requirements
        - Requires `--allow-write`.
        - The resulting image consumes storage and is **billable** until it is
          deleted with delete_user_image.
        - Capture a stopped server when the workload needs a consistent disk
          state; a live capture may catch a partially written filesystem.

        ## Workflow
        - Confirm the image name and the storage cost with the user.
        - The image appears in list_user_images and its id can then be used as
          `imageId` in create_server.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.post(
            f"/v2/{pid}/user-images/servers/{server_id}",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return UserImageItem.from_api(unwrap(data) or {})

    async def attach_server_internal_interface(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: AttachInternalInterfaceDto = Field(..., description="Interfaces to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerInterfacesData:
        """Attach new internal network interfaces to a server.

        ## Requirements
        - Requires `--allow-write`.
        - Each `subnetId` must be an ACTIVE subnet in the server's own zone.
        - Leave `ip` unset to let the platform assign one; a requested IP must
          be free and inside the subnet's CIDR.

        ## Workflow
        - The guest OS usually needs to bring the new NIC up before it is
          usable; tell the user that.
        - Confirm the result with list_server_interfaces.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        for request in body.subnetRequests:
            validate_id(request.subnetId, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.post(
            f"/v2/{pid}/servers/{server_id}/internal-network-interfaces",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return await self.list_server_interfaces(server_id=server_id, region=region)

    async def attach_server_internal_interface_floating_ip(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: AttachInternalInterfaceDto = Field(
            ..., description="Interfaces to create, each with a floating IP attached."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerInterfacesData:
        """Attach internal network interfaces that each come with a floating IP.

        ## Requirements
        - Requires `--allow-write`. Each interface consumes a **floating IP**
          from the project's quota and is billable.
        - Each `subnetId` must be an ACTIVE subnet in the server's own zone.
        - The instance becomes reachable from the **internet** on the new
          address, so its security group has to be right first — check it with
          list_server_security_groups.

        ## Workflow
        - Prefer plain attach_server_internal_interface plus
          attach_server_floating_ip when the two steps should be reviewed
          separately; use this when the user wants a public NIC in one go.
        - Confirm with list_server_interfaces afterwards.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        for request in body.subnetRequests:
            validate_id(request.subnetId, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.post(
            f"/v2/{pid}/servers/{server_id}/internal-network-interfaces-floating",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return await self.list_server_interfaces(server_id=server_id, region=region)

    async def detach_server_internal_interface_floating_ip(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: DetachInternalInterfacesDto = Field(..., description="Interfaces to detach."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerInterfacesData:
        """Detach internal interfaces together with their floating IPs.

        ## Requirements
        - Requires `--allow-write`.
        - Both the NIC and its public address go: anything reaching the server
          on that address stops immediately, and the address returns to the pool
          — you will not get the same one back.
        - Detaching the NIC that carries the primary private IP also cuts SSH.

        ## Workflow
        - Call list_server_interfaces first, show the user the private and
          public IPs that would disappear, and get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        for interface_id in body.networkInterfaceIds:
            validate_id(interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete_with_body(
            f"/v2/{pid}/servers/{server_id}/internal-network-interfaces-floating",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return await self.list_server_interfaces(server_id=server_id, region=region)

    async def detach_server_internal_interfaces(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        body: DetachInternalInterfacesDto = Field(..., description="Interfaces to detach."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerInterfacesData:
        """Detach internal network interfaces from a server. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Detaching the interface that carries the server's primary private IP
          cuts its connectivity, including SSH.

        ## Workflow
        - Call list_server_interfaces first, show the user which NIC and which
          IP would go, and get explicit confirmation.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        for interface_id in body.networkInterfaceIds:
            validate_id(interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete_with_body(
            f"/v2/{pid}/servers/{server_id}/internal-network-interfaces",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        return await self.list_server_interfaces(server_id=server_id, region=region)

    async def attach_server_external_interface(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        network_interface_id: str = Field(
            ..., description="Elastic interface ID from list_network_interfaces."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerInterfacesData:
        """Attach an existing elastic network interface to a server.

        ## Requirements
        - Requires `--allow-write`.
        - The interface must be free (`server_id` empty in
          list_network_interfaces) and in the server's zone.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.post(
            f"/v2/{pid}/servers/{server_id}/external-network-interfaces",
            region=region,
            json={"externalNetworkInterfaceId": network_interface_id},
        )
        return await self.list_server_interfaces(server_id=server_id, region=region)

    async def detach_server_external_interface(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        network_interface_id: str = Field(
            ..., description="Elastic interface ID from list_server_interfaces."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ServerInterfacesData:
        """Detach an elastic network interface from a server.

        ## Requirements
        - Requires `--allow-write`.
        - This removes the public connectivity the interface carried; the
          interface itself survives and can be attached elsewhere.

        ## Workflow
        - Confirm with the user, then verify with list_server_interfaces.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete_with_body(
            f"/v2/{pid}/servers/{server_id}/external-network-interfaces",
            region=region,
            json={"networkInterfaceId": network_interface_id},
        )
        return await self.list_server_interfaces(server_id=server_id, region=region)

    async def attach_server_floating_ip(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        floating_ip_id: str = Field(..., description="Floating IP ID from list_floating_ips."),
        network_interface_id: str = Field(
            ...,
            description="Interface the IP binds to, from list_server_interfaces.",
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Attach a floating (public) IP to one of a server's interfaces.

        ## Requirements
        - Requires `--allow-write`.
        - The floating IP must be AVAILABLE in list_floating_ips; one that is
          already ATTACHED must be detached first.
        - This makes the server **reachable from the internet** on whatever
          ports its security groups allow.

        ## Workflow
        - Call list_server_interfaces for the interface id and
          list_floating_ips for a free address.
        - Check the server's security groups before exposing it, and confirm
          with the user.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        validate_id(floating_ip_id, "floating_ip_id")
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.put(
            f"/v2/{pid}/servers/{server_id}/wan-ips/{floating_ip_id}/attach",
            region=region,
            json={"networkInterfaceId": network_interface_id},
        )
        return f"Floating IP {floating_ip_id} attached to server {server_id}."

    async def detach_server_floating_ip(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        floating_ip_id: str = Field(..., description="Floating IP ID currently attached."),
        network_interface_id: str = Field(
            ..., description="Interface the IP is bound to, from list_server_interfaces."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Detach a floating IP from a server's interface.

        ## Requirements
        - Requires `--allow-write`.
        - The server loses its public address immediately; anything connecting
          over that IP, including SSH sessions, breaks.
        - The IP itself is kept and returns to AVAILABLE — delete it separately
          with delete_floating_ip if it is no longer wanted, since an unused
          floating IP is still billable.

        ## Workflow
        - Confirm with the user before removing public access.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        validate_id(floating_ip_id, "floating_ip_id")
        validate_id(network_interface_id, "network_interface_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.put(
            f"/v2/{pid}/servers/{server_id}/wan-ips/{floating_ip_id}/detach",
            region=region,
            json={"networkInterfaceId": network_interface_id},
        )
        return f"Floating IP {floating_ip_id} detached from server {server_id}."

    async def delete_server(
        self,
        server_id: str = Field(..., description="Server ID from list_servers."),
        delete_all_volumes: bool = Field(
            False,
            description=(
                "Also delete every volume attached to the server. When false the "
                "volumes survive as unattached disks and keep costing money."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a server. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - `delete_all_volumes=true` destroys the root disk and every attached
          data disk with no recovery path. With false, those volumes remain in
          list_volumes and continue to be billed.
        - A floating IP attached to the server is released back to the project,
          not deleted.

        ## Workflow
        - Call get_server and list_volumes first, show the user the server's id,
          name, status and exactly which volumes would be destroyed or left
          behind, and get explicit confirmation.
        - Consider create_server_image first if the disk contents matter.
        """
        require_write(self.allow_write)
        validate_id(server_id, "server_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete_with_body(
            f"/v2/{pid}/servers/{server_id}",
            region=region,
            json={"deleteAllVolume": delete_all_volumes},
        )
        return f"Server {server_id} deleted" + (
            " together with its volumes." if delete_all_volumes else "; its volumes were kept."
        )
