"""Quota-order handler for the vMonitor MCP server (billing API).

The only tools in the server that SPEND MONEY. Everything else under billing
(``quota_catalog_handler``, ``quota_price_handler``, ``quota_usage_handler``)
reads the catalogue, quotes a price or reports usage; the tools here place the
actual order that buys, resizes or deletes a quota.

Every order follows the same three steps, and the discovery step is not
optional — a package ID guessed without it is the usual cause of a rejected
order:

1. Read the current quota: ``get_current_quota`` (metric/sms/email) or
   ``list_log_quotas`` + ``get_quota_detail`` (log) → gives the resource ID,
   the current ``packageId`` and the current quantity.
2. Pick the target package: ``list_quota_classes`` (metric/log) exposes each
   class's ``config.retentions[]``, and every retention entry carries the
   ``packageId`` plus the ``minSize``/``maxSize``/``step`` (log) or
   ``minResource``/``maxResource``/``step`` (metric) bounds the quantity must
   respect. For sms/email use ``list_packages``.
3. Quote it: ``get_resize_price`` / ``get_creation_price`` with the same
   ``quantity`` — that hits the v2 pricing endpoint with the exact payload the
   order will use, so a payload the pricing call rejects would also fail here.

Quota changes are upgrades: the platform does not shrink a quota below what is
already stored/used, and a resize cannot be undone, so these tools are
annotated DESTRUCTIVE.

One asymmetry between quoting and ordering, verified live: the price endpoints
accept ``redirectUrl: ""``, but an order requires a NON-EMPTY, **allow-listed**
URL — missing/null → ``redirectUrl: must not be null``, blank → ``redirect URL
is invalid``, well-formed but unlisted → ``redirect URL is incorrect``. The
accepted value is the console's own payment-return page, so each tool fills
``PAYMENT_REDIRECT_BASE`` + its category page by default and callers never have
to supply one.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorBillingClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    BillingOrderResult,
    CreateLogProjectDto,
    ResizeLogProjectDto,
    ResizeMetricQuotaDto,
    ResizeNotificationQuotaDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


PAYMENT_REDIRECT_BASE = "https://vmonitor.console.vngcloud.vn"

QUOTA_PAGE = {
    "log": "/quota-usages/log",
    "metric": "/quota-usages/metric",
    "notification": "/quota-usages/notification",
}


def _order_payload(body, page: str) -> dict:
    """Serialise an order DTO, defaulting redirectUrl to the console's quota page.

    The billing API allow-lists ``redirectUrl`` and rejects an empty or unknown
    one, so an order that omits it gets the console page for its category — the
    same URL the vMonitor web app sends.
    """
    payload = body.model_dump(exclude_none=True)
    payload.setdefault("redirectUrl", PAYMENT_REDIRECT_BASE + QUOTA_PAGE[page])
    return payload


class QuotaOrderHandler:
    """Register and serve vMonitor billing order MCP tools (write-only, paid)."""

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

        if self.allow_write:
            self.mcp.tool(name="resize_metric_quota", annotations=DESTRUCTIVE)(
                self.resize_metric_quota
            )
            self.mcp.tool(name="create_log_project", annotations=WRITE)(self.create_log_project)
            self.mcp.tool(name="resize_log_project", annotations=DESTRUCTIVE)(
                self.resize_log_project
            )
            self.mcp.tool(name="delete_log_project", annotations=DESTRUCTIVE)(
                self.delete_log_project
            )
            self.mcp.tool(name="resize_sms_quota", annotations=DESTRUCTIVE)(self.resize_sms_quota)
            self.mcp.tool(name="resize_email_quota", annotations=DESTRUCTIVE)(
                self.resize_email_quota
            )

    async def resize_metric_quota(
        self,
        body: ResizeMetricQuotaDto = Field(
            ...,
            description=(
                "ResizeMetricQuotaDto body. Required: packageId, quantity (host count). "
                "Optional: redirectUrl (defaulted), pay."
            ),
        ),
    ) -> BillingOrderResult:
        """Resize the account's metric quota — PLACES A PAID ORDER.

        An account owns at most one metric quota, so there is no ID: this always
        resizes the quota `get_current_quota category=metric` returns.

        ## Requirements
        - Server must run with --allow-write
        - Leave `redirectUrl` unset: the upstream allow-list rejects an empty or
          arbitrary URL, so the tool fills in the console's own quota page.
        - Costs money and cannot be undone — quote it with `get_resize_price
          category=metric package_id=<pkg> quantity=<n>` and show the user the
          amount before calling this.
        - `packageId` must come from a metric quota class's
          `config.retentions[].packageId` (`list_quota_classes category=metric`),
          and `quantity` must respect that retention's minResource/maxResource/step.
        - The platform only grows a quota; `quantity` must stay above current usage
          (`get_quota_usage category=metric`).

        ## Workflow
        1. `get_current_quota category=metric` → resource id + current packageId
        2. `get_quota_detail category=metric resource_id=<id>` → current host count,
           retention and classId
        3. `list_quota_classes category=metric` → target class → retention → packageId
        4. `get_resize_price` with the same packageId + quantity → confirm the amount
        5. call this tool
        """
        data = await self.client.post(
            "/v2/metric/quota/resize", json=_order_payload(body, "metric")
        )
        return BillingOrderResult.from_api(data)

    async def create_log_project(
        self,
        body: CreateLogProjectDto = Field(
            ...,
            description=(
                "CreateLogProjectDto body. Required: projectName, packageId, quantity "
                "(GB-days). Optional: projectDescription, monthPeriod, buyWith, "
                "redirectUrl (defaulted), pay."
            ),
        ),
    ) -> BillingOrderResult:
        """Buy a new log project (log quota) — PLACES AN ORDER.

        Buying the log quota is what creates the log project; there is no separate
        "create project" call. The Basic class is the free option (1-day retention,
        fixed 10 GB/day); Pro classes are paid.

        ## Requirements
        - Server must run with --allow-write
        - Leave `redirectUrl` unset: the upstream allow-list rejects an empty or
          arbitrary URL, so the tool fills in the console's own quota page.
        - `packageId` must come from a log quota class's
          `config.retentions[].packageId` (`list_quota_classes category=log`).
        - `quantity` is GB-days: (log size per day) x (retention days). The per-day
          size must respect that retention's minSize/maxSize/step.
        - `projectName` must be unique on the account and match the naming rule
          (lowercase letters/digits/hyphen, starts with a letter).
        - Quote it first with `get_creation_price category=log package_id=<pkg>
          quantity=<n>` and show the user the amount unless the class is free.

        ## Workflow
        1. `list_quota_classes category=log` → pick class → retention entry
           (`amount` = retention days, `packageId`, `minSize`/`maxSize`/`step`)
        2. quantity = chosen GB per day x retention `amount`
        3. `get_creation_price category=log package_id=<pkg> quantity=<n>` → amount
        4. call this tool; if the response carries a `payment_url`, the order is
           pending until the user opens it
        5. `list_log_quotas` / `list_projects` to confirm the new project
        """
        data = await self.client.post("/v2/log/quotas", json=_order_payload(body, "log"))
        return BillingOrderResult.from_api(data)

    async def resize_log_project(
        self,
        resource_id: str = Field(
            ...,
            description=(
                "Log quota / log project ID to resize (the same id in list_log_quotas "
                "and list_projects)"
            ),
        ),
        body: ResizeLogProjectDto = Field(
            ...,
            description=(
                "ResizeLogProjectDto body. Required: packageId, quantity (GB-days). "
                "Optional: redirectUrl (defaulted), pay."
            ),
        ),
    ) -> BillingOrderResult:
        """Resize a log project's quota — PLACES A PAID ORDER.

        Used both to grow a Pro project and to upgrade a Basic (free) project to
        Pro. The billing resource ID and the log project ID are the same value.

        ## Requirements
        - Server must run with --allow-write
        - Leave `redirectUrl` unset: the upstream allow-list rejects an empty or
          arbitrary URL, so the tool fills in the console's own quota page.
        - Costs money and cannot be undone — quote it with `get_resize_price
          category=log package_id=<pkg> resource_id=<id> quantity=<n>` first.
        - `packageId` must come from a log quota class's
          `config.retentions[].packageId` (`list_quota_classes category=log`).
        - `quantity` is GB-days: (log size per day) x (retention days), and must stay
          above what the project already stores (`get_log_usage project_id=<id>`).

        ## Workflow
        1. `list_log_quotas` → the project's id
        2. `get_quota_detail category=log resource_id=<id>` → current size (GB-days),
           retention, classId, packageId
        3. `list_quota_classes category=log` → target class → retention → packageId
        4. `get_resize_price` with the same packageId + quantity → confirm the amount
        5. call this tool
        """
        validate_id(resource_id, "resource_id")
        data = await self.client.post(
            f"/v2/log/quotas/{resource_id}/resize", json=_order_payload(body, "log")
        )
        return BillingOrderResult.from_api(data)

    async def delete_log_project(
        self,
        resource_id: str = Field(
            ...,
            description=(
                "Log quota / log project ID to delete. IRREVERSIBLE — the project and "
                "its stored logs go with it."
            ),
        ),
    ) -> str:
        """Delete a log project and its quota. Treat as IRREVERSIBLE.

        Deletes the billing quota AND the log project it pays for — the ingested
        logs, mappings and certificates go with it. Unused prepaid balance is
        refunded to the credit wallet. The quota lands in the trash
        (`list_trash_quotas`), but getting it back is a paid recovery order placed
        from the console, not an undo — so plan as if the data is gone.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id AND the name with `list_log_quotas` (or `get_project`) and
          get the user to confirm that exact name first.
        - The account's `required` project (projectType=required) backs the platform
          itself — never delete it.
        """
        validate_id(resource_id, "resource_id")
        await self.client.delete(f"/v1/log/quotas/{resource_id}")
        return f"Log project {resource_id} deleted."

    async def resize_sms_quota(
        self,
        body: ResizeNotificationQuotaDto = Field(
            ...,
            description=(
                "ResizeNotificationQuotaDto body. Required: packageId. Optional: "
                "redirectUrl (defaulted), pay."
            ),
        ),
    ) -> BillingOrderResult:
        """Resize the SMS notification quota to another package — PLACES A PAID ORDER.

        An account owns at most one SMS quota, so there is no ID: this always
        resizes the quota `get_current_quota category=sms` returns. Packages are
        fixed bundles (sms5/sms10/... = 50/100/... messages), so only the package
        changes — there is no quantity.

        ## Requirements
        - Server must run with --allow-write
        - Leave `redirectUrl` unset: the upstream allow-list rejects an empty or
          arbitrary URL, so the tool fills in the console's own quota page.
        - Costs money and cannot be undone — quote it with `get_resize_price
          category=sms package_id=<pkg>` first.
        - `packageId` must come from `list_packages category=sms`, must differ from
          the current one, and its `amount` must cover what is already used
          (`get_quota_usage category=sms`).

        ## Workflow
        1. `get_current_quota category=sms` → current packageId
        2. `get_quota_usage category=sms` → messages already sent
        3. `list_packages category=sms` → pick a package whose amount exceeds usage
        4. `get_resize_price category=sms package_id=<pkg>` → confirm the amount
        5. call this tool
        """
        data = await self.client.post(
            "/v1/sms/quota/resize", json=_order_payload(body, "notification")
        )
        return BillingOrderResult.from_api(data)

    async def resize_email_quota(
        self,
        body: ResizeNotificationQuotaDto = Field(
            ...,
            description=(
                "ResizeNotificationQuotaDto body. Required: packageId. Optional: "
                "redirectUrl (defaulted), pay."
            ),
        ),
    ) -> BillingOrderResult:
        """Resize the email notification quota to another package — PLACES A PAID ORDER.

        An account owns at most one email quota, so there is no ID: this always
        resizes the quota `get_current_quota category=email` returns. Packages are
        fixed bundles (email5k/email10k/... messages), so only the package changes —
        there is no quantity.

        ## Requirements
        - Server must run with --allow-write
        - Leave `redirectUrl` unset: the upstream allow-list rejects an empty or
          arbitrary URL, so the tool fills in the console's own quota page.
        - Costs money and cannot be undone — quote it with `get_resize_price
          category=email package_id=<pkg>` first.
        - `packageId` must come from `list_packages category=email`, must differ from
          the current one, and its `amount` must cover what is already used
          (`get_quota_usage category=email`).

        ## Workflow
        1. `get_current_quota category=email` → current packageId
        2. `get_quota_usage category=email` → emails already sent
        3. `list_packages category=email` → pick a package whose amount exceeds usage
        4. `get_resize_price category=email package_id=<pkg>` → confirm the amount
        5. call this tool
        """
        data = await self.client.post(
            "/v1/email/quota/resize", json=_order_payload(body, "notification")
        )
        return BillingOrderResult.from_api(data)
