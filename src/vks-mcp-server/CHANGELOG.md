# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0](https://github.com/vngcloud/greennode-mcp/compare/vks-mcp-server-v0.8.0...vks-mcp-server-v0.9.0) (2026-07-14)


### Features

* field-test fixes — vDNS flag, partial updates, kubeconfig envelope + generate tool, force delete ([#38](https://github.com/vngcloud/greennode-mcp/issues/38)) ([7713736](https://github.com/vngcloud/greennode-mcp/commit/7713736086c316349a6f2ab5ad2b26cf3099d933))

## [0.8.0](https://github.com/vngcloud/greennode-mcp/compare/vks-mcp-server-v0.7.0...vks-mcp-server-v0.8.0) (2026-07-12)


### Features

* create_cluster gets the pinned question order and one-setting rules ([#33](https://github.com/vngcloud/greennode-mcp/issues/33)) ([d3a9d45](https://github.com/vngcloud/greennode-mcp/commit/d3a9d45566472b1efe7856e13046338ad3cec7d1))
* create_nodegroup asks the user about each optional config group ([#28](https://github.com/vngcloud/greennode-mcp/issues/28)) ([4ee6226](https://github.com/vngcloud/greennode-mcp/commit/4ee62266e151f7193b8da6e610de9eb2e20103d8))
* get_creation_guide serves the create choreography on demand ([#34](https://github.com/vngcloud/greennode-mcp/issues/34)) ([1a1ecaf](https://github.com/vngcloud/greennode-mcp/commit/1a1ecafe3a1d27c8609edae0ad43aa1921e2016c))
* node-group questions follow a fixed order, one setting per question ([0385b9a](https://github.com/vngcloud/greennode-mcp/commit/0385b9a5aad768af24a3ca3e7db3584a1d4926f9))
* SERVER_INSTRUCTIONS carry the session's runtime mode (EKS pattern) ([#36](https://github.com/vngcloud/greennode-mcp/issues/36)) ([1406137](https://github.com/vngcloud/greennode-mcp/commit/140613701f5d54b14dbeb6492f26d9eb03cab32b))


### Bug Fixes

* only ask about ServiceEndpoint when the cluster is private ([#35](https://github.com/vngcloud/greennode-mcp/issues/35)) ([77aac6a](https://github.com/vngcloud/greennode-mcp/commit/77aac6ab2583f40b48fe56a887e6f55d12c201b9))


### Documentation

* catch READMEs and CLAUDE.md files up with the guidance/paging work ([#37](https://github.com/vngcloud/greennode-mcp/issues/37)) ([9363c59](https://github.com/vngcloud/greennode-mcp/commit/9363c593486832971b62a36aa795d80d24799764))

## [0.7.0](https://github.com/vngcloud/greennode-mcp/compare/vks-mcp-server-v0.6.0...vks-mcp-server-v0.7.0) (2026-07-11)


### ⚠ BREAKING CHANGES

* list_flavors and list_volume_types now take cluster_id + subnet_id; the zone and region parameters are gone.
* list_clusters and list_nodes no longer accept page/pageSize (they always return the full collection).
* discovery output schemas slimmed (fields dropped); list_flavors and list_volume_types now require zone; list_volume_types lost type_name.

### Features

* discovery tools guide the zone-scoped create flows (AWS-pattern descriptions) ([#23](https://github.com/vngcloud/greennode-mcp/issues/23)) ([f48f17f](https://github.com/vngcloud/greennode-mcp/commit/f48f17f0208597800de2343d418753b4697e707b))
* list_flavors/list_volume_types derive region+zone from cluster_id+subnet_id ([#27](https://github.com/vngcloud/greennode-mcp/issues/27)) ([92d411e](https://github.com/vngcloud/greennode-mcp/commit/92d411ebc468503a4b3b2714b961ba736ad0bd4e))
* main VKS tools join the guided flows (descriptions, fetch-all paging, region echo) ([#25](https://github.com/vngcloud/greennode-mcp/issues/25)) ([dff73f2](https://github.com/vngcloud/greennode-mcp/commit/dff73f23d0d72b3c8dfecf8d6c73c3e2c20d4f8e))
* SERVER_INSTRUCTIONS teach the creation chains, region model, and prompts ([#26](https://github.com/vngcloud/greennode-mcp/issues/26)) ([58d26cf](https://github.com/vngcloud/greennode-mcp/commit/58d26cfe6fe148611ceb19e071f3949b899fe069))

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
