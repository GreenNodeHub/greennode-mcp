# GreenNode {{PRODUCT}} MCP Server

An MCP (Model Context Protocol) server for **{{PRODUCT}}** on VNG Cloud.

> Scaffolded from `templates/new-server`. Replace the example tool with real
> ones, then update this README (tool tables, prompts) following the pattern in
> [`src/vks-mcp-server/README.md`](../vks-mcp-server/README.md).

## Configuration

Credentials are read from `~/.greennode/credentials` and `~/.greennode/config`
(INI format, shared with greennode-cli; `GRN_*` env vars override — see the
repo-root CLAUDE.md).

## Running

```bash
# Read-only mode (default)
uv run {{product}}-mcp-server

# Enable create/update/delete operations
uv run {{product}}-mcp-server --allow-write

# HTTP transport
uv run {{product}}-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

## Development

```bash
cd src/{{product}}-mcp-server
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format --check .
```

Manual testing with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run {{product}}-mcp-server
```

## Tools

| Tool | Access | Description |
|------|--------|-------------|
| `list_examples` | read | Example tool — replace with your first real tool |
