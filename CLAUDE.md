# CLAUDE.md — GreenNode MCP monorepo

Monorepo-wide conventions for all GreenNode MCP servers. **Product-specific
guidance lives in each package's own CLAUDE.md** (e.g.
`src/vks-mcp-server/CLAUDE.md`) — read the one for the package you're touching.

## Project overview

MCP (Model Context Protocol) servers for GreenNode products, giving AI
assistants (Claude, Cursor, Gemini, etc.) tools to manage cloud resources.
Organized as a **uv workspace** (root `pyproject.toml`, `members = ["src/*"]`),
mirroring the AWS Labs MCP layout.

## Repository layout

- Shared core: `src/mcp-core/` (`greennode.mcp_core`) — config/profile loading, IAM `TokenManager`, `BaseClient` (retry/401), `validate_id`, `DiscoveryCache`. Product servers **import** it (workspace dependency), never copy it.
- Product projects: `src/<product>-mcp-server/` (own `pyproject.toml`, `tests/`, `README.md`, `CLAUDE.md`, `Dockerfile`) sharing the `greennode` namespace (pkgutil-style `greennode/__init__.py`).
- Import package: `greennode.<product>_mcp_server`; CLI entry point `<product>-mcp-server`.
- Repo-root `tests/` holds cross-package convention tests (run by the CI `Conventions` job).

## Adding a new MCP server

```bash
uv run python scripts/new_server.py <product>    # e.g. vdb
```

Scaffolds `src/<product>-mcp-server/` from `templates/new-server` — working
example tool, tests, Dockerfile, per-package CLAUDE.md — registers it with
release-please, and prints the remaining manual steps (deploy job, CODEOWNERS
line, real API endpoints). CI discovers the package automatically. See also the
`new-mcp-server` skill in `.claude/skills/`.

## Branch & release flow (trunk-based)

`main` is the only long-lived branch. **All work goes through a PR to `main`**
(feature branch → squash merge). The PR **title must follow Conventional
Commits** (`feat:`, `fix:`, `feat!:` …) — with squash merge it becomes the
commit message on `main` and drives release automation; `pr-title.yml` enforces
it.

Releases are fully automated: release-please maintains a release PR per
package; merging it bumps versions + CHANGELOG and tags
`<component>-vX.Y.Z`, which deploys to production. Never edit versions,
CHANGELOGs, or tags by hand. See the `release-mcp` skill in `.claude/skills/`.

## CI/CD

GitHub Actions live in `.github/workflows/`:

- `ci.yml` — runs on pull requests and pushes to `main`. **Auto-discovers workspace members** under `src/` (a new `src/<product>-mcp-server/` gets lint/format/pytest + Docker build with zero YAML changes), plus a repo-wide `Conventions` job running `tests/test_conventions.py` (verb_noun tool names, `extra="forbid"` on `*Dto` models, `## Requirements` docstrings on write tools — the rules below enforced as failing tests). Branch protection requires the single `CI OK` gate job, so adding packages never touches protection settings.
- `pr-title.yml` — enforces Conventional Commits on PR titles (semantic-pull-request action).
- `deploy.yml` — builds and pushes the image to a registry, using **GitHub Environments** so dev and production can have different registry config (environment names `develop`/`production` are decoupled from branch names). Triggers: push to `main` touching watched paths (path filter — no magic strings) → `develop` environment (image tag = commit sha); production deploys run only via `workflow_dispatch` with a `tag` input — chained automatically from `release-please.yml` after a release, or manual (`gh workflow run deploy.yml --ref main -f tag=<component>-vX.Y.Z`); image tag = `vX.Y.Z` (component prefix stripped). The production environment requires **reviewer approval** before the job runs (Actions run page → Review deployments). In each environment (Settings → Environments) set the registry variable `IMAGE_REGISTRY` and the `REGISTRY_USERNAME` / `REGISTRY_PASSWORD` secrets. Both environments' "Deployment branches" policies must allow `main` (dispatch runs on main).
- `release-please.yml` — per-package release automation (manifest mode). With the default `GITHUB_TOKEN`, required checks don't start on the release PR automatically — close & reopen it once; set the `RELEASE_PLEASE_TOKEN` secret (fine-grained PAT: contents + pull-requests write) to remove that friction.
- `dependabot.yml` — weekly grouped updates for uv deps and GitHub Actions pins.
- `.github/CODEOWNERS` — per-product ownership: each team owns its `src/<product>-mcp-server/`.

## Code conventions

