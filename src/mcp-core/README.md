# greennode.mcp-core

Shared core for GreenNode MCP servers. Product servers
(`src/<product>-mcp-server`) **import** this instead of copying plumbing:

| Module | Provides |
|--------|----------|
| `config` | `load_profile()` — `~/.greenode` credentials/config INI + `GRN_*` env overrides |
| `auth` | `TokenManager` — VNG IAM client-credentials token, auto-refresh (camelCase API) |
| `http` | `BaseClient` — retry on 5xx/timeout (1s→2s→4s), auto-refresh on 401, 30s timeout |
| `validators` | `validate_id()` — safe resource-ID check before URL construction |
| `cache` | `DiscoveryCache` — per-tool TTL cache with `refresh` bypass |

## Usage in a product server

```python
from greennode.mcp_core import BaseClient, DiscoveryCache, TokenManager, load_profile

profile = load_profile(Path.home() / ".greenode")           # credentials + region
config = MyProductConfig(..., profile)                      # adds region -> base URLs

class MyClient(BaseClient):
    def __init__(self, config, token_manager):
        super().__init__(config, token_manager, default_service="myproduct")
```

The config object passed to `BaseClient` must expose
`get_base_url(region, service) -> str`.

Add the dependency in the product's `pyproject.toml`:

```toml
dependencies = ["greennode.mcp-core", ...]

[tool.uv.sources]
"greennode.mcp-core" = { workspace = true }
```
