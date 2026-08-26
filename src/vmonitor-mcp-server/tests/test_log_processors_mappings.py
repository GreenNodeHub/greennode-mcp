"""Tests for the vMonitor Log API processor and resource log-mapping tools."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.log_mapping_handler import LogMappingHandler
from greennode.vmonitor_mcp_server.log_processor_handler import LogProcessorHandler
from greennode.vmonitor_mcp_server.models import (
    LogMappingEnableDto,
    LogPageData,
    LogResource,
    ProcessorDto,
    ReorderProcessorsDto,
)
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
LOG = "https://vmonitorapis.vngcloud.vn/log-api/v1"
PAGE = {
    "content": [{"id": "m1"}],
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
async def test_processor_gating(sample_config):
    ro = {t.name for t in await _h(LogProcessorHandler, sample_config).mcp.list_tools()}
    assert {"get_processor_group", "list_date_formats", "validate_grok_parser"} <= ro
    assert "create_processor" not in ro
    rw = {t.name for t in await _h(LogProcessorHandler, sample_config, rw=True).mcp.list_tools()}
    assert {"create_processor", "update_processor_order", "create_processor_group_library"} <= rw


@respx.mock
@pytest.mark.asyncio
async def test_create_processor_posts_nested_path(sample_config):
    _iam(respx.mock)
    route = respx.post(f"{LOG}/processors/pl1/g1").mock(
        return_value=httpx.Response(200, json={"id": "pr1"})
    )
    h = _h(LogProcessorHandler, sample_config, rw=True)
    body = ProcessorDto(
        name="p", pipelineId="pl1", processorGroupId="g1", parserType="grok", parserRule="%{IP}"
    )
    await h.create_processor(pipeline_id="pl1", processor_group_id="g1", body=body)
    assert route.called
    assert json.loads(route.calls.last.request.content)["parserType"] == "grok"


@respx.mock
@pytest.mark.asyncio
async def test_reorder_hits_reorder_path(sample_config):
    _iam(respx.mock)
    route = respx.put(f"{LOG}/processor-groups/pl1/g1/re-order").mock(
        return_value=httpx.Response(200)
    )
    h = _h(LogProcessorHandler, sample_config, rw=True)
    body = ReorderProcessorsDto(pipelineId="pl1", processorGroupId="g1", processors=["a", "b"])
    msg = await h.update_processor_order(pipeline_id="pl1", processor_group_id="g1", body=body)
    assert route.called
    assert json.loads(route.calls.last.request.content)["processors"] == ["a", "b"]
    assert "g1" in msg


@respx.mock
@pytest.mark.asyncio
async def test_list_date_formats_returns_list(sample_config):
    _iam(respx.mock)
    respx.get(f"{LOG}/processors/formats-date").mock(
        return_value=httpx.Response(200, json=["yyyy-MM-dd", "ISO8601"])
    )
    result = await _h(LogProcessorHandler, sample_config).list_date_formats()
    assert result == ["yyyy-MM-dd", "ISO8601"]


@pytest.mark.asyncio
async def test_mapping_gating(sample_config):
    ro = {t.name for t in await _h(LogMappingHandler, sample_config).mcp.list_tools()}
    assert {
        "list_vcdn_log_mappings",
        "list_vstorage_bucket_log_mappings",
        "list_vstorage_log_mapping_regions",
    } <= ro
    assert "update_vcdn_log_mapping_enabled" not in ro
    rw = {t.name for t in await _h(LogMappingHandler, sample_config, rw=True).mcp.list_tools()}
    assert len(rw) == 20


@respx.mock
@pytest.mark.asyncio
async def test_list_vstorage_maps_region_id_param(sample_config):
    _iam(respx.mock)
    route = respx.get(f"{LOG}/vstorage-log-mappings").mock(
        return_value=httpx.Response(200, json=PAGE)
    )
    h = _h(LogMappingHandler, sample_config)
    result = await h.list_vstorage_log_mappings(
        query=None, region_id="HCM03", sort_by=None, sort_order=None, page=None, size=None
    )
    assert dict(route.calls.last.request.url.params.multi_items()) == {"region-id": "HCM03"}
    assert isinstance(result, LogPageData)


@respx.mock
@pytest.mark.asyncio
async def test_enable_vcdn_encodes_domain_segment(sample_config):
    _iam(respx.mock)
    route = respx.patch(f"{LOG}/vcdn-log-mapping/enable/cdn.example.com").mock(
        return_value=httpx.Response(200, json={"id": "m1"})
    )
    h = _h(LogMappingHandler, sample_config, rw=True)
    body = LogMappingEnableDto(logProjectId="p1", status="ENABLED")
    result = await h.update_vcdn_log_mapping_enabled(cdn_domain="cdn.example.com", body=body)
    assert route.called
    assert json.loads(route.calls.last.request.content) == {
        "logProjectId": "p1",
        "status": "ENABLED",
    }
    assert isinstance(result, LogResource)


@pytest.mark.asyncio
async def test_mapping_rejects_path_separator(sample_config):
    h = _h(LogMappingHandler, sample_config, rw=True)
    body = LogMappingEnableDto(logProjectId="p1", status="ENABLED")
    with pytest.raises(ValueError):
        await h.update_vcdn_log_mapping_enabled(cdn_domain="a/b", body=body)


def test_dtos_forbid_extra():
    with pytest.raises(ValidationError):
        ProcessorDto(
            name="p",
            pipelineId="x",
            processorGroupId="y",
            parserType="grok",
            parserRule="r",
            bogus=1,
        )
    with pytest.raises(ValidationError):
        LogMappingEnableDto(logProjectId="p", status="s", bogus=1)
