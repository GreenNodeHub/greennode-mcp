---
name: new-mcp-server
description: Use when creating a new MCP server for a VNG Cloud product in this monorepo ("tạo MCP cho <product>", "add a new server", "implement MCP for vDB/vStorage/..."). Scaffolds the package and walks the implementation flow.
---

# New MCP server for a VNG Cloud product

## Step 1 — Scaffold (never hand-create the package)

```bash
uv run python scripts/new_server.py <product>   # e.g. vdb
uv sync --all-packages --all-groups
cd src/<product>-mcp-server && uv run pytest tests/ -v   # must pass out of the box
```

The scaffold already follows every repo convention (mcp_core imports,
verb_noun example tool, structured output, `extra="forbid"` DTOs pattern,
Dockerfile, per-package CLAUDE.md) and registers the package with
release-please. CI discovers it automatically.

## Step 2 — Ground truth before coding

- **The product's greennode-cli commands (if any) are the source of truth** for
  endpoints, request bodies, and field names — read the CLI source, not stale
  OpenAPI specs.
- Fill in the real API base URLs in `config.py` (`REGIONS`).
- Record every API quirk you discover (pagination base, status codes, casing)
  in the package `CLAUDE.md` immediately.

## Step 3 — Implement tools (TDD, one tool at a time)

Follow "Adding a new tool" in the repo-root CLAUDE.md. Non-negotiables
(CI enforces them):

- Tool names `verb_noun`, mirroring CLI command names (`list-clusters` → `list_clusters`)
- RED test first (respx-mocked), then implement, then GREEN
- `validate_id()` on every ID in a URL; write tools behind `allow_write`
- Typed request DTOs with `extra="forbid"`; `## Requirements` docstring on write tools
- Structured Pydantic outputs for data tools

Mirror `src/vks-mcp-server` for worked examples of every pattern (discovery
caching, dry-run tools, MCP prompts).

## Step 4 — Wire the remaining pieces

- Deploy job / tag mapping for the package in `.github/workflows/deploy.yml`
- `/src/<product>-mcp-server/ @<team>` line in `.github/CODEOWNERS`
- Package README: tool tables (copy the vks README structure)

## Step 5 — Ship

Branch → PR to `main` with a Conventional Commits title → CI green → squash
merge. Releases happen via the release-please PR (see the `release-mcp` skill).
