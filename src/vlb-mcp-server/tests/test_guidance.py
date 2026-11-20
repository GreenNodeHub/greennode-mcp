"""The guidance layer: guides exist, prompts resolve, and they name real tools."""

from __future__ import annotations

import pytest
import re
from greennode.mcp_core.auth import TokenManager
from greennode.vlb_mcp_server.discovery_cache import TTL_CONFIG
from greennode.vlb_mcp_server.prompts_handler import _FEATURE_GUIDES, PromptsHandler
from greennode.vlb_mcp_server.server import create_server, register_handlers
from mcp.server.mcpserver import MCPServer


FEATURES = sorted(_FEATURE_GUIDES)


@pytest.fixture
def prompts():
    return PromptsHandler(MCPServer("t"))


@pytest.fixture
def full_server(config, client, cache):
    """A server with every handler wired, in write mode — the complete tool surface."""
    server = create_server(allow_write=True)
    return register_handlers(server, config, client, TokenManager(config), cache, allow_write=True)


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.asyncio
async def test_every_feature_has_a_non_empty_guide(prompts, feature):
    text = await prompts.get_feature_guide(feature=feature)
    assert len(text) > 200


@pytest.mark.asyncio
async def test_every_guide_is_registered_as_a_prompt(prompts):
    names = {p.name for p in await prompts.mcp.list_prompts()}
    assert names == {f"vlb_{f}" for f in FEATURES}


@pytest.mark.asyncio
async def test_get_feature_guide_is_the_only_registered_tool(prompts):
    assert {t.name for t in await prompts.mcp.list_tools()} == {"get_feature_guide"}


def _tool_names_mentioned(text: str) -> set[str]:
    """Tool-looking identifiers a guide names in backticks."""
    return {
        m
        for m in re.findall(r"`([a-z][a-z0-9_]{4,})`", text)
        if m.split("_")[0]
        in {
            "list",
            "get",
            "create",
            "update",
            "delete",
            "validate",
            "attach",
            "detach",
            "resize",
            "configure",
            "import",
        }
    }


@pytest.mark.asyncio
async def test_guides_only_reference_tools_that_exist(full_server):
    """A guide that drifts after a tool rename is worse than no guide at all."""
    registered = {t.name for t in await full_server.list_tools()}
    for feature, (_title, body) in _FEATURE_GUIDES.items():
        unknown = _tool_names_mentioned(body) - registered
        assert not unknown, f"guide '{feature}' names non-existent tools: {sorted(unknown)}"


@pytest.mark.asyncio
async def test_server_instructions_only_reference_tools_that_exist(full_server):
    registered = {t.name for t in await full_server.list_tools()}
    unknown = _tool_names_mentioned(full_server.instructions or "") - registered
    assert not unknown, f"SERVER_INSTRUCTIONS names non-existent tools: {sorted(unknown)}"


@pytest.mark.asyncio
async def test_every_cached_tool_name_is_a_real_tool(full_server):
    """A TTL keyed on a name no tool uses caches nothing and hides a typo."""
    registered = {t.name for t in await full_server.list_tools()}
    # project_lookup is an internal cache key, not a tool.
    unknown = set(TTL_CONFIG) - registered - {"project_lookup"}
    assert not unknown, f"TTL_CONFIG keys that are not tools: {sorted(unknown)}"
