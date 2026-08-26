"""Quota-usage handler for the vMonitor MCP server (billing API).

Read-only views of how much of each quota is consumed and what the current
active quota is, across the billing categories (metric, synthetic, log, sms,
email). All tools here only READ — nothing here creates orders or spends money.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorBillingClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import BillingListData, BillingResource
from greennode.vmonitor_mcp_server.tool_annotations import READ
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Literal


UsageCategory = Literal["metric", "synthetic", "sms", "email"]
QuotaDetailCategory = Literal["metric", "synthetic", "log"]


class QuotaUsageHandler:
    """Register and serve vMonitor billing quota-usage MCP tools (read-only)."""

    def __init__(
        self,
        mcp,
        config: VmonitorConfig,
        client: VmonitorBillingClient,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="get_quota_usage", annotations=READ)(self.get_quota_usage)
        self.mcp.tool(name="get_log_usage", annotations=READ)(self.get_log_usage)
        self.mcp.tool(name="get_composite_usage", annotations=READ)(self.get_composite_usage)
        self.mcp.tool(name="get_current_quota", annotations=READ)(self.get_current_quota)
        self.mcp.tool(name="list_log_quotas", annotations=READ)(self.list_log_quotas)
        self.mcp.tool(name="get_log_quota", annotations=READ)(self.get_log_quota)
        self.mcp.tool(name="get_quota_detail", annotations=READ)(self.get_quota_detail)
        self.mcp.tool(name="get_billing_settings", annotations=READ)(self.get_billing_settings)
        self.mcp.tool(name="list_trash_quotas", annotations=READ)(self.list_trash_quotas)
        self.mcp.tool(name="get_convert_result", annotations=READ)(self.get_convert_result)

    async def get_quota_usage(
        self,
        category: UsageCategory = Field(..., description="Quota category to read usage for"),
    ) -> BillingResource:
        """Get current usage vs. limits for a quota category (metric/synthetic/sms/email)."""
        data = await self.client.get(f"/v1/{category}/quota/usages")
        return BillingResource.from_api(data)

    async def get_log_usage(
        self,
        project_id: str = Field(..., description="Log project ID to read usage for"),
    ) -> BillingResource:
        """Get log-quota usage for a specific log project."""
        validate_id(project_id, "project_id")
        data = await self.client.get(f"/v1/log/quotas/{project_id}/usages")
        return BillingResource.from_api(data)

    async def get_composite_usage(self) -> BillingResource:
        """Get combined usage across all quota categories in one call."""
        data = await self.client.get("/v1/quotas/usages")
        return BillingResource.from_api(data)

    async def get_current_quota(
        self,
        category: UsageCategory = Field(
            ..., description="Quota category to read the active quota"
        ),
    ) -> BillingResource:
        """Get the active quota (tier/package) for a category (metric/synthetic/sms/email)."""
        data = await self.client.get(f"/v1/{category}/quota")
        return BillingResource.from_api(data)

    async def list_log_quotas(self) -> BillingListData:
        """List the user's log quotas (one per log project)."""
        data = await self.client.get("/v1/log/quotas")
        return BillingListData.from_api(data)

    async def get_log_quota(
        self,
        quota_id: str = Field(..., description="Log quota ID to retrieve"),
    ) -> BillingResource:
        """Get a single log quota by ID."""
        validate_id(quota_id, "quota_id")
        data = await self.client.get(f"/v1/log/quotas/{quota_id}")
        return BillingResource.from_api(data)

    async def get_quota_detail(
        self,
        category: QuotaDetailCategory = Field(
            ..., description="Quota category (metric/synthetic/log)"
        ),
        resource_id: str = Field(..., description="Quota resource ID to read the detail for"),
    ) -> BillingResource:
        """Get the detailed quota breakdown for a resource (v2 quota-detail)."""
        validate_id(resource_id, "resource_id")
        data = await self.client.get(f"/v2/{category}/{resource_id}/quota-detail")
        return BillingResource.from_api(data)

    async def get_billing_settings(self) -> BillingResource:
        """Get billing settings (payment method, allowed month periods, ...)."""
        data = await self.client.get("/v1/settings")
        return BillingResource.from_api(data)

    async def list_trash_quotas(self) -> BillingListData:
        """List quotas currently in the trash (deleted but recoverable)."""
        data = await self.client.get("/v1/trash/quotas")
        return BillingListData.from_api(data)

    async def get_convert_result(self) -> BillingResource:
        """Get the result of a prepaid/postpaid billing conversion, if any."""
        data = await self.client.get("/v1/billing/convert")
        return BillingResource.from_api(data)
