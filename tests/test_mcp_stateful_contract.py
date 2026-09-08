from __future__ import annotations

import inspect

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")


def test_manual_stateful_compact_tools_expose_exact_capability():
    from apatch.mcp import server

    tools = server.mcp._tool_manager._tools
    for name in (
        "apatch_session_end",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_rollback",
    ):
        signature = inspect.signature(tools[name].fn)
        assert "governed_session_id" in signature.parameters, name
        assert "session_token" in signature.parameters, name
    recovery = inspect.signature(tools["apatch_recover"].fn)
    assert "governed_session_id" in recovery.parameters
