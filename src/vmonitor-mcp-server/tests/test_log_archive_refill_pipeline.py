"""Tests for the vMonitor Log API archive, refill and pipeline tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.log_archive_handler import LogArchiveHandler
from greennode.vmonitor_mcp_server.log_pipeline_handler import LogPipelineHandler
from greennode.vmonitor_mcp_server.log_refill_handler import LogRefillHandler
from greennode.vmonitor_mcp_server.models import (
    CreateArchiveDto,
    CreateRefillFromArchiveDto,
    LogPageData,
    LogResource,
    PipelineDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
LOG = "https://vmonitorapis.vngcloud.vn/log-api/v1"
PAGE = {
    "content": [{"id": "a1"}],
    "currentPage": 1,
    "pageSize": 10,
    "totalElements": 1,
    "totalPages": 1,
}


def _iam(mock):
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def _h(cls, sample_config, rw=False):
    c = load_config(sample_config)
    return cls(MCPServer("t"), c, VmonitorLogClient(c, TokenManager(c)), allow_write=rw)


@pytest.mark.asyncio
async def test_gating(sample_config):
    ar = _h(LogArchiveHandler, sample_config)
    ar_rw = _h(LogArchiveHandler, sample_config, rw=True)
    ro = {t.name for t in await ar.mcp.list_tools()}
    assert {"list_archives", "get_archive", "validate_archive_connection"} <= ro
    assert "create_archive" not in ro
    assert {"create_archive", "update_archive", "delete_archive"} <= {
        t.name for t in await ar_rw.mcp.list_tools()
    }


@respx.mock
@pytest.mark.asyncio
async def test_list_archives_project_filter(sample_config):
    _iam(respx.mock)
    route = respx.get(f"{LOG}/archives").mock(return_value=httpx.Response(200, json=PAGE))
    ar = _h(LogArchiveHandler, sample_config)
    result = await ar.list_archives(query=None, project_id="p1", page=None, size=None)
    assert dict(route.calls.last.request.url.params.multi_items()) == {"project_id": "p1"}
    assert isinstance(result, LogPageData)


@respx.mock
@pytest.mark.asyncio
async def test_create_archive_posts_body(sample_config):
    _iam(respx.mock)
    route = respx.post(f"{LOG}/archives").mock(return_value=httpx.Response(200, json={"id": "a1"}))
    ar = _h(LogArchiveHandler, sample_config, rw=True)
    body = CreateArchiveDto(
        name="arch", projectId="p1", storageType="S3", storageSettings={"bucket": "b"}
    )
    result = await ar.create_archive(body=body)
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "name": "arch",
        "projectId": "p1",
        "storageType": "S3",
        "storageSettings": {"bucket": "b"},
    }
    assert isinstance(result, LogResource)


@respx.mock
@pytest.mark.asyncio
async def test_refill_from_archive_posts_collections(sample_config):
    _iam(respx.mock)
    route = respx.post(f"{LOG}/refills/collections").mock(
        return_value=httpx.Response(200, json={"id": "r1"})
    )
    rf = _h(LogRefillHandler, sample_config, rw=True)
    body = CreateRefillFromArchiveDto(
        name="rf", projectId="p1", archiveId="a1", startAt="1", endAt="2"
    )
    await rf.create_refill_from_archive(body=body)
    assert route.called
    assert json.loads(route.calls.last.request.content)["archiveId"] == "a1"


@respx.mock
@pytest.mark.asyncio
async def test_list_refills_requires_project(sample_config):
    _iam(respx.mock)
    route = respx.get(f"{LOG}/refills").mock(return_value=httpx.Response(200, json=PAGE))
    rf = _h(LogRefillHandler, sample_config)
    await rf.list_refills(project_id="p1", query=None, page=None, size=None)
    assert dict(route.calls.last.request.url.params.multi_items()) == {"project_id": "p1"}


@respx.mock
@pytest.mark.asyncio
async def test_create_and_delete_pipeline(sample_config):
    _iam(respx.mock)
    respx.post(f"{LOG}/pipelines").mock(return_value=httpx.Response(200, json={"id": "pl1"}))
    respx.delete(f"{LOG}/pipelines/pl1").mock(return_value=httpx.Response(200))
    pl = _h(LogPipelineHandler, sample_config, rw=True)
    r = await pl.create_pipeline(body=PipelineDto(name="p"))
    assert isinstance(r, LogResource)
    msg = await pl.delete_pipeline(pipeline_id="pl1")
    assert "pl1" in msg


@pytest.mark.asyncio
async def test_pipeline_rejects_bad_id(sample_config):
    pl = _h(LogPipelineHandler, sample_config)
    with pytest.raises(ValueError):
        await pl.get_pipeline(pipeline_id="../x")


def test_dtos_forbid_extra():
    with pytest.raises(ValidationError):
        CreateArchiveDto(name="a", projectId="p", storageType="s", storageSettings={}, bogus=1)
    with pytest.raises(ValidationError):
        PipelineDto(name="p", bogus=1)
