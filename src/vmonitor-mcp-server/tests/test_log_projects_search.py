"""Tests for the vMonitor Log API project + search/export tools."""

from __future__ import annotations

import base64
import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.certificate_handler import CertificateHandler
from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.log_project_handler import LogProjectHandler
from greennode.vmonitor_mcp_server.log_search_handler import LogSearchHandler
from greennode.vmonitor_mcp_server.models import LogPageData, LogResource, LogSearchDto
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
LOG = "https://vmonitorapis.vngcloud.vn/log-api/v1"

PAGE = {
    "content": [{"id": "p1", "name": "proj-1", "status": "ACTIVE"}],
    "currentPage": 1,
    "pageSize": 10,
    "totalElements": 3,
    "totalPages": 1,
}


def _iam(mock):
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def _cfg(sample_config):
    return load_config(sample_config)


@pytest.fixture
def projects(sample_config):
    c = _cfg(sample_config)
    return LogProjectHandler(MCPServer("t"), c, VmonitorLogClient(c, TokenManager(c)))


@pytest.fixture
def projects_rw(sample_config):
    c = _cfg(sample_config)
    return LogProjectHandler(
        MCPServer("t"), c, VmonitorLogClient(c, TokenManager(c)), allow_write=True
    )


@pytest.fixture
def search(sample_config):
    c = _cfg(sample_config)
    return LogSearchHandler(MCPServer("t"), c, VmonitorLogClient(c, TokenManager(c)))


@pytest.fixture
def certs_rw(sample_config):
    c = _cfg(sample_config)
    return CertificateHandler(
        MCPServer("t"), c, VmonitorLogClient(c, TokenManager(c)), allow_write=True
    )


@pytest.fixture
def certs(sample_config):
    c = _cfg(sample_config)
    return CertificateHandler(MCPServer("t"), c, VmonitorLogClient(c, TokenManager(c)))


@pytest.mark.asyncio
async def test_project_reads_registered_writes_gated(projects, projects_rw):
    read_only = {t.name for t in await projects.mcp.list_tools()}
    assert {"list_projects", "get_project", "get_project_mappings"} <= read_only
    assert "update_project" not in read_only
    with_write = {t.name for t in await projects_rw.mcp.list_tools()}
    assert {"update_project", "update_project_mappings"} <= with_write


@pytest.mark.asyncio
async def test_certificate_tools_registered_write_gated(certs_rw, sample_config):
    c = _cfg(sample_config)
    certs = CertificateHandler(MCPServer("t"), c, VmonitorLogClient(c, TokenManager(c)))
    read_only = {t.name for t in await certs.mcp.list_tools()}
    assert "get_project_certificate_download" in read_only
    assert "create_project_certificate" not in read_only
    with_write = {t.name for t in await certs_rw.mcp.list_tools()}
    assert {"create_project_certificate", "delete_project_certificate"} <= with_write


@respx.mock
@pytest.mark.asyncio
async def test_list_projects_parses_page_envelope(projects):
    _iam(respx.mock)
    respx.get(f"{LOG}/projects").mock(return_value=httpx.Response(200, json=PAGE))
    result = await projects.list_projects(
        query=None, status=None, project_type=None, billing_status=None, page=1, size=10
    )
    assert isinstance(result, LogPageData)
    assert result.total_elements == 3
    assert result.items[0]["name"] == "proj-1"


@respx.mock
@pytest.mark.asyncio
async def test_get_project_wraps(projects):
    _iam(respx.mock)
    respx.get(f"{LOG}/projects/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "name": "proj-1"})
    )
    result = await projects.get_project(project_id="p1")
    assert isinstance(result, LogResource)
    assert result.id == "p1"


@respx.mock
@pytest.mark.asyncio
async def test_update_project_uses_patch(projects_rw):
    _iam(respx.mock)
    from greennode.vmonitor_mcp_server.models import UpdateProjectDto

    route = respx.patch(f"{LOG}/projects/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1"})
    )
    await projects_rw.update_project(project_id="p1", body=UpdateProjectDto(description="d"))
    assert route.called
    assert json.loads(route.calls.last.request.content) == {"description": "d"}


@respx.mock
@pytest.mark.asyncio
async def test_create_certificate_confirms(certs_rw):
    _iam(respx.mock)
    respx.post(f"{LOG}/projects/p1/certificates").mock(return_value=httpx.Response(200))
    msg = await certs_rw.create_project_certificate(project_id="p1")
    assert "p1" in msg


@respx.mock
@pytest.mark.asyncio
async def test_certificate_download_base64_encodes_binary(certs):
    _iam(respx.mock)
    binary = b"\x30\x82\x01\xfb\x00\x01certificate-bytes"
    respx.get(f"{LOG}/downloads/certificates/projects/p1/c1").mock(
        return_value=httpx.Response(
            200, content=binary, headers={"Content-Type": "application/octet-stream"}
        )
    )

    result = await certs.get_project_certificate_download(project_id="p1", cert_id="c1")

    assert base64.b64decode(result) == binary


@pytest.mark.asyncio
async def test_project_rejects_bad_id(projects):
    with pytest.raises(ValueError):
        await projects.get_project(project_id="../../secret")


@pytest.mark.asyncio
async def test_search_tools_registered(search):
    names = {t.name for t in await search.mcp.list_tools()}
    assert {
        "search_logs",
        "search_logs_default",
        "get_project_log_data_exists",
        "get_log_export",
    } <= names


