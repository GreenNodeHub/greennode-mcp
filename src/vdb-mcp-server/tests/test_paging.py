"""Tests for the vDB envelope/paging helpers.

The shapes below are the ones recorded in docs/superpowers/vdb-pr0-spike.md and
confirmed against the live API on 2026-08-12 and re-probed 2026-09-08.
"""

from __future__ import annotations

import pytest
from greennode.vdb_mcp_server.paging import (
    MAX_PAGE_SIZE,
    as_list,
    build_filter_params,
    build_page_params,
    unwrap_content_list,
    unwrap_nested_list,
    unwrap_wrapped,
)


def _envelope(data):
    return {"code": 200, "message": "Successfully", "data": data}


def _page_object(number=1, size=20, total_pages=3, total_elements=42):
    return {
        "number": number,
        "size": size,
        "totalPages": total_pages,
        "totalElements": total_elements,
        "maxSize": MAX_PAGE_SIZE,
    }


# --------------------------------------------------------------------------
# unwrap_wrapped
# --------------------------------------------------------------------------


def test_unwrap_wrapped_strips_the_envelope():
    assert unwrap_wrapped(_envelope({"id": "db-1"})) == {"id": "db-1"}


def test_unwrap_wrapped_passes_through_an_unwrapped_payload():
    """Not every family wraps; a bare payload must survive untouched."""
    assert unwrap_wrapped({"id": "db-1"}) == {"id": "db-1"}
    assert unwrap_wrapped([1, 2]) == [1, 2]


def test_unwrap_wrapped_tolerates_a_null_data():
    assert unwrap_wrapped(_envelope(None)) is None


def test_unwrap_wrapped_does_not_mistake_a_resource_field_named_data():
    """Only the 3-key {code, message, data} shape is an envelope.

    All 42 Wrap* schemas carry exactly those three properties and no non-Wrap
    schema carries both `code` and `message`, so shape detection is safe -- but
    only if it insists on `message` being present too.
    """
    resource = {"id": "db-1", "data": {"nested": True}}
    assert unwrap_wrapped(resource) == resource


# --------------------------------------------------------------------------
# unwrap_nested_list -- the doubly nested one
# --------------------------------------------------------------------------


def test_unwrap_nested_list_reads_the_inner_data():
    """`GET /database-instances` nests the items one level deeper than everything else."""
    payload = _envelope(
        {
            "projectId": "pro-xxx",
            "data": [{"id": "db-1"}, {"id": "db-2"}],
            "pageObject": _page_object(),
        }
    )
    page = unwrap_nested_list(payload)
    assert [i["id"] for i in page.items] == ["db-1", "db-2"]
    assert page.total_elements == 42
    assert page.max_size == MAX_PAGE_SIZE


def test_unwrap_nested_list_handles_an_empty_account():
    page = unwrap_nested_list(_envelope({"projectId": "pro-xxx", "data": [], "pageObject": None}))
    assert page.items == []
    assert page.total_elements is None


def test_unwrap_nested_list_reads_volume_types_which_do_not_paginate():
    """The three `volume-types` endpoints nest like the instance list but have no pageObject.

    Live shape, 2026-09-08. Reaching for `as_list` here silently yields an
    empty catalogue, so the nesting has to be handled explicitly.
    """
    payload = _envelope(
        {
            "projectId": "pro-xxx",
            "data": [
                {"id": 36, "type": "Gen2-NVMe2-IOPS3000", "minVolumeSize": 20},
                {"id": 37, "type": "Gen2-NVMe2-IOPS5000", "minVolumeSize": 20},
            ],
        }
    )
    page = unwrap_nested_list(payload)
    assert [i["type"] for i in page.items] == [
        "Gen2-NVMe2-IOPS3000",
        "Gen2-NVMe2-IOPS5000",
    ]
    assert page.page_number is None and page.total_elements is None
    assert as_list(payload) == [], "as_list must not be used as a fallback here"


# --------------------------------------------------------------------------
# unwrap_content_list -- the `content` key
# --------------------------------------------------------------------------


def test_unwrap_content_list_reads_the_content_key():
    """Six endpoints use `content` where the instance list uses `data`."""
    payload = _envelope(
        {"content": [{"id": "bk-1"}], "pageObject": _page_object(total_elements=1)}
    )
    page = unwrap_content_list(payload)
    assert [i["id"] for i in page.items] == ["bk-1"]
    assert page.total_elements == 1


def test_content_and_nested_helpers_are_not_interchangeable():
    """Guard against one generic list unwrapper creeping back in."""
    content_payload = _envelope({"content": [{"id": "bk-1"}], "pageObject": None})
    assert unwrap_nested_list(content_payload).items == []
    assert unwrap_content_list(content_payload).items == [{"id": "bk-1"}]


# --------------------------------------------------------------------------
# as_list -- the plain-array majority
# --------------------------------------------------------------------------


def test_as_list_reads_an_array_inside_the_envelope():
    """81 of 138 operations put a plain array in `data` -- most of the catalogue."""
    assert as_list(_envelope([{"name": "MySQL"}, {"name": "PostgreSQL"}])) == [
        {"name": "MySQL"},
        {"name": "PostgreSQL"},
    ]


def test_as_list_reads_a_bare_kafka_array():
    """`GET /vdb-kafka/clusters` answers with the array and no envelope at all."""
    assert as_list([{"id": "kf-1"}]) == [{"id": "kf-1"}]


def test_as_list_on_an_empty_collection():
    """A resource the account does not own answers 200 with an empty array."""
    assert as_list(_envelope([])) == []


# --------------------------------------------------------------------------
# query parameter builders
# --------------------------------------------------------------------------


def test_build_page_params_is_one_based():
    """`pageNumber` starts at 1 -- confirmed live, unlike vks which is 0-based."""
    assert build_page_params(1, 20) == {"pageNumber": 1, "pageSize": 20}


def test_build_page_params_rejects_page_zero():
    with pytest.raises(ValueError, match="1-based"):
        build_page_params(0, 20)


def test_build_page_params_caps_page_size_at_the_api_maximum():
    assert build_page_params(1, 5000)["pageSize"] == MAX_PAGE_SIZE


def test_build_filter_params_flattens_the_filter_request():
    """`filterRequest` fields travel as plain query params, never JSON-encoded."""
    assert build_filter_params(name="my-db", statuses=["ACTIVE"]) == {
        "name": "my-db",
        "status": ["ACTIVE"],
    }


def test_build_filter_params_repeats_the_status_key():
    params = build_filter_params(name=None, statuses=["ACTIVE", "BUILDING"])
    assert params == {"status": ["ACTIVE", "BUILDING"]}


def test_build_filter_params_omits_empty_values():
    assert build_filter_params(name=None, statuses=None) == {}
    assert build_filter_params(name="", statuses=[]) == {}
