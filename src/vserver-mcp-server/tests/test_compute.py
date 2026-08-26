"""Tests for the compute handlers: servers, volumes, images, tags and quota."""

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
    AttachInternalInterfaceDto,
    CreateServerDto,
    CreateVolumeDto,
    QuotaItem,
    ResizeVolumeDto,
    ServerItem,
    SubnetRequestDto,
    UpdateResourceTagsDto,
    UpdateServerSecurityGroupsDto,
)
from greennode.vserver_mcp_server.server_handler import ServerHandler
from greennode.vserver_mcp_server.userimage_handler import UserImageHandler
from greennode.vserver_mcp_server.volume_handler import VolumeHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
PROJECT = "pro-test-0001"
SERVER = "ins-1111"
VOLUME = "vol-2222"
NIC = "net-in-3333"


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
def servers(config, client):
    return ServerHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def servers_ro(config, client):
    return ServerHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=False)


@pytest.fixture
def volumes(config, client):
    return VolumeHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def images(config, client):
    return UserImageHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


def _valid_server_body(**overrides) -> CreateServerDto:
    payload = {
        "name": "web-server-01",
        "zoneId": "HCM03-1C",
        "networkId": "net-1",
        "subnetId": "sub-1",
        "imageId": "img-1",
        "flavorId": "flav-1",
        "rootDiskTypeId": "vtype-1",
        "rootDiskSize": 20,
    }
    payload.update(overrides)
    return CreateServerDto(**payload)


# ── registration and the write gate ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_read_tools_available_without_write(servers_ro):
    assert {t.name for t in await servers_ro.mcp.list_tools()} == {
        "list_servers",
        "get_server",
        "list_server_interfaces",
        "list_server_security_groups",
        "get_server_console_url",
        "get_server_console_log",
        "list_server_actions",
        "list_subnet_servers",
        "get_server_external_interface",
    }


@pytest.mark.asyncio
async def test_server_write_tools_appear_only_with_allow_write(servers):
    names = {t.name for t in await servers.mcp.list_tools()}
    assert {"create_server", "start_server", "delete_server", "resize_server"} <= names


@pytest.mark.asyncio
async def test_power_tools_refuse_in_read_only_mode(servers_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await servers_ro.start_server(server_id=SERVER, region="HCM-3")


# ── create_server ─────────────────────────────────────────────────────────────


def test_create_server_dto_excludes_billing_fields():
    for field in ("period", "isEnableAutoRenew", "isPoc", "osLicence", "enableBackup"):
        with pytest.raises(ValidationError):
            _valid_server_body(**{field: True})


def test_create_server_dto_enforces_minimum_root_disk():
    with pytest.raises(ValidationError):
        _valid_server_body(rootDiskSize=10)


@respx.mock
@pytest.mark.asyncio
async def test_create_server_sends_created_from_new(servers):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/servers").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": SERVER, "name": "web-server-01"}})
    )
    result = await servers.create_server(body=_valid_server_body(), region="HCM-3")
    assert result.id == SERVER
    body = _sent(route)
    assert body["createdFrom"] == "NEW"
    assert body["name"] == "web-server-01"
    assert "period" not in body


@respx.mock
@pytest.mark.asyncio
async def test_create_server_omits_unset_optional_fields(servers):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/servers").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": SERVER}})
    )
    await servers.create_server(body=_valid_server_body(), region="HCM-3")
    body = _sent(route)
    for absent in ("sshKeyId", "userName", "userPassword", "dataDiskSize", "serverGroupId"):
        assert absent not in body


# ── server reads ──────────────────────────────────────────────────────────────


def test_server_item_flattens_nested_flavor_image_and_addresses():
    item = ServerItem.from_api(
        {
            "uuid": SERVER,
            "name": "web",
            "status": "ACTIVE",
            "zone": {"uuid": "HCM03-1B"},
            "flavor": {"flavorId": "flav-9"},
            "image": {"id": "img-9"},
            "internalInterfaces": [{"fixedIp": "10.0.1.5", "floatingIp": "198.51.100.10"}],
        }
    )
    assert (item.flavor_id, item.image_id) == ("flav-9", "img-9")
    assert (item.private_ip, item.public_ip) == ("10.0.1.5", "198.51.100.10")
    assert item.zone_id == "HCM03-1B"


def test_server_item_survives_missing_nested_objects():
    item = ServerItem.from_api({"uuid": SERVER, "internalInterfaces": []})
    assert (item.flavor_id, item.image_id, item.private_ip) == ("", "", "")


@respx.mock
@pytest.mark.asyncio
async def test_list_server_interfaces_splits_internal_and_external(servers):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/network-interfaces").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "internalInterfaces": [{"uuid": NIC, "fixedIp": "10.0.1.5"}],
                    "externalInterfaces": [{"uuid": "net-ex-1", "ip": "198.51.100.10"}],
                }
            },
        )
    )
    result = await servers.list_server_interfaces(server_id=SERVER, region="HCM-3")
    assert [i.id for i in result.internal_interfaces] == [NIC]
    assert [i.id for i in result.external_interfaces] == ["net-ex-1"]


