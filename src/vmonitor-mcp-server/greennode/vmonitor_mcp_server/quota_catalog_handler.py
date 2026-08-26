"""Quota-catalog handler for the vMonitor MCP server (billing API).

Read-only catalog of what can be purchased: tiers, packages, their field
descriptions and the v2 quota classes, across the billing categories. All tools
here only READ — browsing the catalog never creates an order or spends money.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorBillingClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import BillingListData, BillingResource
from greennode.vmonitor_mcp_server.tool_annotations import READ
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Literal


TierCategory = Literal["metric", "synthetic", "log"]
PackageCategory = Literal["metric", "synthetic", "log", "sms", "email"]
PackageDetailCategory = Literal["metric", "synthetic", "log"]
QuotaClassCategory = Literal["metric", "synthetic", "log"]


class QuotaCatalogHandler:
    """Register and serve vMonitor billing catalog MCP tools (read-only)."""

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

        self.mcp.tool(name="list_tiers", annotations=READ)(self.list_tiers)
        self.mcp.tool(name="get_tier", annotations=READ)(self.get_tier)
        self.mcp.tool(name="get_tier_description", annotations=READ)(self.get_tier_description)
        self.mcp.tool(name="list_packages", annotations=READ)(self.list_packages)
        self.mcp.tool(name="get_package", annotations=READ)(self.get_package)
        self.mcp.tool(name="get_package_detail", annotations=READ)(self.get_package_detail)
        self.mcp.tool(name="get_package_description", annotations=READ)(
            self.get_package_description
        )
        self.mcp.tool(name="get_package_description_detail", annotations=READ)(
            self.get_package_description_detail
        )
        self.mcp.tool(name="list_quota_classes", annotations=READ)(self.list_quota_classes)
        self.mcp.tool(name="list_quota_class_packages", annotations=READ)(
            self.list_quota_class_packages
        )

    async def list_tiers(
        self,
        category: TierCategory = Field(..., description="Category (metric/synthetic/log)"),
    ) -> BillingListData:
        """List the available quota tiers for a category."""
        data = await self.client.get(f"/v1/{category}/tiers")
        return BillingListData.from_api(data)

    async def get_tier(
        self,
        category: TierCategory = Field(..., description="Category (metric/synthetic/log)"),
        tier_id: int = Field(..., description="Tier ID to retrieve"),
    ) -> BillingResource:
        """Get a single quota tier by ID."""
        data = await self.client.get(f"/v1/{category}/tiers/{tier_id}")
        return BillingResource.from_api(data)

    async def get_tier_description(
        self,
        category: TierCategory = Field(..., description="Category (metric/synthetic/log)"),
    ) -> BillingResource:
        """Get the field descriptions for a category's tiers."""
        data = await self.client.get(f"/v1/{category}/tier-description")
        return BillingResource.from_api(data)

    async def list_packages(
        self,
        category: PackageCategory = Field(
            ..., description="Category (metric/synthetic/log/sms/email)"
        ),
        tier_id: int | None = Field(
            None, description="Filter by tier ID (metric/log only; ignored otherwise)"
        ),
    ) -> BillingListData:
        """List the purchasable packages for a category (optionally within a tier)."""
        params = {"tier_id": tier_id} if tier_id is not None else {}
        data = await self.client.get(f"/v1/{category}/packages", params=params)
        return BillingListData.from_api(data)

    async def get_package(
        self,
        category: PackageCategory = Field(
            ..., description="Category (metric/synthetic/log/sms/email)"
        ),
        package_id: str = Field(..., description="Package ID to retrieve"),
    ) -> BillingResource:
        """Get a single package by ID."""
        validate_id(package_id, "package_id")
        data = await self.client.get(f"/v1/{category}/packages/{package_id}")
        return BillingResource.from_api(data)

    async def get_package_detail(
        self,
        category: PackageDetailCategory = Field(
            ..., description="Category (metric/synthetic/log)"
        ),
        package_id: str = Field(..., description="Package ID to retrieve details for"),
    ) -> BillingResource:
        """Get the detailed spec of a package (metric/synthetic/log only)."""
        validate_id(package_id, "package_id")
        data = await self.client.get(f"/v1/{category}/packages/{package_id}/details")
        return BillingResource.from_api(data)

    async def get_package_description(
        self,
        category: PackageCategory = Field(
            ..., description="Category (metric/synthetic/log/sms/email)"
        ),
    ) -> BillingResource:
        """Get the field descriptions for a category's packages."""
        data = await self.client.get(f"/v1/{category}/package-description")
        return BillingResource.from_api(data)

    async def get_package_description_detail(
        self,
        category: PackageDetailCategory = Field(
            ..., description="Category (metric/synthetic/log)"
        ),
    ) -> BillingResource:
        """Get the detailed field descriptions for a category's packages."""
        data = await self.client.get(f"/v1/{category}/package-description-details")
        return BillingResource.from_api(data)

    async def list_quota_classes(
        self,
        category: QuotaClassCategory = Field(..., description="Category (metric/synthetic/log)"),
    ) -> BillingListData:
        """List the v2 quota classes for a category."""
        data = await self.client.get(f"/v2/{category}/quota-class")
        return BillingListData.from_api(data)

    async def list_quota_class_packages(
        self,
        category: QuotaClassCategory = Field(..., description="Category (metric/synthetic/log)"),
        quota_class_id: str = Field(..., description="Quota class ID to list packages for"),
    ) -> BillingListData:
        """List the packages inside a v2 quota class."""
        validate_id(quota_class_id, "quota_class_id")
        data = await self.client.get(f"/v2/{category}/quota-class/{quota_class_id}/packages")
        return BillingListData.from_api(data)
