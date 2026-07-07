# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0](https://github.com/vngcloud/greennode-mcp/compare/vks-mcp-server-v0.5.0...vks-mcp-server-v0.6.0) (2026-07-07)


### ⚠ BREAKING CHANGES

* create_cluster no longer accepts nodeGroups; create node groups separately via create_nodegroup.

### Features

* create_cluster creates the control plane only (drop deprecated nodeGroups) ([#21](https://github.com/vngcloud/greennode-mcp/issues/21)) ([0e8f34b](https://github.com/vngcloud/greennode-mcp/commit/0e8f34bf6c78969591efe10c4126eb25db87c66d))

## [0.5.0](https://github.com/vngcloud/greennode-mcp/compare/vks-mcp-server-v0.4.1...vks-mcp-server-v0.5.0) (2026-07-06)


### Features

* **core:** extract shared greennode.mcp_core package ([#15](https://github.com/vngcloud/greennode-mcp/issues/15)) ([5491ad0](https://github.com/vngcloud/greennode-mcp/commit/5491ad0af867a6f2bc8c788ec433a3571696709f))
* GreenNode MCP monorepo — VKS MCP server ([3c2f7a5](https://github.com/vngcloud/greennode-mcp/commit/3c2f7a5c69f29b161547bd3e1d2e15eba1c65140))
* new-server scaffold, agent skills, and tiered CLAUDE.md ([#17](https://github.com/vngcloud/greennode-mcp/issues/17)) ([95cf935](https://github.com/vngcloud/greennode-mcp/commit/95cf93505ab7d17d820ac004c128e3f7741df627))

## [Unreleased]

### Fixed

- `list_nodes` now maps the correct VKS `NodeDto` fields. It previously
  read non-existent `ipAddress`/`ip` and `createdAt` fields (both always blank);
  it now reports `floatingIp`, `fixedIp`, `ready`, and `poc`, and returns a
  structured `NodesData` model (with `NodeItem` items) instead of a markdown `str`,
  consistent with the other read tools.
- `create_nodegroup` (and `create_cluster`) now send the node OS as a top-level
  `os` field. It was previously nested inside `upgradeConfig`, where the API never
  read it, so the OS selection was silently ignored. `os` also gains `rocky`
  (was `ubuntu`/`linux` only), matching the CLI.
- `UpgradeConfig` no longer carries an `os` field; `maxSurge` is now bounded 1-100
  (API minimum is 1, was `ge=0`) and `maxUnavailable` 0-100.
- MCP prompts corrected to match the current API/tooling: network types are
  `TIGERA`/`CILIUM_OVERLAY` (need `cidr`) and `CILIUM_NATIVE_ROUTING` (needs
  `secondarySubnets`) — `CALICO` removed; node OS enum is ubuntu/linux/rocky
  (`flatcar` removed); routing notes for `update_nodegroup_metadata` and the
  real `update_cluster` scope (version + whitelistNodeCIDRs) added; a cluster
  no longer "requires" a node group at create time.
- `update_cluster` (`UpdateClusterDto`) now sends what the API actually accepts:
  required `version` and `whitelistNodeCIDRs`, plus optional `enabledLoadBalancerPlugin`
  / `enabledBlockStoreCsiPlugin` toggles. It previously sent `name` / `description` /
  `releaseChannel`, which this endpoint does not accept, and omitted the required
  fields — so version/whitelist updates were impossible.

### Added

- `list_volume_types` discovery tool — two-step vServer lookup (volume-type zones →
  volume types), returning the volume-type **ID** that VKS expects as `diskType`
  (it is not the literal string "SSD"); supports `zone_id`/`type_name` filters,
  cached 30 min. `NodeGroupSpec.diskType` description now points at it.
- `get_quota` tool — VKS `GET /v1/quota` (max/used clusters, node groups per
  cluster, nodes per node group), not cached; prompts now check it before create.
- `list_placement_groups` discovery tool — vServer `GET /v2/{pid}/serverGroups`,
  returning the group **uuid** to use as `placementGroupId` with
  `placementGroupConfigDto.type = EXISTING` (cached 2 min).
- `list_subnets` items now include `secondary_subnets` (IDs extracted from the
  vServer `SecondarySubnetDto` objects) so `secondarySubnets` for
  CILIUM_NATIVE_ROUTING clusters and node groups is discoverable.
- New MCP prompt `vks_create_cluster` — guided cluster-creation flow (discovery →
  safe defaults with `CILIUM_OVERLAY`+cidr → `validate_cluster_create` → hard
  confirmation gate → create → poll), including the control-plane-only path.
- `update_nodegroup_metadata` tool — `PATCH .../node-groups/{id}/metadata` to set
  `labels`, `tags`, and `taints` (typed `NodeGroupTaint`), mirroring the CLI
  `update-nodegroup-metadata` command.
- `create_nodegroup` full parity with the CLI: `os`, `enabledEncryptionVolume`,
  `subnetId`, `secondarySubnets`, `labels`, `taints`, `tags`, `autoScaleConfig`
  (typed), and `placementGroupConfigDto` (typed) are now settable. `upgradeConfig`
  and `securityGroups` are optional (sensible defaults).
- New typed nested DTOs: `NodeGroupTaint` (effect enum), `AutoScaleConfig`
  (min/max bounds), `PlacementGroupConfig` (NEW/EXISTING), `AutoUpgradeConfig`
  (weekdays/time), `AutoHealingConfig` (enable + thresholds).
- `create_cluster` (`CreateClusterComboDto`) full parity with the CLI: `nodeGroups`
  is now optional (control-plane-only create), plus `enabled{LoadBalancer,
  BlockStoreCsi,ServiceEndpoint}Plugin`, `azStrategy`, `description`, `subnetId`,
  `listSubnetIds`, `nodeNetmaskSize`, `autoUpgradeConfig` (typed), and
  `autoHealingConfig` (typed) are now settable.
- `list_clusters` summaries (`ClusterSummary`) now include `enablePrivateCluster`
  and `azStrategy`.
- `get_nodegroup` detail (`NodeGroupDetail`) now surfaces `subnetId`,
  `secondarySubnets`, `enabledEncryptionVolume`, and `placementGroupId`.

### Changed

- **BREAKING: all VKS tool names standardized to EKS-style `verb_noun`** (30
  renames), matching the AWS Labs MCP convention already used by the K8s tools
  and mapping 1:1 to greennode-cli command names. Examples: `cluster_list` →
  `list_clusters`, `cluster_get` → `get_cluster`, `nodegroup_create` →
  `create_nodegroup`, `nodegroup_list_nodes` → `list_nodes`,
  `cluster_versions_list` → `list_cluster_versions`, `vpc_list` → `list_vpcs`,
  `sshkey_list` → `list_ssh_keys`, `secgroup_list` → `list_security_groups`,
  `volumetype_list` → `list_volume_types`, `placementgroup_list` →
  `list_placement_groups`, `quota_get` → `get_quota`,
  `cluster_auto_upgrade_config` → `configure_auto_upgrade`,
  `cluster_auto_upgrade_delete` → `delete_auto_upgrade`,
  `cluster_auto_healing_config` → `configure_auto_healing`,
  `cluster_create_validate` → `validate_cluster_create`. The 7 K8s tools and
  `get_access_token` already followed the convention and are unchanged.
- `update_nodegroup` (`UpdateNodeGroupDto`) is scoped to `numNodes`,
  `securityGroups`, `autoScaleConfig` (now typed), and `upgradeConfig`; `labels`
  and `taints` moved to `update_nodegroup_metadata`. Both update tools reject an
  empty body with a clear "nothing to update" message instead of issuing a no-op
  request.

### Removed

- `nodegroup_images_list` tool (and its `/v1/node-group-images` helper).

### Changed

- Reorganized as an independent project under `src/vks-mcp-server/` within the
  GreenNode MCP monorepo (uv workspace). The import package is now
  `greennode.vks_mcp_server`.

### Added

- Typed request bodies (Pydantic DTOs) for create/update cluster & node group tools: `CreateClusterComboDto`, `UpdateClusterDto`, `CreateNodeGroupDto`, `UpdateNodeGroupDto` with nested `NodeGroupSpec`/`UpgradeConfig`, camelCase fields, Literal enums, and numeric bounds. All write DTOs use `extra="forbid"`: unsupported fields are rejected with a clear `ValidationError` rather than silently dropped. Advanced node-group options (private subnet, labels/taints, autoscale, tags, encryption, placement group) are NOT YET exposed via MCP and are a planned follow-up.
- `UpgradeConfig` now carries sensible defaults (`maxSurge=1`, `maxUnavailable=0`, `strategy="SURGE"`) matching the greennode-cli defaults, so `UpgradeConfig()` produces a non-empty body.
- Structured (code-mode-friendly) tool outputs: data tools return Pydantic models; FastMCP emits `outputSchema` + `structuredContent` (JSON). Blob tools (`get_access_token`, `get_cluster_kubeconfig`) remain `str`.
- `region` parameter on all tools is now `Literal["HCM-3", "HAN"]` (was `str`), making the valid regions visible in the tool schema.
- GitHub Actions CI (ruff lint/format + pytest) and a build job; a manual deploy stub for image build/push.
- Self-documenting tool schemas: `Literal` enums and numeric `Field(ge=, le=)`
  constraints, enriched `body` parameter descriptions, and structured docstrings
  across the cluster, nodegroup, version, and k8s tools.
- `generate_app_manifest` tool: scaffolds a Deployment + LoadBalancer Service manifest (VKS `vks.vngcloud.vn/scheme` annotation) and writes it for `apply_yaml`.
- `upgrade_nodegroup_version` tool: upgrade a node group's Kubernetes version (POST .../node-groups/{id}/upgrade-version).
- `configure_auto_healing` tool: configure cluster auto-healing (PATCH /v1/clusters/{id}/auto-healing-config).
- HTTP transport: unauthenticated `/health` endpoint for liveness/readiness probes (exempt from the API-key guard); the container now serves streamable-http on port **8080**.
- Inbound auth modes for the HTTP transport via `--auth-mode none|api-key|jwt`. `jwt` makes the server an OAuth 2.1 Resource Server: verifies Bearer JWTs against a JWKS (`--jwt-issuer/--jwt-jwks-uri/--jwt-audience/--resource-url`) and emits 401 + WWW-Authenticate + Protected Resource Metadata. `/health` stays unauthenticated.
- `--auth-debug` diagnostic (env `GRN_MCP_AUTH_DEBUG`): logs a redacted inbound
  request auth summary and exposes `GET /whoami`. Opt-in, never verifies, never
  logs full tokens; HTTP transport only. For measuring upstream/Gateway auth.

### Fixed

- `CONFIG_PATH` now points to the `~/.greenode` directory (was `~/.vks/config.json`, which broke credential loading).

## [0.1.0]

### Added

- Initial VKS MCP server: 27 tools across Auth, Cluster, NodeGroup, Version, and
  Kubernetes handlers. Read-only by default; write and sensitive-data access are
  opt-in via `--allow-write` and `--allow-sensitive-data-access`.