# ── power and lifecycle ───────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_power_tools_hit_their_endpoints(servers):
    _mock_iam(respx.mock)
    for action, method in (("start", servers.start_server), ("stop", servers.stop_server)):
        route = respx.put(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/{action}").mock(
            return_value=httpx.Response(200, json={"data": {"uuid": SERVER}})
        )
        await method(server_id=SERVER, region="HCM-3")
        assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_resize_server_includes_server_id_in_body(servers):
    from greennode.vserver_mcp_server.models import ResizeServerDto

    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/resize").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": SERVER}})
    )
    await servers.resize_server(
        server_id=SERVER, body=ResizeServerDto(flavorId="flav-9"), region="HCM-3"
    )
    body = _sent(route)
    assert body == {"flavorId": "flav-9", "serverId": SERVER}


@respx.mock
@pytest.mark.asyncio
async def test_update_server_security_groups_validates_every_id(servers):
    with pytest.raises(ValueError, match="Invalid security_group_id"):
        await servers.update_server_security_groups(
            server_id=SERVER,
            body=UpdateServerSecurityGroupsDto(securityGroup=["secg-ok", "../evil"]),
            region="HCM-3",
        )


@respx.mock
@pytest.mark.asyncio
async def test_delete_server_sends_the_spec_field_name(servers):
    _mock_iam(respx.mock)
    route = respx.delete(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}").mock(
        return_value=httpx.Response(200, json={})
    )
    message = await servers.delete_server(
        server_id=SERVER, delete_all_volumes=True, region="HCM-3"
    )
    assert _sent(route) == {"deleteAllVolume": True}
    assert "together with its volumes" in message


@respx.mock
@pytest.mark.asyncio
async def test_delete_server_message_states_volumes_were_kept(servers):
    _mock_iam(respx.mock)
    respx.delete(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}").mock(
        return_value=httpx.Response(200, json={})
    )
    message = await servers.delete_server(
        server_id=SERVER, delete_all_volumes=False, region="HCM-3"
    )
    assert "volumes were kept" in message


@respx.mock
@pytest.mark.asyncio
async def test_attach_internal_interface_validates_subnet_ids(servers):
    with pytest.raises(ValueError, match="Invalid subnet_id"):
        await servers.attach_server_internal_interface(
            server_id=SERVER,
            body=AttachInternalInterfaceDto(
                subnetRequests=[SubnetRequestDto(subnetId="../etc/passwd")]
            ),
            region="HCM-3",
        )


def test_attach_internal_interface_requires_at_least_one_subnet():
    with pytest.raises(ValidationError):
        AttachInternalInterfaceDto(subnetRequests=[])


# ── volumes ───────────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_attach_volume_sends_an_empty_body_the_api_insists_on(volumes):
    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/volumes/{VOLUME}/servers/{SERVER}/attach").mock(
        return_value=httpx.Response(200, json={"data": {"id": VOLUME, "status": "IN-USE"}})
    )
    result = await volumes.attach_volume(volume_id=VOLUME, server_id=SERVER, region="HCM-3")
    assert route.call_count == 1
    assert result.status == "IN-USE"
    assert route.calls[0].request.read() == b"{}"


@respx.mock
@pytest.mark.asyncio
async def test_detach_volume_sends_an_empty_body_the_api_insists_on(volumes):
    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/volumes/{VOLUME}/servers/{SERVER}/detach").mock(
        return_value=httpx.Response(200, json={"data": {"id": VOLUME, "status": "AVAILABLE"}})
    )
    await volumes.detach_volume(volume_id=VOLUME, server_id=SERVER, region="HCM-3")
    assert route.call_count == 1
    assert route.calls[0].request.read() == b"{}"


def test_resize_volume_dto_requires_the_target_volume_type():
    with pytest.raises(ValidationError):
        ResizeVolumeDto(newSize=100)


def test_create_volume_dto_rejects_billing_and_restore_fields():
    base = {"name": "data", "size": 20, "volumeTypeId": "vtype-1", "zoneId": "HCM03-1C"}
    CreateVolumeDto(**base)
    for field in ("isPoc", "configVolumeRestore", "createdFrom"):
        with pytest.raises(ValidationError):
            CreateVolumeDto(**base, **{field: "x"})


@respx.mock
@pytest.mark.asyncio
async def test_list_server_volumes_reads_the_nested_endpoint(volumes):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/volumes/servers/{SERVER}").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": VOLUME, "name": "root", "size": 20, "bootable": True}]}
        )
    )
    result = await volumes.list_server_volumes(server_id=SERVER, region="HCM-3")
    assert result.volumes[0].bootable is True
    assert result.volumes[0].size_gb == 20


