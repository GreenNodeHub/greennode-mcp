"""Load balancer CRUD, the RPC-style actions, status roll-up and local validation."""

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
    SAMPLE_LB,
    SAMPLE_LISTENER,
    SAMPLE_POOL,
    SUBNET,
    VLB_HCM3,
    data_env,
    list_env,
    mock_iam,
    paging_env,
)
from greennode.vlb_mcp_server.loadbalancer_handler import LoadBalancerHandler
from greennode.vlb_mcp_server.models import (
    CreatedResourceData,
    CreateHealthMonitorDto,
    CreateLoadBalancerDto,
    EmbeddedListenerDto,
    EmbeddedPoolDto,
    LoadBalancerListData,
    MemberDto,
)
from mcp.server.mcpserver import MCPServer


BASE = f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers"


@pytest.fixture
def lbs_rw(config, client, no_cache):
    return LoadBalancerHandler(MCPServer("t"), config, client, no_cache, allow_write=True)


@pytest.fixture
def lbs_ro(config, client, no_cache):
    return LoadBalancerHandler(MCPServer("t"), config, client, no_cache, allow_write=False)


async def _list(handler, **over):
    """Call list_load_balancers with every parameter set.

    Handlers are called directly here, so `Field(...)` defaults arrive as
    FieldInfo objects rather than values — MCP resolves them only when a tool is
    invoked through the protocol.
    """
    args = {
        "name": None,
        "status": None,
        "subnet_id": None,
        "vpc_id": None,
        "zone_id": None,
        "page": 1,
        "size": 500,
        "region": "HCM-3",
        "refresh": True,
    }
    args.update(over)
    return await handler.list_load_balancers(**args)


def _minimal_create() -> CreateLoadBalancerDto:
    return CreateLoadBalancerDto(
        name="prod-lb",
        packageId="lbp-small",
        scheme="Internet",
        subnetId=SUBNET,
        type="Layer 7",
    )


# --- registration ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_mode_registers_no_write_tools(lbs_ro):
    assert {t.name for t in await lbs_ro.mcp.list_tools()} == {
        "list_load_balancers",
        "get_load_balancer",
        "list_load_balancers_by_subnet",
        "list_load_balancer_scale_history",
        "get_load_balancer_clone_metadata",
        "get_load_balancer_status",
        "validate_load_balancer_create",
    }


@pytest.mark.asyncio
async def test_write_mode_adds_exactly_the_mutating_tools(lbs_rw, lbs_ro):
    read = {t.name for t in await lbs_ro.mcp.list_tools()}
    write = {t.name for t in await lbs_rw.mcp.list_tools()}
    assert write - read == {
        "create_load_balancer",
        "delete_load_balancer",
        "resize_load_balancer",
        "configure_load_balancer_scaling",
        "update_load_balancer_bandwidth",
        "create_load_balancer_clone",
    }


# --- reads ------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_load_balancers_maps_the_response_spellings(lbs_ro):
    mock_iam(respx.mock)
    respx.get(BASE).mock(return_value=httpx.Response(200, json=paging_env([SAMPLE_LB])))
    result = await _list(lbs_ro)
    assert isinstance(result, LoadBalancerListData)
    lb = result.load_balancers[0]
    assert lb.id == LB
    # request spells it `scheme`, response spells it `loadBalancerSchema`
    assert lb.scheme == "Internet"
    assert lb.type == "Layer 7"
    assert result.total_item == 1


@respx.mock
@pytest.mark.asyncio
async def test_list_load_balancers_sends_flat_filter_params(lbs_ro):
    mock_iam(respx.mock)
    route = respx.get(BASE).mock(return_value=httpx.Response(200, json=paging_env([SAMPLE_LB])))
    await _list(lbs_ro, name="prod", page=1, size=10)
    params = dict(route.calls[0].request.url.params)
    assert params == {"page": "1", "size": "10", "name": "prod"}