- All source code text must be in **English** — error messages, descriptions, comments, docstrings
- Async/await throughout — all handlers and client methods are async
- Use `from __future__ import annotations` in all files
- Follow existing handler pattern: class with `__init__` registering tools via `self.mcp.tool()`
- **Tool naming**: EKS-style `verb_noun` (`list_clusters`, `get_nodegroup`, `create_cluster`), matching the AWS Labs MCP convention and mapping 1:1 to greennode-cli command names (`list-clusters` → `list_clusters`). Never `noun_verb`.
- Import shared plumbing from `greennode.mcp_core` — do not copy config/auth/HTTP/validator/cache code into a product package.

## GreenNode platform quirks

- **IAM API uses camelCase**: `grantType`, `accessToken`, `expiresIn` (not snake_case OAuth2 standard) — handled by `mcp_core.auth.TokenManager`.
- Product API quirks (pagination base, status codes, field casing) belong in the **package** CLAUDE.md.

## Configuration

All servers read `~/.greennode/credentials` and `~/.greennode/config` (INI
format, shared with greennode-cli) via `mcp_core.config.load_profile`. Resolve
the directory with `mcp_core.config.resolve_config_dir()` — it prefers
`~/.greennode` and falls back to the pre-rename `~/.greenode` when only the
legacy directory exists (mirrors greennode-cli).

**Environment variable overrides** (highest priority):

| Variable | Purpose |
|----------|---------|
| `GRN_CLIENT_ID` | Override client_id |
| `GRN_CLIENT_SECRET` | Override client_secret |
| `GRN_PROFILE` | Select profile (default: "default") |
| `GRN_DEFAULT_REGION` | Override region |
| `GRN_PROJECT_ID` | Override project_id |

## Adding a new tool

1. Choose the appropriate handler or create a new one in the package
2. Define async method with docstring (used as tool description)
3. Register in handler's `__init__` with annotations: `self.mcp.tool(name="tool_name", annotations=READ)(self.method)` — pick `READ`/`WRITE`/`DESTRUCTIVE` by effect (dry-run delete = READ; an irreversible upgrade = DESTRUCTIVE)
4. Add `validate_id()` (from `mcp_core`) for any ID args used in URL construction
5. Check `self.allow_write` for mutating operations
6. Register handler in `server.py` if new handler class
7. Add tests in `tests/` (TDD — write them first)
8. Use `Literal[...]` for parameters with a fixed value set, and `Field(ge=, le=)` for numeric bounds, so the schema is self-documenting
9. For create/update operations, use typed Pydantic request DTOs with camelCase fields, nested specs, and Literal enums instead of `body: dict`; set `extra="forbid"`
10. Write structured docstrings (`## Requirements`, `## Workflow`) for create/update/delete tools — keep them lean (discovery chain + guardrails); long conversation choreography belongs in an on-demand guidance tool / prompt, not in the docstring (see vks `get_creation_guide`)
11. For discovery tools (read-only lookups), wrap the fetch in `mcp_core.cache.DiscoveryCache.get_or_fetch`, add a per-tool TTL, and expose a `refresh: bool` parameter
12. Name the tool `verb_noun`, mirroring the greennode-cli command where one exists

The `Conventions` CI job enforces 4, 9, 10, and 12 mechanically.

## Security rules

- **Input validation**: every ID used in URL construction goes through `mcp_core.validators.validate_id()` — prevents path traversal
- **Write guard**: mutating operations must check the `allow_write` flag
- **Tokens in memory only**: never written to disk or logged
- **Credentials not logged**: error messages and debug logs never include tokens or secrets
- **Timeout**: all HTTP requests have a 30s timeout (from `mcp_core.http`)

## Testing

```bash
# One package
cd src/<product>-mcp-server && uv run pytest tests/ -v

# Repo-wide convention tests
uv run pytest tests/ -v

# Lint + format (what CI runs)
uv run ruff check . && uv run ruff format --check .
```

Tests use `respx` for async HTTP mocking — no real API calls, no credentials.
Manual testing (MCP Inspector over stdio, JSON-RPC smoke tests) is documented
per package (see `src/vks-mcp-server/README.md` → Development).

## Documentation update rule

After completing any feature or bugfix, update ALL related documentation:

1. **Package README.md** — tool tables, usage examples
2. **Package CLAUDE.md** — API quirks, key files
3. Root docs only when monorepo-level behavior changes (CI/CD, conventions)

CHANGELOGs are generated by release-please — do not edit them by hand.
Code without docs is not done.
