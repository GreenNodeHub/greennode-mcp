"""Tests for response formatting models."""

from __future__ import annotations

import pytest
from greennode.vks_mcp_server.models import (
    AutoHealingConfig,
    AutoScaleConfig,
    AutoUpgradeConfig,
    ClusterDetail,
    ClusterSummary,
    CreateClusterComboDto,
    CreateNodeGroupDto,
    NodeGroupDetail,
    NodeGroupSpec,
    NodeGroupTaint,
    NodeItem,
    NodesData,
    PlacementGroupConfig,
    QuotaData,
    SubnetItem,
    UpdateClusterDto,
    UpdateNodeGroupDto,
    UpdateNodeGroupMetadataDto,
    UpgradeConfig,
    VersionItem,
    VersionsData,
    VolumeTypeItem,
    VpcItem,
    VpcListData,
    format_cluster_detail,
)
from pydantic import ValidationError


def test_cluster_summary_maps_private_cluster_and_az_strategy():
    """ClusterSummary carries enablePrivateCluster and azStrategy from the API."""
    summary = ClusterSummary.from_api(
        {
            "uid": "k8s-abc",
            "name": "prod",
            "status": "ACTIVE",
            "version": "1.29",
            "nodeCount": 3,
            "enablePrivateCluster": True,
            "azStrategy": "MULTI_AZ",
        }
    )
    assert summary.enable_private_cluster == "True"
    assert summary.az_strategy == "MULTI_AZ"


def test_node_item_maps_floating_and_fixed_ip():
    """NodeItem maps floatingIp/fixedIp/ready/poc from the API NodeDto."""
    node = NodeItem.from_api(
        {
            "id": "node-1",
            "name": "worker-1",
            "status": "ACTIVE",
            "floatingIp": "1.2.3.4",
            "fixedIp": "10.0.0.5",
            "ready": True,
            "poc": False,
        }
    )
    assert node.id == "node-1"
    assert node.name == "worker-1"
    assert node.status == "ACTIVE"
    assert node.floating_ip == "1.2.3.4"
    assert node.fixed_ip == "10.0.0.5"
    assert node.ready == "True"
    assert node.poc == "False"


def test_nodes_data_markdown_shows_ips_and_ready():
    """NodesData markdown table renders floating/fixed IP and ready columns."""
    data = NodesData(
        nodegroup_id="ng-1",
        nodes=[
            NodeItem(
                id="node-1",
                name="worker-1",
                status="ACTIVE",
                floating_ip="1.2.3.4",
                fixed_ip="10.0.0.5",
                ready="True",
                poc="False",
            )
        ],
    )
    md = data.to_markdown()
    assert "1.2.3.4" in md
    assert "10.0.0.5" in md
    assert "worker-1" in md


def test_nodegroup_detail_maps_advanced_read_fields():
    """NodeGroupDetail exposes subnet/encryption/placement fields on read."""
    detail = NodeGroupDetail.from_api(
        {
            "uid": "ng-1",
            "name": "ng-default",
            "subnetId": "sub-123",
            "secondarySubnets": ["sub-a", "sub-b"],
            "enabledEncryptionVolume": True,
            "placementGroupId": "pg-9",
        }
    )
    assert detail.subnet_id == "sub-123"
    assert detail.secondary_subnets == ["sub-a", "sub-b"]
    assert detail.enabled_encryption_volume == "True"
    assert detail.placement_group_id == "pg-9"


def test_format_cluster_detail():
    """Full cluster dict renders id, networkType, and vpcId."""
    cluster = {
        "uid": "cluster-uid-abcdef",
        "name": "prod-cluster",
        "status": "ACTIVE",
        "version": "1.29",
        "networkType": "CALICO",
        "vpcId": "vpc-0011223344",
        "subnetId": "subnet-99887766",
        "cidr": "10.0.0.0/16",
        "nodeCount": 5,
        "privateCluster": False,
        "enabledAddons": {"lbPlugin": "true", "csiPlugin": "true"},
        "createdAt": "2024-03-10T12:00:00Z",
        "updatedAt": "2024-03-11T09:30:00Z",
    }
    result = format_cluster_detail(cluster)
    assert "cluster-uid-abcdef" in result
    assert "CALICO" in result
    assert "vpc-0011223344" in result


