from __future__ import annotations

import pytest
from greennode.vks_mcp_server.server import create_server


async def _prompt_text(server, name: str, args: dict) -> str:
    result = await server.get_prompt(name, args)
    return " ".join(m.content.text for m in result.messages if hasattr(m.content, "text"))


@pytest.mark.asyncio
async def test_prompts_registered():
    server = create_server()
    prompts = await server.list_prompts()
    names = {p.name for p in prompts}
    assert "vks_getting_started" in names
    assert "vks_create_nodegroup" in names
    assert "vks_create_cluster" in names


@pytest.mark.asyncio
async def test_getting_started_returns_vietnamese_guidance():
    server = create_server()
    result = await server.get_prompt("vks_getting_started", {})
    # result.messages is list[PromptMessage]; content is a TextContent object with .text
    text = " ".join(m.content.text for m in result.messages if hasattr(m.content, "text"))
    assert "VKS" in text
    assert "grn configure" in text  # auth setup guidance present
    assert len(text) > 200


@pytest.mark.asyncio
async def test_create_nodegroup_prompt_accepts_cluster_id():
    server = create_server()
    text = await _prompt_text(server, "vks_create_nodegroup", {"cluster_id": "k8s-abc"})
    assert "create_nodegroup" in text
    assert "k8s-abc" in text  # the passed cluster id is woven into the guidance


@pytest.mark.asyncio
async def test_getting_started_network_types_accurate():
    """Network-type guidance matches the API enum and per-type requirements."""
    server = create_server()
    text = await _prompt_text(server, "vks_getting_started", {})
    assert "CALICO" not in text  # not a valid enum value
    assert "TIGERA" in text
    assert "secondarySubnets" in text  # CILIUM_NATIVE_ROUTING requirement stated
    # control-plane-only create is supported now
    assert "Cluster cần ít nhất 1" not in text
    assert "control-plane-only" in text


@pytest.mark.asyncio
async def test_getting_started_routes_new_tools():
    """Tool routing mentions the metadata tool and correct update_cluster scope."""
    server = create_server()
    text = await _prompt_text(server, "vks_getting_started", {})
    assert "update_nodegroup_metadata" in text
    assert "whitelistNodeCIDRs" in text  # update_cluster semantics
    assert "vks_create_cluster" in text  # points to the new prompt


@pytest.mark.asyncio
async def test_create_nodegroup_prompt_os_enum_and_metadata():
    """OS suggestions match the API enum; metadata routing is explained."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_nodegroup", {})
    assert "flatcar" not in text  # not a valid OS
    assert "rocky" in text
    assert "update_nodegroup_metadata" in text


@pytest.mark.asyncio
async def test_create_nodegroup_prompt_disktype_from_discovery():
    """diskType guidance points at list_volume_types (it is a volume type ID, not 'SSD')."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_nodegroup", {})
    assert "list_volume_types" in text
    assert '"diskType":"SSD"' not in text  # literal SSD string is not a valid diskType
    assert "get_quota" in text


@pytest.mark.asyncio
async def test_getting_started_routes_quota_and_volumetype():
    """Getting-started routing covers the new discovery tools."""
    server = create_server()
    text = await _prompt_text(server, "vks_getting_started", {})
    assert "list_volume_types" in text
    assert "get_quota" in text
    assert "list_placement_groups" in text


@pytest.mark.asyncio
async def test_create_nodegroup_prompt_placement_group_discovery():
    """Placement-group guidance points EXISTING ids at list_placement_groups."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_nodegroup", {})
    assert "list_placement_groups" in text


@pytest.mark.asyncio
async def test_create_cluster_prompt_checks_quota():
    """Cluster-creation flow includes a quota check before creating."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_cluster", {})
    assert "get_quota" in text


@pytest.mark.asyncio
async def test_create_cluster_prompt_guided_flow():
    """New cluster prompt: overlay default, validate + hard gate, control-plane-only."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_cluster", {})
    assert "CILIUM_OVERLAY" in text
    assert "validate_cluster_create" in text
    assert "create_cluster" in text
    assert "HARD GATE" in text
    assert "control plane" in text  # create_cluster is control-plane only
    assert "vks_create_nodegroup" in text  # cross-links the nodegroup flow


@pytest.mark.asyncio
async def test_create_nodegroup_prompt_zone_chained_discovery():
    """Discovery follows the zone chain: subnet picked first, then zone-scoped tools."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_nodegroup", {})
    assert "get_cluster" in text  # source of vpcId (and the cluster's region)
    assert "list_subnets" in text
    # subnet is picked BEFORE flavors/volume types (its zone.uuid scopes both)
    assert text.index("list_subnets") < text.index("list_flavors")
    assert text.index("list_subnets") < text.index("list_volume_types")
    assert "zone" in text
    assert "SSD" in text  # zone without NVME falls back to SSD; explicit SSD supported
    assert "IOPS" in text


@pytest.mark.asyncio
async def test_create_nodegroup_prompt_question_order():
    """Prompt asks in the agreed order: name/numNodes, public/private, os, subnet,
    secgroups, flavor, volume, ssh key, autoscale, upgrade, placement, metadata."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_nodegroup", {})
    anchors = [
        "numNodes",
        "enablePrivateNodes",
        "ubuntu",  # the os question (ubuntu|linux|rocky)
        "list_subnets",
        "list_security_groups",
        "list_flavors",
        "list_volume_types",
        "list_ssh_keys",
        "autoScaleConfig",
        "upgradeConfig",
        "placementGroupConfigDto",
        "labels",
    ]
    positions = [text.index(a) for a in anchors]
    assert positions == sorted(positions), (
        f"question order broken: {[a for _, a in sorted(zip(positions, anchors))]}"
    )


@pytest.mark.asyncio
async def test_create_cluster_prompt_question_order_and_rules():
    """Prompt mirrors the cluster question order and the one-setting rules."""
    from tests.test_tool_annotations import CLUSTER_QUESTION_ANCHORS

    server = create_server()
    text = await _prompt_text(server, "vks_create_cluster", {})
    anchors = [a for a in CLUSTER_QUESTION_ANCHORS if a not in ("5-20", "FULL body")]
    anchors[anchors.index("get_quota")] = "get_quota"
    positions = [text.index(a) for a in anchors]
    assert positions == sorted(positions), (
        f"question order broken: {[a for _, a in sorted(zip(positions, anchors))]}"
    )
    assert "MỘT cấu hình" in text  # one setting per question
    assert "ĐẦY ĐỦ" in text  # full plan before confirm
    assert "nodeNetmaskSize" in text


@pytest.mark.asyncio
async def test_create_cluster_service_endpoint_only_asked_when_private():
    """ServiceEndpoint is the private-connectivity mechanism — a public cluster
    must not be asked about it."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_cluster", {})
    assert "Chỉ khi private" in text
    # the conditional guard sits on the ServiceEndpoint step itself
    seg = text[text.index("Chỉ khi private") : text.index("Chỉ khi private") + 200]
    assert "enabledServiceEndpoint" in seg
    assert "BẬT" in seg  # default is ON for private clusters


@pytest.mark.asyncio
async def test_create_cluster_prompt_multi_requires_vdns_vpc():
    """azStrategy=MULTI can only use vDNS-enabled VPCs — the VPC step must say
    to filter by enabled_dns when MULTI was chosen."""
    server = create_server()
    text = await _prompt_text(server, "vks_create_cluster", {})
    assert "enabled_dns" in text
    i = text.index("enabled_dns")
    seg = text[max(0, i - 250) : i + 250]
    assert "MULTI" in seg  # the rule is tied to the MULTI strategy
