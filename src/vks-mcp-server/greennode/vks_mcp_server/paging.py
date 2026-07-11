"""Fetch-all helper for paged VKS API list endpoints.

Unlike vServer (which ignores paging params), the VKS API enforces them with a
server-side default of pageSize=10 — a bare GET silently truncates once a
collection outgrows one page. Every list tool goes through here so agents
always see the complete collection.
"""

from __future__ import annotations

from greennode.vks_mcp_server.client import VksClient


async def fetch_all_vks_items(
    client: VksClient,
    path: str,
    region: str | None = None,
    page_size: int = 100,
) -> list:
    """Collect every item from a VKS ``{items, total, page, pageSize}`` endpoint."""
    page = 0
    collected: list = []
    while True:
        data = await client.get(path, region=region, params={"page": page, "pageSize": page_size})
        items = data.get("items", data) if isinstance(data, dict) else data
        collected.extend(items)
        total = data.get("total") if isinstance(data, dict) else None
        if not items or not isinstance(total, int) or len(collected) >= total:
            return collected
        page += 1
