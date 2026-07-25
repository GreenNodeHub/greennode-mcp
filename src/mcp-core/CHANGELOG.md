# Changelog

## [0.4.1](https://github.com/vngcloud/greennode-mcp/compare/mcp-core-v0.4.0...mcp-core-v0.4.1) (2026-07-25)


### Bug Fixes

* migrate config dir to ~/.greennode with legacy fallback ([#61](https://github.com/vngcloud/greennode-mcp/issues/61)) ([040c479](https://github.com/vngcloud/greennode-mcp/commit/040c479c32866f4dfb15e1d354f11c64c7d654b4))

## [0.4.0](https://github.com/vngcloud/greennode-mcp/compare/mcp-core-v0.3.0...mcp-core-v0.4.0) (2026-07-22)


### Features

* send a vks-mcp-server User-Agent on every outbound API request ([#56](https://github.com/vngcloud/greennode-mcp/issues/56)) ([c6a2773](https://github.com/vngcloud/greennode-mcp/commit/c6a27735fa90b2a545bb52446b68c8a79e70c98e))

## [0.3.0](https://github.com/vngcloud/greennode-mcp/compare/mcp-core-v0.2.0...mcp-core-v0.3.0) (2026-07-14)


### ⚠ BREAKING CHANGES

* --auth-mode/--api-key/--jwt-* CLI flags and the GRN_MCP_API_KEY/GRN_MCP_JWT_*/GRN_MCP_VKS_AUTH env vars are gone; deployments using api-key/jwt inbound auth must drop those settings.

### Features

* --vks-auth passthrough — every VKS call runs as the caller ([#40](https://github.com/vngcloud/greennode-mcp/issues/40)) ([7e700f1](https://github.com/vngcloud/greennode-mcp/commit/7e700f1e50733d62763014f3614b4429300c149f))

## [0.2.0](https://github.com/vngcloud/greennode-mcp/compare/mcp-core-v0.1.0...mcp-core-v0.2.0) (2026-07-14)


### Features

* field-test fixes — vDNS flag, partial updates, kubeconfig envelope + generate tool, force delete ([#38](https://github.com/vngcloud/greennode-mcp/issues/38)) ([7713736](https://github.com/vngcloud/greennode-mcp/commit/7713736086c316349a6f2ab5ad2b26cf3099d933))

## 0.1.0 (2026-07-06)


### Features

* **core:** extract shared greennode.mcp_core package ([#15](https://github.com/vngcloud/greennode-mcp/issues/15)) ([5491ad0](https://github.com/vngcloud/greennode-mcp/commit/5491ad0af867a6f2bc8c788ec433a3571696709f))
