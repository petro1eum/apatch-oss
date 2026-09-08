"""RFP-004 Phase 1 — full governed lifecycle via MutationRuntime (MCP parity)."""

import json
import os

import pytest

from apatch.runtime.runtime import MutationRuntime


def _write_probe_patch(tmp_path, logs_path):
    probe = tmp_path / ".apatch" / "probe.txt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("PROBE\n", encoding="utf-8")
    entry = {
        "step_index": 1,
        "tool_calls": [
            {
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": ".apatch/probe.txt",
                    "TargetContent": "PROBE",
                    "ReplacementContent": "PROBE_OK",
                    "AllowMultiple": True,
                },
            }
        ],
    }
    logs_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


def test_mcp_rfp004_lifecycle_without_shell(tmp_path):
    """Intent → Session → Mutation → Verification → Attestation | Rollback (probe file)."""
    logs = tmp_path / "patch.jsonl"
    _write_probe_patch(tmp_path, logs)
    bundle = tmp_path / ".apatch" / "bundle.json"
    rt = MutationRuntime(str(tmp_path))

    doctor = rt.verification_status()
    assert doctor.get("ok") is not False

    start = rt.open_session("RFP-004 MCP lifecycle test")
    assert start["ok"] is True
    assert start["invariant"]["satisfied"] is True
    assert start["session"]["intent"] == "RFP-004 MCP lifecycle test"

    plan = rt.plan(str(logs))
    assert plan.get("ok") is True

    apply_result = rt.apply(str(logs), verify=f"test -f {tmp_path / '.apatch' / 'probe.txt'}")
    assert apply_result.get("ok") is True
    assert (tmp_path / ".apatch" / "probe.txt").read_text(encoding="utf-8").strip() == "PROBE_OK"
    assert apply_result.get("invariant", {}).get("satisfied") is True

    # verify_run returns structured result (semantic may skip if no rules in tmp workspace)
    verify = rt.verify_run(semantic=True)
    assert verify.get("invariant") is not None
    assert "state_update" in verify

    exported = rt.export_attestation(str(bundle))
    assert exported["ok"] is True
    assert bundle.is_file()
    bundle_data = json.loads(bundle.read_text(encoding="utf-8"))
    assert bundle_data["session"]["session"]["intent"] == "RFP-004 MCP lifecycle test"

    tail = rt.events_tail(limit=50)
    assert tail["ok"] is True
    types = {e["type"] for e in tail["events"]}
    assert "SessionStarted" in types
    assert "MutationApplied" in types

    ended = rt.close_session()
    assert ended["ok"] is True
    assert ended["session"]["lifecycle"] == "ended"


def test_runtime_transition_hint_is_mcp_tool_name(tmp_path):
    import json

    from apatch.runtime.state_machine import OP_PLAN, assert_operation

    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "enforcement.json").write_text(json.dumps({"mode": "strict"}), encoding="utf-8")

    from apatch.runtime.errors import RuntimeTransitionError

    with pytest.raises(RuntimeTransitionError) as exc:
        assert_operation(str(tmp_path), OP_PLAN)
    assert "apatch_session_start" in (exc.value.hint or "")


def test_governed_phase_run_requires_session(tmp_path):
    """Governed phase run blocks without session when governed_mode=strict."""
    import json

    from apatch.runtime.runtime import MutationRuntime
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir(parents=True, exist_ok=True)
    (apatch_dir / "enforcement.json").write_text(
        json.dumps({"mode": "off", "governed_mode": "strict"}),
        encoding="utf-8",
    )

    src = tmp_path / "src" / "pages"
    src.mkdir(parents=True)
    page = src / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")

    rt = MutationRuntime(str(tmp_path))
    blocked = rt.phase_run(str(page), str(manifest), verify_cmd="echo ok", no_trustchain=True)
    assert blocked.get("ok") is False
    assert "apatch_session_start" in (blocked.get("hint") or "")

    start = rt.open_session("strip phase test")
    assert start["ok"] is True

    result = rt.phase_run(
        str(page),
        str(manifest),
        profile="frontend",
        out_dir=str(tmp_path / "extracted"),
        module_out_dir=str(tmp_path / "src" / "hooks"),
        to_module="hook",
        verify_cmd="echo ok",
        no_trustchain=True,
    )
    assert result.get("ok") is True, result
    assert result.get("invariant", {}).get("satisfied") is True
    assert (tmp_path / "src" / "hooks" / "usePlanningHandlers.ts").is_file()

    tail = rt.events_tail(limit=20)
    types = {e["type"] for e in tail["events"]}
    assert "MutationApplied" in types


def test_mcp_tools_include_rfp004_domain_family():
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp import server as mcp_server

    tools = getattr(mcp_server.mcp, "_tool_manager", None)
    if tools is None:
        pytest.skip("FastMCP tool manager API unavailable")
    names = set(tools._tools.keys())
    for name in (
        "apatch_session_start",
        "apatch_session_end",
        "apatch_verify_run",
        "apatch_attestation_export",
        "apatch_events_tail",
        "apatch_governed_phase_run",
    ):
        assert name in names
