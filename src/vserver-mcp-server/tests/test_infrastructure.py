"""Tests for SSH keys, placement groups, floating IPs, interfaces, DHCP and guides."""

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
    CreateDhcpOptionDto,
    CreatedSshKeyData,
    CreatePlacementGroupDto,
    CreateServerDto,
    CreateSshKeyDto,
    ImportSshKeyDto,
    PlacementGroupItem,
    UpdateResourceTagsDto,
)
from greennode.vserver_mcp_server.networkinterface_handler import (
    DEFAULT_DNS_SERVERS,
    NetworkInterfaceHandler,
)
from greennode.vserver_mcp_server.placementgroup_handler import PlacementGroupHandler
from greennode.vserver_mcp_server.prompts_handler import _FEATURE_GUIDES, PromptsHandler
from greennode.vserver_mcp_server.sshkey_handler import SshKeyHandler
from mcp.server.mcpserver import MCPServer


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
PROJECT = "pro-test-0001"
VPC = "net-1111"
NIC = "network-interface-2222"
DHCP = "dop-3333"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def _sent(route) -> dict:
    return json.loads(route.calls[0].request.content)


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VserverClient(config, TokenManager(config))


@pytest.fixture
def keys(config, client):
    return SshKeyHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def groups(config, client):
    return PlacementGroupHandler(
        MCPServer("t"), config, client, DiscoveryCache(), allow_write=True
    )


@pytest.fixture
def net(config, client):
    return NetworkInterfaceHandler(
        MCPServer("t"), config, client, DiscoveryCache(), allow_write=True
    )


# ── SSH keys ──────────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_create_ssh_key_surfaces_the_one_time_private_key(keys):
    _mock_iam(respx.mock)
    respx.post(f"{HCM3}/v2/{PROJECT}/sshKeys").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "ssh-1",
                    "name": "deploy",
                    "pubKey": "ssh-rsa AAAA",
                    "privateKey": "-----BEGIN PRIVATE KEY-----",
                }
            },
        )
    )
    result = await keys.create_ssh_key(body=CreateSshKeyDto(name="deploy"), region="HCM-3")
    assert result.private_key.startswith("-----BEGIN")
    assert result.id == "ssh-1"


def test_created_ssh_key_accepts_the_alternate_private_key_spellings():
    for field in ("privateKey", "private_key", "priKey", "privatekey"):
        item = CreatedSshKeyData.from_api({"id": "k", field: "SECRET"})
        assert item.private_key == "SECRET"
    assert CreatedSshKeyData.from_api({"id": "k"}).private_key == ""


@pytest.mark.asyncio
async def test_import_ssh_key_rejects_something_that_is_not_a_public_key(keys):
    with pytest.raises(ValueError, match="does not look like an SSH public key"):
        await keys.import_ssh_key(
            body=ImportSshKeyDto(name="k", pubKey="-----BEGIN OPENSSH PRIVATE KEY-----"),
            region="HCM-3",
        )


@respx.mock
@pytest.mark.asyncio
async def test_import_ssh_key_accepts_every_supported_algorithm(keys):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/sshKeys/import").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ssh-1", "name": "k"}})
    )
    for prefix in ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256"):
        await keys.import_ssh_key(
            body=ImportSshKeyDto(name="k", pubKey=f"  {prefix} AAAA user@host  "), region="HCM-3"
        )
    assert route.call_count == 3
    assert _sent(route)["pubKey"].startswith("ssh-rsa")


@respx.mock
@pytest.mark.asyncio
async def test_ssh_key_writes_invalidate_the_list_cache(keys):
    _mock_iam(respx.mock)
    list_route = respx.get(f"{HCM3}/v2/{PROJECT}/sshKeys").mock(
        return_value=httpx.Response(200, json={"listData": [{"id": "ssh-1"}], "totalItem": 1})
    )
    respx.delete(f"{HCM3}/v2/{PROJECT}/sshKeys/ssh-1").mock(
        return_value=httpx.Response(200, json={})
    )
    await keys.list_ssh_keys(name_filter=None, region="HCM-3", refresh=False)
    await keys.list_ssh_keys(name_filter=None, region="HCM-3", refresh=False)
    assert list_route.call_count == 1

    await keys.delete_ssh_key(ssh_key_id="ssh-1", region="HCM-3")
    await keys.list_ssh_keys(name_filter=None, region="HCM-3", refresh=False)
    assert list_route.call_count == 2


