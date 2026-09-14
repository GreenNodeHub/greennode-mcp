"""Shared test helpers for the vDB MCP server tests."""

from __future__ import annotations

import httpx
import respx


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
GATEWAY = "https://vdb-gateway.vngcloud.vn"

RELATIONAL = f"{GATEWAY}/vdb-relational"
MEMORY = f"{GATEWAY}/vdb-memory"
POSTGRESQL = f"{GATEWAY}/vdb-postgresql"
KAFKA = f"{GATEWAY}/vdb-kafka"


def mock_iam(mock: respx.MockRouter) -> None:
    """Answer the IAM token exchange so no test needs real credentials."""
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def envelope(data) -> dict:
    """Wrap *data* the way relational, memory and postgresql always do."""
    return {"code": 200, "message": "success", "data": data}


def nested_list(items: list, page_object: dict | None = None) -> dict:
    """The ``data.data`` shape: instance listings and the volume-type catalogue."""
    payload: dict = {"projectId": "pro-test-0001", "data": items}
    if page_object is not None:
        payload["pageObject"] = page_object
    return envelope(payload)


def content_list(items: list, page_object: dict | None = None) -> dict:
    """The ``data.content`` shape: backups, configurations, histories."""
    return envelope({"content": items, "pageObject": page_object})


def page_object(number=1, size=20, total_pages=1, total_elements=None) -> dict:
    """A `pageObject` as the API reports it (`number` is 1-based, like the request)."""
    return {
        "number": number,
        "size": size,
        "totalPages": total_pages,
        "totalElements": total_elements,
        "maxSize": 100,
    }
