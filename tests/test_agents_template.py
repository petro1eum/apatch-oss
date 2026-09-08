"""AGENTS.template.md must stay in sync with registered MCP tools."""

from __future__ import annotations

import re

import pytest

from apatch.doctor import _agents_template_path, compose_agents_md, init_consumer

pytest.importorskip("mcp", reason="mcp extra not installed")

from apatch.mcp import server as mcp_server


def _registered_tool_names() -> set[str]:
    tools = getattr(mcp_server.mcp, "_tool_manager", None)
    if tools is None:
        pytest.skip("FastMCP tool manager API unavailable")
    return set(tools._tools.keys()) if hasattr(tools, "_tools") else set()


def test_agents_template_lists_all_mcp_tools():
    template = _agents_template_path().read_text(encoding="utf-8")
    registered = _registered_tool_names()
    missing = sorted(t for t in registered if t not in template)
    assert not missing, f"AGENTS.template.md missing tools: {missing}"


def test_agents_template_has_decision_tree_and_markers():
    template = _agents_template_path().read_text(encoding="utf-8")
    assert "## 2. Что делаешь?" in template
    assert "<!-- apatch:stack:start -->" in template
    assert "<!-- apatch:project:start -->" in template
    assert "apatch_session_start" in template
    assert "recommended_verify_resolved" in template
    assert "фактический enrollment" in template
    assert "свежую `session_capability`" in template
    # Single governed model — no competing "Master workflow" section
    assert "Master workflow" not in template


def test_compose_agents_injects_stack_section():
    template = "<!-- apatch:stack:start -->\n<!-- apatch:stack:end -->\n"
    body = compose_agents_md(template, stack_section="## Stack profile: test\n\nMCP only.")
    assert "Stack profile: test" in body
    assert "MCP only" in body


def test_init_consumer_refresh_preserves_project_block(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "old header\n"
        "<!-- apatch:project:start -->\n"
        "## Project backlog\n"
        "- strip Profile.tsx\n"
        "<!-- apatch:project:end -->\n",
        encoding="utf-8",
    )
    created = init_consumer(str(tmp_path), profile="frontend", refresh_agents=True)
    assert str(agents) in created
    text = agents.read_text(encoding="utf-8")
    assert "strip Profile.tsx" in text
    assert "old header" not in text
    assert "apatch_phase_run" in text  # frontend stack section
    assert "## 2. Что делаешь?" in text


def test_init_consumer_frontend_stack_is_mcp_not_cli(tmp_path):
    init_consumer(str(tmp_path), profile="frontend")
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "apatch_strip_dry_run" in text
    assert "apatch phase run" not in text
