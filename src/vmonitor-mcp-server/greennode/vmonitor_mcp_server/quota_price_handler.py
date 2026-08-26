"""Quota-price handler for the vMonitor MCP server (billing API).

Price *quotes* for quota operations: how much creating / resizing / renewing /
recovering a package would cost. These call the billing ``prices/*`` endpoints,
which only COMPUTE a price — they never create an order or charge anything, so
they are annotated read-only. Results depend on account state (a postpaid
account gets 409 on time-extension; recovery needs items in trash; some free /
postpaid combinations return 500) — those are upstream conditions, not errors
in the tool.

Two pricing generations exist and ``get_creation_price`` / ``get_resize_price``
pick between them by whether you pass ``quantity``:

- no ``quantity`` → the v1 endpoints, which price a whole fixed package from
  query parameters;
- a ``quantity`` → the v2 quota-class endpoints, which take a JSON body and
  price the exact amount of resource being bought.

The v2 form is the pre-flight for the order tools in ``quota_order_handler``:
it takes the same body those tools send, so a payload the quote rejects would
have been rejected by the order too — quote first, then order.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorBillingClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import BillingResource
from greennode.vmonitor_mcp_server.tool_annotations import READ
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any, Literal


CreationCategory = Literal["metric", "synthetic", "log", "sms", "email"]
RecoveryCategory = Literal["metric", "synthetic", "log", "sms", "email"]
RenewalCategory = Literal["metric", "log", "sms", "email"]
ResizeCategory = Literal["metric", "log", "sms", "email"]


def _require_log_resource(category: str, resource_id: str | None) -> str | None:
    """Validate that log price quotes carry the resource_id they route on."""
    if category == "log":
        if not resource_id:
            raise ValueError("resource_id is required when category is 'log'.")
        validate_id(resource_id, "resource_id")
        return resource_id
    return None


class QuotaPriceHandler:
    """Register and serve vMonitor billing price-quote MCP tools (read-only)."""

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

        self.mcp.tool(name="get_creation_price", annotations=READ)(self.get_creation_price)
        self.mcp.tool(name="get_resize_price", annotations=READ)(self.get_resize_price)
        self.mcp.tool(name="get_recovery_price", annotations=READ)(self.get_recovery_price)
        self.mcp.tool(name="get_renewal_price", annotations=READ)(self.get_renewal_price)

    async def get_creation_price(
        self,
        category: CreationCategory = Field(
            ..., description="Category (metric/synthetic/log/sms/email)"
        ),
        package_id: str = Field(..., description="Package ID to price a creation for"),
        month_period: int | None = Field(
            None, ge=1, description="Billing period in months (prepaid only)"
        ),
        quantity: int | None = Field(
            None,
            ge=1,
            description=(
                "Amount of resource being bought (metric: host count; log: GB-days = "
                "GB per day x retention days). Passing it switches to the v2 "
                "quota-class pricing — the pre-flight for create_log_project"
            ),
        ),
        buy_with: dict[str, str] | None = Field(
            None,
            description=(
                'v2 only: notification quota bought in the same order, e.g. {"email": '
                '"<package id>"} — mirrors create_log_project\'s buyWith'
            ),
        ),
    ) -> BillingResource:
        """Quote the price of creating a quota from a package (no order is placed).

        Pass `quantity` to price a v2 quota-class purchase with the exact body
        `create_log_project` would send; omit it to price a whole fixed package
        the v1 way.
        """
        validate_id(package_id, "package_id")
        if quantity is not None:
            body: dict[str, Any] = {
                "redirectUrl": "",
                "packageId": package_id,
                "quantity": quantity,
            }
            if buy_with:
                body["buyWith"] = buy_with
            if month_period is not None:
                body["monthPeriod"] = month_period
            data = await self.client.post(f"/v2/{category}/prices/created-price", json=body)
            return BillingResource.from_api(data)
        params: dict[str, Any] = {"package_id": package_id}
        if month_period is not None:
            params["month_period"] = month_period
        data = await self.client.post(f"/v1/{category}/prices/created-price", params=params)
        return BillingResource.from_api(data)

    async def get_resize_price(
        self,
        category: ResizeCategory = Field(..., description="Category (metric/log/sms/email)"),
        package_id: str = Field(..., description="Target package ID to price a resize to"),
        resource_id: str | None = Field(
            None,
            description=(
                "Quota resource ID — required for log, and sent in the v2 body for every "
                "category (get it from get_current_quota / list_log_quotas)"
            ),
        ),
        quantity: int | None = Field(
            None,
            ge=1,
            description=(
                "New amount of resource (metric: host count; log: GB-days = GB per day x "
                "retention days; sms/email: 1). Passing it switches to the v2 "
                "quota-class pricing — the pre-flight for the resize_* order tools"
            ),
        ),
    ) -> BillingResource:
        """Quote the price of resizing a quota to another package (no order is placed).

        Pass `quantity` to price the resize with the exact body the matching
        `resize_*` tool would send; omit it to price a whole fixed package the v1
        way. sms/email have no real quantity — the console sends 1.
        """
        validate_id(package_id, "package_id")
        res = _require_log_resource(category, resource_id)
        if quantity is not None:
            body: dict[str, Any] = {
                "redirectUrl": "",
                "packageId": package_id,
                "quantity": quantity,
            }
            if resource_id:
                validate_id(resource_id, "resource_id")
                body["resourceId"] = resource_id
            if res is not None:
                path = f"/v2/log/prices/quotas/{res}/resized-price"
            else:
                path = f"/v2/{category}/prices/resized-price"
            data = await self.client.post(path, json=body)
            return BillingResource.from_api(data)
        params = {"package_id": package_id}
        if res is not None:
            path = f"/v1/log/prices/quotas/{res}/resized-price"
        else:
            path = f"/v1/{category}/prices/resized-price"
        data = await self.client.post(path, params=params)
        return BillingResource.from_api(data)

    async def get_recovery_price(
        self,
        category: RecoveryCategory = Field(
            ..., description="Category (metric/synthetic/log/sms/email)"
        ),
        month_period: int | None = Field(
            None, ge=1, description="Billing period in months (prepaid only)"
        ),
        resource_id: str | None = Field(None, description="Quota resource ID (required for log)"),
    ) -> BillingResource:
        """Quote the price of recovering a quota from trash (no order is placed)."""
        res = _require_log_resource(category, resource_id)
        params: dict[str, Any] = {}
        if month_period is not None:
            params["month_period"] = month_period
        if res is not None:
            path = f"/v1/log/prices/quotas/{res}/recovery-price"
        else:
            path = f"/v1/{category}/prices/recovery-price"
        data = await self.client.post(path, params=params)
        return BillingResource.from_api(data)

    async def get_renewal_price(
        self,
        category: RenewalCategory = Field(..., description="Category (metric/log/sms/email)"),
        month_period: int = Field(..., ge=1, description="Extension period in months"),
        resource_id: str | None = Field(None, description="Quota resource ID (required for log)"),
    ) -> BillingResource:
        """Quote the price of extending a quota's time (no order is placed)."""
        res = _require_log_resource(category, resource_id)
        params = {"month_period": month_period}
        if res is not None:
            path = f"/v1/log/prices/quotas/{res}/time-extension-price"
        else:
            path = f"/v1/{category}/prices/time-extension-price"
        data = await self.client.post(path, params=params)
        return BillingResource.from_api(data)