# ── placement groups ──────────────────────────────────────────────────────────


def test_placement_group_prefers_the_string_uuid_over_the_numeric_id():
    item = PlacementGroupItem.from_api(
        {
            "uuid": "server-group-9",
            "serverGroupId": 19877,
            "name": "web",
            "policyName": "SOFT ANTI AFFINITY",
            "servers": [{"uuid": "ins-1"}, {"uuid": "ins-2"}],
        }
    )
    assert item.id == "server-group-9"
    assert item.server_ids == ["ins-1", "ins-2"]


@respx.mock
@pytest.mark.asyncio
async def test_update_placement_group_injects_its_own_id(groups):
    from greennode.vserver_mcp_server.models import UpdatePlacementGroupDto

    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/serverGroups/server-group-9").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": "server-group-9"}})
    )
    await groups.update_placement_group(
        placement_group_id="server-group-9",
        body=UpdatePlacementGroupDto(name="renamed"),
        region="HCM-3",
    )
    assert _sent(route)["serverGroupId"] == "server-group-9"


@respx.mock
@pytest.mark.asyncio
async def test_list_placement_group_policies_reads_the_data_envelope(groups):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/serverGroups/policies").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"uuid": "pol-1", "name": "SOFT ANTI AFFINITY", "status": "ACTIVE"}]},
        )
    )
    result = await groups.list_placement_group_policies(region="HCM-3", refresh=False)
    assert result.policies[0].id == "pol-1"


def test_create_placement_group_dto_requires_a_policy():
    with pytest.raises(Exception):
        CreatePlacementGroupDto(name="web")


# ── DHCP options ──────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_create_dhcp_option_always_prepends_the_platform_resolvers(net):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/dhcp_option").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": DHCP, "name": "dns"}})
    )
    await net.create_dhcp_option(
        body=CreateDhcpOptionDto(name="dns", dnsServers=["10.0.0.1"]), region="HCM-3"
    )
    assert _sent(route)["dnsServers"] == [*DEFAULT_DNS_SERVERS, "10.0.0.1"]


@respx.mock
@pytest.mark.asyncio
async def test_create_dhcp_option_ignores_defaults_passed_again(net):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/dhcp_option").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": DHCP}})
    )
    await net.create_dhcp_option(
        body=CreateDhcpOptionDto(
            name="dns", dnsServers=[DEFAULT_DNS_SERVERS[0], "10.0.0.1", "10.0.0.2"]
        ),
        region="HCM-3",
    )
    assert _sent(route)["dnsServers"] == [*DEFAULT_DNS_SERVERS, "10.0.0.1", "10.0.0.2"]


@pytest.mark.asyncio
async def test_create_dhcp_option_enforces_the_two_extra_server_limit(net):
    with pytest.raises(ValueError, match="At most 2 DNS servers"):
        await net.create_dhcp_option(
            body=CreateDhcpOptionDto(name="dns", dnsServers=["10.0.0.1", "10.0.0.2", "10.0.0.3"]),
            region="HCM-3",
        )


@pytest.mark.asyncio
async def test_update_vpc_dhcp_option_rejects_contradictory_arguments(net):
    with pytest.raises(ValueError, match="not both"):
        await net.update_vpc_dhcp_option(
            vpc_id=VPC, dhcp_option_id=DHCP, detach=True, region="HCM-3"
        )


@pytest.mark.asyncio
async def test_update_vpc_dhcp_option_requires_one_of_the_two_modes(net):
    with pytest.raises(ValueError, match="detach=true"):
        await net.update_vpc_dhcp_option(
            vpc_id=VPC, dhcp_option_id=None, detach=False, region="HCM-3"
        )


@respx.mock
@pytest.mark.asyncio
async def test_detach_sends_an_empty_body(net):
    _mock_iam(respx.mock)
    route = respx.patch(f"{HCM3}/v2/{PROJECT}/networks/{VPC}/updateDhcpOption").mock(
        return_value=httpx.Response(200, json={})
    )
    message = await net.update_vpc_dhcp_option(
        vpc_id=VPC, dhcp_option_id=None, detach=True, region="HCM-3"
    )
    assert _sent(route) == {}
    assert "detached" in message


# ── elastic interfaces ────────────────────────────────────────────────────────


