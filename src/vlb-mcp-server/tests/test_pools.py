"""Pools, members and the health monitor — the two read-modify-write footguns."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import (
    LB,
    POOL,
    PROJECT,
    SAMPLE_MEMBERS,
    SAMPLE_POOL,
    VLB_HCM3,
    data_env,
    list_env,
    mock_iam,
)
from greennode.vlb_mcp_server.models import (
    CreateHealthMonitorDto,
    CreatePoolDto,
    HealthMonitorUpdateData,
    MemberDto,
    MemberTargetDto,
    PoolMemberItem,
)
from greennode.vlb_mcp_server.pool_handler import PoolHandler
from mcp.server.mcpserver import MCPServer


BASE = f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers"
POOLS = f"{BASE}/{LB}/pools"
MEMBERS_URL = f"{POOLS}/{POOL}/members"

HM = {
    "uuid": "hm-1",
    "healthCheckProtocol": "HTTP",
    "healthCheckPath": "/healthz",
    "healthCheckMethod": "GET",
    "healthyThreshold": 3,
    "unhealthyThreshold": 3,
    "timeout": 5,
    "interval": 30,
    "successCode": "200",
    "httpVersion": "1.1",
    "displayStatus": "ACTIVE",
}


@pytest.fixture
def pools_rw(config, client, no_cache):
    return PoolHandler(MCPServer("t"), config, client, no_cache, allow_write=True)


@pytest.fixture
def pools_ro(config, client, no_cache):
    return PoolHandler(MCPServer("t"), config, client, no_cache, allow_write=False)


def _create_pool() -> CreatePoolDto:
    """A pool the gateway actually accepts.

    An HTTP pool must carry `stickiness` and `tlsEncryption`, and an HTTP probe
    must carry all four of path/method/successCode/httpVersion — verified
    against the gateway's own validator, which answers 400 for each omission.
    """
    return CreatePoolDto(
        poolName="web-pool",
        poolProtocol="HTTP",
        algorithm="ROUND_ROBIN",
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
    )


# --- registration -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_mode_registers_no_write_tools(pools_ro):
    assert {t.name for t in await pools_ro.mcp.list_tools()} == {
        "list_pools",
        "get_pool",
        "list_pool_members",
        "get_pool_health_monitor",
    }


@pytest.mark.asyncio
async def test_write_mode_adds_exactly_the_mutating_tools(pools_rw, pools_ro):
    read = {t.name for t in await pools_ro.mcp.list_tools()}
    write = {t.name for t in await pools_rw.mcp.list_tools()}
    assert write - read == {
        "create_pool",
        "update_pool",
        "delete_pool",
        "update_pool_members",
        "attach_pool_members",
        "detach_pool_members",
        "update_pool_health_monitor",
    }


# --- reads -------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_pools_maps_the_response_spellings(pools_ro):
    mock_iam(respx.mock)
    respx.get(POOLS).mock(return_value=httpx.Response(200, json=list_env([SAMPLE_POOL])))
    result = await pools_ro.list_pools(load_balancer_id=LB, region="HCM-3")
    pool = result.pools[0]
    assert pool.id == POOL
    # request says algorithm/poolProtocol/poolName; response says
    # loadBalanceMethod/protocol/name
    assert pool.algorithm == "ROUND_ROBIN"
    assert pool.protocol == "HTTP"
    assert pool.name == "web-pool"


@respx.mock
@pytest.mark.asyncio
async def test_get_pool(pools_ro):
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(
        return_value=httpx.Response(200, json=data_env({**SAMPLE_POOL, "healthMonitor": HM}))
    )
    pool = await pools_ro.get_pool(load_balancer_id=LB, pool_id=POOL, region="HCM-3")
    assert pool.health_monitor is not None
    assert pool.health_monitor.health_check_path == "/healthz"


@respx.mock
@pytest.mark.asyncio
async def test_list_pool_members_maps_address_and_protocol_port(pools_ro):
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    result = await pools_ro.list_pool_members(load_balancer_id=LB, pool_id=POOL, region="HCM-3")
    assert [(m.ip_address, m.port) for m in result.members] == [
        ("10.0.0.11", 80),
        ("10.0.0.12", 80),
    ]
    # a real read reports live state
    assert result.confirmed is True


@respx.mock
@pytest.mark.asyncio
async def test_get_pool_health_monitor(pools_ro):
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env(HM))
    )
    hm = await pools_ro.get_pool_health_monitor(load_balancer_id=LB, pool_id=POOL, region="HCM-3")
    assert hm.health_check_protocol == "HTTP"
    assert hm.interval == 30


def test_pool_member_item_round_trips_to_a_member_dto():
    """The read side spells it address/protocolPort; the write side ipAddress/port."""
    item = PoolMemberItem.from_api(SAMPLE_MEMBERS[0])
    dto = item.to_member_dto()
    assert isinstance(dto, MemberDto)
    assert dto.model_dump(exclude_none=True) == {
        "ipAddress": "10.0.0.11",
        "port": 80,
        "backup": False,
        "weight": 1,
        "name": "web-01",
        "monitorPort": 80,
    }


# --- creates / updates ---------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_pool_sends_only_declared_fields(pools_rw):
    mock_iam(respx.mock)
    route = respx.post(POOLS).mock(return_value=httpx.Response(200, json={"uuid": POOL}))
    result = await pools_rw.create_pool(load_balancer_id=LB, body=_create_pool(), region="HCM-3")
    assert json.loads(route.calls[0].request.content) == {
        "poolName": "web-pool",
        "poolProtocol": "HTTP",
        "algorithm": "ROUND_ROBIN",
        "stickiness": False,
        "tlsEncryption": False,
        "healthMonitor": {
            "healthCheckProtocol": "HTTP",
            "healthCheckPath": "/",
            "healthCheckMethod": "GET",
            "successCode": "200",
            "httpVersion": "1.0",
            "healthyThreshold": 3,
            "unhealthyThreshold": 3,
            "timeout": 5,
            "interval": 30,
        },
    }
    assert result.id == POOL


@respx.mock
@pytest.mark.asyncio
async def test_delete_pool_tolerates_an_empty_202(pools_rw):
    mock_iam(respx.mock)
    route = respx.delete(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(202))
    message = await pools_rw.delete_pool(load_balancer_id=LB, pool_id=POOL, region="HCM-3")
    assert route.called and POOL in message


@respx.mock
@pytest.mark.asyncio
async def test_update_pool_resends_the_existing_health_monitor(pools_rw):
    """Omitting `healthMonitor` does not leave the probe alone — it 400s.

    The gateway re-validates the pool's stored probe against whatever the body
    carries, so a body without a monitor is rejected with "If
    healthCheckProtocol field is HTTP, following fields must be specified: …".
    Observed live. A TCP probe survives the omission, which is why this only
    showed up on an HTTP pool.
    """
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_POOL)))
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env(HM))
    )
    route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))
    await pools_rw.update_pool(
        load_balancer_id=LB,
        pool_id=POOL,
        algorithm="LEAST_CONNECTIONS",
        stickiness=None,
        tls_encryption=None,
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["algorithm"] == "LEAST_CONNECTIONS"
    # The probe goes back exactly as it was, so this call cannot disturb it.
    assert sent["healthMonitor"]["healthCheckPath"] == HM["healthCheckPath"]
    assert sent["healthMonitor"]["interval"] == HM["interval"]
    assert "healthCheckProtocol" not in sent["healthMonitor"]  # immutable, never sent


@respx.mock
@pytest.mark.asyncio
async def test_update_pool_on_a_monitorless_pool_sends_no_monitor(pools_rw):
    """A pool created without a probe must not gain an invented one."""
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(
        return_value=httpx.Response(200, json=data_env({**SAMPLE_POOL, "protocol": "TCP"}))
    )
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env({}))
    )
    route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))
    await pools_rw.update_pool(
        load_balancer_id=LB,
        pool_id=POOL,
        algorithm="LEAST_CONNECTIONS",
        stickiness=None,
        tls_encryption=None,
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {"algorithm": "LEAST_CONNECTIONS"}


# --- members: the replace-versus-add footgun -------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_update_pool_members_sends_exactly_what_it_was_given(pools_rw):
    mock_iam(respx.mock)
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))
    await pools_rw.update_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        members=[MemberDto(ipAddress="10.0.0.99", port=80, backup=False)],
        region="HCM-3",
    )
    # Both primitive-int fields are filled in. monitorPort would be rejected at
    # 0; weight would be ACCEPTED at 0 and the member would silently take no
    # traffic — verified live, a member added without a weight came back with
    # weight=0.
    assert json.loads(route.calls[0].request.content) == {
        "members": [
            {
                "ipAddress": "10.0.0.99",
                "port": 80,
                "backup": False,
                "weight": 1,
                "monitorPort": 80,
            }
        ]
    }


@respx.mock
@pytest.mark.asyncio
async def test_attach_pool_members_keeps_the_existing_members(pools_rw):
    """The outage regression: a pool with two members must still have three after adding one."""
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))

    result = await pools_rw.attach_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        members=[MemberDto(ipAddress="10.0.0.13", port=80, backup=False)],
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)["members"]
    assert [m["ipAddress"] for m in sent] == ["10.0.0.11", "10.0.0.12", "10.0.0.13"]
    assert result.member_count == 3
    # the write is asynchronous, so this echoes what was requested — it is not a
    # read of live state, and must not be presented to the user as one
    assert result.confirmed is False


@respx.mock
@pytest.mark.asyncio
async def test_attach_pool_members_updates_a_duplicate_instead_of_doubling_it(pools_rw):
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))

    await pools_rw.attach_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        members=[MemberDto(ipAddress="10.0.0.11", port=80, backup=True, weight=5)],
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)["members"]
    assert len(sent) == 2
    replaced = next(m for m in sent if m["ipAddress"] == "10.0.0.11")
    assert replaced["weight"] == 5 and replaced["backup"] is True


@respx.mock
@pytest.mark.asyncio
async def test_detach_pool_members_removes_only_the_named_targets(pools_rw):
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))

    await pools_rw.detach_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        targets=[MemberTargetDto(ipAddress="10.0.0.11", port=80)],
        allow_empty=False,
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)["members"]
    assert [m["ipAddress"] for m in sent] == ["10.0.0.12"]


@respx.mock
@pytest.mark.asyncio
async def test_detach_pool_members_refuses_an_unmatched_target(pools_rw):
    """A typo must not read as a successful removal."""
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(ValueError, match="not in the pool"):
        await pools_rw.detach_pool_members(
            load_balancer_id=LB,
            pool_id=POOL,
            targets=[MemberTargetDto(ipAddress="10.0.0.99", port=80)],
            allow_empty=False,
            region="HCM-3",
        )
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_detach_pool_members_refuses_to_empty_the_pool_by_default(pools_rw):
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(ValueError, match="allow_empty"):
        await pools_rw.detach_pool_members(
            load_balancer_id=LB,
            pool_id=POOL,
            targets=[
                MemberTargetDto(ipAddress="10.0.0.11", port=80),
                MemberTargetDto(ipAddress="10.0.0.12", port=80),
            ],
            allow_empty=False,
            region="HCM-3",
        )
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_detach_pool_members_can_empty_the_pool_when_asked(pools_rw):
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))

    await pools_rw.detach_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        targets=[
            MemberTargetDto(ipAddress="10.0.0.11", port=80),
            MemberTargetDto(ipAddress="10.0.0.12", port=80),
        ],
        allow_empty=True,
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {"members": []}


# --- health monitor: writable only through the pool ---------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_update_health_monitor_carries_forward_the_pool_settings(pools_rw):
    """UpdatePoolRequest requires `algorithm`; sending a partial body silently resets it."""
    mock_iam(respx.mock)
    # The live GET returns the pool WITHOUT a nested healthMonitor, however the
    # spec types it — so the probe has to come from its own endpoint.
    respx.get(f"{POOLS}/{POOL}").mock(
        return_value=httpx.Response(
            200,
            json=data_env(
                {
                    **SAMPLE_POOL,
                    "loadBalanceMethod": "LEAST_CONNECTIONS",
                    "stickiness": True,
                    "tlsEncryption": True,
                }
            ),
        )
    )
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env(HM))
    )
    route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))

    await pools_rw.update_pool_health_monitor(
        load_balancer_id=LB,
        pool_id=POOL,
        health_check_path="/live",
        health_check_method=None,
        healthy_threshold=None,
        unhealthy_threshold=None,
        timeout=None,
        interval=None,
        success_code=None,
        domain_name=None,
        http_version=None,
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["algorithm"] == "LEAST_CONNECTIONS"
    assert sent["stickiness"] is True
    assert sent["tlsEncryption"] is True
    # only the path changed; the four required thresholds came from the pool
    assert sent["healthMonitor"]["healthCheckPath"] == "/live"
    assert sent["healthMonitor"]["healthyThreshold"] == 3
    assert sent["healthMonitor"]["interval"] == 30
    assert sent["healthMonitor"]["timeout"] == 5
    # the protocol is immutable and must never be sent on the update path
    assert "healthCheckProtocol" not in sent["healthMonitor"]


@respx.mock
@pytest.mark.asyncio
async def test_update_health_monitor_returns_the_requested_config_not_stale_state(pools_rw):
    """The result must not mix pre-update status with post-update settings.

    The PUT is asynchronous, so reading the monitor back immediately would
    report the old values. Echoing the requested configuration is honest only if
    it is not dressed up as a read: a distinct type, and no carried-over
    lifecycle fields.
    """
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_POOL)))
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env({**HM, "displayStatus": "ACTIVE"}))
    )
    respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))

    result = await pools_rw.update_pool_health_monitor(
        load_balancer_id=LB,
        pool_id=POOL,
        health_check_path="/live",
        health_check_method=None,
        healthy_threshold=None,
        unhealthy_threshold=None,
        timeout=None,
        interval=None,
        success_code=None,
        domain_name=None,
        http_version=None,
        region="HCM-3",
    )
    assert isinstance(result, HealthMonitorUpdateData)
    assert result.pool_id == POOL
    assert result.health_check_path == "/live"
    assert result.healthy_threshold == 3
    # the immutable protocol is carried through so the caller knows which probe
    # the settings apply to
    assert result.health_check_protocol == "HTTP"
    # ...but no lifecycle field is echoed: those would be pre-update values
    assert not hasattr(result, "display_status")
    assert not hasattr(result, "progress_status")
    assert "get_pool_health_monitor" in result.hint


@respx.mock
@pytest.mark.asyncio
async def test_update_health_monitor_refuses_when_the_pool_has_none(pools_rw):
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_POOL)))
    # A pool created without a monitor: the dedicated route has nothing either.
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env({}))
    )
    with pytest.raises(ValueError, match="no health monitor"):
        await pools_rw.update_pool_health_monitor(
            load_balancer_id=LB,
            pool_id=POOL,
            health_check_path="/live",
            health_check_method=None,
            healthy_threshold=None,
            unhealthy_threshold=None,
            timeout=None,
            interval=None,
            success_code=None,
            domain_name=None,
            http_version=None,
            region="HCM-3",
        )


def test_update_health_monitor_has_no_protocol_parameter(pools_rw):
    """healthCheckProtocol is immutable — offering it would promise what the API cannot do."""
    import inspect

    params = inspect.signature(pools_rw.update_pool_health_monitor).parameters
    assert "health_check_protocol" not in params


# --- guards ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_refuse_in_read_only_mode(pools_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await pools_ro.create_pool(load_balancer_id=LB, body=_create_pool(), region="HCM-3")
    with pytest.raises(ValueError, match="--allow-write"):
        await pools_ro.update_pool_members(
            load_balancer_id=LB, pool_id=POOL, members=[], region="HCM-3"
        )


@pytest.mark.asyncio
async def test_pool_tools_reject_a_reserved_load_balancer_segment(pools_ro):
    with pytest.raises(ValueError, match="reserved VLB path segment"):
        await pools_ro.list_pools(load_balancer_id="packages", region="HCM-3")


def test_create_pool_dto_forbids_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreatePoolDto(
            poolName="web-pool",
            poolProtocol="HTTP",
            algorithm="ROUND_ROBIN",
            healthMonitor=CreateHealthMonitorDto(healthCheckProtocol="TCP"),
            bogus=1,
        )


@respx.mock
@pytest.mark.asyncio
async def test_update_health_monitor_reads_the_monitor_from_its_own_endpoint(pools_rw):
    """The live regression: the pool response has no nested healthMonitor.

    `PoolDto` declares one, but `GET /pools/{poolId}` never populates it. When
    this tool trusted that field it raised "Pool <id> has no health monitor"
    for every pool that had one — the tool was dead for its entire surface, and
    no test caught it because the fixtures supplied the nested object the
    gateway does not send.
    """
    mock_iam(respx.mock)
    pool_route = respx.get(f"{POOLS}/{POOL}").mock(
        return_value=httpx.Response(200, json=data_env(SAMPLE_POOL))
    )
    monitor_route = respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env(HM))
    )
    put_route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))

    result = await pools_rw.update_pool_health_monitor(
        load_balancer_id=LB,
        pool_id=POOL,
        interval=45,
        health_check_path=None,
        health_check_method=None,
        healthy_threshold=None,
        unhealthy_threshold=None,
        timeout=None,
        success_code=None,
        domain_name=None,
        http_version=None,
        region="HCM-3",
    )
    assert pool_route.called and monitor_route.called
    sent = json.loads(put_route.calls[0].request.content)
    # The one requested change...
    assert sent["healthMonitor"]["interval"] == 45
    # ...and everything else carried forward from the two reads.
    assert sent["algorithm"] == SAMPLE_POOL["loadBalanceMethod"]
    assert sent["healthMonitor"]["healthCheckPath"] == HM["healthCheckPath"]
    assert sent["healthMonitor"]["timeout"] == HM["timeout"]
    assert result.interval == 45
    assert result.health_check_protocol == HM["healthCheckProtocol"]


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["TCP", "UDP", "PROXY"])
@pytest.mark.parametrize("flag", [False, None])
async def test_update_health_monitor_on_a_non_http_pool_omits_stickiness(pools_rw, protocol, flag):
    """The QC regression: changing `interval` on a TCP pool answered
    `400 "Stickiness cannot be specified for non-HTTP pools"`.

    The pool read turns a missing flag into `False`, and `exclude_none` keeps
    `False`, so the PUT carried `stickiness: false, tlsEncryption: false`. The
    gateway forbids both fields on any non-HTTP pool, even when false. Only
    the live tests on HTTP pools had covered this tool.
    """
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(
        return_value=httpx.Response(
            200,
            json=data_env(
                {**SAMPLE_POOL, "protocol": protocol, "stickiness": flag, "tlsEncryption": flag}
            ),
        )
    )
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(
            200,
            json=data_env(
                {
                    "healthCheckProtocol": "PING-UDP" if protocol == "UDP" else "TCP",
                    "healthyThreshold": 3,
                    "unhealthyThreshold": 3,
                    "timeout": 5,
                    "interval": 30,
                }
            ),
        )
    )
    put_route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))

    await pools_rw.update_pool_health_monitor(
        load_balancer_id=LB,
        pool_id=POOL,
        interval=15,
        health_check_path=None,
        health_check_method=None,
        healthy_threshold=None,
        unhealthy_threshold=None,
        timeout=None,
        success_code=None,
        domain_name=None,
        http_version=None,
        region="HCM-3",
    )
    sent = json.loads(put_route.calls[0].request.content)
    assert "stickiness" not in sent
    assert "tlsEncryption" not in sent
    assert sent["healthMonitor"]["interval"] == 15


@respx.mock
@pytest.mark.asyncio
async def test_update_health_monitor_on_an_http_pool_still_sends_stickiness(pools_rw):
    """The other half of the rule: an HTTP pool must carry both flags."""
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_POOL)))
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env(HM))
    )
    put_route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))

    await pools_rw.update_pool_health_monitor(
        load_balancer_id=LB,
        pool_id=POOL,
        interval=15,
        health_check_path=None,
        health_check_method=None,
        healthy_threshold=None,
        unhealthy_threshold=None,
        timeout=None,
        success_code=None,
        domain_name=None,
        http_version=None,
        region="HCM-3",
    )
    sent = json.loads(put_route.calls[0].request.content)
    assert sent["stickiness"] is False
    assert sent["tlsEncryption"] is False


@respx.mock
@pytest.mark.asyncio
async def test_a_member_without_a_weight_is_not_left_drained(pools_rw):
    """weight=0 is legal and means "take no traffic" — so it cannot be the default.

    `MemberRequest.weight` is a Java primitive int with `@Range(min = 0, ...)`.
    An omitted weight therefore arrives as 0, passes validation, and is stored:
    a backend that is in the pool and receives nothing. Observed live.
    """
    mock_iam(respx.mock)
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))
    await pools_rw.update_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        members=[MemberDto(ipAddress="10.0.0.99", port=80, backup=False)],
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content)["members"][0]["weight"] == 1


def test_an_explicit_zero_weight_is_still_honoured():
    """Draining a member without removing it is a real operation."""
    assert MemberDto(ipAddress="10.0.0.1", port=80, backup=False, weight=0).weight == 0


@respx.mock
@pytest.mark.asyncio
async def test_attach_pool_members_keeps_a_member_with_a_portal_style_name(pools_rw):
    """PR #29 review: existing members were rebuilt through the write-side
    MemberDto, so a member created in the portal or by VKS with a name outside
    NAME_PATTERN (too short, too long, or a space) failed validation and the
    tool could not touch that pool at all. Stored members must go back as stored.
    """
    mock_iam(respx.mock)
    odd = [
        {**SAMPLE_MEMBERS[0], "name": "a"},
        {**SAMPLE_MEMBERS[1], "name": "vks node " + "x" * 60},
    ]
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(odd)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))

    await pools_rw.attach_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        members=[MemberDto(ipAddress="10.0.0.13", port=80, backup=False)],
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)["members"]
    assert [m["ipAddress"] for m in sent] == ["10.0.0.11", "10.0.0.12", "10.0.0.13"]
    assert sent[0]["name"] == "a"
    assert sent[1]["name"] == odd[1]["name"]
    # the primitive-int defaults still hold for stored members
    assert sent[0]["monitorPort"] == 80 and sent[0]["weight"] == 1


# --- PR #29 review, second round -----------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_update_pool_keeps_stickiness_and_tls_on_an_http_pool(pools_rw):
    """An HTTP pool must carry both flags on every PUT; changing only the
    algorithm must not drop them (the gateway rejects that, or resets them).
    """
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(
        return_value=httpx.Response(
            200, json=data_env({**SAMPLE_POOL, "stickiness": True, "tlsEncryption": True})
        )
    )
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env(HM))
    )
    route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))
    await pools_rw.update_pool(
        load_balancer_id=LB,
        pool_id=POOL,
        algorithm="LEAST_CONNECTIONS",
        stickiness=None,
        tls_encryption=None,
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["stickiness"] is True
    assert sent["tlsEncryption"] is True


@respx.mock
@pytest.mark.asyncio
async def test_update_pool_sends_no_flags_on_a_tcp_pool(pools_rw):
    mock_iam(respx.mock)
    respx.get(f"{POOLS}/{POOL}").mock(
        return_value=httpx.Response(200, json=data_env({**SAMPLE_POOL, "protocol": "TCP"}))
    )
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env({}))
    )
    route = respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))
    await pools_rw.update_pool(
        load_balancer_id=LB,
        pool_id=POOL,
        algorithm="ROUND_ROBIN",
        stickiness=None,
        tls_encryption=None,
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert "stickiness" not in sent and "tlsEncryption" not in sent


@respx.mock
@pytest.mark.asyncio
async def test_attach_pool_members_sends_a_repeated_new_member_once(pools_rw):
    """The last entry for a new (ip, port) wins, and it is sent only once."""
    mock_iam(respx.mock)
    respx.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=list_env(SAMPLE_MEMBERS)))
    route = respx.put(MEMBERS_URL).mock(return_value=httpx.Response(200))
    await pools_rw.attach_pool_members(
        load_balancer_id=LB,
        pool_id=POOL,
        members=[
            MemberDto(ipAddress="10.0.0.5", port=80, backup=False, weight=1),
            MemberDto(ipAddress="10.0.0.5", port=80, backup=False, weight=5),
        ],
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)["members"]
    new = [m for m in sent if m["ipAddress"] == "10.0.0.5"]
    assert len(new) == 1 and new[0]["weight"] == 5
    assert len(sent) == len(SAMPLE_MEMBERS) + 1


def _hm_args(**overrides):
    args = {
        "load_balancer_id": LB,
        "pool_id": POOL,
        "health_check_path": None,
        "health_check_method": None,
        "healthy_threshold": None,
        "unhealthy_threshold": None,
        "timeout": None,
        "interval": None,
        "success_code": None,
        "domain_name": None,
        "http_version": None,
        "region": "HCM-3",
    }
    args.update(overrides)
    return args


def _mock_hm_reads(monitor):
    respx.get(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_POOL)))
    respx.get(f"{POOLS}/{POOL}/healthMonitor").mock(
        return_value=httpx.Response(200, json=data_env(monitor))
    )
    return respx.put(f"{POOLS}/{POOL}").mock(return_value=httpx.Response(200))


@respx.mock
@pytest.mark.asyncio
async def test_switching_the_probe_to_http_1_0_drops_the_stored_domain(pools_rw):
    """HTTP/1.0 forbids a domainName; carrying the stored one forward made the
    switch impossible (gateway 400 on every attempt).
    """
    mock_iam(respx.mock)
    route = _mock_hm_reads({**HM, "domainName": "app.example.com"})
    await pools_rw.update_pool_health_monitor(**_hm_args(http_version="1.0"))
    sent = json.loads(route.calls[0].request.content)["healthMonitor"]
    assert sent["httpVersion"] == "1.0"
    assert "domainName" not in sent


@respx.mock
@pytest.mark.asyncio
async def test_an_empty_string_clears_an_optional_probe_field(pools_rw):
    mock_iam(respx.mock)
    route = _mock_hm_reads({**HM, "domainName": "app.example.com"})
    await pools_rw.update_pool_health_monitor(**_hm_args(domain_name=""))
    sent = json.loads(route.calls[0].request.content)["healthMonitor"]
    assert "domainName" not in sent


@respx.mock
@pytest.mark.asyncio
async def test_update_health_monitor_checks_timeout_below_interval_locally(pools_rw):
    mock_iam(respx.mock)
    route = _mock_hm_reads(HM)  # interval=30
    with pytest.raises(ValueError, match="Timeout must be less than interval"):
        await pools_rw.update_pool_health_monitor(**_hm_args(timeout=30))
    assert not route.called