def test_vpc_item_is_minimal_id_name_projection():
    """VpcItem carries only what the user chooses by: id + name + the vDNS flag
    (azStrategy=MULTI eligibility) — not VPC management fields like cidr."""
    item = VpcItem.from_api(
        {
            "id": "net-1",
            "displayName": "prod-vpc",
            "cidr": "10.0.0.0/16",
            "status": "ACTIVE",
            "dnsStatus": "ENABLED",
        }
    )
    assert item.model_dump() == {"id": "net-1", "name": "prod-vpc", "enabled_dns": True}


def test_subnet_item_is_minimal_projection():
    """SubnetItem carries id + name + zone + secondary_subnets — no cidr/status."""
    item = SubnetItem.from_api(
        {
            "uuid": "sub-9",
            "name": "s1",
            "cidr": "10.0.1.0/24",
            "status": "ACTIVE",
            "zone": {"uuid": "zone-a", "name": "HCM03-1A", "isDefault": True},
        }
    )
    assert item.model_dump() == {
        "id": "sub-9",
        "name": "s1",
        "zone": {"uuid": "zone-a", "name": "HCM03-1A"},
        "secondary_subnets": [],
    }


def test_subnet_item_zone_absent():
    """A subnet with no zone yields zone=None."""
    item = SubnetItem.from_api({"uuid": "sub-0", "name": "s0"})
    assert item.zone is None


def test_subnet_item_exposes_secondary_subnet_cidrs():
    """The create APIs take secondarySubnets as CIDR strings (per API spec) —
    NOT the sec-sub-* uuids. Extract the cidr from SecondarySubnetDto."""
    item = SubnetItem.from_api(
        {
            "uuid": "sub-9",
            "name": "s1",
            "secondarySubnets": [
                {"uuid": "ssub-1", "name": "sec-1", "cidr": "10.1.0.0/24"},
                {"uuid": "ssub-2", "name": "sec-2", "cidr": "10.1.1.0/24"},
            ],
        }
    )
    assert item.secondary_subnets == ["10.1.0.0/24", "10.1.1.0/24"]
    # plain strings are tolerated (already CIDRs); absent -> empty list
    assert SubnetItem.from_api(
        {"uuid": "sub-8", "secondarySubnets": ["10.2.0.0/24"]}
    ).secondary_subnets == ["10.2.0.0/24"]
    assert SubnetItem.from_api({"uuid": "sub-0"}).secondary_subnets == []


def test_volume_type_item_is_minimal_id_iops():
    """VolumeTypeItem carries only {id, iops} — id is the diskType, user picks by iops."""
    item = VolumeTypeItem.from_api(
        {"id": "vtype-abc", "name": "3000", "iops": 3000, "minSize": 20, "maxSize": 2000}
    )
    assert item.model_dump() == {"id": "vtype-abc", "iops": 3000}


def test_quota_data_maps_api_fields():
    """QuotaData carries the four VKS quota numbers."""
    q = QuotaData.from_api(
        {
            "maxClusters": 10,
            "numClusters": 3,
            "maxNodeGroupsPerCluster": 5,
            "maxNodesPerNodeGroup": 100,
        }
    )
    assert q.max_clusters == 10
    assert q.num_clusters == 3
    assert q.max_node_groups_per_cluster == 5
    assert q.max_nodes_per_node_group == 100


def test_vpc_list_data_dump_shape():
    data = VpcListData(region="HCM-3", vpcs=[VpcItem(id="net-1", name="a")])
    dumped = data.model_dump(mode="json")
    assert dumped["region"] == "HCM-3"
    assert dumped["vpcs"][0]["id"] == "net-1"


def test_versions_data_marks_recommended():
    data = VersionsData(
        recommended="v1.29.0",
        versions=[VersionItem(version="v1.29.0", stage="STABLE", recommended=True)],
    )
    assert data.versions[0].recommended is True
    assert data.recommended == "v1.29.0"


# ---------------------------------------------------------------------------
# Request DTO tests
# ---------------------------------------------------------------------------


