"""Infrastructure host handler for the vMonitor MCP server.

Every monitored resource in vMonitor is a *host* that pushes metrics to the
platform, and each host owns an auto-generated default dashboard named after
it (these built-in dashboards are read-only, unlike user-created or cloned
ones). These tools list the hosts grouped by infrastructure type so an agent
can find a resource and, from its host id, its default dashboard.

The upstream list endpoints return HTTP 500 unless BOTH ``page`` and ``size``
are supplied, so every tool always sends them (an optional name filter is added
only when provided).
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    HostDetail,
    HostListData,
    HostMetricInfo,
    HostMetricSnapshot,
    HostSummary,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class InfrastructureHandler:
    """Register and serve vMonitor infrastructure host MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_hosts", annotations=READ)(self.list_hosts)
        self.mcp.tool(name="list_vserver_hosts", annotations=READ)(self.list_vserver_hosts)
        self.mcp.tool(name="list_vstorage_hosts", annotations=READ)(self.list_vstorage_hosts)
        self.mcp.tool(name="list_vdb_hosts", annotations=READ)(self.list_vdb_hosts)
        self.mcp.tool(name="list_vdb_kafka_hosts", annotations=READ)(self.list_vdb_kafka_hosts)
        self.mcp.tool(name="list_vlb_hosts", annotations=READ)(self.list_vlb_hosts)
        self.mcp.tool(name="list_vbackup_hosts", annotations=READ)(self.list_vbackup_hosts)
        self.mcp.tool(name="list_vbandwidth_hosts", annotations=READ)(self.list_vbandwidth_hosts)
        self.mcp.tool(name="list_vas_hosts", annotations=READ)(self.list_vas_hosts)
        self.mcp.tool(name="get_host", annotations=READ)(self.get_host)
        self.mcp.tool(name="get_host_metrics", annotations=READ)(self.get_host_metrics)

        self.mcp.tool(name="get_vserver_host_metrics", annotations=READ)(
            self.get_vserver_host_metrics
        )
        self.mcp.tool(name="get_vstorage_host_metrics", annotations=READ)(
            self.get_vstorage_host_metrics
        )
        self.mcp.tool(name="get_vdb_host_metrics", annotations=READ)(self.get_vdb_host_metrics)
        self.mcp.tool(name="get_vlb_host_metrics", annotations=READ)(self.get_vlb_host_metrics)
        self.mcp.tool(name="get_vbackup_host_metrics", annotations=READ)(
            self.get_vbackup_host_metrics
        )
        self.mcp.tool(name="get_vbandwidth_host_metrics", annotations=READ)(
            self.get_vbandwidth_host_metrics
        )
        self.mcp.tool(name="get_vas_host_metrics", annotations=READ)(self.get_vas_host_metrics)

        if self.allow_write:
            self.mcp.tool(name="update_host_enabled", annotations=WRITE)(self.update_host_enabled)
            self.mcp.tool(name="update_host_disabled", annotations=WRITE)(
                self.update_host_disabled
            )
            self.mcp.tool(name="delete_host", annotations=DESTRUCTIVE)(self.delete_host)

            self.mcp.tool(name="update_vserver_host", annotations=WRITE)(self.update_vserver_host)
            self.mcp.tool(name="update_vstorage_host", annotations=WRITE)(
                self.update_vstorage_host
            )
            self.mcp.tool(name="update_vdb_host", annotations=WRITE)(self.update_vdb_host)
            self.mcp.tool(name="update_vlb_host", annotations=WRITE)(self.update_vlb_host)
            self.mcp.tool(name="update_vbackup_host", annotations=WRITE)(self.update_vbackup_host)
            self.mcp.tool(name="update_vbandwidth_host", annotations=WRITE)(
                self.update_vbandwidth_host
            )
            self.mcp.tool(name="update_vas_host", annotations=WRITE)(self.update_vas_host)

            self.mcp.tool(name="delete_vserver_host", annotations=DESTRUCTIVE)(
                self.delete_vserver_host
            )
            self.mcp.tool(name="delete_vstorage_host", annotations=DESTRUCTIVE)(
                self.delete_vstorage_host
            )
            self.mcp.tool(name="delete_vdb_host", annotations=DESTRUCTIVE)(self.delete_vdb_host)
            self.mcp.tool(name="delete_vlb_host", annotations=DESTRUCTIVE)(self.delete_vlb_host)
            self.mcp.tool(name="delete_vbackup_host", annotations=DESTRUCTIVE)(
                self.delete_vbackup_host
            )
            self.mcp.tool(name="delete_vbandwidth_host", annotations=DESTRUCTIVE)(
                self.delete_vbandwidth_host
            )
            self.mcp.tool(name="delete_vas_host", annotations=DESTRUCTIVE)(self.delete_vas_host)

    async def _list_hosts(
        self,
        path: str,
        kind: str,
        name: str | None,
        page: int,
        size: int,
        filter_param: str = "name",
    ) -> HostListData:
        """Fetch one page of hosts from *path* and normalise it to HostListData."""
        params: dict[str, Any] = {"page": page, "size": size}
        if name:
            params[filter_param] = name
        data = await self.client.get(path, params=params)
        return HostListData.from_api(data if isinstance(data, dict) else {}, kind)

    async def list_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List agent-based infrastructure hosts (servers running the Metric Agent).

        These are hosts registered by installing the vMonitor Metric Agent on a
        server; each reports OS and enabled agent plugins.
        """
        return await self._list_hosts(
            "/api/v1/infrastructure/hosts", "host", name, page, size, filter_param="searching_text"
        )

    async def list_vserver_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List vServer instances monitored as infrastructure hosts."""
        return await self._list_hosts(
            "/api/v1/infrastructure/vserver/hosts", "vserver", name, page, size
        )

    async def list_vstorage_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List vStorage resources monitored as infrastructure hosts."""
        return await self._list_hosts(
            "/api/v1/infrastructure/vstorage/hosts", "vstorage", name, page, size
        )

    async def list_vdb_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List vDB (managed database) instances monitored as infrastructure hosts."""
        return await self._list_hosts("/api/v1/infrastructure/vdb/hosts", "vdb", name, page, size)

    async def list_vdb_kafka_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List vDB Kafka broker instances monitored as infrastructure hosts.

        Kafka clusters provisioned through the vDB service are monitored as
        their own host group, separate from relational vDB instances.
        """
        return await self._list_hosts(
            "/api/v1/infrastructure/vdb-kafka/hosts", "vdb-kafka", name, page, size
        )

    async def list_vlb_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List vLB (load balancer) resources monitored as infrastructure hosts."""
        return await self._list_hosts("/api/v1/infrastructure/vlb/hosts", "vlb", name, page, size)

    async def list_vbackup_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List vBackup resources monitored as infrastructure hosts."""
        return await self._list_hosts(
            "/api/v1/infrastructure/vbackup/hosts", "vbackup", name, page, size
        )

    async def list_vbandwidth_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List vBandwidth resources monitored as infrastructure hosts."""
        return await self._list_hosts(
            "/api/v1/infrastructure/vbandwidth/hosts", "vbandwidth", name, page, size
        )

    async def list_vas_hosts(
        self,
        name: str | None = Field(None, description="Filter hosts by name (partial match)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(20, ge=1, description="Page size"),
    ) -> HostListData:
        """List VAS resources monitored as infrastructure hosts."""
        return await self._list_hosts("/api/v1/infrastructure/vas/hosts", "vas", name, page, size)

    async def get_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_hosts)"),
    ) -> HostDetail:
        """Get one agent-based host by ID.

        An agent-based host is a machine on which the user installed the
        vMonitor Metric Agent to collect and view its metrics.
        """
        validate_id(host_id, "host_id")
        data = await self.client.get(f"/api/v1/infrastructure/hosts/{host_id}")
        return HostDetail.from_api(data if isinstance(data, dict) else {})

    async def get_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_hosts)"),
    ) -> HostMetricInfo:
        """Get the current metric snapshot for an agent-based host.

        Returns the latest reported status plus CPU, CPU load, CPU usage, I/O
        wait, system load, and available-memory samples.
        """
        validate_id(host_id, "host_id")
        data = await self.client.get(f"/api/v1/infrastructure/hosts/{host_id}/metric")
        return HostMetricInfo.from_api(data if isinstance(data, dict) else {})

    async def update_host_enabled(
        self,
        host_id: str = Field(..., description="Host ID to enable monitoring for"),
    ) -> HostDetail:
        """Enable monitoring for an agent-based host (resume metric collection).

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(host_id, "host_id")
        data = await self.client.put(f"/api/v1/infrastructure/hosts/{host_id}/enabled")
        return HostDetail.from_api(data if isinstance(data, dict) else {})

    async def update_host_disabled(
        self,
        host_id: str = Field(..., description="Host ID to disable monitoring for"),
    ) -> HostDetail:
        """Disable monitoring for an agent-based host (pause metric collection).

        The agent stays installed; monitoring is paused (and stops consuming
        metric quota) until re-enabled with update_host_enabled.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(host_id, "host_id")
        data = await self.client.put(f"/api/v1/infrastructure/hosts/{host_id}/disabled")
        return HostDetail.from_api(data if isinstance(data, dict) else {})

    async def delete_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete an agent-based host from the Infrastructure list. IRREVERSIBLE.

        Note: if the Metric Agent is still installed and running on the machine,
        the host will re-register automatically the next time it pushes metrics;
        uninstall or disable the agent first to remove it for good.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_host first; deletion cannot be undone.
        """
        validate_id(host_id, "host_id")
        await self.client.delete(f"/api/v1/infrastructure/hosts/{host_id}")
        return f"Host {host_id} deleted."

    async def _typed_host_metrics(
        self, prefix: str, kind: str, host_id: str
    ) -> HostMetricSnapshot:
        """Fetch the current metric snapshot for one product host."""
        validate_id(host_id, "host_id")
        data = await self.client.get(f"/api/v1/infrastructure/{prefix}/hosts/{host_id}/metric")
        return HostMetricSnapshot.from_api(data if isinstance(data, dict) else {}, kind)

    async def _update_typed_host(
        self, prefix: str, kind: str, host_id: str, enabled: bool
    ) -> HostSummary:
        """Toggle monitoring for one product host and return its updated summary."""
        validate_id(host_id, "host_id")
        data = await self.client.put(
            f"/api/v1/infrastructure/{prefix}/hosts/{host_id}", json={"enabled": enabled}
        )
        return HostSummary.from_api(data if isinstance(data, dict) else {}, kind)

    async def _delete_typed_host(self, prefix: str, kind: str, host_id: str) -> str:
        """Delete one product host from the Infrastructure list."""
        validate_id(host_id, "host_id")
        await self.client.delete(f"/api/v1/infrastructure/{prefix}/hosts/{host_id}")
        return f"{kind} host {host_id} deleted."

    async def get_vserver_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_vserver_hosts)"),
    ) -> HostMetricSnapshot:
        """Get the current metric snapshot for a monitored vServer host."""
        return await self._typed_host_metrics("vserver", "vserver", host_id)

    async def get_vstorage_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_vstorage_hosts)"),
    ) -> HostMetricSnapshot:
        """Get the current metric snapshot for a monitored vStorage host."""
        return await self._typed_host_metrics("vstorage", "vstorage", host_id)

    async def get_vdb_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_vdb_hosts)"),
    ) -> HostMetricSnapshot:
        """Get the current metric snapshot for a monitored vDB host."""
        return await self._typed_host_metrics("vdb", "vdb", host_id)

    async def get_vlb_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_vlb_hosts)"),
    ) -> HostMetricSnapshot:
        """Get the current metric snapshot for a monitored vLB host."""
        return await self._typed_host_metrics("vlb", "vlb", host_id)

    async def get_vbackup_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_vbackup_hosts)"),
    ) -> HostMetricSnapshot:
        """Get the current metric snapshot for a monitored vBackup host."""
        return await self._typed_host_metrics("vbackup", "vbackup", host_id)

    async def get_vbandwidth_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_vbandwidth_hosts)"),
    ) -> HostMetricSnapshot:
        """Get the current metric snapshot for a monitored vBandwidth host."""
        return await self._typed_host_metrics("vbandwidth", "vbandwidth", host_id)

    async def get_vas_host_metrics(
        self,
        host_id: str = Field(..., description="Host ID (from list_vas_hosts)"),
    ) -> HostMetricSnapshot:
        """Get the current metric snapshot for a monitored VAS host."""
        return await self._typed_host_metrics("vas", "vas", host_id)

    async def update_vserver_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_vserver_hosts)"),
        enabled: bool = Field(..., description="True to enable monitoring, False to pause it"),
    ) -> HostSummary:
        """Enable or disable monitoring for a vServer host.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._update_typed_host("vserver", "vserver", host_id, enabled)

    async def update_vstorage_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_vstorage_hosts)"),
        enabled: bool = Field(..., description="True to enable monitoring, False to pause it"),
    ) -> HostSummary:
        """Enable or disable monitoring for a vStorage host.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._update_typed_host("vstorage", "vstorage", host_id, enabled)

    async def update_vdb_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_vdb_hosts)"),
        enabled: bool = Field(..., description="True to enable monitoring, False to pause it"),
    ) -> HostSummary:
        """Enable or disable monitoring for a vDB host.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._update_typed_host("vdb", "vdb", host_id, enabled)

    async def update_vlb_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_vlb_hosts)"),
        enabled: bool = Field(..., description="True to enable monitoring, False to pause it"),
    ) -> HostSummary:
        """Enable or disable monitoring for a vLB host.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._update_typed_host("vlb", "vlb", host_id, enabled)

    async def update_vbackup_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_vbackup_hosts)"),
        enabled: bool = Field(..., description="True to enable monitoring, False to pause it"),
    ) -> HostSummary:
        """Enable or disable monitoring for a vBackup host.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._update_typed_host("vbackup", "vbackup", host_id, enabled)

    async def update_vbandwidth_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_vbandwidth_hosts)"),
        enabled: bool = Field(..., description="True to enable monitoring, False to pause it"),
    ) -> HostSummary:
        """Enable or disable monitoring for a vBandwidth host.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._update_typed_host("vbandwidth", "vbandwidth", host_id, enabled)

    async def update_vas_host(
        self,
        host_id: str = Field(..., description="Host ID (from list_vas_hosts)"),
        enabled: bool = Field(..., description="True to enable monitoring, False to pause it"),
    ) -> HostSummary:
        """Enable or disable monitoring for a VAS host.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._update_typed_host("vas", "vas", host_id, enabled)

    async def delete_vserver_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Remove a vServer host from the Infrastructure list. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id via list_vserver_hosts first; deletion cannot be undone.
        """
        return await self._delete_typed_host("vserver", "vserver", host_id)

    async def delete_vstorage_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Remove a vStorage host from the Infrastructure list. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id via list_vstorage_hosts first; deletion cannot be undone.
        """
        return await self._delete_typed_host("vstorage", "vstorage", host_id)

    async def delete_vdb_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Remove a vDB host from the Infrastructure list. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id via list_vdb_hosts first; deletion cannot be undone.
        """
        return await self._delete_typed_host("vdb", "vdb", host_id)

    async def delete_vlb_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Remove a vLB host from the Infrastructure list. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id via list_vlb_hosts first; deletion cannot be undone.
        """
        return await self._delete_typed_host("vlb", "vlb", host_id)

    async def delete_vbackup_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Remove a vBackup host from the Infrastructure list. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id via list_vbackup_hosts first; deletion cannot be undone.
        """
        return await self._delete_typed_host("vbackup", "vbackup", host_id)

    async def delete_vbandwidth_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Remove a vBandwidth host from the Infrastructure list. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id via list_vbandwidth_hosts first; deletion cannot be undone.
        """
        return await self._delete_typed_host("vbandwidth", "vbandwidth", host_id)

    async def delete_vas_host(
        self,
        host_id: str = Field(..., description="Host ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Remove a VAS host from the Infrastructure list. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id via list_vas_hosts first; deletion cannot be undone.
        """
        return await self._delete_typed_host("vas", "vas", host_id)
