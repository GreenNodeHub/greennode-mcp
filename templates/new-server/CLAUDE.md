# CLAUDE.md — {{PRODUCT}} MCP Server

Product-specific guidance for `src/{{product}}-mcp-server`. Monorepo-wide
conventions (tool naming, DTOs, TDD, branch/release flow) live in the
**repo-root CLAUDE.md** — read that first.

## Product overview

MCP server for {{PRODUCT}} (VNG Cloud). Scaffolded from
`templates/new-server`; the `ExampleHandler` demonstrates the required
patterns (structured output model, `verb_noun` naming, allow_write gating).

## {{PRODUCT}} API quirks

<!-- Record every surprise here as you discover it, e.g.:
- pagination base (0 or 1?)
- success status codes (200? 202?)
- camelCase vs snake_case fields
- which endpoints need project_id in the path
-->

- TODO: fill in as the API is integrated. **The greennode-cli implementation
  (if one exists) is the source of truth for the current API.**

## Key files

| File | Purpose |
|------|---------|
| `server.py` | FastMCP entry point, handler registration, CLI flags |
| `config.py` | {{Product}}Config + REGIONS endpoints (profile loading from `mcp_core`) |
| `client.py` | {{Product}}Client — `mcp_core.http.BaseClient` subclass |
| `example_handler.py` | Example tool — replace with real handlers |

## Testing

```bash
cd src/{{product}}-mcp-server && uv run pytest tests/ -v
```

Tests use `respx` for async HTTP mocking — no real API calls, no credentials.
