"""RFP-019 Level 1 — profiles, write-behind, hygiene, resources."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from apatch.mcp.hygiene import run_mcp_hygiene
from apatch.mcp.profiles import PROFILE_CORE, allowed_tools, mcp_profile_name
from apatch.session_state import session_state_changed

pytest.importorskip("mcp", reason="mcp extra not installed")


def test_session_state_changed_ignores_last_tool_within_phase():
    prev = {"phase": "idle", "last_tool": "apatch_doctor", "next_action": "x"}
    new = dict(prev)
    new["last_tool"] = "apatch_plan"
    assert session_state_changed(prev, new) is False
    new["phase"] = "plan"
    assert session_state_changed(prev, new) is True


def test_session_state_changed_on_checkpoint():
    prev = {"phase": "apply", "checkpoint": "sess_a"}
    new = {"phase": "apply", "checkpoint": "sess_b"}
    assert session_state_changed(prev, new) is True


def test_mcp_profile_core_subset():
    with mock.patch.dict(os.environ, {"APATCH_MCP_PROFILE": "core"}, clear=False):
        allowed = allowed_tools()
    assert allowed is not None
    assert "apatch_doctor" in allowed
    assert "apatch_mcp_hygiene" in allowed
    assert "apatch_spec_run" in allowed
    assert len(allowed) == len(PROFILE_CORE)


def test_mcp_hygiene_returns_process_report(tmp_path):
    out = run_mcp_hygiene(str(tmp_path))
    assert out["ok"] is True
    assert "healthy" in out
    assert "process_count" in out
    assert "ghost_count" in out
    assert "current_pid" in out
    assert out["workspace"] == str(tmp_path.resolve())


def test_register_mcp_resources_attaches_handlers():
    from mcp.server.fastmcp import FastMCP

    from apatch.mcp.resources import register_mcp_resources

    mcp = FastMCP("test-scale-l1")
    register_mcp_resources(mcp)
    rm = getattr(mcp, "_resource_manager", None)
    if rm is None:
        pytest.skip("FastMCP resource manager API unavailable")
    resources = getattr(rm, "_resources", {}) or {}
    uris = set(resources.keys()) if resources else set()
    if not uris:
        templates = getattr(rm, "_templates", {}) or {}
        uris = set(templates.keys())
    assert "apatch://playbook/protocol_contract" in uris or any(
        "protocol_contract" in str(u) for u in uris
    )
    joined = " ".join(str(u) for u in uris)
    assert "runtime_hygiene" in joined


def test_recommended_mcp_block_sets_profile_compact():
    from apatch.mcp_health import recommended_mcp_server_block

    env = recommended_mcp_server_block().get("env") or {}
    assert env.get("APATCH_MCP_PROFILE") == "compact"
