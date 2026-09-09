"""Every tool declares how dangerous it is, and the labels are honest.

A client uses `readOnlyHint` to decide whether a call needs confirmation, so a
mutating tool that forgets the annotation gets run without asking. Nothing
asserted this before: the other test files build handlers directly and never
look at the registered annotations.
"""

from __future__ import annotations

import pytest
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.server import register_handlers
from mcp.server.mcpserver import MCPServer


#: Tools that mutate without a `delete_` name. Each replaces or rewrites state
#: that cannot be recovered from the API, so each must be flagged destructive.
EXPECTED_DESTRUCTIVE_NON_DELETES = {
    # Replaces the entire member list — the pool's other members are dropped.
    "update_pool_members",
    # Removes members; can empty a pool and black-hole its traffic.
    "detach_pool_members",
    # A full replacement: a body omitting defaultPoolId and insertHeaders
    # clears both. Measured live — a listener serving a pool was left with no
    # pool by a body carrying only its three timeouts.
    "update_listener",
}


def _register(config, client, *, allow_write):
    mcp = MCPServer("t")
    register_handlers(
        mcp, config, client, None, DiscoveryCache(ttl_config={}), allow_write=allow_write
    )
    return mcp


@pytest.fixture
async def read_only_tools(config, client):
    return list(await _register(config, client, allow_write=False).list_tools())


@pytest.fixture
async def write_tools(config, client):
    return list(await _register(config, client, allow_write=True).list_tools())


def test_every_tool_declares_annotations(write_tools):
    missing = [t.name for t in write_tools if t.annotations is None]
    assert missing == []


def test_every_tool_declares_read_only_hint(write_tools):
    assert [t.name for t in write_tools if t.annotations.read_only_hint is None] == []


def test_every_mutating_tool_declares_destructive_hint(write_tools):
    """Only mutating tools need it — `readOnlyHint` already settles the rest.

    It matters that it is explicit here: the MCP default for `destructiveHint`
    is true, so a write tool that omits it would warn on every call.
    """
    incomplete = [
        t.name
        for t in write_tools
        if not t.annotations.read_only_hint and t.annotations.destructive_hint is None
    ]
    assert incomplete == []


def test_read_only_mode_registers_only_read_only_tools(read_only_tools):
    """The write guard is defence in depth; the real gate is not registering them."""
    mutating = [t.name for t in read_only_tools if not t.annotations.read_only_hint]
    assert mutating == []


def test_read_only_mode_registers_nothing_destructive(read_only_tools):
    assert [t.name for t in read_only_tools if t.annotations.destructive_hint] == []


def test_write_mode_adds_only_mutating_tools(read_only_tools, write_tools):
    """Enabling writes must not change how an existing tool is labelled."""
    before = {t.name: t.annotations.read_only_hint for t in read_only_tools}
    after = {t.name: t.annotations.read_only_hint for t in write_tools}
    assert all(after[name] is hint for name, hint in before.items())
    added = set(after) - set(before)
    assert added and all(after[name] is False for name in added)


def test_read_only_tools_are_the_ones_that_only_read(write_tools):
    """A read-only tool's name must start with list_, get_ or validate_.

    validate_load_balancer_create is the odd one out on purpose: it checks a
    create body locally and issues no request at all.
    """
    liars = [
        t.name
        for t in write_tools
        if t.annotations.read_only_hint and not t.name.startswith(("list_", "get_", "validate_"))
    ]
    assert liars == []


def test_every_delete_tool_is_destructive(write_tools):
    not_flagged = [
        t.name
        for t in write_tools
        if t.name.startswith("delete_") and not t.annotations.destructive_hint
    ]
    assert not_flagged == []


def test_destructive_tools_that_are_not_deletes_are_the_expected_ones(write_tools):
    """Pins the list so a new replace-semantics tool cannot slip in unflagged."""
    found = {
        t.name
        for t in write_tools
        if t.annotations.destructive_hint and not t.name.startswith("delete_")
    }
    assert found == EXPECTED_DESTRUCTIVE_NON_DELETES


def test_nothing_is_both_read_only_and_destructive(write_tools):
    contradictory = [
        t.name
        for t in write_tools
        if t.annotations.read_only_hint and t.annotations.destructive_hint
    ]
    assert contradictory == []


def test_tool_counts_are_pinned(read_only_tools, write_tools):
    """A change here should be deliberate, so it has to be edited in.

    These are `register_handlers`' own counts. The running server has one more
    of each — `create_server` registers `PromptsHandler.get_feature_guide`
    separately — which is where the documented 25 / 47 come from.
    """
    assert len(read_only_tools) == 24
    assert len(write_tools) == 46
    assert sum(1 for t in write_tools if t.annotations.destructive_hint) == 8


def test_every_tool_has_a_description(write_tools):
    """The docstring is the tool description an agent reads to choose."""
    undocumented = [t.name for t in write_tools if not (t.description or "").strip()]
    assert undocumented == []


def test_write_tool_descriptions_state_the_write_flag(write_tools):
    """A refusal an agent cannot explain wastes a turn; say it up front."""
    silent = [
        t.name
        for t in write_tools
        if not t.annotations.read_only_hint and "--allow-write" not in (t.description or "")
    ]
    assert silent == []
