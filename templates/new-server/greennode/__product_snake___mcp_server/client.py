"""HTTP client for the {{PRODUCT}} API (built on greennode.mcp_core)."""

from __future__ import annotations

from greennode.mcp_core.auth import TokenManager
from greennode.mcp_core.http import BaseClient
from greennode.{{product_snake}}_mcp_server.config import {{Product}}Config


class {{Product}}Client(BaseClient):
    """Async client for the {{PRODUCT}} API (retry + token refresh from BaseClient)."""

    def __init__(self, config: {{Product}}Config, token_manager: TokenManager) -> None:
        super().__init__(config, token_manager, default_service="{{product}}")
