"""Listeners: CRUD, the CIDR string quirk and the request/response name drift."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import (
    LB,
    LISTENER,
    POOL,
    PROJECT,
    SAMPLE_LISTENER,
    VLB_HCM3,
    data_env,
    list_env,
    mock_iam,
)
from greennode.vlb_mcp_server.listener_handler import ListenerHandler
from greennode.vlb_mcp_server.models import (
    CreateListenerDto,
    HeaderDto,
    UpdateListenerDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


LISTENERS = f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/{LB}/listeners"


@pytest.fixture
def listeners_rw(config, client, no_cache):
    return ListenerHandler(MCPServer("t"), config, client, no_cache, allow_write=True)


@pytest.fixture
def listeners_ro(config, client, no_cache):
    return ListenerHandler(MCPServer("t"), config, client, no_cache, allow_write=False)


def _create(**over) -> CreateListenerDto:
    args = {
        "listenerName": "web-listen",
        "listenerProtocol": "HTTP",
        "listenerProtocolPort": 80,
        "timeoutClient": 50,
        "timeoutMember": 50,
        "timeoutConnection": 5,
    }
    args.update(over)
    return CreateListenerDto(**args)


# --- registration ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_mode_registers_no_write_tools(listeners_ro):
    assert {t.name for t in await listeners_ro.mcp.list_tools()} == {
        "list_listeners",
        "get_listener",
    }


@pytest.mark.asyncio
async def test_write_mode_adds_exactly_the_mutating_tools(listeners_rw, listeners_ro):
    read = {t.name for t in await listeners_ro.mcp.list_tools()}
    write = {t.name for t in await listeners_rw.mcp.list_tools()}
    assert write - read == {"create_listener", "update_listener", "delete_listener"}


# --- reads -------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_listeners_maps_the_response_spellings(listeners_ro):
    mock_iam(respx.mock)
    respx.get(LISTENERS).mock(return_value=httpx.Response(200, json=list_env([SAMPLE_LISTENER])))
    result = await listeners_ro.list_listeners(load_balancer_id=LB, region="HCM-3")
    listener = result.listeners[0]
    assert listener.id == LISTENER
    # request: listenerName / listenerProtocol / listenerProtocolPort
    # response: name / protocol / protocolPort
    assert (listener.name, listener.protocol, listener.port) == ("https-443", "HTTPS", 443)
    assert listener.default_pool_id == POOL


@respx.mock
@pytest.mark.asyncio
async def test_get_listener_splits_the_cidr_string_into_a_list(listeners_ro):
    mock_iam(respx.mock)
    respx.get(f"{LISTENERS}/{LISTENER}").mock(
        return_value=httpx.Response(
            200,
            json=data_env({**SAMPLE_LISTENER, "allowedCidrs": "10.0.0.0/8, 192.168.0.0/16"}),
        )
    )
    listener = await listeners_ro.get_listener(
        load_balancer_id=LB, listener_id=LISTENER, region="HCM-3"
    )
    assert listener.allowed_cidrs == ["10.0.0.0/8", "192.168.0.0/16"]
    assert listener.blocked_cidrs == []


@respx.mock
@pytest.mark.asyncio
async def test_the_headers_field_is_not_the_available_set(listeners_ro):
    """`headers` holds the names of the CONFIGURED insertHeaders, nothing more.

    This file used to assert the opposite — that `headers` was "names a
    listener MAY insert" and `insertHeaders` "what IS inserted" — because the
    spec reads that way. Two live observations killed it: an HTTPS listener
    reported the three names of its own configured headers rather than the
    eleven `list_listener_headers` returns, and clearing `insertHeaders`
    emptied `headers` in the same write. `list_listener_headers` is the only
    source of the available set.
    """
    mock_iam(respx.mock)
    respx.get(f"{LISTENERS}/{LISTENER}").mock(
        return_value=httpx.Response(200, json=data_env(SAMPLE_LISTENER))
    )
    listener = await listeners_ro.get_listener(
        load_balancer_id=LB, listener_id=LISTENER, region="HCM-3"
    )
    assert listener.inserted_header_names == ["X-Forwarded-For"]
    assert listener.insert_headers[0].headerName == "X-Forwarded-Proto"
    assert not hasattr(listener, "available_headers")


@respx.mock
@pytest.mark.asyncio
async def test_get_listener_maps_the_client_certificate_field(listeners_ro):
    """Request says clientCertificate; response says clientCertificateAuthentication."""
    mock_iam(respx.mock)
    respx.get(f"{LISTENERS}/{LISTENER}").mock(
        return_value=httpx.Response(
            200,
            json=data_env({**SAMPLE_LISTENER, "clientCertificateAuthentication": "ca-mtls"}),
        )
    )
    listener = await listeners_ro.get_listener(
        load_balancer_id=LB, listener_id=LISTENER, region="HCM-3"
    )
    assert listener.client_certificate == "ca-mtls"


# --- CIDR handling -------------------------------------------------------------------


def test_create_dto_joins_a_cidr_list_into_one_string():
    dto = _create(allowedCidrs=["10.0.0.0/8", "192.168.0.0/16"])
    assert dto.allowedCidrs == "10.0.0.0/8,192.168.0.0/16"


def test_create_dto_accepts_a_cidr_string_unchanged():
    dto = _create(blockedCidrs="9.9.9.9/32")
    assert dto.blockedCidrs == "9.9.9.9/32"


# --- writes ------------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_listener_sends_only_declared_fields(listeners_rw):
    mock_iam(respx.mock)
    route = respx.post(LISTENERS).mock(return_value=httpx.Response(200, json={"uuid": LISTENER}))
    result = await listeners_rw.create_listener(
        load_balancer_id=LB, body=_create(defaultPoolId=POOL), region="HCM-3"
    )
    assert json.loads(route.calls[0].request.content) == {
        "listenerName": "web-listen",
        "listenerProtocol": "HTTP",
        "listenerProtocolPort": 80,
        "timeoutClient": 50,
        "timeoutMember": 50,
        "timeoutConnection": 5,
        "defaultPoolId": POOL,
    }
    assert result.id == LISTENER


@respx.mock
@pytest.mark.asyncio
async def test_create_listener_serialises_headers_and_alpn(listeners_rw):
    mock_iam(respx.mock)
    route = respx.post(LISTENERS).mock(return_value=httpx.Response(200, json={"uuid": LISTENER}))
    body = _create(
        listenerProtocol="HTTPS",
        listenerProtocolPort=443,
        defaultCertificateAuthority="ca-1",
        # Outside the Access-* family the only value the gateway accepts is
        # "true" — it answers 400 "Invalid value for header '<name>': <value>".
        insertHeaders=[HeaderDto(headerName="X-Forwarded-Proto", headerValue="true")],
        alpnProtocols=["h2", "http/1.1"],
        tlsSecurityPolicy="MODERN",
    )
    await listeners_rw.create_listener(load_balancer_id=LB, body=body, region="HCM-3")
    sent = json.loads(route.calls[0].request.content)
    assert sent["insertHeaders"] == [{"headerName": "X-Forwarded-Proto", "headerValue": "true"}]
    assert sent["alpnProtocols"] == ["h2", "http/1.1"]
    assert sent["tlsSecurityPolicy"] == "MODERN"


@respx.mock
@pytest.mark.asyncio
async def test_update_listener_tolerates_an_empty_200(listeners_rw):
    mock_iam(respx.mock)
    route = respx.put(f"{LISTENERS}/{LISTENER}").mock(return_value=httpx.Response(200))
    message = await listeners_rw.update_listener(
        load_balancer_id=LB,
        listener_id=LISTENER,
        body=UpdateListenerDto(timeoutClient=60, timeoutMember=60, timeoutConnection=10),
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {
        "timeoutClient": 60,
        "timeoutMember": 60,
        "timeoutConnection": 10,
    }
    assert LISTENER in message


@respx.mock
@pytest.mark.asyncio
async def test_delete_listener_tolerates_an_empty_202(listeners_rw):
    mock_iam(respx.mock)
    route = respx.delete(f"{LISTENERS}/{LISTENER}").mock(return_value=httpx.Response(202))
    message = await listeners_rw.delete_listener(
        load_balancer_id=LB, listener_id=LISTENER, region="HCM-3"
    )
    assert route.called and LISTENER in message


# --- DTO strictness ---------------------------------------------------------------------


def test_create_listener_dto_rejects_an_unknown_alpn_protocol():
    with pytest.raises(ValidationError):
        _create(alpnProtocols=["h3"])


def test_create_listener_dto_rejects_an_unknown_tls_policy():
    with pytest.raises(ValidationError):
        _create(tlsSecurityPolicy="ANCIENT")


def test_create_listener_dto_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        _create(bogus=1)


def test_create_listener_dto_requires_the_three_timeouts():
    """Unlike the embedded listener in create_load_balancer, these are mandatory here."""
    with pytest.raises(ValidationError):
        CreateListenerDto(
            listenerName="web-listen", listenerProtocol="HTTP", listenerProtocolPort=80
        )


# --- guards -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_refuse_in_read_only_mode(listeners_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await listeners_ro.create_listener(load_balancer_id=LB, body=_create(), region="HCM-3")


@pytest.mark.asyncio
async def test_listener_tools_reject_a_reserved_load_balancer_segment(listeners_ro):
    with pytest.raises(ValueError, match="reserved VLB path segment"):
        await listeners_ro.list_listeners(load_balancer_id="headers", region="HCM-3")


@respx.mock
@pytest.mark.asyncio
async def test_a_null_header_value_does_not_break_a_listener_read(listeners_ro):
    """A present-but-null header field must read as "", not crash HeaderDto."""
    mock_iam(respx.mock)
    respx.get(f"{LISTENERS}/{LISTENER}").mock(
        return_value=httpx.Response(
            200,
            json=data_env(
                {
                    **SAMPLE_LISTENER,
                    "insertHeaders": [{"headerName": "X-Forwarded-For", "headerValue": None}],
                }
            ),
        )
    )
    listener = await listeners_ro.get_listener(
        load_balancer_id=LB, listener_id=LISTENER, region="HCM-3"
    )
    assert listener.insert_headers[0].headerValue == ""