def test_create_cluster_dto_defaults_and_enums():
    dto = CreateClusterComboDto(
        name="demo",
        version="v1.29.0",
        networkType="CILIUM_NATIVE_ROUTING",
        vpcId="net-1",
        listSubnetIds=["sub-1"],
        nodeNetmaskSize=25,
        enablePrivateCluster=False,
    )
    assert dto.releaseChannel == "STABLE"


def test_create_cluster_dto_rejects_bad_network_type():
    with pytest.raises(ValidationError):
        CreateClusterComboDto(
            name="demo",
            version="v1.29.0",
            networkType="BOGUS",
            vpcId="net-1",
            enablePrivateCluster=False,
        )


def test_create_cluster_dto_rejects_deprecated_nodegroups():
    """The deprecated nodeGroups array is rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        CreateClusterComboDto(
            name="demo",
            version="v1.29.0",
            networkType="CILIUM_OVERLAY",
            vpcId="net-1",
            enablePrivateCluster=False,
            cidr="10.96.0.0/16",
            nodeGroups=[],
        )


def test_nodegroup_spec_disksize_bounds():
    with pytest.raises(ValidationError):
        NodeGroupSpec(
            name="x",
            flavorId="f",
            diskSize=5,
            diskType="SSD",
            numNodes=1,
            securityGroups=[],
            sshKeyId="k",
            upgradeConfig=UpgradeConfig(),
        )


# ---------------------------------------------------------------------------
# UpgradeConfig defaults (Fix 2)
# ---------------------------------------------------------------------------


def test_upgrade_config_defaults_non_empty():
    """UpgradeConfig() produces a non-empty body with sensible defaults."""
    uc = UpgradeConfig()
    dumped = uc.model_dump(exclude_none=True)
    assert dumped["maxSurge"] == 1
    assert dumped["maxUnavailable"] == 0
    assert dumped["strategy"] == "SURGE"
    # os is optional — should not appear when not set
    assert "os" not in dumped


def test_upgrade_config_no_longer_carries_os():
    """`os` moved to the node-group top level; UpgradeConfig rejects it."""
    with pytest.raises(ValidationError, match="extra"):
        UpgradeConfig(os="ubuntu")


def test_upgrade_config_maxsurge_min_one():
    """maxSurge must be >= 1 (API minimum)."""
    with pytest.raises(ValidationError):
        UpgradeConfig(maxSurge=0)


def test_nodegroup_spec_os_top_level_default_ubuntu():
    """NodeGroupSpec exposes `os` at the top level, defaulting to ubuntu."""
    spec = NodeGroupSpec(
        name="ng",
        flavorId="f",
        diskSize=100,
        diskType="SSD",
        numNodes=1,
        sshKeyId="k",
        subnetId="sub-1",
        secondarySubnets=[],
    )
    assert spec.os == "ubuntu"
    dumped = spec.model_dump(exclude_none=True)
    assert dumped["os"] == "ubuntu"
    assert "os" not in dumped["upgradeConfig"]


def test_nodegroup_spec_os_supports_rocky():
    """NodeGroupSpec accepts rocky and rejects unknown OS."""
    assert (
        NodeGroupSpec(
            name="ng",
            flavorId="f",
            diskSize=100,
            diskType="SSD",
            numNodes=1,
            sshKeyId="k",
            subnetId="sub-1",
            secondarySubnets=[],
            os="rocky",
        ).os
        == "rocky"
    )
    with pytest.raises(ValidationError):
        NodeGroupSpec(
            name="ng",
            flavorId="f",
            diskSize=100,
            diskType="SSD",
            numNodes=1,
            sshKeyId="k",
            subnetId="sub-1",
            secondarySubnets=[],
            os="windows",
        )


def test_nodegroup_spec_requires_subnet_id():
    """subnetId is required; secondarySubnets is optional at the schema level
    (only CILIUM_NATIVE_ROUTING clusters use it — the validator enforces that)
    and stays off the wire when omitted."""
    with pytest.raises(ValidationError, match="subnetId"):
        NodeGroupSpec(
            name="ng",
            flavorId="f",
            diskSize=100,
            diskType="SSD",
            numNodes=1,
            sshKeyId="k",
        )
    spec = NodeGroupSpec(
        name="ng",
        flavorId="f",
        diskSize=100,
        diskType="SSD",
        numNodes=1,
        sshKeyId="k",
        subnetId="sub-1",
    )
    assert "secondarySubnets" not in spec.model_dump(exclude_none=True)


def test_create_cluster_combo_rejects_secondary_subnets():
    """secondarySubnets moved to the node group — the cluster DTO rejects it."""
    with pytest.raises(ValidationError, match="secondarySubnets"):
        CreateClusterComboDto(
            name="demo01",
            version="v1.29.0",
            networkType="CILIUM_NATIVE_ROUTING",
            vpcId="net-1",
            secondarySubnets=["10.5.60.0/22"],
        )


def test_nodegroup_spec_exposes_advanced_fields():
    """NodeGroupSpec accepts the full CLI field set with typed nested DTOs."""
    spec = NodeGroupSpec(
        name="ng",
        flavorId="f",
        diskSize=100,
        diskType="SSD",
        numNodes=1,
        sshKeyId="k",
        enabledEncryptionVolume=True,
        subnetId="sub-1",
        secondarySubnets=["sub-a"],
        labels={"team": "core"},
        taints=[NodeGroupTaint(key="gpu", value="true", effect="NoSchedule")],
        tags={"env": "prod"},
        autoScaleConfig=AutoScaleConfig(minSize=1, maxSize=5),
        placementGroupConfigDto=PlacementGroupConfig(type="NEW", placementGroupName="pg"),
    )
    dumped = spec.model_dump(exclude_none=True)
    assert dumped["enabledEncryptionVolume"] is True
    assert dumped["subnetId"] == "sub-1"
    assert dumped["taints"][0]["effect"] == "NoSchedule"
    assert dumped["autoScaleConfig"] == {"minSize": 1, "maxSize": 5}
    assert dumped["placementGroupConfigDto"]["type"] == "NEW"


def test_node_group_taint_rejects_bad_effect():
    """NodeGroupTaint effect must be a valid Kubernetes taint effect."""
    with pytest.raises(ValidationError):
        NodeGroupTaint(key="k", effect="BogusEffect")


def test_auto_scale_config_bounds():
    """AutoScaleConfig enforces minSize>=0 and maxSize>=1."""
    assert AutoScaleConfig(minSize=0, maxSize=1).maxSize == 1
    with pytest.raises(ValidationError):
        AutoScaleConfig(minSize=0, maxSize=0)


def test_auto_scale_config_rejects_inverted_range():
    """minSize above maxSize is a guaranteed server error; reject it locally."""
    assert AutoScaleConfig(minSize=3, maxSize=3).minSize == 3
    with pytest.raises(ValidationError, match="must not exceed maxSize"):
        AutoScaleConfig(minSize=5, maxSize=2)


def test_update_nodegroup_dto_never_serializes_the_disable_sentinel():
    """disable_auto_scale is a local flag: it must not appear in any dump."""
    dto = UpdateNodeGroupDto(numNodes=3, disable_auto_scale=True)
    assert dto.disable_auto_scale is True
    assert "disable_auto_scale" not in dto.model_dump()
    assert "disable_auto_scale" not in dto.model_dump(exclude_none=True)
    assert "disable_auto_scale" not in dto.model_dump_json()
    # Still advertised to MCP clients so the tool schema can offer it.
    assert "disable_auto_scale" in UpdateNodeGroupDto.model_json_schema()["properties"]


def test_placement_group_config_type_enum():
    """PlacementGroupConfig type must be NEW or EXISTING."""
    with pytest.raises(ValidationError):
        PlacementGroupConfig(type="OTHER")


def test_update_nodegroup_dto_rejects_labels_now():
    """labels/taints moved to metadata; UpdateNodeGroupDto rejects them."""
    with pytest.raises(ValidationError, match="extra"):
        UpdateNodeGroupDto(numNodes=3, labels={"a": "b"})


def test_update_nodegroup_dto_types_autoscale():
    """UpdateNodeGroupDto autoScaleConfig is a typed AutoScaleConfig."""
    dto = UpdateNodeGroupDto(autoScaleConfig=AutoScaleConfig(minSize=2, maxSize=10))
    dumped = dto.model_dump(exclude_none=True)
    assert dumped["autoScaleConfig"] == {"minSize": 2, "maxSize": 10}


def test_update_nodegroup_metadata_dto_accepts_labels_tags_taints():
    """UpdateNodeGroupMetadataDto carries labels, tags, and typed taints."""
    dto = UpdateNodeGroupMetadataDto(
        labels={"team": "core"},
        tags={"env": "prod"},
        taints=[NodeGroupTaint(key="gpu", effect="NoExecute")],
    )
    dumped = dto.model_dump(exclude_none=True)
    assert dumped["labels"] == {"team": "core"}
    assert dumped["tags"] == {"env": "prod"}
    assert dumped["taints"][0]["effect"] == "NoExecute"


def test_update_nodegroup_metadata_dto_rejects_unknown():
    """UpdateNodeGroupMetadataDto rejects unknown fields."""
    with pytest.raises(ValidationError, match="extra"):
        UpdateNodeGroupMetadataDto(labels={"a": "b"}, numNodes=3)


# ---------------------------------------------------------------------------
# extra="forbid" enforcement tests (Fix 1)
# ---------------------------------------------------------------------------


def test_extra_forbid_upgrade_config():
    """UpgradeConfig rejects unknown fields with ValidationError."""
    with pytest.raises(ValidationError, match="extra"):
        UpgradeConfig(maxSurge=1, unknownField="oops")


def test_extra_forbid_create_nodegroup_dto():
    """CreateNodeGroupDto rejects unknown fields (e.g. subnetId) with ValidationError."""
    with pytest.raises(ValidationError, match="extra"):
        CreateNodeGroupDto(
            name="ng-test",
            flavorId="f1",
            diskSize=100,
            diskType="SSD",
            numNodes=1,
            securityGroups=["sg1"],
            sshKeyId="k1",
            upgradeConfig=UpgradeConfig(),
            totallyUnknownField="oops",
        )


def test_extra_forbid_update_cluster_dto():
    """UpdateClusterDto rejects unknown fields with ValidationError."""
    with pytest.raises(ValidationError, match="extra"):
        UpdateClusterDto(version="v1.29.0", whitelistNodeCIDRs=["0.0.0.0/0"], bogusField="no")


def test_auto_upgrade_config_typed():
    """AutoUpgradeConfig carries weekdays and time; rejects unknown fields."""
    uc = AutoUpgradeConfig(weekdays="Mon,Wed", time="03:00")
    assert uc.model_dump() == {"weekdays": "Mon,Wed", "time": "03:00"}
    with pytest.raises(ValidationError, match="extra"):
        AutoUpgradeConfig(weekdays="Mon", time="03:00", bogus=1)


def test_auto_healing_config_typed_and_bounds():
    """AutoHealingConfig carries the healing knobs and bounds timeoutUnhealthy."""
    hc = AutoHealingConfig(
        enableAutoHealing=True, maxUnhealthy="20%", unhealthyRange="[2-5]", timeoutUnhealthy=10
    )
    dumped = hc.model_dump(exclude_none=True)
    assert dumped["enableAutoHealing"] is True
    assert dumped["maxUnhealthy"] == "20%"
    with pytest.raises(ValidationError):
        AutoHealingConfig(enableAutoHealing=True, timeoutUnhealthy=1)


def test_create_cluster_combo_allows_control_plane_only():
    """create_cluster is control-plane only; the body carries no node group (CLI parity)."""
    dto = CreateClusterComboDto(
        name="demo01",
        version="v1.29.0",
        networkType="CILIUM_OVERLAY",
        vpcId="net-1",
        listSubnetIds=["sub-1"],
        cidr="10.0.0.0/16",
    )
    dumped = dto.model_dump(exclude_none=True)
    assert "nodeGroups" not in dumped
    # CLI-aligned defaults are always present
    assert dumped["releaseChannel"] == "STABLE"
    assert dumped["enabledLoadBalancerPlugin"] is True
    assert dumped["enabledBlockStoreCsiPlugin"] is True
    # private-cluster-only field: absent unless explicitly set
    assert "enabledServiceEndpoint" not in dumped
    assert dumped["azStrategy"] == "SINGLE"
    assert dumped["enablePrivateCluster"] is False


def test_create_cluster_combo_new_fields():
    """CreateClusterComboDto exposes the full CLI create-cluster field set."""
    dto = CreateClusterComboDto(
        name="demo01",
        version="v1.29.0",
        networkType="CILIUM_NATIVE_ROUTING",
        vpcId="net-1",
        enablePrivateCluster=True,
        enabledServiceEndpoint=True,
        azStrategy="MULTI",
        description="prod cluster",
        listSubnetIds=["sub-a", "sub-b"],
        nodeNetmaskSize=25,
        autoUpgradeConfig=AutoUpgradeConfig(weekdays="Mon", time="03:00"),
        autoHealingConfig=AutoHealingConfig(enableAutoHealing=True),
    )
    dumped = dto.model_dump(exclude_none=True)
    assert dumped["description"] == "prod cluster"
    assert "subnetId" not in dumped
    assert dumped["listSubnetIds"] == ["sub-a", "sub-b"]
    assert dumped["nodeNetmaskSize"] == 25
    assert dumped["autoUpgradeConfig"] == {"weekdays": "Mon", "time": "03:00"}
    assert dumped["autoHealingConfig"]["enableAutoHealing"] is True


def test_update_cluster_dto_is_partial():
    """The API accepts partial updates — every field optional (empty bodies are
    rejected at the handler, not the DTO)."""
    dto = UpdateClusterDto(version="v1.29.0")  # version alone is valid now
    assert dto.model_dump(exclude_none=True) == {"version": "v1.29.0"}


def test_update_cluster_dto_rejects_name_and_release_channel():
    """name/description/releaseChannel are not updatable via this endpoint anymore."""
    with pytest.raises(ValidationError, match="extra"):
        UpdateClusterDto(version="v1.29.0", whitelistNodeCIDRs=["0.0.0.0/0"], name="x")


def test_update_cluster_dto_plugin_toggles():
    """UpdateClusterDto sends version, whitelist, and optional plugin toggles."""
    dto = UpdateClusterDto(
        version="v1.29.0",
        whitelistNodeCIDRs=["10.0.0.0/8"],
        enabledLoadBalancerPlugin=False,
    )
    dumped = dto.model_dump(exclude_none=True)
    assert dumped["version"] == "v1.29.0"
    assert dumped["whitelistNodeCIDRs"] == ["10.0.0.0/8"]
    assert dumped["enabledLoadBalancerPlugin"] is False
    assert "enabledBlockStoreCsiPlugin" not in dumped


def test_extra_forbid_update_nodegroup_dto():
    """UpdateNodeGroupDto rejects unknown fields with ValidationError."""
    with pytest.raises(ValidationError, match="extra"):
        UpdateNodeGroupDto(numNodes=3, subnetId="not-allowed")


def test_extra_forbid_create_cluster_combo_dto():
    """CreateClusterComboDto rejects unknown fields with ValidationError."""
    with pytest.raises(ValidationError, match="extra"):
        CreateClusterComboDto(
            name="mycluster01",
            version="v1.29.0",
            networkType="CILIUM_NATIVE_ROUTING",
            vpcId="net-1",
            enablePrivateCluster=False,
            unknownTopLevel="bad",
        )


def test_vpc_item_maps_dns_status():
    """azStrategy=MULTI clusters need a vDNS-enabled VPC — the item carries it."""
    from greennode.vks_mcp_server.models import VpcItem

    on = VpcItem.from_api({"id": "net-1", "displayName": "vpc-a", "dnsStatus": "ENABLED"})
    assert on.enabled_dns is True
    off = VpcItem.from_api({"id": "net-2", "displayName": "vpc-b", "dnsStatus": "DISABLED"})
    assert off.enabled_dns is False
    missing = VpcItem.from_api({"id": "net-3", "displayName": "vpc-c"})
    assert missing.enabled_dns is False


def test_create_cluster_dto_service_endpoint_omitted_by_default():
    """enabledServiceEndpoint is private-cluster-only: unset -> absent from the
    wire body (public clusters must not carry it)."""
    from greennode.vks_mcp_server.models import CreateClusterComboDto

    dto = CreateClusterComboDto(
        name="mycluster01",
        version="1.28",
        networkType="CILIUM_OVERLAY",
        vpcId="vpc-1",
        listSubnetIds=["sub-1"],
        cidr="10.96.0.0/16",
    )
    assert "enabledServiceEndpoint" not in dto.model_dump(exclude_none=True)
    private = CreateClusterComboDto(
        name="mycluster01",
        version="1.28",
        networkType="CILIUM_OVERLAY",
        vpcId="vpc-1",
        listSubnetIds=["sub-1"],
        cidr="10.96.0.0/16",
        enablePrivateCluster=True,
        enabledServiceEndpoint=True,
    )
    assert private.model_dump(exclude_none=True)["enabledServiceEndpoint"] is True


# ---------------------------------------------------------------------------
# create_cluster subnet / network rules (parity with greennode-cli create-cluster)
# ---------------------------------------------------------------------------


def _cluster_kwargs(**overrides):
    base = {
        "name": "mycluster01",
        "version": "v1.29.0",
        "networkType": "CILIUM_OVERLAY",
        "vpcId": "net-1",
        "cidr": "10.96.0.0/16",
        "listSubnetIds": ["sub-a"],
    }
    base.update(overrides)
    return base


def test_create_cluster_requires_subnets():
    """listSubnetIds carries one or many subnets and is required — the API needs it
    for both azStrategy values."""
    kwargs = _cluster_kwargs()
    del kwargs["listSubnetIds"]
    with pytest.raises(ValidationError):
        CreateClusterComboDto(**kwargs)
    with pytest.raises(ValidationError):
        CreateClusterComboDto(**_cluster_kwargs(listSubnetIds=[]))


def test_create_cluster_rejects_deprecated_subnet_id():
    """subnetId is deprecated on the API and no longer accepted by the CLI or here."""
    with pytest.raises(ValidationError, match="extra"):
        CreateClusterComboDto(**_cluster_kwargs(subnetId="sub-a"))


def test_create_cluster_single_az_takes_one_subnet():
    """azStrategy SINGLE (the default) takes a single-element list; MULTI takes several."""
    assert CreateClusterComboDto(**_cluster_kwargs()).azStrategy == "SINGLE"
    with pytest.raises(ValidationError, match="exactly one listSubnetIds value"):
        CreateClusterComboDto(**_cluster_kwargs(listSubnetIds=["sub-a", "sub-b"]))
    multi = CreateClusterComboDto(
        **_cluster_kwargs(azStrategy="MULTI", listSubnetIds=["sub-a", "sub-b"])
    )
    assert multi.listSubnetIds == ["sub-a", "sub-b"]


def test_create_cluster_multi_az_needs_two_subnets():
    """MULTI spreads the control plane across zones, so one subnet is a single-zone
    cluster wearing a MULTI label — reject it instead of letting the API decide."""
    with pytest.raises(ValidationError, match="at least two listSubnetIds values"):
        CreateClusterComboDto(**_cluster_kwargs(azStrategy="MULTI", listSubnetIds=["sub-a"]))


def test_create_cluster_rejects_duplicate_subnets():
    """A repeated id passes a count check while still being one zone."""
    with pytest.raises(ValidationError, match="must not repeat a subnet"):
        CreateClusterComboDto(
            **_cluster_kwargs(azStrategy="MULTI", listSubnetIds=["sub-a", "sub-a"])
        )
    with pytest.raises(ValidationError, match="must not repeat a subnet"):
        CreateClusterComboDto(
            **_cluster_kwargs(azStrategy="MULTI", listSubnetIds=["sub-a", "sub-b", "sub-a"])
        )


def test_create_cluster_network_type_requirements():
    """CILIUM_OVERLAY/TIGERA need cidr; CILIUM_NATIVE_ROUTING needs nodeNetmaskSize.
    Enforced on the model so create_cluster fails before the request, not only in
    validate_cluster_create."""
    kwargs = _cluster_kwargs()
    del kwargs["cidr"]
    with pytest.raises(ValidationError, match="requires cidr"):
        CreateClusterComboDto(**kwargs)
    with pytest.raises(ValidationError, match="requires cidr"):
        CreateClusterComboDto(**_cluster_kwargs(networkType="TIGERA", cidr=None))
    with pytest.raises(ValidationError, match="requires nodeNetmaskSize"):
        CreateClusterComboDto(**_cluster_kwargs(networkType="CILIUM_NATIVE_ROUTING", cidr=None))
    ok = CreateClusterComboDto(
        **_cluster_kwargs(networkType="CILIUM_NATIVE_ROUTING", cidr=None, nodeNetmaskSize=25)
    )
    assert ok.nodeNetmaskSize == 25


def test_create_cluster_node_netmask_size_bounds():
    """nodeNetmaskSize is 24-26 per the API schema."""
    for size in (24, 25, 26):
        assert (
            CreateClusterComboDto(
                **_cluster_kwargs(
                    networkType="CILIUM_NATIVE_ROUTING", cidr=None, nodeNetmaskSize=size
                )
            ).nodeNetmaskSize
            == size
        )
    for bad in (23, 27):
        with pytest.raises(ValidationError):
            CreateClusterComboDto(
                **_cluster_kwargs(
                    networkType="CILIUM_NATIVE_ROUTING", cidr=None, nodeNetmaskSize=bad
                )
            )


def test_update_cluster_whitelist_cidrs_max_items():
    """whitelistNodeCIDRs is capped at 30 entries by the API."""
    assert len(UpdateClusterDto(whitelistNodeCIDRs=["10.0.0.0/24"] * 30).whitelistNodeCIDRs) == 30
    with pytest.raises(ValidationError):
        UpdateClusterDto(whitelistNodeCIDRs=["10.0.0.0/24"] * 31)


def test_nodegroup_list_field_caps():
    """securityGroups (50), secondarySubnets (10), taints (50), labels/tags (50)."""
    base = {
        "name": "ng-demo",
        "flavorId": "flav-1",
        "diskType": "vtype-1",
        "sshKeyId": "key-1",
        "diskSize": 100,
        "numNodes": 1,
        "subnetId": "sub-1",
    }
    with pytest.raises(ValidationError):
        NodeGroupSpec(**base, securityGroups=[f"secg-{i}" for i in range(51)])
    with pytest.raises(ValidationError):
        NodeGroupSpec(**base, secondarySubnets=[f"10.5.{i}.0/24" for i in range(11)])
    with pytest.raises(ValidationError):
        NodeGroupSpec(**base, labels={f"k{i}": "v" for i in range(51)})
    with pytest.raises(ValidationError):
        UpdateNodeGroupMetadataDto(tags={f"k{i}": "v" for i in range(51)})


# ---------------------------------------------------------------------------
# Read models must not drop what the API returns
# ---------------------------------------------------------------------------


def test_cluster_detail_keeps_auto_healing_and_netmask():
    """ClusterDetailDto carries autoHealingConfig, nodeNetmaskSize and the ready
    counts; dropping them in the read model hides them from every caller."""
    detail = ClusterDetail.from_api(
        {
            "uid": "k8s-abc",
            "name": "prod",
            "nodeNetmaskSize": 25,
            "numReadyNodes": 3,
            "numNotReadyNodes": 1,
            "autoHealingConfig": {
                "enableAutoHealing": True,
                "maxUnhealthy": "20%",
                "timeoutUnhealthy": 10,
            },
        }
    )
    assert detail.node_netmask_size == 25
    assert detail.auto_healing_config["enableAutoHealing"] is True
    md = detail.to_markdown()
    assert "Node netmask size" in md and "25" in md
    assert "enabled=True" in md and "20%" in md and "10m" in md
    assert "3 / 1" in md


def test_cluster_detail_auto_healing_absent_reads_as_not_configured():
    md = ClusterDetail.from_api({"uid": "k8s-abc", "name": "prod"}).to_markdown()
    assert "| Auto-Healing | (not configured) |" in md


def test_nodegroup_detail_keeps_tags_os_and_version():
    detail = NodeGroupDetail.from_api(
        {
            "uid": "ng-1",
            "name": "default",
            "tags": {"env": "prod"},
            "imageOS": "ubuntu",
            "kubernetesVersion": "v1.29.13",
        }
    )
    assert detail.tags == {"env": "prod"}
    md = detail.to_markdown()
    assert "env=prod" in md
    assert "ubuntu" in md
    assert "v1.29.13" in md
