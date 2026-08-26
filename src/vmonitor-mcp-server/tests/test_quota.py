"""Tests for the vMonitor billing quota-usage / catalog / price / order tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorBillingClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.models import (
    BillingListData,
    BillingOrderResult,
    BillingResource,
    CreateLogProjectDto,
    ResizeLogProjectDto,
    ResizeMetricQuotaDto,
    ResizeNotificationQuotaDto,
)
from greennode.vmonitor_mcp_server.quota_catalog_handler import QuotaCatalogHandler
from greennode.vmonitor_mcp_server.quota_order_handler import QuotaOrderHandler
from greennode.vmonitor_mcp_server.quota_price_handler import QuotaPriceHandler
from greennode.vmonitor_mcp_server.quota_usage_handler import QuotaUsageHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
API = "https://vmonitorapis.vngcloud.vn/billing-api"

RES_ID = "11111111-2222-3333-4444-555555555555"
PKG_ID = "99999999-8888-7777-6666-555555555555"
REDIRECT = "https://console.example/quota-usages/log"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def _billing_client(sample_config):
    config = load_config(sample_config)
    return config, VmonitorBillingClient(config, TokenManager(config))


@pytest.fixture
def usage(sample_config):
    config, client = _billing_client(sample_config)
    return QuotaUsageHandler(MCPServer("test"), config, client)


@pytest.fixture
def catalog(sample_config):
    config, client = _billing_client(sample_config)
    return QuotaCatalogHandler(MCPServer("test"), config, client)


@pytest.fixture
def price(sample_config):
    config, client = _billing_client(sample_config)
    return QuotaPriceHandler(MCPServer("test"), config, client)


@pytest.fixture
def orders(sample_config):
    config, client = _billing_client(sample_config)
    return QuotaOrderHandler(MCPServer("test"), config, client, allow_write=True)


@pytest.mark.asyncio
async def test_all_billing_tools_read_only(usage, catalog, price):
    for handler, expected in (
        (usage, {"get_quota_usage", "get_composite_usage", "list_log_quotas"}),
        (catalog, {"list_tiers", "list_packages", "list_quota_classes"}),
        (price, {"get_creation_price", "get_resize_price"}),
    ):
        tools = await handler.mcp.list_tools()
        names = {t.name for t in tools}
        assert expected <= names
        for tool in tools:
            assert tool.annotations.read_only_hint is True


@respx.mock
@pytest.mark.asyncio
async def test_get_quota_usage_routes_by_category(usage):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/v1/email/quota/usages").mock(
        return_value=httpx.Response(200, json={"usage": {"amount": 138}})
    )

    result = await usage.get_quota_usage(category="email")

    assert route.called
    assert isinstance(result, BillingResource)
    assert result.data["usage"]["amount"] == 138


@respx.mock
@pytest.mark.asyncio
async def test_get_log_usage_validates_and_calls(usage):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/v1/log/quotas/{RES_ID}/usages").mock(
        return_value=httpx.Response(200, json={"usage": {"size": 10}})
    )

    result = await usage.get_log_usage(project_id=RES_ID)

    assert route.called
    assert result.data["usage"]["size"] == 10


@pytest.mark.asyncio
async def test_get_log_usage_rejects_traversal(usage):
    with pytest.raises(ValueError):
        await usage.get_log_usage(project_id="../etc")


@respx.mock
@pytest.mark.asyncio
async def test_get_quota_detail_uses_v2(usage):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/v2/metric/{RES_ID}/quota-detail").mock(
        return_value=httpx.Response(200, json={"id": RES_ID, "price": 0.0})
    )

    result = await usage.get_quota_detail(category="metric", resource_id=RES_ID)

    assert route.called
    assert result.id == RES_ID


@respx.mock
@pytest.mark.asyncio
async def test_list_tiers_parses_bare_list(catalog):
    _mock_iam(respx.mock)
    respx.get(f"{API}/v1/log/tiers").mock(
        return_value=httpx.Response(200, json=[{"id": 14, "name": "Free Tier"}])
    )

    result = await catalog.list_tiers(category="log")

    assert isinstance(result, BillingListData)
    assert result.total_item == 1
    assert result.items[0]["name"] == "Free Tier"


@respx.mock
@pytest.mark.asyncio
async def test_list_packages_passes_tier_id(catalog):
    _mock_iam(respx.mock)
    route = respx.get(f"{API}/v1/metric/packages").mock(
        return_value=httpx.Response(200, json=[{"id": "p1", "name": "free5x2"}])
    )

    result = await catalog.list_packages(category="metric", tier_id=6)

    assert route.calls.last.request.url.params["tier_id"] == "6"
    assert result.items[0]["name"] == "free5x2"


@respx.mock
@pytest.mark.asyncio
async def test_get_creation_price_builds_params(price):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v1/sms/prices/created-price").mock(
        return_value=httpx.Response(200, json={"originalCost": 880000})
    )

    result = await price.get_creation_price(
        category="sms",
        package_id="5cd4896e-938e-11eb-aad3-e0071b70d291",
        month_period=1,
        quantity=None,
        buy_with=None,
    )

    params = route.calls.last.request.url.params
    assert params["package_id"] == "5cd4896e-938e-11eb-aad3-e0071b70d291"
    assert params["month_period"] == "1"
    assert result.data["originalCost"] == 880000


@respx.mock
@pytest.mark.asyncio
async def test_resize_price_routes_log_through_resource(price):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v1/log/prices/quotas/{RES_ID}/resized-price").mock(
        return_value=httpx.Response(200, json={"originalCost": 0})
    )

    await price.get_resize_price(
        category="log", package_id=RES_ID, resource_id=RES_ID, quantity=None
    )

    assert route.called


@pytest.mark.asyncio
async def test_log_price_requires_resource_id(price):
    with pytest.raises(ValueError):
        await price.get_resize_price(
            category="log", package_id=RES_ID, resource_id=None, quantity=None
        )


@respx.mock
@pytest.mark.asyncio
async def test_creation_price_switches_to_v2_when_quantity_given(price):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v2/log/prices/created-price").mock(
        return_value=httpx.Response(200, json={"originalCost": 0})
    )

    await price.get_creation_price(
        category="log",
        package_id=PKG_ID,
        quantity=140,
        month_period=None,
        buy_with={"email": "e1"},
    )

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "redirectUrl": "",
        "packageId": PKG_ID,
        "quantity": 140,
        "buyWith": {"email": "e1"},
    }


@respx.mock
@pytest.mark.asyncio
async def test_resize_price_v2_sends_resource_in_body(price):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v2/metric/prices/resized-price").mock(
        return_value=httpx.Response(200, json={"originalCost": 1000})
    )

    await price.get_resize_price(
        category="metric", package_id=PKG_ID, resource_id=RES_ID, quantity=15
    )

    body = json.loads(route.calls.last.request.content)
    assert body["quantity"] == 15
    assert body["resourceId"] == RES_ID


@respx.mock
@pytest.mark.asyncio
async def test_resize_price_v2_routes_log_through_resource(price):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v2/log/prices/quotas/{RES_ID}/resized-price").mock(
        return_value=httpx.Response(200, json={"originalCost": 0})
    )

    await price.get_resize_price(
        category="log", package_id=PKG_ID, resource_id=RES_ID, quantity=140
    )

    assert route.called


@pytest.mark.asyncio
async def test_order_tools_hidden_without_allow_write(sample_config):
    config, client = _billing_client(sample_config)
    handler = QuotaOrderHandler(MCPServer("test"), config, client, allow_write=False)

    assert await handler.mcp.list_tools() == []


@pytest.mark.asyncio
async def test_order_tools_registered_with_allow_write(orders):
    tools = {t.name: t for t in await orders.mcp.list_tools()}

    assert set(tools) == {
        "resize_metric_quota",
        "create_log_project",
        "resize_log_project",
        "delete_log_project",
        "resize_sms_quota",
        "resize_email_quota",
    }
    assert tools["create_log_project"].annotations.destructive_hint is False
    for name in ("resize_metric_quota", "resize_log_project", "delete_log_project"):
        assert tools[name].annotations.destructive_hint is True


@respx.mock
@pytest.mark.asyncio
async def test_resize_metric_quota_posts_v2_order(orders):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v2/metric/quota/resize").mock(
        return_value=httpx.Response(
            200, json={"orderId": "o-1", "amount": 42000, "paymentUrl": "https://pay/1"}
        )
    )

    result = await orders.resize_metric_quota(
        body=ResizeMetricQuotaDto(packageId=PKG_ID, quantity=15, redirectUrl=REDIRECT)
    )

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "packageId": PKG_ID,
        "quantity": 15,
        "redirectUrl": REDIRECT,
        "pay": False,
    }
    assert isinstance(result, BillingOrderResult)
    assert (result.order_id, result.amount, result.payment_url) == (
        "o-1",
        42000.0,
        "https://pay/1",
    )


@respx.mock
@pytest.mark.asyncio
async def test_create_log_project_posts_v2_order(orders):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v2/log/quotas").mock(
        return_value=httpx.Response(200, json={"orderId": "o-2", "amount": 0})
    )

    result = await orders.create_log_project(
        body=CreateLogProjectDto(
            projectName="mcp-e2e-log", packageId=PKG_ID, quantity=140, redirectUrl=REDIRECT
        )
    )

    body = json.loads(route.calls.last.request.content)
    assert body["projectName"] == "mcp-e2e-log"
    assert body["quantity"] == 140
    assert body["monthPeriod"] == 1
    assert "buyWith" not in body
    assert result.order_id == "o-2"
    assert result.payment_url == ""


@pytest.mark.asyncio
async def test_create_log_project_rejects_invalid_project_name(orders):
    for bad in ("MCP-Upper", "-leading", "trailing-", "1starts-with-digit", ""):
        with pytest.raises(ValidationError):
            CreateLogProjectDto(
                projectName=bad, packageId=PKG_ID, quantity=10, redirectUrl=REDIRECT
            )


@pytest.mark.asyncio
async def test_order_dtos_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        ResizeNotificationQuotaDto(packageId=PKG_ID, redirectUrl=REDIRECT, quantiy=1)


@respx.mock
@pytest.mark.asyncio
async def test_resize_log_project_validates_and_posts(orders):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v2/log/quotas/{RES_ID}/resize").mock(
        return_value=httpx.Response(200, json={"orderId": "o-3", "amount": 1})
    )

    await orders.resize_log_project(
        resource_id=RES_ID,
        body=ResizeLogProjectDto(packageId=PKG_ID, quantity=300, pay=True, redirectUrl=REDIRECT),
    )

    body = json.loads(route.calls.last.request.content)
    assert body["pay"] is True
    assert route.called


@pytest.mark.asyncio
async def test_resize_log_project_rejects_traversal(orders):
    with pytest.raises(ValueError):
        await orders.resize_log_project(
            resource_id="../etc",
            body=ResizeLogProjectDto(packageId=PKG_ID, quantity=10, redirectUrl=REDIRECT),
        )


@respx.mock
@pytest.mark.asyncio
async def test_delete_log_project_calls_v1_and_confirms(orders):
    _mock_iam(respx.mock)
    route = respx.delete(f"{API}/v1/log/quotas/{RES_ID}").mock(
        return_value=httpx.Response(200, json={})
    )

    result = await orders.delete_log_project(resource_id=RES_ID)

    assert route.called
    assert RES_ID in result


@pytest.mark.asyncio
async def test_delete_log_project_rejects_traversal(orders):
    with pytest.raises(ValueError):
        await orders.delete_log_project(resource_id="../../etc/passwd")


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["sms", "email"])
async def test_resize_notification_quota_posts_v1_order(orders, category):
    _mock_iam(respx.mock)
    route = respx.post(f"{API}/v1/{category}/quota/resize").mock(
        return_value=httpx.Response(200, json={"orderId": "o-4", "paymentUrl": "https://pay/4"})
    )

    method = getattr(orders, f"resize_{category}_quota")
    result = await method(body=ResizeNotificationQuotaDto(packageId=PKG_ID, redirectUrl=REDIRECT))

    body = json.loads(route.calls.last.request.content)
    assert body == {"packageId": PKG_ID, "redirectUrl": REDIRECT, "pay": False}
    assert result.payment_url == "https://pay/4"


@pytest.mark.asyncio
async def test_order_dtos_reject_an_empty_redirect_url():
    """The billing API allow-lists redirectUrl, so an empty string is never valid."""
    with pytest.raises(ValidationError):
        ResizeMetricQuotaDto(packageId=PKG_ID, quantity=15, redirectUrl="")


@respx.mock
@pytest.mark.asyncio
async def test_order_defaults_redirect_url_to_the_console_quota_page(orders):
    """An order that omits redirectUrl gets the console page for its category."""
    _mock_iam(respx.mock)
    log = respx.post(f"{API}/v2/log/quotas").mock(
        return_value=httpx.Response(200, json={"orderId": "o-5"})
    )
    sms = respx.post(f"{API}/v1/sms/quota/resize").mock(
        return_value=httpx.Response(200, json={"orderId": "o-6"})
    )

    await orders.create_log_project(
        body=CreateLogProjectDto(projectName="mcp-e2e-log", packageId=PKG_ID, quantity=140)
    )
    await orders.resize_sms_quota(body=ResizeNotificationQuotaDto(packageId=PKG_ID))

    assert json.loads(log.calls.last.request.content)["redirectUrl"] == (
        "https://vmonitor.console.vngcloud.vn/quota-usages/log"
    )
    assert json.loads(sms.calls.last.request.content)["redirectUrl"] == (
        "https://vmonitor.console.vngcloud.vn/quota-usages/notification"
    )
