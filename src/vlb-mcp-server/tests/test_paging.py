"""Envelope normalisation, create-id extraction, list params and id guards."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import PROJECT, VLB_HCM3, mock_iam, paging_env
from greennode.vlb_mcp_server.paging import (
    SERVER_SIDE_FILTERS,
    as_list,
    created_id,
    fetch_all_items,
    fetch_list,
    lb_list_params,
    unwrap,
    unwrap_one,
)
from greennode.vlb_mcp_server.validators import RESERVED_LB_SEGMENTS, validate_lb_id


# --- as_list -----------------------------------------------------------------


def test_as_list_data_envelope():
    assert as_list({"data": [1, 2]}) == [1, 2]


def test_as_list_paging_envelope():
    assert as_list(paging_env([{"uuid": "a"}])) == [{"uuid": "a"}]


def test_as_list_bare_array():
    assert as_list([1, 2]) == [1, 2]


def test_as_list_none_and_scalars():
    assert as_list(None) == []
    assert as_list("nope") == []
    assert as_list({"data": {"uuid": "a"}}) == []


def test_as_list_explicit_wrapper_key_wins():
    assert as_list({"policies": [1], "data": [2]}, "policies") == [1]


# --- unwrap / unwrap_one ------------------------------------------------------


def test_unwrap_returns_inner_object():
    assert unwrap({"data": {"uuid": "a"}}) == {"uuid": "a"}


def test_unwrap_passes_through_bare_object():
    assert unwrap({"uuid": "a"}) == {"uuid": "a"}


def test_unwrap_tolerates_none():
    assert unwrap(None) is None


def test_unwrap_one_handles_single_element_array():
    assert unwrap_one({"data": [{"uuid": "a"}]}) == {"uuid": "a"}


def test_unwrap_one_returns_empty_dict_for_none():
    assert unwrap_one(None) == {}
    assert unwrap_one({"data": []}) == {}


# --- created_id ---------------------------------------------------------------
# The five create operations declare an untyped Map<String,String> with no
# documented key. created_id must never raise: a successful create reported as
# a failure makes an agent retry and duplicate a billed load balancer.


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"uuid": "lb-1"}, "lb-1"),
        ({"id": "lb-1"}, "lb-1"),
        ({"loadBalancerId": "lb-1"}, "lb-1"),
        ({"listenerId": "lis-1"}, "lis-1"),
        ({"poolId": "pool-1"}, "pool-1"),
        ({"l7PolicyId": "pol-1"}, "pol-1"),
        ({"uuid": "lb-1", "id": "9"}, "lb-1"),
        ({"someUnknownKey": "lb-1"}, "lb-1"),
        ({"weirdIdentifier": "lb-1", "message": "ok"}, ""),
        ({}, ""),
        (None, ""),
        ([], ""),
        ({"count": 3}, ""),
    ],
)
def test_created_id(body, expected):
    assert created_id(body) == expected


# --- lb_list_params -----------------------------------------------------------


def test_lb_list_params_defaults_to_flat_page_and_size():
    assert lb_list_params() == {"page": 1, "size": 500}


def test_lb_list_params_emits_flat_filter_keys():
    params = lb_list_params(name="prod", zone_id="HCM03-1A", page=2, size=10)
    assert params == {"page": 2, "size": 10, "name": "prod", "zoneId": "HCM03-1A"}


def test_lb_list_params_omits_unset_filters():
    assert "name" not in lb_list_params(zone_id="HCM03-1A")
    assert "zoneId" not in lb_list_params(name="prod")


def test_lb_list_params_takes_only_the_filters_the_gateway_applies():
    """status / subnetIds / vpcIds must never reach the wire.

    The gateway binds them from the query string and then drops them —
    createSearchCriteria forwards only projectId, name, zoneId, tagIds and
    dhcpOptionIds. Sending them makes an agent believe it filtered when a bogus
    value provably returns the whole project. list_load_balancers applies those
    three itself instead.
    """
    import inspect

    accepted = set(inspect.signature(lb_list_params).parameters)
    assert accepted == {"name", "zone_id", "page", "size"}
    assert SERVER_SIDE_FILTERS == {"name", "zoneId"}


# --- fetch_all_items ----------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_single_page(client):
    mock_iam(respx.mock)
    respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers").mock(
        return_value=httpx.Response(200, json=paging_env([{"uuid": "a"}, {"uuid": "b"}]))
    )
    items = await fetch_all_items(client, f"/v2/{PROJECT}/loadBalancers")
    assert [i["uuid"] for i in items] == ["a", "b"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_loops_when_truncated(client):
    mock_iam(respx.mock)
    pages = [
        httpx.Response(200, json=paging_env([{"uuid": "a"}], total=3)),
        httpx.Response(200, json=paging_env([{"uuid": "b"}], total=3)),
        httpx.Response(200, json=paging_env([{"uuid": "c"}], total=3)),
    ]
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers").mock(side_effect=pages)
    items = await fetch_all_items(
        client, f"/v2/{PROJECT}/loadBalancers", params={"page": 1, "size": 1}
    )
    assert [i["uuid"] for i in items] == ["a", "b", "c"]
    # Page 1 was asked for explicitly, so the loop continues from page 2 — it
    # must not restart and re-collect the batch it already has.
    assert [dict(c.request.url.params).get("page") for c in route.calls[1:]] == ["2", "3"]


def _sized_backend(total: int, default_size: int):
    """A list endpoint that honours page/size, with its own default page size."""
    all_items = [{"uuid": f"i-{n}"} for n in range(total)]

    def respond(request):
        page = int(request.url.params.get("page", "1"))
        size = int(request.url.params.get("size", str(default_size)))
        batch = all_items[(page - 1) * size : page * size]
        return httpx.Response(200, json=paging_env(batch, total=total, page=page, page_size=size))

    return respond


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_restarts_when_first_page_used_backend_default(client):
    """PR #29 review: with no explicit size, page 2 at page_size=500 starts at
    item 501 — continuing from there skipped everything after the backend's
    default page. The walk must restart from page 1 with an explicit size.
    """
    mock_iam(respx.mock)
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers").mock(
        side_effect=_sized_backend(total=30, default_size=10)
    )
    items = await fetch_all_items(client, f"/v2/{PROJECT}/loadBalancers")
    assert [i["uuid"] for i in items] == [f"i-{n}" for n in range(30)]
    assert dict(route.calls[1].request.url.params) == {"page": "1", "size": "500"}


@respx.mock
@pytest.mark.asyncio
async def test_fetch_list_without_walk_returns_only_the_requested_page(client):
    """PR #29 review: a caller asking for one page must get that page only."""
    mock_iam(respx.mock)
    route = respx.get(f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers").mock(
        side_effect=_sized_backend(total=46, default_size=10)
    )
    items, envelope = await fetch_list(
        client,
        f"/v2/{PROJECT}/loadBalancers",
        params={"name": "vks", "page": 2, "size": 10},
        walk=False,
    )
    assert [i["uuid"] for i in items] == [f"i-{n}" for n in range(10, 20)]
    assert envelope["page"] == 2
    assert route.call_count == 1


# --- validate_lb_id -----------------------------------------------------------
# mcp_core.validate_id accepts "packages"/"headers"/"subnet", so without this
# guard get_load_balancer(load_balancer_id="packages") silently fetches the
# package catalogue and parses it as an empty load balancer.


@pytest.mark.parametrize("segment", sorted(RESERVED_LB_SEGMENTS))
def test_validate_lb_id_rejects_reserved_segments(segment):
    with pytest.raises(ValueError, match="reserved VLB path segment"):
        validate_lb_id(segment, "load_balancer_id")


def test_validate_lb_id_is_case_insensitive():
    with pytest.raises(ValueError, match="reserved VLB path segment"):
        validate_lb_id("Packages", "load_balancer_id")


def test_validate_lb_id_accepts_a_real_id():
    validate_lb_id("lb-1111-2222", "load_balancer_id")


def test_validate_lb_id_still_rejects_traversal():
    with pytest.raises(ValueError, match="Invalid load_balancer_id"):
        validate_lb_id("../secrets", "load_balancer_id")
