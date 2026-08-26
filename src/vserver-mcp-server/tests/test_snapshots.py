"""Tests for the snapshot handler: points, policies, rollback and deletion."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.client import VbackupClient, VserverClient
from greennode.vserver_mcp_server.config import load_config
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.models import (
    CreateSnapshotDto,
    CreateSnapshotPolicyDto,
    RollbackSnapshotDto,
    SnapshotPointItem,
    SnapshotPolicyData,
    UpdateSnapshotPolicyDto,
)
from greennode.vserver_mcp_server.snapshot_handler import SnapshotHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
VBACKUP = "https://hcm-3.console.greennode.ai/vserver/vbackup-gateway"
PROJECT = "pro-test-0001"
SERVER = "ins-1111"
VOLUME = "vol-2222"
POINT = "snap-3333"


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


def backup_client(config):
    return VbackupClient(config, TokenManager(config))


@pytest.fixture
def snapshots(config, client):
    return SnapshotHandler(
        MCPServer("t"), config, client, DiscoveryCache(), backup_client(config), allow_write=True
    )


@pytest.fixture
def snapshots_ro(config, client):
    return SnapshotHandler(
        MCPServer("t"), config, client, DiscoveryCache(), backup_client(config), allow_write=False
    )


# ── registration and the write gate ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_tools_available_without_write(snapshots_ro):
    assert {t.name for t in await snapshots_ro.mcp.list_tools()} == {
        "list_snapshot_policies",
        "list_server_snapshots",
        "get_server_snapshot_policy",
        "list_shared_server_snapshots",
        "list_volume_snapshots",
        "get_volume_snapshot_policy",
    }


@pytest.mark.asyncio
async def test_rollback_and_delete_appear_only_with_allow_write(snapshots):
    names = {t.name for t in await snapshots.mcp.list_tools()}
    assert {
        "rollback_server_snapshot",
        "rollback_volume_snapshot",
        "delete_server_snapshot_policy",
        "enable_server_auto_snapshot",
    } <= names


@pytest.mark.asyncio
async def test_rollback_refuses_in_read_only_mode(snapshots_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await snapshots_ro.rollback_server_snapshot(
            server_id=SERVER,
            snapshot_point_id=POINT,
            body=RollbackSnapshotDto(),
            region="HCM-3",
        )


# ── the snapshot envelope ─────────────────────────────────────────────────────


def test_snapshot_point_reads_retention_from_nested_config():
    item = SnapshotPointItem.from_api(
        {
            "id": POINT,
            "name": "nightly",
            "serverId": SERVER,
            "scheduleType": "AUTO",
            "size": 40,
            "snapshotConfig": {"isPermanently": False, "retainedDays": 7},
        }
    )
    assert (item.id, item.size_gb, item.is_permanent, item.retained_days) == (POINT, 40, False, 7)


def test_snapshot_policy_reports_unset_instead_of_failing():
    policy = SnapshotPolicyData.from_api("HCM-3", SERVER, None)
    assert policy.configured is False
    assert policy.enabled is False


def test_snapshot_policy_counts_points_of_either_kind():
    server = SnapshotPolicyData.from_api(
        "HCM-3", SERVER, {"id": "cfg-1", "enableSnapshot": True, "snapshotServerPoints": [{}, {}]}
    )
    volume = SnapshotPolicyData.from_api(
        "HCM-3", VOLUME, {"id": "cfg-2", "snapshotVolumePoints": [{}]}
    )
    assert (server.configured, server.enabled, server.snapshot_count) == (True, True, 2)
    assert (volume.configured, volume.enabled, volume.snapshot_count) == (True, False, 1)


@respx.mock
@pytest.mark.asyncio
async def test_list_server_snapshots_reads_the_items_envelope(snapshots):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/snapshots").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"id": POINT, "name": "before-upgrade", "serverId": SERVER, "size": 20}],
                "page": None,
                "pageSize": None,
                "totalItems": 1,
                "totalPages": 1,
            },
        )
    )
    result = await snapshots.list_server_snapshots(server_id=SERVER, region="HCM-3")
    assert result.resource_id == SERVER
    assert [s.id for s in result.snapshots] == [POINT]


@respx.mock
@pytest.mark.asyncio
async def test_list_snapshot_policies_uses_the_backup_gateway(snapshots):
    _mock_iam(respx.mock)
    route = respx.get(f"{VBACKUP}/v1/snapshot-policies").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "snap-pol-1",
                        "name": "Daily",
                        "policyType": "DEFAULT",
                        "config": {
                            "hour": 2.0,
                            "minute": 0.0,
                            "timeZone": "Asia/Ho_Chi_Minh",
                            "hourlyEnabled": False,
                            "dailyEnabled": True,
                            "dailyConfig": {"retention": 7.0},
                            "weeklyEnabled": False,
                            "monthlyEnabled": False,
                        },
                        "snapshotServerCount": 2,
                        "snapshotVolumeCount": 0,
                    }
                ],
                "totalItems": 1,
            },
        )
    )
    result = await snapshots.list_snapshot_policies(region="HCM-3", refresh=True)
    assert route.called
    policy = result.policies[0]
    assert (policy.id, policy.policy_type) == ("snap-pol-1", "DEFAULT")
    assert policy.schedule == "daily, keep 7"
    assert policy.run_at == "02:00 Asia/Ho_Chi_Minh"


@respx.mock
@pytest.mark.asyncio
async def test_snapshot_policy_summary_reports_an_hourly_cadence(snapshots):
    _mock_iam(respx.mock)
    respx.get(f"{VBACKUP}/v1/snapshot-policies").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "snap-pol-2",
                        "name": "Enhanced",
                        "config": {
                            "hourlyEnabled": True,
                            "hourlyConfig": {"interval": 1.0, "retention": 24.0},
                            "dailyEnabled": True,
                            "dailyConfig": {"retention": 7.0},
                        },
                    }
                ]
            },
        )
    )
    result = await snapshots.list_snapshot_policies(region="HCM-3", refresh=True)
    assert result.policies[0].schedule == "hourly every 1, keep 24; daily, keep 7"


@respx.mock
@pytest.mark.asyncio
async def test_snapshot_paging_follows_total_items_spelling(snapshots):
    _mock_iam(respx.mock)
    route = respx.get(f"{HCM3}/v2/{PROJECT}/volumes/{VOLUME}/snapshots")
    route.side_effect = [
        httpx.Response(200, json={"items": [{"id": "s1"}], "totalItems": 2}),
        httpx.Response(200, json={"items": [{"id": "s1"}, {"id": "s2"}], "totalItems": 2}),
    ]
    result = await snapshots.list_volume_snapshots(volume_id=VOLUME, region="HCM-3")
    assert [s.id for s in result.snapshots] == ["s1", "s2"]


@respx.mock
@pytest.mark.asyncio
async def test_get_server_snapshot_policy_tolerates_null(snapshots):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/snapshots/detail").mock(
        return_value=httpx.Response(200, json=None)
    )
    policy = await snapshots.get_server_snapshot_policy(server_id=SERVER, region="HCM-3")
    assert policy.configured is False


# ── write bodies ──────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_create_server_snapshot_sends_retention(snapshots):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/snapshots").mock(
        return_value=httpx.Response(200, json={"data": {"id": POINT, "name": "pre-resize"}})
    )
    result = await snapshots.create_server_snapshot(
        server_id=SERVER,
        body=CreateSnapshotDto(
            name="pre-resize", description="before flavor change", retainedDays=7
        ),
        region="HCM-3",
    )
    assert result.id == POINT
    assert route.calls[0].request.read() == (
        b'{"name":"pre-resize","description":"before flavor change","retainedDays":7}'
    )


def test_create_snapshot_requires_a_description():
    with pytest.raises(ValidationError):
        CreateSnapshotDto(name="x")


def test_snapshot_dtos_reject_unknown_fields():
    with pytest.raises(ValidationError):
        CreateSnapshotDto(name="x", description="y", period=12)
    with pytest.raises(ValidationError):
        UpdateSnapshotPolicyDto(snapshotPolicyId="pol-1", frequency="daily")
    with pytest.raises(ValidationError):
        CreateSnapshotPolicyDto(description="d", isEnableAutoRenew=True)


@respx.mock
@pytest.mark.asyncio
async def test_rollback_passes_the_restart_flag(snapshots):
    _mock_iam(respx.mock)
    route = respx.post(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/snapshots/rollback/{POINT}").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    message = await snapshots.rollback_server_snapshot(
        server_id=SERVER,
        snapshot_point_id=POINT,
        body=RollbackSnapshotDto(restartServerWhenRevertCompleted=True),
        region="HCM-3",
    )
    assert POINT in message
    assert route.calls[0].request.read() == b'{"restartServerWhenRevertCompleted":true}'


@respx.mock
@pytest.mark.asyncio
async def test_volume_auto_snapshot_is_scoped_by_its_server(snapshots):
    _mock_iam(respx.mock)
    route = respx.put(
        f"{HCM3}/v2/{PROJECT}/volumes/{VOLUME}/volume-snapshots/servers/{SERVER}/enable-auto"
    ).mock(return_value=httpx.Response(200, json={"data": {}}))
    await snapshots.enable_volume_auto_snapshot(volume_id=VOLUME, server_id=SERVER, region="HCM-3")
    assert route.called


@pytest.mark.asyncio
async def test_snapshot_ids_are_validated(snapshots):
    with pytest.raises(ValueError, match="Invalid"):
        await snapshots.delete_server_snapshot(
            server_id=SERVER, snapshot_point_id="../../etc/passwd", region="HCM-3"
        )