def test_server_interface_links_back_through_its_uuid_fields():
    from greennode.vserver_mcp_server.models import NetworkInterfaceItem

    item = NetworkInterfaceItem.from_api(
        {
            "uuid": "net-in-1",
            "serverUuid": "ins-1",
            "subnetUuid": "sub-1",
            "networkUuid": VPC,
            "fixedIp": "10.0.1.5",
            "floatingIp": "198.51.100.10",
            "floatingIpId": "wan-1",
            "interfaceType": "PRIVATE",
        }
    )
    assert (item.server_id, item.subnet_id, item.vpc_id) == ("ins-1", "sub-1", VPC)
    assert (item.fixed_ip, item.floating_ip, item.floating_ip_id) == (
        "10.0.1.5",
        "198.51.100.10",
        "wan-1",
    )


def test_elastic_interface_ip_is_reported_as_public_not_private():
    from greennode.vserver_mcp_server.models import NetworkInterfaceItem

    item = NetworkInterfaceItem.from_api(
        {
            "uuid": NIC,
            "ip": "198.51.100.20",
            "elasticIpId": "elastic-1",
            "zone": {"uuid": "HCM03-1C"},
        }
    )
    assert (item.fixed_ip, item.floating_ip) == ("", "198.51.100.20")
    assert (item.floating_ip_id, item.zone_id) == ("elastic-1", "HCM03-1C")


@pytest.mark.asyncio
async def test_update_network_interface_tags_pins_the_resource_type(net):
    from greennode.vserver_mcp_server.models import TagRequestDto

    with pytest.raises(ValueError, match="NETWORK-INTERFACE"):
        await net.update_network_interface_tags(
            network_interface_id=NIC,
            body=UpdateResourceTagsDto(
                resourceType="SERVER", tagRequestList=[TagRequestDto(key="env")]
            ),
            region="HCM-3",
        )


@respx.mock
@pytest.mark.asyncio
async def test_list_dhcp_option_vpcs_filters_by_the_option_set(net):
    _mock_iam(respx.mock)
    route = respx.get(f"{HCM3}/v2/{PROJECT}/networks").mock(
        return_value=httpx.Response(
            200,
            json={"listData": [{"id": VPC, "displayName": "prod"}], "totalItem": 1},
        )
    )
    result = await net.list_dhcp_option_vpcs(dhcp_option_id=DHCP, region="HCM-3")
    assert [v.id for v in result.vpcs] == [VPC]
    assert route.calls[0].request.url.params["dhcpOptionIds"] == DHCP


# ── guides ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_feature_guide_is_reachable_through_the_tool():
    handler = PromptsHandler(MCPServer("t"))
    for feature in _FEATURE_GUIDES:
        text = await handler.get_feature_guide(feature=feature)
        assert len(text) > 200


@pytest.mark.asyncio
async def test_guides_are_registered_as_prompts_too():
    mcp = MCPServer("t")
    PromptsHandler(mcp)
    prompts = {p.name for p in await mcp.list_prompts()}
    assert prompts == {
        "vserver_getting_started",
        "vserver_create_server",
        "vserver_manage_server",
        "vserver_create_volume",
        "vserver_create_network",
        "vserver_secure_server",
        "vserver_snapshot_and_restore",
        "vserver_network_acl",
        "vserver_connect_networks",
        "vserver_high_availability",
    }


@pytest.mark.asyncio
async def test_create_server_guide_names_every_discovery_step():
    handler = PromptsHandler(MCPServer("t"))
    guide = await handler.get_feature_guide(feature="create_server")
    for tool in (
        "list_zones",
        "list_vpcs",
        "list_subnets",
        "list_images",
        "list_flavors",
        "list_volume_types",
        "list_ssh_keys",
        "list_user_images",
        "get_quota",
    ):
        assert tool in guide


@pytest.mark.asyncio
async def test_create_server_guide_makes_the_agent_ask_for_user_data():
    handler = PromptsHandler(MCPServer("t"))
    guide = await handler.get_feature_guide(feature="create_server")
    assert "UserData" in guide
    assert "#cloud-config" in guide
    assert "userDataBase64Encoded" in guide


def test_create_server_dto_documents_where_user_data_comes_from():
    fields = CreateServerDto.model_fields
    assert "user image" in fields["userData"].description
    assert "#cloud-config" in fields["userData"].description
    assert "never encodes for you" in fields["userDataBase64Encoded"].description