@respx.mock
@pytest.mark.asyncio
async def test_list_load_balancers_flags_a_filter_the_server_ignored(lbs_ro):
    """A silently-ignored server-side filter must not look like a successful filter."""
    mock_iam(respx.mock)
    respx.get(BASE).mock(
        return_value=httpx.Response(
            200, json=paging_env([SAMPLE_LB, {**SAMPLE_LB, "uuid": "lb-2", "name": "other"}])
        )
    )
    result = await _list(lbs_ro, name="prod")
    assert result.filter_applied is False
    # ...and the unmatched entries are still returned, not silently dropped
    assert len(result.load_balancers) == 2


@respx.mock
@pytest.mark.asyncio
async def test_list_load_balancers_follows_pages_when_truncated(lbs_ro):
    mock_iam(respx.mock)
    respx.get(BASE).mock(
        side_effect=[
            httpx.Response(200, json=paging_env([SAMPLE_LB], total=2)),
            httpx.Response(200, json=paging_env([{**SAMPLE_LB, "uuid": "lb-2"}], total=2)),
        ]
    )
    result = await _list(lbs_ro)
    assert [lb.id for lb in result.load_balancers] == [LB, "lb-2"]


@respx.mock
@pytest.mark.asyncio
async def test_get_load_balancer(lbs_ro):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/{LB}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_LB)))
    lb = await lbs_ro.get_load_balancer(load_balancer_id=LB, region="HCM-3")
    assert (lb.id, lb.name, lb.display_status) == (LB, "prod-lb", "ACTIVE")


@pytest.mark.parametrize("reserved", ["packages", "all_packages", "headers", "subnet"])
@pytest.mark.asyncio
async def test_get_load_balancer_refuses_a_reserved_path_segment(lbs_ro, reserved):
    """These static segments collide with /loadBalancers/{id} and would silently hit them."""
    with pytest.raises(ValueError, match="reserved VLB path segment"):
        await lbs_ro.get_load_balancer(load_balancer_id=reserved, region="HCM-3")


@respx.mock
@pytest.mark.asyncio
async def test_list_load_balancers_by_subnet_reads_a_bare_array(lbs_ro):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/subnet/{SUBNET}").mock(return_value=httpx.Response(200, json=[SAMPLE_LB]))
    result = await lbs_ro.list_load_balancers_by_subnet(
        subnet_id=SUBNET, region="HCM-3", refresh=True
    )
    assert result.load_balancers[0].id == LB


@respx.mock
@pytest.mark.asyncio
async def test_list_scale_history_reads_a_bare_array(lbs_ro):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/{LB}/scale-history").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 7,
                    "eventType": "SCALE_OUT",
                    "adjustmentNumber": 1,
                    "total": 3,
                    "scaledAt": "2026-01-02T03:04:05Z",
                }
            ],
        )
    )
    result = await lbs_ro.list_load_balancer_scale_history(load_balancer_id=LB, region="HCM-3")
    assert result.events[0].event_type == "SCALE_OUT"
    assert result.events[0].total == 3


@respx.mock
@pytest.mark.asyncio
async def test_get_clone_metadata_reads_a_bare_object(lbs_ro):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/{LB}/cloneMetadata").mock(
        return_value=httpx.Response(
            200,
            json={
                "loadBalancerName": "prod-lb",
                "certificates": [
                    {
                        "listenerId": LISTENER,
                        "defaultCertificateId": "ca-1",
                        "sniCertificateIds": ["ca-2"],
                    }
                ],
            },
        )
    )
    meta = await lbs_ro.get_load_balancer_clone_metadata(load_balancer_id=LB, region="HCM-3")
    assert meta.load_balancer_name == "prod-lb"
    assert meta.certificates[0].listener_id == LISTENER


# --- status roll-up ----------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_get_load_balancer_status_fans_out_and_settles(lbs_ro):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/{LB}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_LB)))
    respx.get(f"{BASE}/{LB}/listeners").mock(
        return_value=httpx.Response(200, json=list_env([SAMPLE_LISTENER]))
    )
    respx.get(f"{BASE}/{LB}/pools").mock(
        return_value=httpx.Response(200, json=list_env([SAMPLE_POOL]))
    )
    status = await lbs_ro.get_load_balancer_status(load_balancer_id=LB, region="HCM-3")
    assert status.settled is True
    assert [x.id for x in status.listeners] == [LISTENER]
    assert [x.id for x in status.pools] == [POOL]


