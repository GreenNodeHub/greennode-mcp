"""Rules read out of the vlb-gateway source, pinned so they cannot drift back.

Each test names the Java file it was transcribed from. Every value here was
also confirmed against the running HCM-3 gateway — the status vocabulary and
the filter behaviour by reading live responses, the 400 messages through the
gateway's own dry-run validator (`POST /v2/{projectId}/validation`), which runs
the real validators without creating anything.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import PROJECT, SAMPLE_LB, VLB_HCM3, mock_iam, paging_env
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.loadbalancer_handler import LoadBalancerHandler
from greennode.vlb_mcp_server.status import IN_FLIGHT, is_settled
from mcp.server.mcpserver import MCPServer


BASE = f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers"


# LBGatewayStatus.LoadBalancer, in full.
GATEWAY_STATUSES = [
    "CREATING",
    "CREATED",
    "ERROR",
    "ACTIVE",
    "DELETED",
    "UPDATING",
    "DELETING",
    "MIGRATING",
    "CREATING-BILLING",
    "UNDETERMINED",
    "SCALING",
    "SCALING_IN",
    "SCALING_OUT",
    "REBALANCING",
    "PENDING_CREATE",
    "PENDING_UPDATE",
]
SETTLED = {"CREATED", "ACTIVE", "ERROR", "DELETED"}


@pytest.fixture
def lbs(config, client):
    return LoadBalancerHandler(
        MCPServer("t"), config, client, DiscoveryCache(ttl_config={}), allow_write=False
    )


async def _list(handler, **kwargs):
    """Call list_load_balancers with every parameter supplied.

    Handlers are called directly here, so an unpassed Field default would
    arrive as a FieldInfo object rather than its default.
    """
    params = {
        "name": None,
        "status": None,
        "subnet_id": None,
        "vpc_id": None,
        "zone_id": None,
        "page": 1,
        "size": 500,
        "region": "HCM-3",
        "refresh": False,
    }
    params.update(kwargs)
    return await handler.list_load_balancers(**params)


# --- status vocabulary (helper/LBGatewayStatus.java) --------------------------


@pytest.mark.parametrize("status", GATEWAY_STATUSES)
def test_every_gateway_status_is_classified(status):
    assert is_settled(status) is (status in SETTLED)


@pytest.mark.parametrize("status", ["MIGRATING", "SCALING_OUT", "SCALING_IN", "CREATING-BILLING"])
def test_the_statuses_that_used_to_read_as_settled(status):
    """The regression: these are transitions the guessed vocabulary omitted.

    A load balancer reported SCALING_OUT looked settled, so an agent went
    straight to create_listener and the gateway answered 400
    LoadBalancerNotReady — the exact loop get_load_balancer_status exists to
    prevent.
    """
    assert is_settled(status) is False


def test_undetermined_counts_as_in_flight():
    """The gateway says it cannot read the state; that is not a green light."""
    assert is_settled("UNDETERMINED") is False


def test_in_flight_invents_nothing():
    """Statuses the gateway cannot emit must not be in the set.

    PENDING_DELETE, RESIZING, IN_PROGRESS and PROCESSING were all guesses.
    """
    assert IN_FLIGHT <= {s.replace("-", "_") for s in GATEWAY_STATUSES}


def test_an_unknown_status_still_fails_open():
    assert is_settled("SOMETHING_NEW") is True


def test_hyphens_and_case_are_normalised():
    assert is_settled("creating-billing") is False


# --- list filters (helper/VlbGatewayHelper.java:189) --------------------------


@respx.mock
@pytest.mark.asyncio
async def test_only_name_and_zone_reach_the_query(lbs):
    """createSearchCriteria forwards nothing else, so nothing else is sent.

    Verified live: a bogus status or vpcIds returns the whole project.
    """
    route = respx.get(BASE).mock(return_value=httpx.Response(200, json=paging_env([SAMPLE_LB])))
    mock_iam(respx.mock)
    await _list(lbs, name="prod", zone_id="HCM03-1B", status="ACTIVE", vpc_id="net-1")
    sent = dict(route.calls[0].request.url.params)
    assert set(sent) == {"name", "zoneId", "page", "size"}
    assert sent["zoneId"] == "HCM03-1B"


@respx.mock
@pytest.mark.asyncio
async def test_status_is_applied_here_and_reported_as_such(lbs):
    mock_iam(respx.mock)
    other = {**SAMPLE_LB, "uuid": "lb-2", "name": "other", "displayStatus": "CREATING"}
    respx.get(BASE).mock(
        return_value=httpx.Response(200, json=paging_env([SAMPLE_LB, other], total=2))
    )
    result = await _list(lbs, status="ACTIVE")
    assert [lb.id for lb in result.load_balancers] == [SAMPLE_LB["uuid"]]
    assert result.filtered_locally == ["status"]
    # The gateway's total describes its own unfiltered query, so it must not be
    # passed through as the count of a locally filtered list.
    assert result.total_item == 1


@respx.mock
@pytest.mark.asyncio
async def test_an_unfiltered_list_keeps_the_gateway_total(lbs):
    mock_iam(respx.mock)
    respx.get(BASE).mock(return_value=httpx.Response(200, json=paging_env([SAMPLE_LB], total=48)))
    result = await _list(lbs)
    assert result.total_item == 48
    assert result.filtered_locally == []


@respx.mock
@pytest.mark.asyncio
async def test_page_zero_is_passed_through_not_hidden(lbs):
    """Unpaged responses really do report page 0.

    The gateway pages this endpoint only when `name` is set; otherwise the
    plain @GetMapping wins and page/pageSize/totalPage come back as 0.
    Surfacing that is how an agent learns its page argument did nothing.
    """
    mock_iam(respx.mock)
    respx.get(BASE).mock(
        return_value=httpx.Response(
            200, json={"listData": [SAMPLE_LB], "page": 0, "pageSize": 0, "totalItem": 48}
        )
    )
    result = await _list(lbs)
    assert (result.page, result.page_size) == (0, 0)


# --- error surface (controller/ApiExceptionHandler.java) ---------------------
# All three of these are HTTP 400, so without translation they read as "your
# body is wrong" when the body was accepted and the resource was merely busy.


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,resource",
    [
        ("LoadBalancerNotReady", "load balancer"),
        ("ListenerNotReady", "listener"),
        ("PoolNotReady", "pool"),
    ],
)
async def test_not_ready_is_reported_as_transient(lbs, code, resource):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/lb-1").mock(
        return_value=httpx.Response(
            400,
            json={
                "message": f"The {resource} id lb-1 is not ready",
                "errorCode": code,
                "statusCode": 400,
            },
        )
    )
    with pytest.raises(RuntimeError, match="still processing an earlier change") as exc:
        await lbs.get_load_balancer(load_balancer_id="lb-1", region="HCM-3")
    assert resource in str(exc.value)
    assert "get_load_balancer_status" in str(exc.value)


@respx.mock
@pytest.mark.asyncio
async def test_a_lost_lock_is_reported_as_transient(lbs):
    """LoggingExecutor.runAtControllerWithLock answers 400 "<id> is in-process"."""
    mock_iam(respx.mock)
    respx.get(f"{BASE}/lb-1").mock(
        return_value=httpx.Response(400, json={"message": "LOAD-BALANCER lb-1 is in-process"})
    )
    with pytest.raises(RuntimeError, match="an earlier operation is still running"):
        await lbs.get_load_balancer(load_balancer_id="lb-1", region="HCM-3")


@respx.mock
@pytest.mark.asyncio
async def test_exceeding_a_quota_stays_a_plain_bad_request(lbs):
    """vLB answers quota exhaustion with 400, not the 409 other services use.

    It is terminal, so it must NOT get the "poll and retry" hint — that would
    send an agent into a loop that can never succeed.
    """
    mock_iam(respx.mock)
    respx.get(f"{BASE}/lb-1").mock(
        return_value=httpx.Response(
            400, json={"message": "Exceeded LOAD_BALANCER quota. Current used: 48, max: 48."}
        )
    )
    with pytest.raises(RuntimeError, match="Exceeded LOAD_BALANCER quota") as exc:
        await lbs.get_load_balancer(load_balancer_id="lb-1", region="HCM-3")
    assert "Poll get_load_balancer_status" not in str(exc.value)


@respx.mock
@pytest.mark.asyncio
async def test_an_ordinary_bad_request_is_unchanged(lbs):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/lb-1").mock(
        return_value=httpx.Response(400, json={"message": "name: must not be blank"})
    )
    with pytest.raises(RuntimeError, match="Bad request: name: must not be blank"):
        await lbs.get_load_balancer(load_balancer_id="lb-1", region="HCM-3")


@respx.mock
@pytest.mark.asyncio
async def test_a_400_without_json_still_raises_cleanly(lbs):
    mock_iam(respx.mock)
    respx.get(f"{BASE}/lb-1").mock(return_value=httpx.Response(400, text="<html>nope</html>"))
    with pytest.raises(RuntimeError):
        await lbs.get_load_balancer(load_balancer_id="lb-1", region="HCM-3")


# --- observed live, creating and deleting a real load balancer ----------------
# Phase 0.2: one throwaway NLB, then a pool, a member and a listener on it.
# Provision took ~105s; each sub-resource write ~10-20s.


#: The (displayStatus, progressStatus) pairs a load balancer actually reported.
#: Note they are two different vocabularies — display says ACTIVE where
#: progress says CREATED — and that ANY sub-resource write (pool, member,
#: listener, delete) puts the load balancer itself into UPDATING/UPDATING.
OBSERVED_LB_PAIRS = [
    (("CREATING", "CREATING"), False),
    (("ACTIVE", "CREATED"), True),
    (("UPDATING", "UPDATING"), False),
    (("DELETING", "DELETING"), False),
]

#: Listener and pool `displayStatus` values seen live. Three of them —
#: HEALTHY, UNHEALTHY, OFFLINE — are health states and appear nowhere in
#: LBGatewayStatus, which lists only lifecycle values.
OBSERVED_CHILD_HEALTH = ["HEALTHY", "UNHEALTHY", "OFFLINE"]
OBSERVED_CHILD_LIFECYCLE = ["CREATING", "DELETING"]


@pytest.mark.parametrize("pair,settled", OBSERVED_LB_PAIRS)
def test_observed_load_balancer_pairs_are_classified(pair, settled):
    assert is_settled(*pair) is settled


@pytest.mark.parametrize("status", OBSERVED_CHILD_HEALTH)
def test_child_health_states_do_not_block_a_write(status):
    """An UNHEALTHY pool is settled — it is just unhealthy.

    `LoadBalancerStatusData.build` folds every listener and pool status into
    `settled`, so treating a health value as in-flight would leave an agent
    polling forever on a pool whose backends are simply down.
    """
    assert is_settled(status) is True


@pytest.mark.parametrize("status", OBSERVED_CHILD_LIFECYCLE)
def test_child_lifecycle_states_do_block_a_write(status):
    """A listener mid-create must hold the roll-up unsettled."""
    assert is_settled(status) is False


def test_the_roll_up_folds_children_into_settled():
    from greennode.vlb_mcp_server.models import LoadBalancerStatusData

    ready = LoadBalancerStatusData.build(
        "lb-1",
        {"name": "x", "displayStatus": "ACTIVE", "progressStatus": "CREATED"},
        [{"displayStatus": "HEALTHY", "progressStatus": "CREATED"}],
        [{"displayStatus": "UNHEALTHY", "progressStatus": "CREATED"}],
    )
    assert ready.settled is True

    mid_create = LoadBalancerStatusData.build(
        "lb-1",
        {"name": "x", "displayStatus": "ACTIVE", "progressStatus": "CREATED"},
        [{"displayStatus": "CREATING", "progressStatus": "CREATING"}],
        [],
    )
    assert mid_create.settled is False


@respx.mock
@pytest.mark.asyncio
async def test_a_create_response_carries_only_uuid(lbs):
    """Observed live: `{"uuid": "lb-<uuid4>"}` and nothing else, with 202.

    So `created_id` finds it on the first key it tries and `hint` stays empty —
    the fallback chain exists for the shapes the spec allowed, not for this one.
    """
    mock_iam(respx.mock)
    respx.post(BASE).mock(
        return_value=httpx.Response(202, json={"uuid": "lb-0d5bb3ea-53da-4121-b3d2-5d398fdaebb0"})
    )
    lbs.allow_write = True
    from greennode.vlb_mcp_server.models import CreateLoadBalancerDto

    created = await lbs.create_load_balancer(
        body=CreateLoadBalancerDto(
            name="mcptest-nlb-01",
            packageId="lbp-1",
            scheme="Internet",
            subnetId="sub-1",
            type="Layer 4",
        ),
        region="HCM-3",
    )
    assert created.id == "lb-0d5bb3ea-53da-4121-b3d2-5d398fdaebb0"
    assert created.hint == ""


# --- the "busy" 400s, in every shape the gateway builds them ------------------
# Eight validators format their rejection as
# `String.format("<Resource> id %s is %s", uuid, status.toLowerCase())`, so the
# message ends in the resource's live status. The first of these was found by
# accident: a delete during a stuck UPDATING answered "Load balancer id <id> is
# updating", which the original phrase-list did not match and which therefore
# reached the agent as "Bad request" — advice to change a body that was fine.


@pytest.mark.parametrize(
    "message",
    [
        "Load balancer id lb-1 is updating",
        "Listener id lis-1 is creating",
        "Pool id pool-1 is deleting",
        "Policy id policy-1 is migrating",
        "Load balancer id lb-1 is scaling_out",
        "LOAD-BALANCER lb-1 is in-process",
        "LOAD-BALANCER is timeout",
        "The load balancer id lb-1 is not ready",
        "Pool id pool-1 is not ready for member change; an operation is already in progress",
        "Load balancer is not ready for updating",
    ],
)
def test_transient_rejections_are_recognised(message):
    from greennode.vlb_mcp_server.client import _transient_reason

    assert _transient_reason(message) is not None


@pytest.mark.parametrize(
    "message",
    [
        # Same grammatical shape, but none of these clears on its own.
        "Load Balancer lb-1 is expired",
        "Load balancer id lb-1 is not a leader",
        "Load balancer id lb-1 is created",
        "Pool id: pool-1 is used in listener",
        "Pool id: pool-1 is used in policy",
        "Exceeded LOAD_BALANCER quota. Current used: 48, max: 48.",
        "CA is in use.",
        "name: must not be blank",
        "Invalid package type for Application load balancer",
    ],
)
def test_terminal_rejections_are_left_alone(message):
    """Telling an agent to poll and retry one of these creates a loop."""
    from greennode.vlb_mcp_server.client import _transient_reason

    assert _transient_reason(message) is None


@respx.mock
@pytest.mark.asyncio
async def test_a_delete_during_an_update_reads_as_transient(lbs):
    """The live message that exposed the gap, end to end."""
    mock_iam(respx.mock)
    respx.get(f"{BASE}/lb-1").mock(
        return_value=httpx.Response(400, json={"message": "Load balancer id lb-1 is updating"})
    )
    with pytest.raises(RuntimeError, match="cannot accept this change yet") as exc:
        await lbs.get_load_balancer(load_balancer_id="lb-1", region="HCM-3")
    assert "currently updating" in str(exc.value)
    assert "get_load_balancer_status" in str(exc.value)
