from __future__ import annotations

from pathlib import Path

from apatch.path_leases import (
    acquire_path_lease,
    writer_protocol_preflight,
    writer_protocol_status,
)


def test_old_writer_protocol_fails_actionably_with_live_v2_guard(tmp_path):
    acquire_path_lease(
        str(tmp_path),
        ["apple/runtime.py"],
        tool="v2-holder",
        governed_session_id="v2-session",
    )
    status = writer_protocol_status(
        str(tmp_path),
        current_protocol=1,
        runtime_version="0.8.30",
        installed_version="0.8.34",
        enforce_runtime_match=False,
    )
    assert status["ok"] is False
    assert status["error_type"] == "MCP_WRITER_PROTOCOL_MISMATCH"
    assert status["mutation_performed"] is False
    assert status["session_started"] is False
    assert status["guard"]["infrastructure"] is True
    assert status["recommended_action"] == "restart_mcp_and_retry"
    assert "delete write_lease.json" in status["human_steps"][-1]


def test_execute_next_protocol_refusal_does_not_open_draft(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    (docs / "SPEC-PROTOCOL.md").write_text(
        "# SPEC-PROTOCOL\n"
        "> **apatch artifact:** `spec:SPEC-PROTOCOL`\n\n"
        "## R1 Change marker\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    error = writer_protocol_status(
        str(tmp_path),
        current_protocol=1,
        runtime_version="0.8.30",
        installed_version="0.8.34",
        enforce_runtime_match=False,
    )
    monkeypatch.setattr(
        "apatch.path_leases.writer_protocol_preflight",
        lambda _root: dict(error),
    )

    from apatch.spec_executor import execute_next_workspace

    result = execute_next_workspace(str(tmp_path), spec="SPEC-PROTOCOL")
    assert result["error_type"] == "MCP_WRITER_PROTOCOL_MISMATCH"
    assert result["execution_phase"] == "protocol_preflight"
    assert result["steps_completed"] == ["protocol_preflight"]
    assert not list(Path(tmp_path).rglob("session_state.json"))


def test_session_start_protocol_refusal_does_not_write_state(tmp_path, monkeypatch):
    error = writer_protocol_preflight(
        str(tmp_path),
        current_protocol=1,
        runtime_version="0.8.30",
        installed_version="0.8.34",
        enforce_runtime_match=False,
    )
    assert error is not None
    monkeypatch.setattr(
        "apatch.path_leases.writer_protocol_preflight",
        lambda _root: dict(error),
    )

    from apatch.runtime.session import start_session

    result = start_session(str(tmp_path), "must fail before draft")
    assert result["error_type"] == "MCP_WRITER_PROTOCOL_MISMATCH"
    assert not list(Path(tmp_path).rglob("session_state.json"))


def test_doctor_exposes_compact_catalog_and_internal_v2_api(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_MCP_PROFILE", "compact")
    from apatch.doctor import run_doctor

    result = run_doctor(str(tmp_path))
    protocol = result["writer_protocol"]
    assert protocol["ready"] is True
    assert protocol["writer_protocol_version"] == 2
    assert protocol["workspace_required_protocol"] == 2
    assert protocol["disjoint_concurrency"] is True
    assert protocol["path_lease_api"] == [
        "acquire_path_lease",
        "release_path_leases",
        "find_covering_lease",
        "sweep_stale_leases",
    ]
    from apatch.mcp.profiles import PROFILE_COMPACT

    assert result["mcp_profile"] == "compact"
    assert 10 <= len(PROFILE_COMPACT) <= 17