@respx.mock
@pytest.mark.asyncio
async def test_search_logs_sends_from_alias_and_returns_text(search):
    _iam(respx.mock)
    route = respx.post(f"{LOG}/projects/p1/search-logs").mock(
        return_value=httpx.Response(200, json={"hits": 2})
    )
    body = LogSearchDto(query={"match_all": {}}, size=10, from_offset=5)
    result = await search.search_logs(project_id="p1", body=body)
    sent = json.loads(route.calls.last.request.content)
    assert sent["from"] == 5 and sent["size"] == 10
    assert sent["query"] == {
        "type": "bool",
        "value": {"filter": [], "should": [], "must": [], "mustNot": []},
    }
    assert isinstance(result, str)
    assert "hits" in result


@respx.mock
@pytest.mark.asyncio
async def test_search_default_defaults_query_to_match_all_bool(search):
    """Omitting query sends the accepted match-all bool, not a rejected match_all."""
    _iam(respx.mock)
    route = respx.post(f"{LOG}/projects/p1/search-logs/default").mock(
        return_value=httpx.Response(200, json={"hits": 0})
    )
    await search.search_logs_default(project_id="p1", body=LogSearchDto())
    sent = json.loads(route.calls.last.request.content)
    assert sent["query"] == {
        "type": "bool",
        "value": {"filter": [], "should": [], "must": [], "mustNot": []},
    }


@respx.mock
@pytest.mark.asyncio
async def test_search_normalizes_match_all_and_fills_bool(search):
    """A match_all query becomes an empty bool; a bool is filled and match_all clauses dropped."""
    _iam(respx.mock)
    route = respx.post(f"{LOG}/projects/p1/search-logs").mock(
        return_value=httpx.Response(200, json={"hits": 1})
    )
    await search.search_logs(
        project_id="p1", body=LogSearchDto(query={"type": "match_all", "value": {}})
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["query"]["type"] == "bool"
    assert sent["query"]["value"] == {"filter": [], "should": [], "must": [], "mustNot": []}

    await search.search_logs(
        project_id="p1",
        body=LogSearchDto(query={"type": "bool", "value": {"must": [{"type": "match_all"}]}}),
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["query"]["value"] == {"filter": [], "should": [], "must": [], "mustNot": []}


@respx.mock
@pytest.mark.asyncio
async def test_search_translates_elasticsearch_shorthands(search):
    """ES-style clauses (match/range/exists/bool with must_not) map to the vMonitor DSL."""
    _iam(respx.mock)
    route = respx.post(f"{LOG}/projects/p1/search-logs").mock(
        return_value=httpx.Response(200, json={"hits": 0})
    )

    await search.search_logs(
        project_id="p1", body=LogSearchDto(query={"match": {"message": "err"}})
    )
    assert json.loads(route.calls.last.request.content)["query"] == {
        "type": "match",
        "value": {"field": "message", "value": "err"},
    }

    await search.search_logs(
        project_id="p1", body=LogSearchDto(query={"range": {"@timestamp": {"gte": "now-1h"}}})
    )
    assert json.loads(route.calls.last.request.content)["query"] == {
        "type": "range",
        "value": {"field": "@timestamp", "gte": "now-1h"},
    }

    await search.search_logs(
        project_id="p1", body=LogSearchDto(query={"exists": {"field": "level"}})
    )
    assert json.loads(route.calls.last.request.content)["query"] == {
        "type": "exists",
        "value": {"field": "level"},
    }

    await search.search_logs(
        project_id="p1",
        body=LogSearchDto(
            query={"bool": {"must": [{"match": {"level": "error"}}], "must_not": []}}
        ),
    )
    assert json.loads(route.calls.last.request.content)["query"] == {
        "type": "bool",
        "value": {
            "filter": [],
            "should": [],
            "must": [{"type": "match", "value": {"field": "level", "value": "error"}}],
            "mustNot": [],
        },
    }


@respx.mock
@pytest.mark.asyncio
async def test_search_normalizes_sorts(search):
    """{field, order} and the ES {field: order} shorthand become field_sort clauses."""
    _iam(respx.mock)
    route = respx.post(f"{LOG}/projects/p1/search-logs").mock(
        return_value=httpx.Response(200, json={"hits": 0})
    )

    await search.search_logs(
        project_id="p1", body=LogSearchDto(sorts=[{"field": "@timestamp", "order": "desc"}])
    )
    assert json.loads(route.calls.last.request.content)["sorts"] == [
        {"type": "field_sort", "value": {"field": "@timestamp", "order": "desc"}}
    ]

    await search.search_logs(project_id="p1", body=LogSearchDto(sorts=[{"@timestamp": "asc"}]))
    assert json.loads(route.calls.last.request.content)["sorts"] == [
        {"type": "field_sort", "value": {"field": "@timestamp", "order": "asc"}}
    ]

    await search.search_logs(
        project_id="p1",
        body=LogSearchDto(
            sorts=[{"type": "field_sort", "value": {"field": "x", "order": "desc"}}]
        ),
    )
    assert json.loads(route.calls.last.request.content)["sorts"] == [
        {"type": "field_sort", "value": {"field": "x", "order": "desc"}}
    ]


@respx.mock
@pytest.mark.asyncio
async def test_exists_returns_bool(search):
    _iam(respx.mock)
    respx.get(f"{LOG}/projects/p1/exists-log-data").mock(
        return_value=httpx.Response(200, json=True)
    )
    assert await search.get_project_log_data_exists(project_id="p1") is True


def test_log_search_dto_forbids_extra():
    with pytest.raises(ValidationError):
        LogSearchDto(query={}, bogus=1)
