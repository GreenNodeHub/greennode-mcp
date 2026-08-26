"""The envelope and paging normalisers."""

from __future__ import annotations

import httpx
import pytest
import respx
from .helpers import API_BASE, envelope, mock_iam
from greennode.vbackup_mcp_server.paging import as_list, fetch_all_items, total_items, unwrap


def test_as_list_reads_the_items_envelope():
    assert as_list(envelope([{"id": "a"}])) == [{"id": "a"}]


def test_as_list_passes_through_a_bare_array():
    """/v1/backup-instances/{id}/volumes answers with a bare array."""
    assert as_list([{"volumeId": "vol-1"}]) == [{"volumeId": "vol-1"}]


def test_as_list_accepts_an_explicit_wrapper_key():
    """/v1/backup-instances/protected-servers puts its ids under `ids`."""
    assert as_list({"ids": ["ins-1", "ins-2"]}, "ids") == ["ins-1", "ins-2"]


def test_as_list_returns_empty_for_shapes_it_cannot_read():
    assert as_list({"configs": {"a": 1}}) == []
    assert as_list(None) == []


def test_unwrap_returns_the_object_itself():
    """vBackup detail endpoints return the resource directly, with no envelope."""
    assert unwrap({"id": "bk-ins-0001"}) == {"id": "bk-ins-0001"}
    assert unwrap({"data": {"id": "bk-ins-0001"}}) == {"id": "bk-ins-0001"}
    assert unwrap(None) == {}


def test_total_items_reads_the_plural_spelling():
    assert total_items(envelope([], total=7)) == 7
    assert total_items({"totalItem": 7}) is None


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_takes_the_unpaged_fast_path(client):
    """The whole collection comes back in one call, so do not page needlessly."""
    mock_iam(respx.mock)
    route = respx.get(f"{API_BASE}/v1/backends").mock(
        return_value=httpx.Response(200, json=envelope([{"id": "be-0001"}]))
    )
    items = await fetch_all_items(client, "/v1/backends")
    assert items == [{"id": "be-0001"}]
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_fetch_all_items_repages_a_truncated_response(client):
    """A response reporting more items than it returned must not truncate."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backends").mock(
        side_effect=[
            httpx.Response(200, json=envelope([{"id": "be-0001"}], total=2)),
            httpx.Response(200, json=envelope([{"id": "be-0001"}], total=2)),
            httpx.Response(200, json=envelope([{"id": "be-0002"}], total=2)),
        ]
    )
    items = await fetch_all_items(client, "/v1/backends")
    assert [i["id"] for i in items] == ["be-0001", "be-0002"]


@respx.mock
@pytest.mark.asyncio
async def test_repaging_sends_size_not_pagesize(client):
    """`pageSize` as a REQUEST parameter is ignored by the API — always send `size`."""
    mock_iam(respx.mock)
    respx.get(f"{API_BASE}/v1/backends").mock(
        side_effect=[
            httpx.Response(200, json=envelope([{"id": "be-0001"}], total=2)),
            httpx.Response(200, json=envelope([{"id": "be-0001"}], total=2)),
            httpx.Response(200, json=envelope([{"id": "be-0002"}], total=2)),
        ]
    )
    await fetch_all_items(client, "/v1/backends")
    paged = respx.calls[-1].request.url.params
    assert "size" in paged
    assert "pageSize" not in paged
