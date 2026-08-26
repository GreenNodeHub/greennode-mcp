"""Resource log-mapping handler for the vMonitor MCP server (Log API).

A log mapping routes a product resource's logs (vCDN domain, vDB resource, vLB
project, vStorage project/bucket) into a log project. Each product family exposes
the same shape — list, enable, disable, edit — so the tools share private
helpers; vCDN adds a type lookup and vStorage a region lookup, and vStorage
buckets use a single update instead of enable/disable/edit.
"""

from __future__ import annotations

import urllib.parse
from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    BucketLogMappingUpdateDto,
    LogMappingDisableDto,
    LogMappingEditDto,
    LogMappingEnableDto,
    LogPageData,
    LogResource,
)
from greennode.vmonitor_mcp_server.tool_annotations import READ, WRITE
from pydantic import Field
from typing import Any


def _seg(value: str, name: str) -> str:
    """URL-encode a resource identifier for a path segment (traversal-safe)."""
    if not value or "/" in value or "\\" in value:
        raise ValueError(
            f"Invalid {name}: '{value}'. Must be non-empty and contain no path separators."
        )
    return urllib.parse.quote(value, safe="")


class LogMappingHandler:
    """Register and serve vMonitor Log API resource log-mapping MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_vcdn_log_mappings", annotations=READ)(self.list_vcdn_log_mappings)
        self.mcp.tool(name="list_vcdn_log_mapping_types", annotations=READ)(
            self.list_vcdn_log_mapping_types
        )
        self.mcp.tool(name="list_vdb_log_mappings", annotations=READ)(self.list_vdb_log_mappings)
        self.mcp.tool(name="list_vlb_log_mappings", annotations=READ)(self.list_vlb_log_mappings)
        self.mcp.tool(name="list_vstorage_log_mappings", annotations=READ)(
            self.list_vstorage_log_mappings
        )
        self.mcp.tool(name="list_vstorage_log_mapping_regions", annotations=READ)(
            self.list_vstorage_log_mapping_regions
        )
        self.mcp.tool(name="list_vstorage_bucket_log_mappings", annotations=READ)(
            self.list_vstorage_bucket_log_mappings
        )

        if self.allow_write:
            self.mcp.tool(name="update_vcdn_log_mapping_enabled", annotations=WRITE)(
                self.update_vcdn_log_mapping_enabled
            )
            self.mcp.tool(name="update_vcdn_log_mapping_disabled", annotations=WRITE)(
                self.update_vcdn_log_mapping_disabled
            )
            self.mcp.tool(name="update_vcdn_log_mapping", annotations=WRITE)(
                self.update_vcdn_log_mapping
            )
            self.mcp.tool(name="update_vdb_log_mapping_enabled", annotations=WRITE)(
                self.update_vdb_log_mapping_enabled
            )
            self.mcp.tool(name="update_vdb_log_mapping_disabled", annotations=WRITE)(
                self.update_vdb_log_mapping_disabled
            )
            self.mcp.tool(name="update_vdb_log_mapping", annotations=WRITE)(
                self.update_vdb_log_mapping
            )
            self.mcp.tool(name="update_vlb_log_mapping_enabled", annotations=WRITE)(
                self.update_vlb_log_mapping_enabled
            )
            self.mcp.tool(name="update_vlb_log_mapping_disabled", annotations=WRITE)(
                self.update_vlb_log_mapping_disabled
            )
            self.mcp.tool(name="update_vlb_log_mapping", annotations=WRITE)(
                self.update_vlb_log_mapping
            )
            self.mcp.tool(name="update_vstorage_log_mapping_enabled", annotations=WRITE)(
                self.update_vstorage_log_mapping_enabled
            )
            self.mcp.tool(name="update_vstorage_log_mapping_disabled", annotations=WRITE)(
                self.update_vstorage_log_mapping_disabled
            )
            self.mcp.tool(name="update_vstorage_log_mapping", annotations=WRITE)(
                self.update_vstorage_log_mapping
            )
            self.mcp.tool(name="update_vstorage_bucket_log_mapping", annotations=WRITE)(
                self.update_vstorage_bucket_log_mapping
            )

    async def _list(self, base: str, params: dict[str, Any]) -> LogPageData:
        data = await self.client.get(
            base, params={k: v for k, v in params.items() if v is not None}
        )
        return LogPageData.from_api(data)

    async def _patch(
        self, base: str, action: str, resource_id: str, name: str, body
    ) -> LogResource:
        segment = _seg(resource_id, name)
        data = await self.client.patch(
            f"{base}/{action}/{segment}", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)

    async def list_vcdn_log_mappings(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        type: str | None = Field(None, description="Filter by CDN type"),
        sort_by: str | None = Field(None, description="Field to sort by"),
        sort_order: str | None = Field(None, description="Sort order (asc/desc)"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List vCDN domain log mappings."""
        return await self._list(
            "/v1/vcdn-log-mapping",
            {
                "query": query,
                "type": type,
                "sortBy": sort_by,
                "sortOrder": sort_order,
                "page": page,
                "size": size,
            },
        )

    async def list_vcdn_log_mapping_types(self) -> list[dict]:
        """List the vCDN log-mapping types available."""
        data = await self.client.get("/v1/vcdn-log-mapping/type")
        return data if isinstance(data, list) else []

    async def list_vdb_log_mappings(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        region: str | None = Field(None, description="Filter by region"),
        sort_by: str | None = Field(None, description="Field to sort by"),
        sort_order: str | None = Field(None, description="Sort order (asc/desc)"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List vDB resource log mappings."""
        return await self._list(
            "/v1/vdb-log-mapping",
            {
                "query": query,
                "region": region,
                "sortBy": sort_by,
                "sortOrder": sort_order,
                "page": page,
                "size": size,
            },
        )

    async def list_vlb_log_mappings(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        region: str | None = Field(None, description="Filter by region"),
        sort_by: str | None = Field(None, description="Field to sort by"),
        sort_order: str | None = Field(None, description="Sort order (asc/desc)"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List vLB project log mappings."""
        return await self._list(
            "/v1/vlb-log-mapping",
            {
                "query": query,
                "region": region,
                "sortBy": sort_by,
                "sortOrder": sort_order,
                "page": page,
                "size": size,
            },
        )

    async def list_vstorage_log_mappings(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        region_id: str | None = Field(None, description="Filter by region ID"),
        sort_by: str | None = Field(None, description="Field to sort by"),
        sort_order: str | None = Field(None, description="Sort order (asc/desc)"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List vStorage project log mappings."""
        return await self._list(
            "/v1/vstorage-log-mappings",
            {
                "query": query,
                "region-id": region_id,
                "sortBy": sort_by,
                "sortOrder": sort_order,
                "page": page,
                "size": size,
            },
        )

    async def list_vstorage_log_mapping_regions(self) -> list[dict]:
        """List the regions available for vStorage log mappings."""
        data = await self.client.get("/v1/vstorage-log-mappings/regions")
        return data if isinstance(data, list) else []

    async def list_vstorage_bucket_log_mappings(
        self,
        query: str | None = Field(None, description="Free-text filter"),
        region_id: str | None = Field(None, description="Filter by region ID"),
        sort_by: str | None = Field(None, description="Field to sort by"),
        sort_order: str | None = Field(None, description="Sort order (asc/desc)"),
        page: int | None = Field(
            None, ge=0, description="0-based page number (Log API pages start at 0)"
        ),
        size: int | None = Field(None, ge=1, description="Page size"),
    ) -> LogPageData:
        """List vStorage bucket log mappings."""
        return await self._list(
            "/v1/vstorage-bucket-log-mappings",
            {
                "query": query,
                "region-id": region_id,
                "sortBy": sort_by,
                "sortOrder": sort_order,
                "page": page,
                "size": size,
            },
        )

    async def update_vcdn_log_mapping_enabled(
        self,
        cdn_domain: str = Field(..., description="CDN domain to enable log mapping for"),
        body: LogMappingEnableDto = Field(
            ..., description="LogMappingEnableDto body. Required: logProjectId, status."
        ),
    ) -> LogResource:
        """Enable log mapping for a vCDN domain (route its logs to a log project).

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch("/v1/vcdn-log-mapping", "enable", cdn_domain, "cdn_domain", body)

    async def update_vcdn_log_mapping_disabled(
        self,
        cdn_domain: str = Field(..., description="CDN domain to disable log mapping for"),
        body: LogMappingDisableDto = Field(
            ..., description="LogMappingDisableDto body. Required: status."
        ),
    ) -> LogResource:
        """Disable log mapping for a vCDN domain.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch("/v1/vcdn-log-mapping", "disable", cdn_domain, "cdn_domain", body)

    async def update_vcdn_log_mapping(
        self,
        cdn_domain: str = Field(..., description="CDN domain whose log mapping to edit"),
        body: LogMappingEditDto = Field(
            ..., description="LogMappingEditDto body. Required: logProjectId."
        ),
    ) -> LogResource:
        """Edit a vCDN domain's log mapping (change the target log project).

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch("/v1/vcdn-log-mapping", "edit", cdn_domain, "cdn_domain", body)

    async def update_vdb_log_mapping_enabled(
        self,
        vdb_resource_id: str = Field(..., description="vDB resource ID to enable log mapping for"),
        body: LogMappingEnableDto = Field(
            ..., description="LogMappingEnableDto body. Required: logProjectId, status."
        ),
    ) -> LogResource:
        """Enable log mapping for a vDB resource.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vdb-log-mapping", "enable", vdb_resource_id, "vdb_resource_id", body
        )

    async def update_vdb_log_mapping_disabled(
        self,
        vdb_resource_id: str = Field(
            ..., description="vDB resource ID to disable log mapping for"
        ),
        body: LogMappingDisableDto = Field(
            ..., description="LogMappingDisableDto body. Required: status."
        ),
    ) -> LogResource:
        """Disable log mapping for a vDB resource.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vdb-log-mapping", "disable", vdb_resource_id, "vdb_resource_id", body
        )

    async def update_vdb_log_mapping(
        self,
        vdb_resource_id: str = Field(..., description="vDB resource ID whose log mapping to edit"),
        body: LogMappingEditDto = Field(
            ..., description="LogMappingEditDto body. Required: logProjectId."
        ),
    ) -> LogResource:
        """Edit a vDB resource's log mapping (change the target log project).

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vdb-log-mapping", "edit", vdb_resource_id, "vdb_resource_id", body
        )

    async def update_vlb_log_mapping_enabled(
        self,
        vlb_project_id: str = Field(..., description="vLB project ID to enable log mapping for"),
        body: LogMappingEnableDto = Field(
            ..., description="LogMappingEnableDto body. Required: logProjectId, status."
        ),
    ) -> LogResource:
        """Enable log mapping for a vLB project.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vlb-log-mapping", "enable", vlb_project_id, "vlb_project_id", body
        )

    async def update_vlb_log_mapping_disabled(
        self,
        vlb_project_id: str = Field(..., description="vLB project ID to disable log mapping for"),
        body: LogMappingDisableDto = Field(
            ..., description="LogMappingDisableDto body. Required: status."
        ),
    ) -> LogResource:
        """Disable log mapping for a vLB project.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vlb-log-mapping", "disable", vlb_project_id, "vlb_project_id", body
        )

    async def update_vlb_log_mapping(
        self,
        vlb_project_id: str = Field(..., description="vLB project ID whose log mapping to edit"),
        body: LogMappingEditDto = Field(
            ..., description="LogMappingEditDto body. Required: logProjectId."
        ),
    ) -> LogResource:
        """Edit a vLB project's log mapping (change the target log project).

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vlb-log-mapping", "edit", vlb_project_id, "vlb_project_id", body
        )

    async def update_vstorage_log_mapping_enabled(
        self,
        vstorage_project_id: str = Field(
            ..., description="vStorage project ID to enable log mapping for"
        ),
        body: LogMappingEnableDto = Field(
            ..., description="LogMappingEnableDto body. Required: logProjectId, status."
        ),
    ) -> LogResource:
        """Enable log mapping for a vStorage project.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vstorage-log-mappings", "enable", vstorage_project_id, "vstorage_project_id", body
        )

    async def update_vstorage_log_mapping_disabled(
        self,
        vstorage_project_id: str = Field(
            ..., description="vStorage project ID to disable log mapping for"
        ),
        body: LogMappingDisableDto = Field(
            ..., description="LogMappingDisableDto body. Required: status."
        ),
    ) -> LogResource:
        """Disable log mapping for a vStorage project.

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vstorage-log-mappings",
            "disable",
            vstorage_project_id,
            "vstorage_project_id",
            body,
        )

    async def update_vstorage_log_mapping(
        self,
        vstorage_project_id: str = Field(
            ..., description="vStorage project ID whose log mapping to edit"
        ),
        body: LogMappingEditDto = Field(
            ..., description="LogMappingEditDto body. Required: logProjectId."
        ),
    ) -> LogResource:
        """Edit a vStorage project's log mapping (change the target log project).

        ## Requirements
        - Server must run with --allow-write
        """
        return await self._patch(
            "/v1/vstorage-log-mappings", "edit", vstorage_project_id, "vstorage_project_id", body
        )

    async def update_vstorage_bucket_log_mapping(
        self,
        bucket_name: str = Field(..., description="vStorage bucket name whose log mapping to set"),
        body: BucketLogMappingUpdateDto = Field(
            ...,
            description=(
                "BucketLogMappingUpdateDto body. Required: logProjectId, "
                "vstorageProjectId, status."
            ),
        ),
    ) -> LogResource:
        """Set a vStorage bucket's log mapping (enable/disable + target log project).

        ## Requirements
        - Server must run with --allow-write
        """
        segment = _seg(bucket_name, "bucket_name")
        data = await self.client.patch(
            f"/v1/vstorage-bucket-log-mappings/{segment}",
            json=body.model_dump(exclude_none=True),
        )
        return LogResource.from_api(data)
