"""Tests for route tables, network ACLs, peering, virtual IPs and interconnects."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import load_config
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.interconnect_handler import InterconnectHandler
from greennode.vserver_mcp_server.models import (
    AddressPairDto,
    CreateInterconnectConnectionDto,
    CreateInterconnectDto,
    CreateNetworkAclDto,
    CreateRouteTableDto,
    CreateVirtualIpDto,
    InterconnectConnectionItem,
    NetworkAclItem,
    NetworkAclRuleDto,
    PeeringItem,
    RouteRequestDto,
    UpdateNetworkAclRulesDto,
    UpdateNetworkAclSubnetsDto,
    UpdateRouteTableRoutesDto,
    UpdateVirtualIpDto,
    VirtualIpItem,
)
from greennode.vserver_mcp_server.networkacl_handler import NetworkAclHandler
from greennode.vserver_mcp_server.peering_handler import PeeringHandler
from greennode.vserver_mcp_server.routetable_handler import RouteTableHandler
from greennode.vserver_mcp_server.virtualip_handler import VirtualIpHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
PROJECT = "pro-test-0001"
VPC = "net-1111"
SUBNET = "sub-2222"
ROUTE_TABLE = "rt-3333"
ACL = "netPolicy-4444"
VIP = "vip-5555"
PAIR = "address-pair-6666"
INTERCONNECT = "ic-7777"
CONNECTION = "icc-8888"


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
def route_tables(config, client):
    return RouteTableHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def acls(config, client):
    return NetworkAclHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def acls_ro(config, client):
    return NetworkAclHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=False)


@pytest.fixture
def peerings(config, client):
    return PeeringHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def peerings_ro(config, client):
    return PeeringHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=False)


@pytest.fixture
def vips(config, client):
    return VirtualIpHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def interconnects(config, client):
    return InterconnectHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


# ── the required name/page/size query params ──────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_route_table_list_always_sends_name_page_size(route_tables):
    _mock_iam(respx.mock)
    route = respx.get(f"{HCM3}/v2/{PROJECT}/route-table").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [{"uuid": ROUTE_TABLE, "name": "rt", "networkId": VPC, "routes": []}],
                "totalItem": 1,
            },
        )
    )
    result = await route_tables.list_route_tables(name_filter="", region="HCM-3", refresh=False)
    assert [t.id for t in result.route_tables] == [ROUTE_TABLE]
    params = route.calls[0].request.url.params
    assert params["name"] == "" and params["page"] == "1" and "size" in params


@respx.mock
@pytest.mark.asyncio
async def test_network_acl_list_uses_the_list_subpath(acls):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/network-acl/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [{"uuid": ACL, "name": "web", "interfaceNetworkUuid": VPC}],
                "totalItem": 1,
            },
        )
    )
    result = await acls.list_network_acls(name_filter="", region="HCM-3", refresh=False)
    assert [a.id for a in result.network_acls] == [ACL]


# ── route tables ──────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_update_route_table_routes_sends_the_full_set(route_tables):
    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/route-table/{ROUTE_TABLE}/routes").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": ROUTE_TABLE, "routes": []}})
    )
    await route_tables.update_route_table_routes(
        route_table_id=ROUTE_TABLE,
        body=UpdateRouteTableRoutesDto(
            routes=[RouteRequestDto(destinationCidrBlock="10.21.0.0/24", target="10.21.0.1")]
        ),
        region="HCM-3",
    )
    assert route.calls[0].request.read() == (
        b'{"routes":[{"destinationCidrBlock":"10.21.0.0/24","target":"10.21.0.1"}]}'
    )


def test_route_table_name_length_is_enforced():
    with pytest.raises(ValidationError):
        CreateRouteTableDto(name="rt", networkId=VPC)
    assert CreateRouteTableDto(name="prod-rt", networkId=VPC).name == "prod-rt"


# ── network ACLs ──────────────────────────────────────────────────────────────


def test_acl_rules_split_by_direction_and_sort_by_sequence():
    item = NetworkAclItem.from_api(
        {
            "uuid": ACL,
            "name": "web",
            "defaultAcl": False,
            "subnetAssociationList": [{"subnetUuid": SUBNET}],
            "aclPolicyRules": [
                {"uuid": "r2", "type": "inbound", "seqNumber": 2000, "action": "drop"},
                {"uuid": "r1", "type": "inbound", "seqNumber": 10, "action": "pass"},
                {"uuid": "r3", "type": "outbound", "seqNumber": 0, "action": "pass"},
            ],
        }
    )
    assert item.subnet_ids == [SUBNET]
    assert len(item.rules) == 3


@respx.mock
@pytest.mark.asyncio
async def test_list_acl_rules_orders_by_evaluation_order(acls):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}/rules").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"uuid": "r2", "type": "inbound", "seqNumber": 2000, "action": "drop"},
                    {"uuid": "r1", "type": "inbound", "seqNumber": 10, "action": "pass"},
                    {"uuid": "r3", "type": "outbound", "seqNumber": 0, "action": "pass"},
                ]
            },
        )
    )
    result = await acls.list_network_acl_rules(network_acl_id=ACL, region="HCM-3")
    assert [r.id for r in result.inbound] == ["r1", "r2"]
    assert [r.id for r in result.outbound] == ["r3"]


@respx.mock
@pytest.mark.asyncio
async def test_update_acl_rules_keeps_defaults_and_lowercases_the_protocol(acls):
    _mock_iam(respx.mock)
    custom = {
        "type": "inbound",
        "seqNumber": 10,
        "protocol": "tcp",
        "port": "443",
        "source": "0.0.0.0/0",
        "action": "pass",
    }
    defaults = [
        {
            "uuid": "aclr-0",
            "type": "inbound",
            "seqNumber": 0,
            "protocol": "ANY",
            "port": "0-65535",
            "source": "0.0.0.0/0",
            "action": "pass",
        },
        {
            "uuid": "aclr-2000",
            "type": "inbound",
            "seqNumber": 2000,
            "protocol": "ANY",
            "port": "0-65535",
            "source": "0.0.0.0/0",
            "action": "deny",
        },
    ]
    respx.get(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}/rules").mock(
        return_value=httpx.Response(200, json={"data": defaults})
    )
    route = respx.put(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}/rules").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": ACL, "status": "UPDATING"}})
    )
    respx.get(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "uuid": ACL,
                    "status": "ACTIVE",
                    "aclPolicyRules": [*defaults, {"uuid": "aclr-10", **custom}],
                }
            },
        )
    )
    result = await acls.update_network_acl_rules(
        network_acl_id=ACL,
        body=UpdateNetworkAclRulesDto(
            detailAclRuleList=[
                NetworkAclRuleDto(
                    type="inbound",
                    seqNumber=10,
                    protocol="TCP",
                    port="443",
                    source="0.0.0.0/0",
                    action="pass",
                )
            ]
        ),
        region="HCM-3",
    )
    sent = json.loads(route.calls[0].request.read())
    assert sent["aclId"] == ACL
    assert sent["detailAclRuleList"][0]["protocol"] == "tcp"
    assert sorted(r["seqNumber"] for r in sent["detailAclRuleList"]) == [0, 10, 2000]
    assert result.id == ACL


@respx.mock
@pytest.mark.asyncio
async def test_update_acl_rules_reports_rules_the_platform_dropped(acls):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}/rules").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.put(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}/rules").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": ACL, "status": "UPDATING"}})
    )
    respx.get(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}").mock(
        return_value=httpx.Response(
            200, json={"data": {"uuid": ACL, "status": "ACTIVE", "aclPolicyRules": []}}
        )
    )
    with pytest.raises(RuntimeError, match="did not keep"):
        await acls.update_network_acl_rules(
            network_acl_id=ACL,
            body=UpdateNetworkAclRulesDto(
                detailAclRuleList=[
                    NetworkAclRuleDto(
                        type="inbound",
                        seqNumber=10,
                        protocol="tcp",
                        port="443",
                        source="0.0.0.0/0",
                        action="pass",
                    )
                ]
            ),
            region="HCM-3",
        )


@respx.mock
@pytest.mark.asyncio
async def test_detaching_every_subnet_is_expressible(acls):
    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/network-acl/{ACL}/subnets").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": ACL}})
    )
    await acls.update_network_acl_subnets(
        network_acl_id=ACL, body=UpdateNetworkAclSubnetsDto(subnetUuids=[]), region="HCM-3"
    )
    assert b'"subnetUuids":[]' in route.calls[0].request.read()


def test_acl_rule_sequence_is_bounded():
    with pytest.raises(ValidationError):
        NetworkAclRuleDto(
            type="inbound",
            seqNumber=40000,
            protocol="TCP",
            port="443",
            source="0.0.0.0/0",
            action="pass",
        )


def test_acl_rule_rejects_an_unknown_action():
    with pytest.raises(ValidationError):
        NetworkAclRuleDto(
            type="inbound",
            seqNumber=10,
            protocol="TCP",
            port="443",
            source="0.0.0.0/0",
            action="allow",
        )


def test_acl_dtos_reject_unknown_fields():
    with pytest.raises(ValidationError):
        CreateNetworkAclDto(name="x", vpc=VPC, zoneId="HCM03-1A")


@pytest.mark.asyncio
async def test_acl_write_tools_are_gated(acls_ro):
    names = {t.name for t in await acls_ro.mcp.list_tools()}
    assert "update_network_acl_rules" not in names
    with pytest.raises(ValueError, match="--allow-write"):
        await acls_ro.delete_network_acl(network_acl_id=ACL, region="HCM-3")


# ── peering ───────────────────────────────────────────────────────────────────


def test_peering_maps_both_sides():
    item = PeeringItem.from_api(
        {
            "uuid": "peer-1",
            "name": "prod-to-dev",
            "status": "ACTIVE",
            "fromVpcUuid": VPC,
            "fromCidr": "10.0.0.0/16",
            "endVpcUuid": "net-9999",
            "endCidr": "10.1.0.0/16",
        }
    )
    assert (item.from_vpc_id, item.to_vpc_id) == (VPC, "net-9999")
    assert (item.from_cidr, item.to_cidr) == ("10.0.0.0/16", "10.1.0.0/16")


@pytest.mark.asyncio
async def test_peering_exposes_no_create_tool(peerings):
    names = {t.name for t in await peerings.mcp.list_tools()}
    assert names == {"list_peerings", "delete_peering"}


@pytest.mark.asyncio
async def test_peering_delete_is_gated(peerings_ro):
    assert {t.name for t in await peerings_ro.mcp.list_tools()} == {"list_peerings"}


# ── virtual IPs ───────────────────────────────────────────────────────────────


def test_virtual_ip_reads_zone_from_the_nested_object():
    item = VirtualIpItem.from_api(
        {
            "uuid": VIP,
            "name": "vip-web",
            "ipAddress": "10.0.1.9",
            "type": "private",
            "mode": "Active/Passive",
            "subnetId": SUBNET,
            "addressPairIps": ["10.0.1.4", "10.0.1.5"],
            "zone": {"uuid": "HCM03-1B"},
        }
    )
    assert (item.zone_id, item.mode) == ("HCM03-1B", "Active/Passive")
    assert item.address_pair_ips == ["10.0.1.4", "10.0.1.5"]


def test_virtual_ip_mode_is_mandatory_on_update():
    with pytest.raises(ValidationError):
        UpdateVirtualIpDto(name="renamed")


def test_virtual_ip_rejects_an_unknown_mode():
    with pytest.raises(ValidationError):
        CreateVirtualIpDto(name="v", subnetId=SUBNET, mode="Active/Standby")


@respx.mock
@pytest.mark.asyncio
async def test_create_address_pair_binds_one_interface(vips):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/virtualIpAddress/{VIP}/addressPairs").mock(
        return_value=httpx.Response(
            200, json={"data": {"uuid": PAIR, "networkInterfaceId": "net-in-1"}}
        )
    )
    result = await vips.create_virtual_ip_address_pair(
        virtual_ip_id=VIP, body=AddressPairDto(networkInterfaceId="net-in-1"), region="HCM-3"
    )
    assert result.id == PAIR
    assert route.calls[0].request.read() == b'{"networkInterfaceId":"net-in-1"}'


@respx.mock
@pytest.mark.asyncio
async def test_public_vip_uses_the_public_vips_path(vips):
    _mock_iam(respx.mock)
    route = respx.delete(f"{HCM3}/v2/{PROJECT}/public-vips/{VIP}").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    await vips.delete_public_virtual_ip(virtual_ip_id=VIP, region="HCM-3")
    assert route.called


@pytest.mark.asyncio
async def test_virtual_ip_ids_are_validated(vips):
    with pytest.raises(ValueError, match="Invalid"):
        await vips.get_virtual_ip(virtual_ip_id="../secrets", region="HCM-3")


# ── interconnect ──────────────────────────────────────────────────────────────


def test_interconnect_connection_splits_comma_separated_subnets():
    item = InterconnectConnectionItem.from_api(
        {
            "uuid": CONNECTION,
            "name": "to-hq",
            "networkId": VPC,
            "remoteSubnets": "192.168.1.0/24, 192.168.2.0/24",
        }
    )
    assert item.remote_subnets == ["192.168.1.0/24", "192.168.2.0/24"]


def test_interconnect_connection_accepts_a_list_too():
    item = InterconnectConnectionItem.from_api(
        {"uuid": CONNECTION, "remoteSubnets": ["192.168.1.0/24"]}
    )
    assert item.remote_subnets == ["192.168.1.0/24"]


@respx.mock
@pytest.mark.asyncio
async def test_create_interconnect_leaves_gw2_off_unless_asked(interconnects):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/interconnects").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": INTERCONNECT, "name": "hq"}})
    )
    await interconnects.create_interconnect(
        body=CreateInterconnectDto(name="hq", typeId="type-1", packageId="itp-1Gbps"),
        region="HCM-3",
    )
    assert b"enableGw2" not in route.calls[0].request.read()


@respx.mock
@pytest.mark.asyncio
async def test_create_interconnect_connection_sends_remote_subnets(interconnects):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/interconnects/{INTERCONNECT}/connections").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": CONNECTION}})
    )
    await interconnects.create_interconnect_connection(
        interconnect_id=INTERCONNECT,
        body=CreateInterconnectConnectionDto(
            name="to-hq", networkId=VPC, subnets=["192.168.1.0/24"]
        ),
        region="HCM-3",
    )
    assert b'"subnets":["192.168.1.0/24"]' in route.calls[0].request.read()


@respx.mock
@pytest.mark.asyncio
async def test_ping_reports_an_unreachable_gateway(interconnects):
    _mock_iam(respx.mock)
    respx.put(f"{HCM3}/v2/{PROJECT}/interconnects/{INTERCONNECT}/ping").mock(
        return_value=httpx.Response(200, json={"data": {"success": False}})
    )
    result = await interconnects.ping_interconnect(
        interconnect_id=INTERCONNECT, gateway_number=2, region="HCM-3"
    )
    assert result.reachable is False
    assert result.gateway_number == 2
