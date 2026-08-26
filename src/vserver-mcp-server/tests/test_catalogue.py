"""Tests for the catalogue handlers: flavors, images, volume types."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import load_config
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.flavor_handler import FlavorHandler
from greennode.vserver_mcp_server.image_handler import ImageHandler
from greennode.vserver_mcp_server.models import FlavorItem, VolumeTypeItem
from greennode.vserver_mcp_server.volumetype_handler import VolumeTypeHandler
from mcp.server.mcpserver import MCPServer


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
PROJECT = "pro-test-0001"
ZONE = "HCM03-1C"


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
def flavors(config, client):
    return FlavorHandler(MCPServer("test"), config, client, DiscoveryCache())


@pytest.fixture
def images(config, client):
    return ImageHandler(MCPServer("test"), config, client, DiscoveryCache())


@pytest.fixture
def volume_types(config, client):
    return VolumeTypeHandler(MCPServer("test"), config, client, DiscoveryCache())


# ── registration ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catalogue_tools_registered(flavors, images, volume_types):
    assert {t.name for t in await flavors.mcp.list_tools()} == {
        "list_flavor_families",
        "list_flavor_codes",
        "list_flavors",
        "get_flavor",
    }
    assert {t.name for t in await images.mcp.list_tools()} == {"list_images"}
    assert {t.name for t in await volume_types.mcp.list_tools()} == {
        "list_volume_types",
        "get_volume_type",
        "get_default_volume_type",
    }


# ── flavors ───────────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_list_flavor_families_flattens_types(flavors):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/flavor_zones/families").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "general-purpose",
                    "value": "General Purpose",
                    "types": [{"key": "standard"}, {"key": "high-cpu"}],
                },
                {"key": "gpu", "value": "GPU", "types": []},
            ],
        )
    )
    result = await flavors.list_flavor_families(region="HCM-3", refresh=False)
    assert [f.key for f in result.families] == ["general-purpose", "gpu"]
    assert result.families[0].types == ["standard", "high-cpu"]


@respx.mock
@pytest.mark.asyncio
async def test_list_flavor_codes_drops_na_description(flavors):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/flavor_zones/codes").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"key": "code-e", "value": "Code E", "description": "N/A"},
                {"key": "code-a40", "value": "Code A40", "description": "GPU A40"},
            ],
        )
    )
    result = await flavors.list_flavor_codes(region="HCM-3", refresh=False)
    assert result.codes[0].description == ""
    assert result.codes[1].description == "GPU A40"


@respx.mock
@pytest.mark.asyncio
async def test_list_flavors_filters_unavailable_and_passes_zone(flavors):
    _mock_iam(respx.mock)
    route = respx.get(
        f"{HCM3}/v1/{PROJECT}/flavors/families/general-purpose/platforms/code-s"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "flavorId": "flav-1",
                    "name": "s-general-1x2",
                    "cpu": 1,
                    "memory": 2,
                    "remainingVms": 64,
                    "bandwidth": 1,
                    "bandwidthUnit": "Gbps",
                    "group": "General",
                    "metaData": '{"imageTypeSupport":["Ubuntu", "CentOs"]}',
                },
                {"flavorId": "flav-2", "name": "sold-out", "cpu": 2, "isSoldOut": True},
                {"flavorId": "flav-3", "name": "no-capacity", "cpu": 2, "remainingVms": 0},
            ],
        )
    )
    result = await flavors.list_flavors(
        family="general-purpose", code="code-s", zone_id=ZONE, region="HCM-3", refresh=False
    )
    assert [f.id for f in result.flavors] == ["flav-1"]
    assert result.flavors[0].bandwidth == "1 Gbps"
    assert result.flavors[0].supported_image_types == ["Ubuntu", "CentOs"]
    assert result.zone_id == ZONE
    assert route.calls[0].request.url.params["zoneId"] == ZONE


@respx.mock
@pytest.mark.asyncio
async def test_list_flavors_rejects_unsafe_ids(flavors):
    with pytest.raises(ValueError, match="Invalid family"):
        await flavors.list_flavors(
            family="../../etc", code="code-s", zone_id=None, region="HCM-3", refresh=False
        )


def test_flavor_metadata_parsing_never_raises():
    # metaData is a JSON *string* and is sometimes absent or malformed.
    assert FlavorItem.from_api({"flavorId": "f"}).supported_image_types == []
    assert (
        FlavorItem.from_api({"flavorId": "f", "metaData": "not json"}).supported_image_types == []
    )
    assert FlavorItem.from_api({"flavorId": "f", "metaData": "[1,2]"}).supported_image_types == []
    ok = FlavorItem.from_api({"flavorId": "f", "metaData": '{"imageTypeSupport":["Ubuntu"]}'})
    assert ok.supported_image_types == ["Ubuntu"]


# ── images ────────────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_list_images_unwraps_success_envelope(images):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/images/os").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "errorCode": None,
                "errorMsg": None,
                "extra": {},
                "images": [
                    {"id": "img-1", "imageType": "Ubuntu", "imageVersion": "1-Ubuntu-22.04x64"},
                    {
                        "id": "img-2",
                        "imageType": "Windows",
                        "imageVersion": "2-Win2019",
                        "licence": True,
                    },
                ],
            },
        )
    )
    result = await images.list_images(
        image_type="os", name_filter=None, region="HCM-3", refresh=False
    )
    assert [i.id for i in result.images] == ["img-1", "img-2"]
    assert result.images[1].licence is True


@respx.mock
@pytest.mark.asyncio
async def test_list_images_name_filter_is_case_insensitive(images):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/images/os").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "images": [
                    {"id": "img-1", "imageType": "Ubuntu", "imageVersion": "1-Ubuntu-22.04x64"},
                    {"id": "img-2", "imageType": "Windows", "imageVersion": "2-Win2019"},
                ],
            },
        )
    )
    result = await images.list_images(
        image_type="os", name_filter="UBUNTU", region="HCM-3", refresh=False
    )
    assert [i.id for i in result.images] == ["img-1"]


# ── volume types ──────────────────────────────────────────────────────────────


def _mock_volume_type_zones(zones: list[dict]) -> None:
    respx.get(f"{HCM3}/v1/{PROJECT}/volume_type_zones").mock(
        return_value=httpx.Response(
            200, json={"success": True, "extra": {}, "volumeTypeZones": zones}
        )
    )


@respx.mock
@pytest.mark.asyncio
async def test_list_volume_types_auto_prefers_nvme(volume_types):
    _mock_iam(respx.mock)
    _mock_volume_type_zones([{"id": "vtz-ssd", "name": "SSD"}, {"id": "vtz-nvme", "name": "NVMe"}])
    respx.get(f"{HCM3}/v1/{PROJECT}/vtz-nvme/volume_types").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "volumeTypes": [
                    {
                        "id": "vtype-b",
                        "name": "6400",
                        "iops": 6400,
                        "throughPut": 419430400,
                        "minSize": 1,
                        "maxSize": 5000,
                    },
                    {
                        "id": "vtype-a",
                        "name": "3000",
                        "iops": 3000,
                        "throughPut": 209715200,
                        "minSize": 1,
                        "maxSize": 5000,
                    },
                ],
            },
        )
    )
    result = await volume_types.list_volume_types(
        zone_id=ZONE, disk_type="AUTO", region="HCM-3", refresh=False
    )
    assert result.disk_type == "NVMe"
    assert sorted(result.available_disk_types) == ["NVMe", "SSD"]
    # Sorted by IOPS ascending so the cheapest tier is first.
    assert [v.iops for v in result.volume_types] == [3000, 6400]
    assert result.volume_types[0].throughput_mbps == 200


@respx.mock
@pytest.mark.asyncio
async def test_list_volume_types_auto_falls_back_to_ssd(volume_types):
    _mock_iam(respx.mock)
    _mock_volume_type_zones([{"id": "vtz-ssd", "name": "SSD"}])
    respx.get(f"{HCM3}/v1/{PROJECT}/vtz-ssd/volume_types").mock(
        return_value=httpx.Response(
            200, json={"success": True, "volumeTypes": [{"id": "vtype-a", "iops": 3000}]}
        )
    )
    result = await volume_types.list_volume_types(
        zone_id=ZONE, disk_type="AUTO", region="HCM-3", refresh=False
    )
    assert result.disk_type == "SSD"
    assert [v.id for v in result.volume_types] == ["vtype-a"]


@respx.mock
@pytest.mark.asyncio
async def test_list_volume_types_reports_when_requested_kind_absent(volume_types):
    _mock_iam(respx.mock)
    _mock_volume_type_zones([{"id": "vtz-ssd", "name": "SSD"}])
    result = await volume_types.list_volume_types(
        zone_id=ZONE, disk_type="NVMe", region="HCM-3", refresh=False
    )
    # No NVMe in this zone: empty tiers, but the caller still learns what exists.
    assert result.volume_types == []
    assert result.disk_type == ""
    assert result.available_disk_types == ["SSD"]


def test_volume_type_throughput_converts_bytes_to_mb():
    item = VolumeTypeItem.from_api({"id": "v", "throughPut": 419430400})
    assert item.throughput_mbps == 400
    assert VolumeTypeItem.from_api({"id": "v"}).throughput_mbps == 0
