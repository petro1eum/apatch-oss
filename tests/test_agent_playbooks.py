"""MCP playbook resources — content and registration."""

from __future__ import annotations

import pytest

from apatch.agent_playbooks import (
    doc_outline_playbook,
    mcp_playbook_index,
    runtime_hygiene_playbook,
    tool_usage_playbook,
)
from apatch.agent_guidance import attach_artifact_guidance, protocol_contract


def test_runtime_hygiene_has_ephemeral_routing():
    pb = runtime_hygiene_playbook()
    assert "ephemeral_routing" in pb
    assert ".apatch/tmp/" in pb["ephemeral_routing"]["resolved_to"]
    assert "reconcile" in pb["gc_modes"]


def test_playbook_index_lists_resources():
    idx = mcp_playbook_index()
    uris = idx["resources"]
    assert "apatch://playbook/runtime_hygiene" in uris
    assert "apatch://playbook/doc_outline" in uris
    assert len(uris) >= 8
    assert "runtime_hygiene" in idx["read_order"][1]


def test_doc_outline_playbook_has_workflow_and_example():
    pb = doc_outline_playbook()
    assert pb["resource"] == "apatch://playbook/doc_outline"
    assert pb["example_needle"]["action"] == "insert_section"
    assert "apatch_verify_run" in " ".join(pb["workflow"])
    assert "grep" in " ".join(pb["workflow"])


def test_diagnose_playbook_in_index():
    from apatch.agent_playbooks import diagnose_playbook, mcp_playbook_index

    idx = mcp_playbook_index()
    assert "apatch://playbook/diagnose" in idx["resources"]
    assert any("diagnose" in x for x in idx["read_order"])
    pb = diagnose_playbook()
    assert pb["resource"] == "apatch://playbook/diagnose"


def test_doctor_includes_runtime_hygiene():
    out = attach_artifact_guidance({"ok": True}, "apatch_doctor", target_dir=".")
    assert "runtime_hygiene" in out
    assert "mcp_resources" in out
    assert "tool_usage" in out


def test_protocol_contract_never_hand_create_jsonl():
    never = protocol_contract()["never"]
    assert any("patches-*.jsonl" in n for n in never)


def test_tool_usage_playbook_spec_run():
    tu = tool_usage_playbook()
    assert tu["by_intent"]["implement_whole_spec"]["tool"] == "apatch_spec_run"


def test_register_mcp_resources_all_uris():
    pytest.importorskip("mcp")
    from mcp.server.fastmcp import FastMCP

    from apatch.mcp.resources import register_mcp_resources

    mcp = FastMCP("test-playbooks")
    register_mcp_resources(mcp)
    rm = getattr(mcp, "_resource_manager", None)
    if rm is None:
        pytest.skip("FastMCP resource manager API unavailable")
    uris = set(getattr(rm, "_resources", {}) or {}) | set(getattr(rm, "_templates", {}) or {})
    joined = " ".join(str(u) for u in uris)
    for fragment in (
        "runtime_hygiene",
        "sandbox_protocol",
        "spec_authoring",
        "tool_usage",
        "doc_outline",
        "playbook/diagnose",
        "playbook/index",
    ):
        assert fragment in joined
