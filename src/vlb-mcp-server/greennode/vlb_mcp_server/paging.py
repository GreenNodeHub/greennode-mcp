"""Envelope normalisation and list-fetch helpers for the vLB API.

vLB answers with three different envelope families and no consistency between
them:

- ``{"data": <object | array>}`` — every single-object GET and every
  sub-resource list (listeners, pools, members, L7 policies).
- ``{"listData": [...], "page", "pageSize", "totalPage", "totalItem"}`` — the
  top-level collections (load balancers, certificates, packages).
- bare arrays or bare objects — ``scale-history``, ``loadBalancers/subnet/{id}``,
  ``cloneMetadata``, ``cas/{caId}`` and the bandwidth update.

Every read goes through the helpers here rather than indexing a response
directly, so a resource that changes envelope does not take a handler with it.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.client import VlbClient
from typing import Any


LIST_KEYS = ("data", "listData", "items")
_TOTAL_KEYS = ("totalItem", "totalItems")

DEFAULT_PAGE_SIZE = 500

# How the LB list endpoint's `filter` / `pageRequest` query objects are encoded
# on the wire. The OpenAPI spec declares them as `type: object` with no
# `style`/`explode`, so it does not actually say; Spring `@ModelAttribute`
# flattening ("flat") is what the sibling `/cas` endpoint demonstrably uses.
# The two alternatives, should a live call prove otherwise, are dot-notation
# ("?filter.name=&pageRequest.page=") and a JSON blob ("?filter={...}").
# Change it here and nowhere else — every list tool builds params through
# `lb_list_params`.
_LIST_PARAM_STYLE = "flat"

# Keys a create response might carry the new resource's id under, in preference
# order. The spec types all five create responses as an untyped
# `Map<String,String>` and never names the key.
_CREATED_ID_KEYS = (
    "uuid",
    "id",
    "loadBalancerId",
    "listenerId",
    "poolId",
    "l7PolicyId",
    "policyId",
    "caId",
    "memberId",
)


def as_list(data: Any, *wrapper_keys: str) -> list:
    """Normalise a vLB response to a list.

    Explicit *wrapper_keys* win; otherwise ``data``, ``listData`` and ``items``
    are tried in that order. A bare array passes through. Anything else — a
    single object, ``None``, a scalar — yields an empty list rather than
    raising, because an empty collection and a surprising envelope should both
    read as "nothing to show", not as a crash mid-render.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in wrapper_keys or LIST_KEYS:
        if isinstance(data.get(key), list):
            return data[key]
    return []


def unwrap(data: Any) -> Any:
    """Return the payload of a single-object envelope (``{"data": {...}}``).

    vLB wraps most detail responses but returns several bare
    (``cas/{caId}``, ``cloneMetadata``, the bandwidth update) — normalise both.
    """
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data


def unwrap_one(data: Any) -> dict:
    """Return the single object a detail endpoint answered with, or ``{}``.

    Handles ``{"data": {...}}``, ``{"data": [{...}]}`` and the bare object.
    Never returns ``None``: a 202/204 write comes back as ``None`` from
    ``BaseClient``, and handlers must be able to feed that straight into a
    ``from_api`` without a separate guard.
    """
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        items = data["data"]
        first = items[0] if items else {}
        return first if isinstance(first, dict) else {}
    unwrapped = unwrap(data)
    return unwrapped if isinstance(unwrapped, dict) else {}


def created_id(data: Any) -> str:
    """Extract the new resource's id from a create response, or ``""``.

    ``createLoadBalancer``, ``createListener``, ``createPool``, ``createPolicy``
    and ``cloneLoadBalancer`` all declare ``Map<String,String>`` with no
    documented key, so this looks for one rather than assuming.

    **Never raises.** A model that hard-requires an id turns a *successful*
    create into a validation error, which an agent reads as failure and retries
    — duplicating a billed load balancer. An empty string plus a hint telling
    the caller to confirm with ``list_…`` is the safe degradation.
    """
    if not isinstance(data, dict):
        return ""
    for key in _CREATED_ID_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    strings = [v for v in data.values() if isinstance(v, str) and v]
    if len(data) == 1 and len(strings) == 1:
        return strings[0]
    return ""


def lb_list_params(
    name: str | None = None,
    status: str | None = None,
    subnet_id: str | None = None,
    vpc_id: str | None = None,
    zone_id: str | None = None,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
) -> dict:
    """Build the query for ``GET /v2/{projectId}/loadBalancers``.

    The single place the ``filter`` / ``pageRequest`` encoding lives (see
    ``_LIST_PARAM_STYLE``). Only the five filter fields that mean anything for a
    load balancer are exposed: the spec's ``FilterRequest`` is a shared vServer
    grab-bag carrying ``volumeTypes``, ``encryptionTypes``, ``dhcpOptionIds``
    and friends, which the gateway ignores here — offering them would let an
    agent believe it had filtered when it had not.

    ``page`` is assumed 1-based, matching the sibling ``/cas`` endpoint which
    documents that default. ``PageRequest`` itself documents nothing.
    """
    params: dict = {"page": page, "size": size}
    if name:
        params["name"] = name
    if status:
        params["status"] = status
    if subnet_id:
        params["subnetIds"] = subnet_id
    if vpc_id:
        params["vpcIds"] = vpc_id
    if zone_id:
        params["zoneId"] = zone_id
    return params


def total_items(data: Any) -> int | None:
    """Read the item count an envelope reports, whichever spelling it uses."""
    if not isinstance(data, dict):
        return None
    for key in _TOTAL_KEYS:
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


async def fetch_list(
    client: VlbClient,
    path: str,
    region: str | None = None,
    params: dict | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list, dict]:
    """Fetch a vLB list endpoint completely, returning ``(items, first_envelope)``.

    Fast path: one call. Defensive net: when the envelope reports more items
    than it returned (``totalItem`` above the batch), keep requesting the
    following pages and append until the reported total is collected.

    This matters more here than it looks: the LB list's query encoding is not
    actually pinned down by the spec, so if the paging params fail to land the
    loop is what keeps a long list from being silently cut off.

    The first envelope comes back too, so a caller can report ``page`` /
    ``pageSize`` / ``totalItem`` without a second request.
    """
    first = await client.get(path, region=region, params=params or None)
    envelope = first if isinstance(first, dict) else {}
    items = as_list(first)
    total = total_items(first)
    if total is None or len(items) >= total:
        return items, envelope

    base = dict(params or {})
    size = base.get("size", page_size)
    collected = list(items)
    page = int(base.get("page") or 1)
    while True:
        page += 1
        data = await client.get(path, region=region, params={**base, "page": page, "size": size})
        batch = as_list(data)
        if not batch:
            return collected, envelope
        collected.extend(batch)
        page_total = total_items(data)
        if page_total is None:
            page_total = total
        if len(collected) >= page_total:
            return collected, envelope


async def fetch_all_items(
    client: VlbClient,
    path: str,
    region: str | None = None,
    params: dict | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list:
    """Fetch every item from a vLB list endpoint, never truncating."""
    items, _ = await fetch_list(client, path, region=region, params=params, page_size=page_size)
    return items
