"""List-fetch helpers for vServer API list endpoints.

vServer currently returns the full collection in a single response — paging
params are accepted but the envelope reports ``page=0 / pageSize=0 /
totalPage=0`` with ``len(listData) == totalItem``. Every list tool goes through
``fetch_all_items`` so results are never silently truncated if the backend ever
starts enforcing paging.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from typing import Any


LIST_KEYS = ("listData", "data", "items")
_ENVELOPE_META = frozenset({"success", "errorCode", "errorMsg", "extra"})
_TOTAL_KEYS = ("totalItem", "totalItems")

DEFAULT_PAGE_SIZE = 500


def as_list(data: Any, *wrapper_keys: str) -> list:
    """Normalise a vServer response to a list.

    Handles all three envelope families the gateway uses:

    - v2 list: ``{"listData": [...], "page", "pageSize", "totalItem", "totalPage"}``
    - v1 detail/list: ``{"data": [...]}`` or a bare array
    - v1 catalogue: ``{"success", "errorCode", "errorMsg", "extra",
      "<resource>": [...]}`` where ``<resource>`` differs per endpoint
      (``images``, ``volumeTypeZones``, ``volumeTypes``, ...)

    Explicit *wrapper_keys* win; otherwise the known keys are tried, and as a
    last resort the single list-valued field of a ``success`` envelope is used.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in wrapper_keys or LIST_KEYS:
        if isinstance(data.get(key), list):
            return data[key]
    if wrapper_keys:
        return []
    lists = [v for k, v in data.items() if k not in _ENVELOPE_META and isinstance(v, list)]
    if len(lists) == 1:
        return lists[0]
    return []


def unwrap(data: Any) -> Any:
    """Return the payload of a single-object envelope (``{"data": {...}}``).

    vServer answers most detail/mutation calls with a ``data`` envelope, but a
    few return the object directly — normalise both to the inner object.
    """
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data


def unwrap_one(data: Any) -> dict:
    """Return the single object a detail endpoint answered with.

    Handles the four shapes vServer uses for a by-id GET: the object itself,
    ``{"data": {...}}``, ``{"data": [{...}]}`` — the security-group-rule detail
    endpoint returns a one-element **array** inside the envelope even though it
    addresses a single rule — and the v1 ``success`` envelope wrapping a
    one-element resource array (``{"success": true, "flavors": [{...}]}``),
    which the flavor and volume-type detail endpoints use.

    The v1 branch is gated on the ``success`` marker: an ordinary resource
    object that happens to hold a single list (a route table with one route)
    must come back whole, not collapsed to that list's first element.
    """
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        items = data["data"]
        first = items[0] if items else {}
        return first if isinstance(first, dict) else {}
    unwrapped = unwrap(data)
    if isinstance(unwrapped, dict) and "success" in unwrapped:
        items = as_list(unwrapped)
        if items and isinstance(items[0], dict):
            return items[0]
    return unwrapped if isinstance(unwrapped, dict) else {}


def _total(data: Any) -> int | None:
    """Read the item count an envelope reports, whichever spelling it uses.

    The v2 collections say ``totalItem``; the snapshot collections say
    ``totalItems``. Reading only one of them makes the other look complete no
    matter how much was actually withheld.
    """
    if not isinstance(data, dict):
        return None
    for key in _TOTAL_KEYS:
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


async def fetch_all_items(
    client: VserverClient,
    path: str,
    region: str | None = None,
    params: dict | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list:
    """Fetch every item from a vServer list endpoint, never truncating.

    Fast path: one call, since vServer returns the whole collection.
    Defensive net: if a response reports more items than it returned
    (``totalItem``/``totalItems`` greater than the batch — i.e. the backend
    started enforcing paging), re-fetch from page 1 with explicit
    ``page``/``size`` and collect every page, so results are never silently cut
    off as an account grows.
    """
    first = await client.get(path, region=region, params=params or None)
    items = as_list(first)
    total = _total(first)
    if total is None or len(items) >= total:
        return items

    collected: list = []
    page = 1
    while True:
        paged = {**(params or {}), "page": page, "size": page_size}
        data = await client.get(path, region=region, params=paged)
        batch = as_list(data)
        collected.extend(batch)
        page_total = _total(data)
        if page_total is None:
            page_total = total
        if not batch or len(collected) >= page_total:
            return collected
        page += 1


async def fetch_paged_items(
    client: VserverClient,
    path: str,
    region: str | None = None,
    name: str = "",
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list:
    """Fetch a list endpoint that **requires** ``name``, ``page`` and ``size``.

    The route-table, network-ACL and peering collections answer ``500 Internal
    server error`` — not 400 — when any of the three is missing, so they can
    never go through the bare fetch. An empty ``name`` means "no filter".
    """
    params = {"name": name, "page": 1, "size": page_size}
    return await fetch_all_items(client, path, region=region, params=params, page_size=page_size)
