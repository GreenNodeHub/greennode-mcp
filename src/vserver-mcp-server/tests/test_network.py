"""Tests for the network handlers: VPC, subnet, security group and rules."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import load_config
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.models import (
    CreateSecurityGroupDto,
    CreateSecurityGroupRuleDto,
    CreateSubnetDto,
    CreateVpcDto,
    SubnetItem,
    VpcItem,
)
from greennode.vserver_mcp_server.secgroup_handler import SecurityGroupHandler
from greennode.vserver_mcp_server.subnet_handler import SubnetHandler
from greennode.vserver_mcp_server.vpc_handler import VpcHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
PROJECT = "pro-test-0001"
VPC = "net-1111"
SUBNET = "sub-2222"
SECGROUP = "secg-3333"
RULE = "secr-4444"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VserverClient(config, TokenManager(config))


@pytest.fixture
def vpcs_rw(config, client):
    return VpcHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def vpcs_ro(config, client):
    return VpcHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=False)


@pytest.fixture
def subnets_rw(config, client):
    return SubnetHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def secgroups_rw(config, client):
    return SecurityGroupHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def secgroups_ro(config, client):
    return SecurityGroupHandler(
        MCPServer("t"), config, client, DiscoveryCache(), allow_write=False
    )


# ── registration and the write gate ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_tools_only_registered_in_write_mode(vpcs_ro, vpcs_rw):
    assert {t.name for t in await vpcs_ro.mcp.list_tools()} == {
        "list_vpcs",
        "get_vpc",
        "list_active_vpcs",
    }
    assert {t.name for t in await vpcs_rw.mcp.list_tools()} == {
        "list_vpcs",
        "get_vpc",
        "list_active_vpcs",
        "create_vpc",
        "update_vpc",
        "enable_vpc_dns",
        "delete_vpc",
    }


@pytest.mark.asyncio
async def test_secgroup_read_tools_available_without_write(secgroups_ro):
    assert {t.name for t in await secgroups_ro.mcp.list_tools()} == {
        "list_security_groups",
        "get_security_group",
        "list_security_group_rules",
        "get_security_group_rule",
        "list_security_group_rule_samples",
        "list_security_group_servers",
    }


@pytest.mark.asyncio
async def test_direct_write_call_is_refused_in_read_only_mode(vpcs_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await vpcs_ro.create_vpc(body=CreateVpcDto(name="x", cidr="10.0.0.0/16"), region="HCM-3")


# ── VPC ───────────────────────────────────────────────────────────────────────


def test_vpc_maps_display_name_to_name():
    item = VpcItem.from_api(
        {
            "id": "net-1",
            "displayName": "prod",
            "cidr": "10.0.0.0/16",
            "status": "ACTIVE",
            "zone": {"uuid": "HCM03-1A"},
        }
    )
    assert (item.id, item.name, item.zone_id) == ("net-1", "prod", "HCM03-1A")


@respx.mock
@pytest.mark.asyncio
async def test_list_vpcs_hides_non_active_by_default(vpcs_rw):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/networks").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {"id": "net-1", "displayName": "prod", "status": "ACTIVE"},
                    {"id": "net-2", "displayName": "gone", "status": "DELETING"},
                ],
                "totalItem": 2,
            },
        )
    )
    active = await vpcs_rw.list_vpcs(
        name_filter=None, include_inactive=False, region="HCM-3", refresh=False
    )
    assert [v.id for v in active.vpcs] == ["net-1"]

    everything = await vpcs_rw.list_vpcs(
        name_filter=None, include_inactive=True, region="HCM-3", refresh=False
    )
    assert [v.id for v in everything.vpcs] == ["net-1", "net-2"]


@respx.mock
@pytest.mark.asyncio
async def test_get_vpc_handles_the_unwrapped_detail_response(vpcs_rw):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/networks/{VPC}").mock(
        return_value=httpx.Response(
            200, json={"id": VPC, "displayName": "prod", "cidr": "10.0.0.0/16"}
        )
    )
    vpc = await vpcs_rw.get_vpc(vpc_id=VPC, region="HCM-3")
    assert vpc.id == VPC and vpc.cidr == "10.0.0.0/16"


@respx.mock
@pytest.mark.asyncio
async def test_create_vpc_sends_only_declared_fields(vpcs_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/networks").mock(
        return_value=httpx.Response(200, json={"data": {"id": VPC, "displayName": "prod"}})
    )
    result = await vpcs_rw.create_vpc(
        body=CreateVpcDto(name="prod", cidr="10.0.0.0/16"), region="HCM-3"
    )
    assert result.id == VPC
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {
        "name": "prod",
        "cidr": "10.0.0.0/16",
    }


def test_vpc_dto_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CreateVpcDto(name="p", cidr="10.0.0.0/16", description="not a real API field")


@respx.mock
@pytest.mark.asyncio
async def test_delete_vpc_invalidates_the_list_cache(vpcs_rw):
    _mock_iam(respx.mock)
    list_route = respx.get(f"{HCM3}/v2/{PROJECT}/networks").mock(
        return_value=httpx.Response(
            200, json={"listData": [{"id": VPC, "status": "ACTIVE"}], "totalItem": 1}
        )
    )
    respx.delete(f"{HCM3}/v2/{PROJECT}/networks/{VPC}").mock(
        return_value=httpx.Response(200, json={})
    )

    await vpcs_rw.list_vpcs(
        name_filter=None, include_inactive=False, region="HCM-3", refresh=False
    )
    assert list_route.call_count == 1

    await vpcs_rw.delete_vpc(vpc_id=VPC, region="HCM-3")
    await vpcs_rw.list_vpcs(
        name_filter=None, include_inactive=False, region="HCM-3", refresh=False
    )
    assert list_route.call_count == 2


@pytest.mark.asyncio
async def test_vpc_tools_validate_ids(vpcs_rw):
    with pytest.raises(ValueError, match="Invalid vpc_id"):
        await vpcs_rw.get_vpc(vpc_id="../secrets", region="HCM-3")


# ── subnet ────────────────────────────────────────────────────────────────────


def test_subnet_reads_uuid_and_secondary_cidrs():
    item = SubnetItem.from_api(
        {
            "uuid": SUBNET,
            "name": "web",
            "cidr": "10.0.1.0/24",
            "status": "ACTIVE",
            "networkUuid": VPC,
            "zone": {"uuid": "HCM03-1B"},
            "secondarySubnets": [{"uuid": "ss-1", "cidr": "10.0.9.0/24", "name": "extra"}],
        }
    )
    assert (item.id, item.vpc_id, item.zone_id) == (SUBNET, VPC, "HCM03-1B")
    assert item.secondary_subnets[0].cidr == "10.0.9.0/24"


@respx.mock
@pytest.mark.asyncio
async def test_list_subnets_reads_the_bare_array_response(subnets_rw):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/networks/{VPC}/subnets").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"uuid": SUBNET, "name": "web", "status": "ACTIVE", "networkUuid": VPC},
                {"uuid": "sub-old", "name": "old", "status": "DELETING"},
            ],
        )
    )
    result = await subnets_rw.list_subnets(
        vpc_id=VPC, include_inactive=False, region="HCM-3", refresh=False
    )
    assert [s.id for s in result.subnets] == [SUBNET]
    assert result.vpc_id == VPC


@respx.mock
@pytest.mark.asyncio
async def test_create_subnet_posts_under_the_vpc(subnets_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/networks/{VPC}/subnets").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": SUBNET, "name": "web"}})
    )
    result = await subnets_rw.create_subnet(
        vpc_id=VPC,
        body=CreateSubnetDto(name="web", cidr="10.0.1.0/24", zoneId="HCM03-1C"),
        region="HCM-3",
    )
    assert result.id == SUBNET
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {
        "name": "web",
        "cidr": "10.0.1.0/24",
        "zoneId": "HCM03-1C",
    }


# ── security groups and rules ─────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_list_security_groups_keeps_system_flag(secgroups_rw):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/secgroups").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {"id": SECGROUP, "name": "web", "status": "ACTIVE", "system": False},
                    {"id": "secg-vks", "name": "vks-managed", "status": "ACTIVE", "system": True},
                ],
                "totalItem": 2,
            },
        )
    )
    result = await secgroups_rw.list_security_groups(
        name_filter=None, include_inactive=False, region="HCM-3", refresh=False
    )
    assert [g.system for g in result.security_groups] == [False, True]


@respx.mock
@pytest.mark.asyncio
async def test_list_security_group_rules_reads_the_data_envelope(secgroups_rw):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secGroupRules").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": RULE,
                        "direction": "egress",
                        "protocol": "any",
                        "etherType": "IPv4",
                        "portRangeMin": 0,
                        "portRangeMax": 65535,
                        "remoteIpPrefix": "0.0.0.0/0",
                    }
                ]
            },
        )
    )
    result = await secgroups_rw.list_security_group_rules(
        security_group_id=SECGROUP, region="HCM-3"
    )
    assert result.rules[0].id == RULE
    assert result.rules[0].port_range_max == 65535


@respx.mock
@pytest.mark.asyncio
async def test_create_security_group_rule_sends_ports_for_tcp(secgroups_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules").mock(
        return_value=httpx.Response(200, json={"data": {"id": RULE, "protocol": "tcp"}})
    )
    await secgroups_rw.create_security_group_rule(
        security_group_id=SECGROUP,
        body=CreateSecurityGroupRuleDto(
            direction="ingress",
            protocol="tcp",
            etherType="IPv4",
            remoteIpPrefix="0.0.0.0/0",
            portRangeMin=22,
            portRangeMax=22,
        ),
        region="HCM-3",
    )
    import json as _json

    sent = _json.loads(route.calls[0].request.content)
    assert sent["portRangeMin"] == 22 and sent["portRangeMax"] == 22


@respx.mock
@pytest.mark.asyncio
async def test_create_security_group_reads_the_uuid_not_the_numeric_key(secgroups_rw):
    _mock_iam(respx.mock)
    respx.post(f"{HCM3}/v2/{PROJECT}/secgroups").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": 481287,
                    "uuid": SECGROUP,
                    "secgroupName": "web",
                    "status": "ACTIVE",
                    "isSystem": False,
                }
            },
        )
    )
    group = await secgroups_rw.create_security_group(
        body=CreateSecurityGroupDto(name="web"), region="HCM-3"
    )
    assert (group.id, group.name, group.status) == (SECGROUP, "web", "ACTIVE")


@respx.mock
@pytest.mark.asyncio
async def test_created_rule_reports_its_uuid_and_owning_group(secgroups_rw):
    _mock_iam(respx.mock)
    respx.post(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": 1722026,
                    "uuid": RULE,
                    "secgroupUuid": SECGROUP,
                    "direction": "ingress",
                    "protocol": "tcp",
                    "portRangeMin": 22,
                    "portRangeMax": 22,
                }
            },
        )
    )
    rule = await secgroups_rw.create_security_group_rule(
        security_group_id=SECGROUP,
        body=CreateSecurityGroupRuleDto(
            direction="ingress",
            protocol="tcp",
            etherType="IPv4",
            remoteIpPrefix="10.0.0.0/8",
            portRangeMin=22,
            portRangeMax=22,
        ),
        region="HCM-3",
    )
    assert (rule.id, rule.remote_group_id) == (RULE, SECGROUP)


@respx.mock
@pytest.mark.asyncio
async def test_icmp_rule_accepts_a_type_range(secgroups_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules").mock(
        return_value=httpx.Response(200, json={"data": {"id": RULE, "protocol": "icmp"}})
    )
    await secgroups_rw.create_security_group_rule(
        security_group_id=SECGROUP,
        body=CreateSecurityGroupRuleDto(
            direction="ingress",
            protocol="icmp",
            etherType="IPv4",
            remoteIpPrefix="10.2.0.0/16",
            portRangeMin=1,
            portRangeMax=255,
        ),
        region="HCM-3",
    )
    import json as _json

    sent = _json.loads(route.calls[0].request.content)
    assert (sent["portRangeMin"], sent["portRangeMax"]) == (1, 255)


@pytest.mark.asyncio
async def test_icmp_rule_rejects_a_range_above_the_type_ceiling(secgroups_rw):
    with pytest.raises(ValueError, match="ICMP type range"):
        await secgroups_rw.create_security_group_rule(
            security_group_id=SECGROUP,
            body=CreateSecurityGroupRuleDto(
                direction="ingress",
                protocol="icmp",
                etherType="IPv4",
                remoteIpPrefix="0.0.0.0/0",
                portRangeMin=1,
                portRangeMax=65535,
            ),
            region="HCM-3",
        )


@respx.mock
@pytest.mark.asyncio
async def test_omitted_ports_are_filled_with_the_full_range(secgroups_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules").mock(
        return_value=httpx.Response(200, json={"data": {"id": RULE, "protocol": "any"}})
    )
    await secgroups_rw.create_security_group_rule(
        security_group_id=SECGROUP,
        body=CreateSecurityGroupRuleDto(
            direction="egress",
            protocol="any",
            etherType="IPv4",
            remoteIpPrefix="0.0.0.0/0",
        ),
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert (sent["portRangeMin"], sent["portRangeMax"]) == (1, 65535)


@respx.mock
@pytest.mark.asyncio
async def test_omitted_icmp_ports_become_the_icmp_type_range(secgroups_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules").mock(
        return_value=httpx.Response(200, json={"data": {"id": RULE, "protocol": "icmp"}})
    )
    await secgroups_rw.create_security_group_rule(
        security_group_id=SECGROUP,
        body=CreateSecurityGroupRuleDto(
            direction="ingress",
            protocol="icmp",
            etherType="IPv4",
            remoteIpPrefix="0.0.0.0/0",
        ),
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert (sent["portRangeMin"], sent["portRangeMax"]) == (1, 255)


@respx.mock
@pytest.mark.asyncio
async def test_protocol_numbers_are_accepted(secgroups_rw):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules").mock(
        return_value=httpx.Response(200, json={"data": {"id": RULE, "protocol": "112"}})
    )
    await secgroups_rw.create_security_group_rule(
        security_group_id=SECGROUP,
        body=CreateSecurityGroupRuleDto(
            direction="ingress",
            protocol="112",
            etherType="IPv4",
            remoteIpPrefix="10.0.0.0/16",
        ),
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["protocol"] == "112"


@respx.mock
@pytest.mark.asyncio
async def test_get_security_group_rule_unwraps_the_single_element_array(secgroups_rw):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules/{RULE}").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": RULE, "direction": "egress", "protocol": "any"}]},
        )
    )
    rule = await secgroups_rw.get_security_group_rule(
        security_group_id=SECGROUP, rule_id=RULE, region="HCM-3"
    )
    assert rule.id == RULE
    assert rule.direction == "egress"


@respx.mock
@pytest.mark.asyncio
async def test_list_security_group_rule_samples_renames_api_fields(secgroups_rw):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/secgroups/{SECGROUP}/secgroupRules/samples").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 6,
                        "name": "All ICMP",
                        "ipProtocol": "icmp",
                        "fromPort": 1,
                        "toPort": 255,
                    },
                    {"id": 8, "name": "SSH", "ipProtocol": "tcp", "fromPort": 22, "toPort": 22},
                ]
            },
        )
    )
    result = await secgroups_rw.list_security_group_rule_samples(
        security_group_id=SECGROUP, region="HCM-3"
    )
    assert [s.name for s in result.samples] == ["All ICMP", "SSH"]
    assert result.samples[1].protocol == "tcp"
    assert (result.samples[1].port_range_min, result.samples[1].port_range_max) == (22, 22)


@pytest.mark.asyncio
async def test_create_security_group_rule_requires_both_port_bounds(secgroups_rw):
    with pytest.raises(ValueError, match="must be given together"):
        await secgroups_rw.create_security_group_rule(
            security_group_id=SECGROUP,
            body=CreateSecurityGroupRuleDto(
                direction="ingress",
                protocol="tcp",
                etherType="IPv4",
                remoteIpPrefix="0.0.0.0/0",
                portRangeMin=80,
            ),
            region="HCM-3",
        )


@pytest.mark.asyncio
async def test_create_security_group_rule_rejects_inverted_port_range(secgroups_rw):
    with pytest.raises(ValueError, match="must be <="):
        await secgroups_rw.create_security_group_rule(
            security_group_id=SECGROUP,
            body=CreateSecurityGroupRuleDto(
                direction="ingress",
                protocol="tcp",
                etherType="IPv4",
                remoteIpPrefix="0.0.0.0/0",
                portRangeMin=443,
                portRangeMax=80,
            ),
            region="HCM-3",
        )


def test_rule_dto_constrains_protocol_direction_and_ports():
    with pytest.raises(ValidationError):
        CreateSecurityGroupRuleDto(
            direction="sideways", protocol="tcp", etherType="IPv4", remoteIpPrefix="0.0.0.0/0"
        )
    with pytest.raises(ValidationError):
        CreateSecurityGroupRuleDto(
            direction="ingress",
            protocol="tcp",
            etherType="IPv4",
            remoteIpPrefix="0.0.0.0/0",
            portRangeMin=70000,
            portRangeMax=70000,
        )
