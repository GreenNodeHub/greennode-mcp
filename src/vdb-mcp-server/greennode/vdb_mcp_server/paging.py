"""Envelope, paging and filter helpers for the vDB API.

There is deliberately **no** single "unwrap a vDB response" function here.
Three families (relational, memory, postgresql) wrap every response in
``{code, message, data}``; Kafka wraps only 9 of its 36 operations. And inside
``data`` the shape still varies: the instance listings nest their items one
level deeper (``data.data``) than the backup, configuration and history
listings (``data.content``). One generic unwrapper would have to guess, and
would guess wrong on whichever endpoint was added last.
"""

from __future__ import annotations

from typing import Any, NamedTuple


MAX_PAGE_SIZE = 100
"""Ceiling the API reports as ``pageObject.maxSize``. Larger values are clamped."""

DEFAULT_PAGE_SIZE = 20


class Page(NamedTuple):
    """One page of a vDB list response, normalised across the two item keys."""

    items: list
    page_number: int | None = None
    page_size: int | None = None
    total_pages: int | None = None
    total_elements: int | None = None
    max_size: int | None = None


def _is_envelope(data: Any) -> bool:
    """True for the ``{code, message, data}`` wrapper and nothing else.

    Every ``Wrap*`` schema in the spec carries exactly those three properties,
    and no other schema carries both ``code`` and ``message`` -- so requiring
    both makes shape detection safe. Testing for ``data`` alone would unwrap
    any resource that happens to have a field of that name.
    """
    return isinstance(data, dict) and "code" in data and "message" in data and "data" in data


def unwrap_wrapped(data: Any) -> Any:
    """Return the payload inside a ``{code, message, data}`` envelope.

    Anything that is not an envelope -- a bare Kafka object or array -- is
    returned unchanged, so a handler never has to ask which family it is in.
    """
    return data["data"] if _is_envelope(data) else data


def _page_from(payload: Any, items: list) -> Page:
    page_object = payload.get("pageObject") if isinstance(payload, dict) else None
    if not isinstance(page_object, dict):
        return Page(items=items)
    return Page(
        items=items,
        page_number=page_object.get("number"),
        page_size=page_object.get("size"),
        total_pages=page_object.get("totalPages"),
        total_elements=page_object.get("totalElements"),
        max_size=page_object.get("maxSize"),
    )


def unwrap_nested_list(data: Any) -> Page:
    """Read a list whose items sit at ``data.data`` -- one level deeper than usual.

    Five endpoints are shaped this way, and they do not all paginate:

    * the two ``GET /database-instances`` listings, which carry a ``pageObject``
    * the three ``volume-types`` catalogue endpoints (one per family), which
      carry only ``projectId`` alongside the items

    Reading the envelope's own ``data`` yields the wrapper object rather than
    the items, which is why this cannot share a code path with the endpoints
    that put a plain array there. ``page_*`` fields come back ``None`` for the
    unpaginated three.
    """
    payload = unwrap_wrapped(data)
    items = payload.get("data") if isinstance(payload, dict) else None
    return _page_from(payload, items if isinstance(items, list) else [])


def unwrap_content_list(data: Any) -> Page:
    """Read a backup / configuration / history page: items live at ``data.content``.

    Six endpoints use ``content`` where the two instance listings use ``data``.
    """
    payload = unwrap_wrapped(data)
    items = payload.get("content") if isinstance(payload, dict) else None
    return _page_from(payload, items if isinstance(items, list) else [])


def as_list(data: Any) -> list:
    """Normalise a plain-array response to a list.

    81 of the 138 operations answer with an array directly inside ``data`` (or,
    for Kafka, as the whole body) -- most of the catalogue among them. This is
    the common case, but it is NOT safe as a fallback for the five endpoints
    handled by :func:`unwrap_nested_list`: there ``data`` is an object, and this
    function would report an empty collection instead of failing loudly.
    """
    payload = unwrap_wrapped(data)
    return payload if isinstance(payload, list) else []


def build_page_params(page: int, page_size: int) -> dict[str, int]:
    """Build the ``pageNumber``/``pageSize`` query parameters.

    ``pageNumber`` is **1-based** -- verified live, and the opposite of vks.
    The response's ``pageObject.number`` echoes the page that was asked for, so
    it is 1-based too and must not be treated as a zero-based offset.
    """
    if page < 1:
        raise ValueError(f"page must be 1-based (got {page}); the first page is 1, not 0")
    return {"pageNumber": page, "pageSize": min(page_size, MAX_PAGE_SIZE)}


def build_filter_params(
    name: str | None = None,
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    """Build the flattened ``filterRequest`` query parameters.

    The spec models these as a nested object marked ``required``, which is a
    springdoc artifact for a non-nullable POJO: on the wire they are plain
    query parameters (``?name=my-db&status=ACTIVE``), never JSON-encoded, and
    each one is optional. Multiple statuses repeat the key.

    Callers must not present these filters as exact: on the relational
    listing they are not applied to the PostgreSQL Cluster rows at all.
    """
    params: dict[str, Any] = {}
    if name:
        params["name"] = name
    if statuses:
        params["status"] = list(statuses)
    return params
