"""List-fetch helpers for vBackup API list endpoints.

vBackup answers its collections with one envelope —
``{"items": [...], "page", "pageSize", "totalPages", "totalItems"}`` — but a
few sub-resources return a bare array instead, and detail endpoints return the
object itself with no envelope at all. ``as_list`` and ``unwrap`` normalise all
three so handlers never branch on the shape.

Paging is opt-in: omitting the query params returns the whole collection with
``page``/``pageSize`` reported as ``null``. ``fetch_all_items`` relies on that
fast path and re-pages explicitly if a response ever reports more items than it
returned, so lists cannot truncate silently as an account grows.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.client import VbackupClient
from typing import Any


LIST_KEYS = ("items", "data")

DEFAULT_PAGE_SIZE = 200


def as_list(data: Any, *wrapper_keys: str) -> list:
    """Normalise a vBackup response to a list.

    Explicit *wrapper_keys* win; otherwise ``items`` then ``data`` are tried.
    A bare array — what the volume sub-resource of a backup instance returns —
    is passed through unchanged.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in wrapper_keys or LIST_KEYS:
        if isinstance(data.get(key), list):
            return data[key]
    return []


def unwrap(data: Any) -> dict:
    """Return the object a detail endpoint answered with.

    vBackup detail endpoints return the resource directly, but a ``data``
    envelope is tolerated so a future endpoint that adds one does not need a
    handler change.
    """
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


def total_items(data: Any) -> int | None:
    """Read the item count an envelope reports, if it reports one."""
    if isinstance(data, dict) and isinstance(data.get("totalItems"), int):
        return data["totalItems"]
    return None


async def fetch_all_items(
    client: VbackupClient,
    path: str,
    region: str | None = None,
    params: dict | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list:
    """Fetch every item from a vBackup list endpoint, never truncating.

    Fast path: one unpaged call, which returns the whole collection. Defensive
    net: when a response reports ``totalItems`` greater than the batch it
    returned, re-fetch from page 1 with explicit ``page``/``size`` and collect
    every page.

    The paging parameter is ``size``. ``pageSize`` is the name the *response*
    uses, and sending it as a request parameter is ignored silently — the call
    then returns the full collection while looking paged.
    """
    first = await client.get(path, region=region, params=params or None)
    items = as_list(first)
    total = total_items(first)
    if total is None or len(items) >= total:
        return items

    collected: list = []
    page = 1
    while True:
        paged = {**(params or {}), "page": page, "size": page_size}
        data = await client.get(path, region=region, params=paged)
        batch = as_list(data)
        collected.extend(batch)
        if not batch or len(collected) >= (total_items(data) or total):
            return collected
        page += 1
