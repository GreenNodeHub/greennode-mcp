"""Tests for the vDB guidance prompts.

Guidance is the one place where a wrong sentence costs as much as a wrong line
of code: an agent that reads "restore rolls back" or "the id tells you the
family" will act on it. So these tests assert on the *content* of the rules
that were expensive to establish, not just that the text is non-empty.

The guide bodies are Vietnamese (matching vks), so the prose assertions below
are Vietnamese too; identifiers, statuses and numbers stay as they are in the
API and are what most of these tests actually pin.
"""

from __future__ import annotations

import pytest
from greennode.vdb_mcp_server.prompts_handler import PromptsHandler
from mcp.server.mcpserver import MCPServer


TOPICS = (
    "getting_started",
    "create_instance",
    "restore_backup",
    "configuration_group",
    "backups_and_storage",
    "troubleshooting",
)


@pytest.fixture
def handler():
    return PromptsHandler(MCPServer("test"))


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_topic_is_registered_as_a_prompt(handler):
    names = {p.name for p in await handler.mcp.list_prompts()}
    assert names == {f"vdb_{topic}" for topic in TOPICS}


@pytest.mark.asyncio
async def test_the_guide_tool_is_registered_and_read_only(handler):
    tools = {t.name: t for t in await handler.mcp.list_tools()}
    assert "get_vdb_guide" in tools
    assert tools["get_vdb_guide"].annotations.read_only_hint is True


@pytest.mark.asyncio
async def test_the_guide_tool_is_available_without_allow_write(handler):
    """Guidance is read-only, so it must not be gated behind write mode.

    PromptsHandler takes no allow_write at all -- this pins that down, since
    guidance is most needed exactly when planning a write.
    """
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert "get_vdb_guide" in tools


@pytest.mark.asyncio
async def test_every_topic_returns_substantial_markdown(handler):
    for topic in TOPICS:
        text = await handler.get_vdb_guide(topic)
        assert text.startswith("# "), topic
        assert len(text) > 800, f"{topic} is too thin to be guidance"


@pytest.mark.asyncio
async def test_the_prompt_and_the_tool_return_the_same_text(handler):
    """Two front doors, one source -- they must not drift apart."""
    for topic in TOPICS:
        prompt_text = await getattr(handler, f"vdb_{topic}")()
        assert prompt_text == await handler.get_vdb_guide(topic), topic


@pytest.mark.asyncio
async def test_an_unknown_topic_is_a_clean_failure(handler):
    with pytest.raises(KeyError):
        await handler.get_vdb_guide("no_such_topic")


# --------------------------------------------------------------------------
# the guides are Vietnamese, and say so by being Vietnamese
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_guide_body_is_vietnamese(handler):
    """Pins the language decision: vDB guidance matches vks's Vietnamese prompts.

    Diacritics are the cheapest reliable signal -- an accidental revert to the
    English draft would carry none of them.
    """
    for topic in TOPICS:
        text = await handler.get_vdb_guide(topic)
        assert any(ch in text for ch in "ạảấầểệỗộơưừứịỉòóồố"), topic


@pytest.mark.asyncio
async def test_getting_started_tells_the_agent_to_answer_in_the_users_language(handler):
    text = await handler.get_vdb_guide("getting_started")
    assert "Trả lời bằng ngôn ngữ người dùng đang dùng" in text


# --------------------------------------------------------------------------
# the rules that were expensive to establish
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_getting_started_denies_that_an_id_identifies_a_family(handler):
    text = await handler.get_vdb_guide("getting_started")
    assert "KHÔNG cho biết nó thuộc họ nào" in text
    assert "get_memory_instance" in text and "get_relational_instance" in text
    assert "datastore_type" in text


@pytest.mark.asyncio
async def test_create_guide_carries_the_same_zone_rule_and_both_password_ranges(handler):
    text = await handler.get_vdb_guide("create_instance")
    # Wrapped prose: compare on collapsed whitespace so a reflow does not
    # silently drop the rule these tests exist to protect.
    flat = " ".join(text.split())
    assert "phải cùng đến từ MỘT zone." in flat
    assert "8-32" in text and "16-128" in text
    assert "0.0.0.0/0" in text, "the default-open rule must be in the confirm gate"
    assert "dryrun" in text
    assert "ROOT_USER" in text, "the flow that answers 200 and creates nothing"


@pytest.mark.asyncio
async def test_restore_guide_leads_with_the_fact_that_it_does_not_roll_back(handler):
    text = await handler.get_vdb_guide("restore_backup")
    head = text[: text.index("## Luồng")]
    assert "không hoàn tác bất cứ thứ gì" in head
    assert "instance thứ hai, hoàn toàn mới" in head
    assert "COMPLETED" in text
    assert "sub-" in text and "net-" in text, "the netIds trap"


@pytest.mark.asyncio
async def test_configuration_guide_says_zero_restart_params_is_not_a_promise(handler):
    text = await handler.get_vdb_guide("configuration_group")
    flat = " ".join(text.split())
    assert "KHÔNG phải là lời hứa rằng không cần reboot." in flat
    assert "RESTART_REQUIRED" in text
    assert "instanceCount" in text, "trust the list, not the count"
    assert "chỉ chạy MỘT lần sửa tại một thời điểm" in flat


@pytest.mark.asyncio
async def test_backup_guide_says_the_free_allowance_moves(handler):
    text = await handler.get_vdb_guide("backups_and_storage")
    flat = " ".join(text.split())
    assert "không phải hằng số" in text
    assert "LẶP LẠI hằng tháng" in flat, "buying quota is a monthly charge"
    assert "`description` không được để rỗng" in text
    assert "hai bể riêng biệt" in flat


@pytest.mark.asyncio
async def test_troubleshooting_leads_with_http_200_not_meaning_success(handler):
    text = await handler.get_vdb_guide("troubleshooting")
    flat = " ".join(text.split())
    assert "HTTP 200 không có nghĩa là thao tác đã thành công." in flat
    assert "list_*_instance_histories" in text
    assert "in_valid" in text
    assert "ERROR" in text and "nền tảng" in text


@pytest.mark.asyncio
async def test_every_guide_that_describes_a_write_points_at_the_confirm_gate(handler):
    for topic in ("create_instance", "restore_backup", "configuration_group"):
        text = await handler.get_vdb_guide(topic)
        assert "xác nhận" in text.lower(), topic


@pytest.mark.asyncio
async def test_no_guide_promises_a_region_parameter(handler):
    """vDB has one gateway for the whole account; a region argument would be a lie."""
    for topic in TOPICS:
        text = await handler.get_vdb_guide(topic)
        assert "region=" not in text, topic


@pytest.mark.asyncio
async def test_guides_never_tell_the_agent_to_reboot_on_its_own(handler):
    text = await handler.get_vdb_guide("configuration_group")
    assert "Không bao giờ tự ý reboot một database" in text
