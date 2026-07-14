# GreenNode MCP

A monorepo of **MCP (Model Context Protocol) servers** for GreenNode products,
organized as a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/).
Each product is an independent project under `src/`, sharing the `greennode`
Python namespace.

## Servers

| Project | Package | Description |
|---------|---------|-------------|
| [`src/mcp-core`](src/mcp-core) | `greennode.mcp_core` | Shared core (config/profile loading, IAM auth, HTTP client with retry/401, validators, discovery cache) — product servers import it instead of copying plumbing. |
| [`src/vks-mcp-server`](src/vks-mcp-server) | `greennode.vks_mcp_server` | **41 tools + 3 prompts** for managing VKS (GreenNode Kubernetes Service): clusters, node groups, resource discovery (name→ID), quota, on-demand creation guides, and in-cluster Kubernetes resources. EKS-style `verb_noun` tool names, structured (JSON) outputs, MCP tool annotations, read-only by default. |

More servers will be added as sibling projects under `src/`.

## Layout

```
greennode-mcp/
├── pyproject.toml          # uv workspace root (members = ["src/*"])
├── src/
│   ├── mcp-core/           # shared core (greennode.mcp_core)
│   └── vks-mcp-server/     # one product = one independent project
│       ├── pyproject.toml
│       ├── greennode/
│       │   └── vks_mcp_server/
│       └── tests/
└── docs/
```

## Getting started

```bash
# Install all workspace members into a shared environment
uv sync

# Run a specific server
uv run vks-mcp-server
```

## Adding a new product server

```bash
uv run python scripts/new_server.py <product>    # e.g. vdb
```

Scaffolds `src/<product>-mcp-server/` from `templates/new-server` (working
example tool + tests + Dockerfile, conventions baked in), registers it with
release-please, and prints the remaining steps. CI (lint/test/build) discovers
the new package automatically. Agent guidance lives in `.claude/skills/`
(`new-mcp-server`, `release-mcp`) and the tiered CLAUDE.md files (repo root =
monorepo conventions; each package has its own).

See each project's own README for configuration and usage.

**Note:** Create/update tools use typed Pydantic request bodies (structured DTOs) for better code-mode support and self-documenting schemas.

### Diagnostics: `--auth-debug` (temporary, opt-in)

`--auth-debug` (env `GRN_MCP_AUTH_DEBUG=1`) makes the HTTP transport log a
**redacted** summary of every inbound request and expose an unauthenticated
`GET /whoami` that echoes the same summary. It is meant for measuring what an
upstream (e.g. the MCP Gateway) actually sends — token scheme, JWT header
(`alg`/`kid`), allow-listed claims (`iss`/`aud`/`sub`/`scope`/...), and any
`X-GreenNode-*` / `X-Forwarded-*` identity headers.

It **never verifies** signatures and **never logs the full token** (only a
6-char prefix + length). It is **off by default** and **must not be enabled in
production**. It is orthogonal to `--auth-mode` and can be combined with any mode.

## Development

```bash
# Run a product's tests
cd src/vks-mcp-server && uv run pytest tests/ -v
```

Conventions for adding tools and new servers live in [CLAUDE.md](CLAUDE.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
