"""L7 routing policies: the duplicate path template, action/rule validation, reordering."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import LB, LISTENER, POLICY, POOL, PROJECT, VLB_HCM3, data_env, list_env, mock_iam
from greennode.vlb_mcp_server.l7policy_handler import L7PolicyHandler
from greennode.vlb_mcp_server.models import (
    CreateL7PolicyDto,
    L7RuleDto,
    ReorderPolicyDto,
    UpdateL7PolicyDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


LISTENERS = f"{VLB_HCM3}/v2/{PROJECT}/loadBalancers/{LB}/listeners"
POLICIES = f"{LISTENERS}/{LISTENER}/l7policies"

SAMPLE_POLICY = {
    "uuid": POLICY,
    "name": "api-route",
    "action": "REDIRECT_TO_POOL",
    "redirectPoolId": POOL,
    "redirectPoolName": "web-pool",
    "position": 0,
    "keepQueryString": True,
    "displayStatus": "ACTIVE",
    "progressStatus": "COMPLETED",
    "l7Rules": [
        {
            "uuid": "rule-1",
            "compareType": "STARTS_WITH",
            "ruleValue": "/api",
            "ruleType": "PATH",
            "provisioningStatus": "ACTIVE",
            "operatingStatus": "ONLINE",
        }
    ],
}


@pytest.fixture
def l7_rw(config, client, no_cache):
    return L7PolicyHandler(MCPServer("t"), config, client, no_cache, allow_write=True)


@pytest.fixture
def l7_ro(config, client, no_cache):
    return L7PolicyHandler(MCPServer("t"), config, client, no_cache, allow_write=False)


# --- registration ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_mode_registers_no_write_tools(l7_ro):
    assert {t.name for t in await l7_ro.mcp.list_tools()} == {
        "list_l7_policies",
        "get_l7_policy",
    }


@pytest.mark.asyncio
async def test_write_mode_adds_exactly_the_mutating_tools(l7_rw, l7_ro):
    read = {t.name for t in await l7_ro.mcp.list_tools()}
    write = {t.name for t in await l7_rw.mcp.list_tools()}
    assert write - read == {
        "create_l7_policy",
        "update_l7_policy",
        "delete_l7_policy",
        "update_l7_policy_order",
    }


# --- reads ------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_l7_policies_maps_rules_and_their_own_status_names(l7_ro):
    """L7RuleDto uses provisioningStatus/operatingStatus, not the displayStatus pair."""
    mock_iam(respx.mock)
    respx.get(POLICIES).mock(return_value=httpx.Response(200, json=list_env([SAMPLE_POLICY])))
    result = await l7_ro.list_l7_policies(
        load_balancer_id=LB, listener_id=LISTENER, region="HCM-3"
    )
    policy = result.policies[0]
    assert (policy.id, policy.action, policy.position) == (POLICY, "REDIRECT_TO_POOL", 0)
    rule = policy.rules[0]
    assert (rule.rule_type, rule.compare_type, rule.rule_value) == (
        "PATH",
        "STARTS_WITH",
        "/api",
    )
    assert rule.provisioning_status == "ACTIVE"
    assert rule.operating_status == "ONLINE"


@respx.mock
@pytest.mark.asyncio
async def test_get_and_update_build_the_identical_url(l7_rw):
    """The spec declares {l7PolicyId} for GET and {policyId} for PUT/DELETE.

    That is a spec-generation artefact, not two endpoints — one parameter name
    and one URL for all four tools.
    """
    mock_iam(respx.mock)
    get_route = respx.get(f"{POLICIES}/{POLICY}").mock(
        return_value=httpx.Response(200, json=data_env(SAMPLE_POLICY))
    )
    put_route = respx.put(f"{POLICIES}/{POLICY}").mock(return_value=httpx.Response(200))

    await l7_rw.get_l7_policy(
        load_balancer_id=LB, listener_id=LISTENER, policy_id=POLICY, region="HCM-3"
    )
    await l7_rw.update_l7_policy(
        load_balancer_id=LB,
        listener_id=LISTENER,
        policy_id=POLICY,
        body=UpdateL7PolicyDto(action="REDIRECT_TO_POOL", redirectPoolId=POOL),
        region="HCM-3",
    )
    assert str(get_route.calls[0].request.url) == str(put_route.calls[0].request.url)


# --- writes -------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_l7_policy_sends_the_rules_array(l7_rw):
    mock_iam(respx.mock)
    route = respx.post(POLICIES).mock(return_value=httpx.Response(200, json={"uuid": POLICY}))
    body = CreateL7PolicyDto(
        name="api-route",
        action="REDIRECT_TO_POOL",
        redirectPoolId=POOL,
        rules=[L7RuleDto(compareType="STARTS_WITH", ruleValue="/api", ruleType="PATH")],
    )
    result = await l7_rw.create_l7_policy(
        load_balancer_id=LB, listener_id=LISTENER, body=body, region="HCM-3"
    )
    assert json.loads(route.calls[0].request.content) == {
        "name": "api-route",
        "action": "REDIRECT_TO_POOL",
        "redirectPoolId": POOL,
        "rules": [{"compareType": "STARTS_WITH", "ruleValue": "/api", "ruleType": "PATH"}],
    }
    assert result.id == POLICY


@respx.mock
@pytest.mark.asyncio
async def test_delete_l7_policy_tolerates_an_empty_202(l7_rw):
    mock_iam(respx.mock)
    route = respx.delete(f"{POLICIES}/{POLICY}").mock(return_value=httpx.Response(202))
    message = await l7_rw.delete_l7_policy(
        load_balancer_id=LB, listener_id=LISTENER, policy_id=POLICY, region="HCM-3"
    )
    assert route.called and POLICY in message


@respx.mock
@pytest.mark.asyncio
async def test_update_l7_policy_order_sends_policy_id_and_position(l7_rw):
    mock_iam(respx.mock)
    route = respx.put(f"{LISTENERS}/{LISTENER}/reorderL7Policies").mock(
        return_value=httpx.Response(200)
    )
    await l7_rw.update_l7_policy_order(
        load_balancer_id=LB,
        listener_id=LISTENER,
        policies=[
            ReorderPolicyDto(policyId=POLICY, position=0),
            ReorderPolicyDto(policyId="pol-2", position=1),
        ],
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {
        "policies": [
            {"policyId": POLICY, "position": 0},
            {"policyId": "pol-2", "position": 1},
        ]
    }


@pytest.mark.asyncio
async def test_update_l7_policy_order_rejects_duplicate_positions(l7_rw):
    with pytest.raises(ValueError, match="duplicate position"):
        await l7_rw.update_l7_policy_order(
            load_balancer_id=LB,
            listener_id=LISTENER,
            policies=[
                ReorderPolicyDto(policyId=POLICY, position=0),
                ReorderPolicyDto(policyId="pol-2", position=0),
            ],
            region="HCM-3",
        )


# --- DTO validation -------------------------------------------------------------------


def test_redirect_to_url_requires_a_url():
    with pytest.raises(ValidationError, match="redirectUrl"):
        CreateL7PolicyDto(name="api-route", action="REDIRECT_TO_URL")


def test_redirect_to_pool_requires_a_pool():
    with pytest.raises(ValidationError, match="redirectPoolId"):
        CreateL7PolicyDto(name="api-route", action="REDIRECT_TO_POOL")


def test_update_dto_enforces_the_same_pairing():
    with pytest.raises(ValidationError, match="redirectUrl"):
        UpdateL7PolicyDto(action="REDIRECT_TO_URL")


def test_rule_dto_rejects_an_unknown_compare_type():
    with pytest.raises(ValidationError):
        L7RuleDto(compareType="MATCHES", ruleValue="/api", ruleType="PATH")


def test_rule_dto_rejects_an_unknown_rule_type():
    with pytest.raises(ValidationError):
        L7RuleDto(compareType="EQUAL_TO", ruleValue="/api", ruleType="HEADER")


def test_redirect_http_code_is_limited_to_301_and_302():
    with pytest.raises(ValidationError):
        CreateL7PolicyDto(
            name="api-route",
            action="REDIRECT_TO_URL",
            redirectUrl="https://example.com",
            redirectHttpCode=307,
        )


# --- guards -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_refuse_in_read_only_mode(l7_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await l7_ro.delete_l7_policy(
            load_balancer_id=LB, listener_id=LISTENER, policy_id=POLICY, region="HCM-3"
        )