@respx.mock
@pytest.mark.asyncio
async def test_get_load_balancer_status_is_unsettled_while_a_child_is_in_flight(lbs_ro):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/{LB}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_LB)))
    respx.get(f"{BASE}/{LB}/listeners").mock(
        return_value=httpx.Response(
            200, json=list_env([{**SAMPLE_LISTENER, "progressStatus": "UPDATING"}])
        )
    )
    respx.get(f"{BASE}/{LB}/pools").mock(return_value=httpx.Response(200, json=list_env([])))
    status = await lbs_ro.get_load_balancer_status(load_balancer_id=LB, region="HCM-3")
    assert status.settled is False


@respx.mock
@pytest.mark.asyncio
async def test_get_load_balancer_status_fails_open_on_an_unknown_status(lbs_ro):
    """No status enums exist in the spec — an unrecognised value must not block forever."""
    mock_iam(respx.mock)
    respx.get(f"{BASE}/{LB}").mock(
        return_value=httpx.Response(
            200, json=data_env({**SAMPLE_LB, "progressStatus": "SOMETHING_NEW"})
        )
    )
    respx.get(f"{BASE}/{LB}/listeners").mock(return_value=httpx.Response(200, json=list_env([])))
    respx.get(f"{BASE}/{LB}/pools").mock(return_value=httpx.Response(200, json=list_env([])))
    status = await lbs_ro.get_load_balancer_status(load_balancer_id=LB, region="HCM-3")
    assert status.settled is True
    assert status.progress_status == "SOMETHING_NEW"


# --- local validation ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_accepts_a_sane_layer7_body(lbs_ro):
    result = await lbs_ro.validate_load_balancer_create(body=_minimal_create())
    assert result.ok is True
    assert result.errors == []


@pytest.mark.asyncio
async def test_validate_rejects_an_http_listener_on_a_layer4_load_balancer(lbs_ro):
    body = CreateLoadBalancerDto(
        name="prod-lb",
        packageId="lbp-small",
        scheme="Internet",
        subnetId=SUBNET,
        type="Layer 4",
        listener=EmbeddedListenerDto(listenerName="web-listen", listenerProtocol="HTTP"),
    )
    result = await lbs_ro.validate_load_balancer_create(body=body)
    assert result.ok is False
    assert any("Layer 4" in e for e in result.errors)