# ── tags and quota ────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_update_resource_tags_injects_the_resource_id(images):
    from greennode.vserver_mcp_server.models import TagRequestDto

    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/tag/resource/{SERVER}").mock(
        return_value=httpx.Response(200, json={"data": [{"key": "env", "value": "prod"}]})
    )
    tags = await images.update_resource_tags(
        resource_id=SERVER,
        body=UpdateResourceTagsDto(
            resourceType="SERVER",
            tagRequestList=[TagRequestDto(key="env", value="prod", isEdited=True)],
        ),
        region="HCM-3",
    )
    body = _sent(route)
    assert body["resourceId"] == SERVER
    assert body["tagRequestList"][0]["isEdited"] is True
    assert tags[0].key == "env"


@respx.mock
@pytest.mark.asyncio
async def test_list_tag_keys_flattens_objects_and_strings(images):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/tag/tag-key").mock(
        return_value=httpx.Response(200, json={"data": [{"key": "env"}, "team"]})
    )
    result = await images.list_tag_keys(region="HCM-3")
    assert result.values == ["env", "team"]


@respx.mock
@pytest.mark.asyncio
async def test_list_tag_values_accepts_a_dotted_platform_key(images):
    _mock_iam(respx.mock)
    route = respx.get(f"{HCM3}/v2/{PROJECT}/tag/tag-key/vng.vpc.id/tag-value").mock(
        return_value=httpx.Response(200, json={"data": ["net-1111"]})
    )
    result = await images.list_tag_values(key="vng.vpc.id", region="HCM-3")
    assert result.values == ["net-1111"]
    assert route.called


@pytest.mark.asyncio
async def test_list_tag_values_still_rejects_a_traversal_key(images):
    with pytest.raises(ValueError, match="Invalid key"):
        await images.list_tag_values(key="../../secrets", region="HCM-3")


def test_quota_item_coerces_the_string_used_field():
    item = QuotaItem.from_api({"quotaName": "SSH_KEY", "limit": 10, "used": "3", "type": "Server"})
    assert (item.used, item.limit) == (3, 10)
    assert QuotaItem.from_api({"quotaName": "X", "used": None}).used == 0


@respx.mock
@pytest.mark.asyncio
async def test_get_quota_returns_every_line(images):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/quotas/quotaUsed").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"quotaName": "SSH_KEY", "limit": 10, "used": "3", "type": "Server"},
                    {"quotaName": "ROUTE", "limit": 100, "used": "1", "type": "Server"},
                ]
            },
        )
    )
    result = await images.get_quota(region="HCM-3")
    assert [q.name for q in result.quotas] == ["SSH_KEY", "ROUTE"]


@respx.mock
@pytest.mark.asyncio
async def test_list_server_security_groups_resolves_groups_from_rule_names(servers):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/sec-groups").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "inbounds": [
                        {
                            "id": "secr-1",
                            "direction": "ingress",
                            "protocol": "tcp",
                            "portRangeMin": 22,
                            "portRangeMax": 22,
                            "secGroupName": "web",
                        }
                    ],
                    "outbounds": [
                        {
                            "id": "secr-2",
                            "direction": "egress",
                            "protocol": "any",
                            "secGroupName": "ghost",
                        }
                    ],
                }
            },
        )
    )
    respx.get(f"{HCM3}/v2/{PROJECT}/secgroups").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [{"id": "secg-web", "name": "web", "status": "ACTIVE"}],
                "totalItem": 1,
            },
        )
    )
    result = await servers.list_server_security_groups(server_id=SERVER, region="HCM-3")
    assert [g.id for g in result.security_groups] == ["secg-web"]
    assert result.unresolved_group_names == ["ghost"]
    assert [r.id for r in result.inbound_rules] == ["secr-1"]
    assert [r.id for r in result.outbound_rules] == ["secr-2"]


@respx.mock
@pytest.mark.asyncio
async def test_console_url_unwraps_a_bare_string_payload(servers):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/console-url").mock(
        return_value=httpx.Response(200, json={"data": "https://console.example/vnc?token=abc"})
    )
    result = await servers.get_server_console_url(server_id=SERVER, region="HCM-3")
    assert result.url == "https://console.example/vnc?token=abc"


@respx.mock
@pytest.mark.asyncio
async def test_list_server_actions_renames_api_fields(servers):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/actions").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "action": "resize",
                        "startTime": "2026-07-31T17:13:45.000+07:00",
                        "userAction": "API",
                    }
                ]
            },
        )
    )
    result = await servers.list_server_actions(server_id=SERVER, region="HCM-3")
    assert result.actions[0].action == "resize"
    assert result.actions[0].source == "API"


def test_user_image_reads_uuid_and_image_size():
    from greennode.vserver_mcp_server.models import UserImageItem

    item = UserImageItem.from_api(
        {"uuid": "img-user-1", "name": "golden", "imageSize": 40, "status": "ACTIVE"}
    )
    assert (item.id, item.size_gb) == ("img-user-1", 40)
