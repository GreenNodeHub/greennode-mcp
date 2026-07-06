"""Shared core for GreenNode MCP servers.

Product servers (src/<product>-mcp-server) import from here instead of
copying config/auth/HTTP plumbing:

- config:     load_profile() — ~/.greenode credentials/config + GRN_* env overrides
- auth:       TokenManager — VNG IAM client-credentials token with auto-refresh
- http:       BaseClient — retry on 5xx/timeout, auto-refresh on 401
- validators: validate_id — safe resource-ID validation for URL construction
- cache:      DiscoveryCache — short-lived per-tool TTL cache with refresh bypass
"""

from greennode.mcp_core.auth import IAM_TOKEN_URL, TokenManager
from greennode.mcp_core.cache import DEFAULT_MAXSIZE, DiscoveryCache
from greennode.mcp_core.config import ProfileSettings, load_profile
from greennode.mcp_core.http import BaseClient
from greennode.mcp_core.validators import validate_id


__version__ = "0.1.0"

__all__ = [
    "IAM_TOKEN_URL",
    "TokenManager",
    "DEFAULT_MAXSIZE",
    "DiscoveryCache",
    "ProfileSettings",
    "load_profile",
    "BaseClient",
    "validate_id",
    "__version__",
]