def test_an_https_listener_without_a_certificate_never_builds():
    """The rule lives on the DTO, so it guards every path into the API.

    It used to be checked only by validate_load_balancer_create, which an agent
    is free to skip; on the DTO it also covers create_load_balancer and
    create_listener. The gateway answers 400 for this body, so failing here
    costs a round trip instead of a rejected create.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="defaultCertificateAuthority"):
        EmbeddedListenerDto(listenerName="web-listen", listenerProtocol="HTTPS")


@pytest.mark.asyncio
async def test_validate_warns_that_an_internal_lb_has_no_public_address(lbs_ro):
    body = CreateLoadBalancerDto(
        name="prod-lb",
        packageId="lbp-small",
        scheme="Internal",
        subnetId=SUBNET,
        type="Layer 7",
    )
    result = await lbs_ro.validate_load_balancer_create(body=body)
    assert result.ok is True
    assert any("Internal" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_validate_warns_the_name_can_never_be_changed(lbs_ro):
    result = await lbs_ro.validate_load_balancer_create(body=_minimal_create())
    assert any("cannot be renamed" in w for w in result.warnings)


# --- writes ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_load_balancer_sends_only_declared_fields(lbs_rw):
    mock_iam(respx.mock)
    route = respx.post(BASE).mock(return_value=httpx.Response(200, json={"uuid": LB}))
    result = await lbs_rw.create_load_balancer(body=_minimal_create(), region="HCM-3")
    assert json.loads(route.calls[0].request.content) == {
        "name": "prod-lb",
        "packageId": "lbp-small",
        "scheme": "Internet",
        "subnetId": SUBNET,
        "type": "Layer 7",
    }
    assert isinstance(result, CreatedResourceData)
    assert result.id == LB
    assert result.hint == ""


@respx.mock
@pytest.mark.asyncio
async def test_create_load_balancer_serialises_nested_members_as_an_array(lbs_rw):
    """The spec types LbPool.members as a single object; that is a spec bug, not a limit."""
    mock_iam(respx.mock)
    route = respx.post(BASE).mock(return_value=httpx.Response(200, json={"uuid": LB}))
    body = CreateLoadBalancerDto(
        name="prod-lb",
        packageId="lbp-small",
        scheme="Internet",
        subnetId=SUBNET,
        type="Layer 7",
        pool=EmbeddedPoolDto(
            poolName="web-pool",
            algorithm="ROUND_ROBIN",
            poolProtocol="HTTP",
            stickiness=False,
            tlsEncryption=False,
            healthMonitor=CreateHealthMonitorDto(
                healthCheckProtocol="HTTP",
                healthCheckPath="/",
                healthCheckMethod="GET",
                successCode="200",
                httpVersion="1.0",
                healthyThreshold=3,
                unhealthyThreshold=3,
                timeout=5,
                interval=30,
            ),
            members=[
                MemberDto(ipAddress="10.0.0.11", port=80, backup=False),
                MemberDto(ipAddress="10.0.0.12", port=80, backup=False),
            ],
        ),
    )
    await lbs_rw.create_load_balancer(body=body, region="HCM-3")
    sent = json.loads(route.calls[0].request.content)
    assert [m["ipAddress"] for m in sent["pool"]["members"]] == ["10.0.0.11", "10.0.0.12"]
    # `backup` is required by the API even when false — it must never be pruned.
    assert sent["pool"]["members"][0]["backup"] is False


@respx.mock
@pytest.mark.asyncio
async def test_create_load_balancer_survives_an_unrecognised_response_body(lbs_rw):
    """A successful create reported as a failure makes an agent retry and double-bill."""
    mock_iam(respx.mock)
    respx.post(BASE).mock(
        return_value=httpx.Response(200, json={"weirdIdentifier": "lb-9", "message": "ok"})
    )
    result = await lbs_rw.create_load_balancer(body=_minimal_create(), region="HCM-3")
    assert result.id == ""
    assert "list_load_balancers" in result.hint
    assert result.raw == {"weirdIdentifier": "lb-9", "message": "ok"}


@respx.mock
@pytest.mark.asyncio
async def test_delete_load_balancer_tolerates_an_empty_202(lbs_rw):
    mock_iam(respx.mock)
    route = respx.delete(f"{BASE}/{LB}").mock(return_value=httpx.Response(202))
    message = await lbs_rw.delete_load_balancer(load_balancer_id=LB, region="HCM-3")
    assert route.called
    assert LB in message


@respx.mock
@pytest.mark.asyncio
async def test_resize_load_balancer(lbs_rw):
    mock_iam(respx.mock)
    route = respx.put(f"{BASE}/{LB}/resize").mock(return_value=httpx.Response(200, json={}))
    await lbs_rw.resize_load_balancer(load_balancer_id=LB, package_id="lbp-medium", region="HCM-3")
    assert json.loads(route.calls[0].request.content) == {"packageId": "lbp-medium"}


@respx.mock
@pytest.mark.asyncio
async def test_update_bandwidth_uses_the_capital_w_spelling(lbs_rw):
    """`bandWidthPackageId` is the spelling the API declares — a typo here is a 400."""
    mock_iam(respx.mock)
    route = respx.put(f"{BASE}/{LB}/bandwidth").mock(
        return_value=httpx.Response(
            200, json={"lbUuid": LB, "packageId": "bandwidth-pack-1", "status": "ACTIVE"}
        )
    )
    result = await lbs_rw.update_load_balancer_bandwidth(
        load_balancer_id=LB, bandwidth_package_id="bandwidth-pack-abc", region="HCM-3"
    )
    assert json.loads(route.calls[0].request.content) == {
        "bandWidthPackageId": "bandwidth-pack-abc"
    }
    assert result.bandwidth_package_id == "bandwidth-pack-1"


@respx.mock
@pytest.mark.asyncio
async def test_configure_scaling_sends_the_nested_shape_and_tolerates_202(lbs_rw):
    mock_iam(respx.mock)
    route = respx.put(f"{BASE}/{LB}/rebalancing").mock(return_value=httpx.Response(202))
    await lbs_rw.configure_load_balancer_scaling(
        load_balancer_id=LB,
        max_size=5,
        subnets=[SUBNET, "sub-b"],
        min_size=3,
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {
        "scaling": {"minSize": 3, "maxSize": 5},
        "networking": {"subnets": [SUBNET, "sub-b"]},
    }


@pytest.mark.asyncio
async def test_configure_scaling_requires_the_primary_subnet_in_the_list(lbs_rw):
    with pytest.raises(ValueError, match="at least one subnet"):
        await lbs_rw.configure_load_balancer_scaling(
            load_balancer_id=LB, max_size=5, subnets=[], min_size=None, region="HCM-3"
        )


@respx.mock
@pytest.mark.asyncio
async def test_create_load_balancer_clone(lbs_rw):
    mock_iam(respx.mock)
    route = respx.post(f"{BASE}/{LB}/clone").mock(
        return_value=httpx.Response(200, json={"uuid": "lb-clone"})
    )
    result = await lbs_rw.create_load_balancer_clone(
        load_balancer_id=LB,
        name="prod-lb-copy",
        package_id="lbp-small",
        certificates=None,
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {
        "name": "prod-lb-copy",
        "packageId": "lbp-small",
    }
    assert result.id == "lb-clone"


# --- write guard -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_refuse_in_read_only_mode(lbs_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await lbs_ro.create_load_balancer(body=_minimal_create(), region="HCM-3")
    with pytest.raises(ValueError, match="--allow-write"):
        await lbs_ro.delete_load_balancer(load_balancer_id=LB, region="HCM-3")


# --- DTO strictness ------------------------------------------------------------------------


def test_create_dto_forbids_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateLoadBalancerDto(
            name="prod-lb",
            packageId="lbp-small",
            scheme="Internet",
            subnetId=SUBNET,
            type="Layer 7",
            bogus="x",
        )


@respx.mock
@pytest.mark.asyncio
async def test_list_load_balancers_cache_key_includes_the_local_filters(config, client, cache):
    """PR #29 review: the cache key held only the API params, while `status`,
    `subnet_id` and `vpc_id` are applied inside the cached fetch. A call with
    `status="ACTIVE"` then served its filtered list to the next unfiltered
    call for the whole TTL.
    """
    from mcp.server.mcpserver import MCPServer

    handler = LoadBalancerHandler(MCPServer("t"), config, client, cache, allow_write=False)
    mock_iam(respx.mock)
    deleting = {**SAMPLE_LB, "uuid": "lb-2", "displayStatus": "DELETING"}
    route = respx.get(BASE).mock(
        return_value=httpx.Response(200, json=paging_env([SAMPLE_LB, deleting]))
    )

    active = await _list(handler, status="ACTIVE", refresh=False)
    everything = await _list(handler, refresh=False)

    assert [lb.id for lb in active.load_balancers] == [SAMPLE_LB["uuid"]]
    assert [lb.id for lb in everything.load_balancers] == [SAMPLE_LB["uuid"], "lb-2"]
    assert everything.filtered_locally == []
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_a_named_list_returns_only_the_requested_page(lbs_ro):
    """PR #29 review: with `name` the gateway pages for real and totalItem
    counts every match, so walking on returned page 2 plus every later page
    under a `page=2` label. One request, one page.
    """
    mock_iam(respx.mock)
    page_two = [{**SAMPLE_LB, "uuid": f"lb-{n}", "name": f"vks-{n:02d}"} for n in range(10)]
    route = respx.get(BASE).mock(
        return_value=httpx.Response(200, json=paging_env(page_two, total=46, page=2, page_size=10))
    )
    result = await _list(lbs_ro, name="vks", page=2, size=10)
    assert route.call_count == 1
    assert len(result.load_balancers) == 10
    assert result.page == 2
