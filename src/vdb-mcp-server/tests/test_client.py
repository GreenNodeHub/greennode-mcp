"""Tests for VdbClient: family routing and the `user-type` billing header."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.mcp_core.auth import TokenManager
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import (
    VDB_KAFKA_SERVICE,
    VDB_MEMORY_SERVICE,
    VDB_POSTGRESQL_SERVICE,
    VDB_RELATIONAL_SERVICE,
    load_config,
)


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
GATEWAY = "https://vdb-gateway.vngcloud.vn"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def client(sample_config):
    config = load_config(sample_config)
    return VdbClient(config, TokenManager(config))


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "prefix"),
    [
        (VDB_RELATIONAL_SERVICE, "vdb-relational"),
        (VDB_MEMORY_SERVICE, "vdb-memory"),
        (VDB_POSTGRESQL_SERVICE, "vdb-postgresql"),
        (VDB_KAFKA_SERVICE, "vdb-kafka"),
    ],
)
async def test_call_routes_to_the_requested_family(client, service, prefix):
    _mock_iam(respx.mock)
    route = respx.get(f"{GATEWAY}/{prefix}/v1/things").mock(
        return_value=httpx.Response(200, json={"code": 200, "message": "ok", "data": []})
    )
    await client.call("GET", "/v1/things", service=service)
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_user_type_header_is_sent_only_when_asked(client):
    """The billing-flow header belongs on order endpoints and nowhere else.

    Sending `ROOT_USER` unconditionally would silently opt every call into the
    Checkout flow, so the header is absent unless a tool asks for it.
    """
    _mock_iam(respx.mock)
    route = respx.post(f"{GATEWAY}/vdb-relational/v1/payment/database-instances").mock(
        return_value=httpx.Response(200, json={"code": 200, "message": "ok", "data": {}})
    )

    await client.call(
        "POST",
        "/v1/payment/database-instances",
        service=VDB_RELATIONAL_SERVICE,
        json={"name": "db-1"},
        user_type="IAM_USER",
    )
    assert route.calls.last.request.headers["user-type"] == "IAM_USER"

    await client.call(
        "POST",
        "/v1/payment/database-instances",
        service=VDB_RELATIONAL_SERVICE,
        json={"name": "db-1"},
    )
    assert "user-type" not in route.calls.last.request.headers


@respx.mock
@pytest.mark.asyncio
async def test_user_type_survives_a_token_refresh(client):
    """A stale token must not quietly change which billing flow is used."""
    _mock_iam(respx.mock)
    route = respx.post(f"{GATEWAY}/vdb-memory/v1/payment/database-instances").mock(
        side_effect=[
            httpx.Response(401, json={"message": "expired"}),
            httpx.Response(200, json={"code": 200, "message": "ok", "data": {}}),
        ]
    )

    await client.call(
        "POST",
        "/v1/payment/database-instances",
        service=VDB_MEMORY_SERVICE,
        json={"name": "redis-1"},
        user_type="ROOT_USER",
    )
    assert route.call_count == 2
    assert [c.request.headers["user-type"] for c in route.calls] == ["ROOT_USER", "ROOT_USER"]


@respx.mock
@pytest.mark.asyncio
async def test_delete_can_carry_an_id_array(client):
    """vDB deletes backups and configurations with a JSON array body."""
    _mock_iam(respx.mock)
    route = respx.delete(f"{GATEWAY}/vdb-relational/v1/configurations/delete").mock(
        return_value=httpx.Response(200, json={"code": 200, "message": "ok", "data": True})
    )
    await client.call(
        "DELETE",
        "/v1/configurations/delete",
        service=VDB_RELATIONAL_SERVICE,
        json=["cfg-1", "cfg-2"],
    )
    assert route.calls.last.request.content == b'["cfg-1","cfg-2"]'


def test_default_service_is_relational(client):
    assert client._default_service == VDB_RELATIONAL_SERVICE


def test_user_agent_identifies_this_server(client):
    assert client._user_agent.startswith("vdb-mcp-server/")
