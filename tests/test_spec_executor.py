"""RFP-008 / SPEC-EXECUTOR-1 — requirement execution orchestrator."""

from __future__ import annotations

import json

import pytest

from apatch.spec_executor import (
    ERROR_SPEC_DEPENDENCY_UNMET,
    check_spec_dependencies,
    execute_next_enriched,
    execute_next_workspace,
    parse_spec_dependencies,
)
from apatch.spec import spec_status_from_entries, parse_spec

SPEC_CHILD = """# SPEC-CHILD — child spec

> **apatch artifact:** `spec:SPEC-CHILD`

## 0. Motivation

**Зависимость:** все требования [SPEC-PARENT](./SPEC-PARENT.md) в состоянии `attested`.

## R1 First child requirement

Do the thing.

(verify: python3 -c "print('child-ok')")
"""

SPEC_PARENT_OPEN = """# SPEC-PARENT — parent spec

> **apatch artifact:** `spec:SPEC-PARENT`

## R1 Parent requirement one

(verify: python3 -c "print(1)")
"""

SPEC_EXEC = """# SPEC-EXEC — executor smoke

> **apatch artifact:** `spec:SPEC-EXEC`

## R1 Change marker file

(verify: python3 -c "print('exec-ok')")
"""


def _write_spec(tmp_path, name: str, text: str):
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_spec_dependencies():
    raw = "**Зависимость:** [SPEC-PARENT](./SPEC-PARENT.md) и [SPEC-OTHER](x.md)\n"
    assert parse_spec_dependencies(raw, "SPEC-CHILD") == ["SPEC-PARENT", "SPEC-OTHER"]


def test_execute_next_discover_dry_run(tmp_path):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    out = execute_next_workspace(str(tmp_path), spec="SPEC-EXEC", dry_run=True)
    assert out["ok"] is True
    assert out["execution_phase"] == "discover"
    assert out["requirement"]["id"] == "R1"
    assert out["requirement_token"] == "SPEC-EXEC#R1"
    assert "execution_plan" in out
    assert "lint" in out["steps_completed"]


def test_execute_next_dependency_blocked(tmp_path):
    _write_spec(tmp_path, "SPEC-PARENT", SPEC_PARENT_OPEN)
    _write_spec(tmp_path, "SPEC-CHILD", SPEC_CHILD)
    out = execute_next_workspace(str(tmp_path), spec="SPEC-CHILD", dry_run=True)
    assert out["ok"] is False
    assert out["error_type"] == ERROR_SPEC_DEPENDENCY_UNMET
    assert "SPEC-PARENT" in out["error"]


def test_check_spec_dependencies_unmet(tmp_path):
    _write_spec(tmp_path, "SPEC-PARENT", SPEC_PARENT_OPEN)
    _write_spec(tmp_path, "SPEC-CHILD", SPEC_CHILD)
    child_path = tmp_path / "docs" / "specs" / "SPEC-CHILD.md"
    deps = check_spec_dependencies(str(tmp_path), "SPEC-CHILD", source_path=str(child_path))
    assert deps["ok"] is False
    assert deps["unmet"] == ["SPEC-PARENT"]


def test_execute_next_starts_session(tmp_path):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    out = execute_next_enriched(str(tmp_path), spec="SPEC-EXEC")
    assert out["ok"] is True
    assert out["execution_phase"] == "session"
    assert out["requirement_token"] == "SPEC-EXEC#R1"
    assert "session_start" in out["steps_completed"]
    assert "state_update" in out

    again = execute_next_enriched(str(tmp_path), spec="SPEC-EXEC")
    assert again["ok"] is True
    assert again.get("session_reused") is True


def test_execute_next_mutate_with_needles(tmp_path):
    f = tmp_path / "marker.txt"
    f.write_text("BEFORE\n", encoding="utf-8")
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC.replace(
        "(verify: python3 -c \"print('exec-ok')\")",
        f"(verify: test -f {f})",
    ))
    start = execute_next_enriched(str(tmp_path), spec="SPEC-EXEC")
    assert start["ok"] is True

    logs = tmp_path / "patches.jsonl"
    needles = [
        {
            "find_text": "BEFORE",
            "replace_text": "AFTER",
            "target_file": "marker.txt",
            "label": "mark",
        }
    ]
    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        needles=needles,
        logs_path=str(logs),
    )
    assert out["ok"] is True
    assert "generate_batch" in out["steps_completed"]
    assert "simulate" in out["steps_completed"]
    assert "apply_session" in out["steps_completed"]
    assert f.read_text(encoding="utf-8").strip() == "AFTER"


def test_execute_next_can_defer_apply_phase_verify(tmp_path):
    f = tmp_path / "marker.txt"
    f.write_text("BEFORE\n", encoding="utf-8")
    _write_spec(
        tmp_path,
        "SPEC-EXEC",
        SPEC_EXEC.replace(
            "(verify: python3 -c \"print('exec-ok')\")",
            "(verify: python3 -c \"raise SystemExit(1)\")",
        ),
    )

    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        needles=[
            {
                "find_text": "BEFORE",
                "replace_text": "AFTER",
                "target_file": "marker.txt",
                "label": "mark",
            }
        ],
        logs_path=str(tmp_path / "patches.jsonl"),
        defer_verify=True,
    )

    assert out["ok"] is True
    assert out["execution_phase"] == "verify"
    assert out["verify"] == 'python3 -c "raise SystemExit(1)"'
    assert f.read_text(encoding="utf-8").strip() == "AFTER"


def test_execute_next_returns_capability_when_new_session_needs_another_chunk(tmp_path):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    needles = []
    for index in range(6):
        marker = tmp_path / f"marker-{index}.txt"
        marker.write_text("BEFORE\n", encoding="utf-8")
        needles.append({
            "find_text": "BEFORE",
            "replace_text": "AFTER",
            "target_file": marker.name,
            "label": f"mark-{index}",
        })

    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        needles=needles,
    )

    assert out["ok"] is True
    assert out["continue"] is True
    capability = out["session_capability"]
    assert capability["session_id"] == out["session"]["session_id"]
    assert capability["session_token"] == out["session_token"]


def test_execute_next_resumes_authoritative_cursor_without_manual_jsonl(tmp_path):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    needles = []
    markers = []
    for index in range(6):
        marker = tmp_path / f"cursor-{index}.txt"
        marker.write_text("BEFORE\n", encoding="utf-8")
        markers.append(marker)
        needles.append({
            "find_text": "BEFORE",
            "replace_text": "AFTER",
            "target_file": marker.name,
            "label": f"cursor-{index}",
        })

    first = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        needles=needles,
    )

    assert first["ok"] is True
    assert first["continue"] is True
    assert first["progress"] == {
        "chunks_done": 1,
        "chunks_total": 2,
        "percent": 50,
    }
    assert first["state_update"]["phase"] == "apply"
    assert first["resumable_cursor"]["authoritative"] is True
    assert first["resumable_cursor"]["next_chunk_index"] == 1
    assert "logs_path" not in first["agent_next"]
    assert sum(path.read_text() == "AFTER\n" for path in markers) == 5

    resumed = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
    )

    assert resumed["ok"] is True
    assert resumed["continue"] is False
    assert resumed["resumed_from_cursor"] is True
    assert resumed["steps_completed"][-1] == "resume_apply_session"
    assert resumed["resumable_cursor"]["complete"] is True
    assert resumed["resumable_cursor"]["logs_path"] == first["logs_path"]
    assert resumed["state_update"]["phase"] == "verify"
    assert all(path.read_text() == "AFTER\n" for path in markers)




def test_execute_next_resets_completed_cursor_for_fresh_needles(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("APATCH_CANONICAL_RUNTIME", raising=False)
    monkeypatch.delenv("APATCH_MCP_BOOTSTRAPPED", raising=False)
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    marker = tmp_path / "fix-forward.txt"
    marker.write_text("BEFORE\n", encoding="utf-8")

    first = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        needles=[{
            "find_text": "BEFORE",
            "replace_text": "MIDDLE",
            "target_file": marker.name,
        }],
    )

    assert first["ok"] is True
    assert first["continue"] is False
    assert marker.read_text(encoding="utf-8") == "MIDDLE\n"
    capability = first["session_capability"]

    second = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        governed_session_id=capability["session_id"],
        session_token=capability["session_token"],
        needles=[{
            "find_text": "MIDDLE",
            "replace_text": "AFTER",
            "target_file": marker.name,
        }],
    )

    assert second["ok"] is True
    assert second.get("status") != "SESSION_ALREADY_COMPLETE"
    assert "reset_completed_apply_cursor" in second["steps_completed"]
    assert marker.read_text(encoding="utf-8") == "AFTER\n"
def test_execute_next_closes_new_session_when_generation_fails(tmp_path):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)

    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        needles=[{
            "action": "replace",
            "target_file": "docs/specs/SPEC-EXEC.md",
            "find_text": "anchor that does not exist",
            "replace_text": "never written",
        }],
    )

    assert out["ok"] is False
    assert out["session_cleanup"] == {
        "attempted": True,
        "ok": True,
        "reason": "pre_mutation_failure",
    }
    assert "session_end" in out["steps_completed"]
    from apatch.session_state import load_session_state

    assert load_session_state(str(tmp_path)).get("ended_at")


def test_execute_next_refreshes_verify_after_mutating_its_own_spec(tmp_path, monkeypatch):
    old_verify = "python3 -c \"raise SystemExit(7)\""
    new_verify = "python3 -c \"print('fresh-spec-verify')\""
    spec_text = SPEC_EXEC.replace(
        "python3 -c \"print('exec-ok')\"",
        old_verify,
    )
    _write_spec(tmp_path, "SPEC-EXEC", spec_text)

    start = execute_next_enriched(str(tmp_path), spec="SPEC-EXEC")
    assert start["ok"] is True
    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        needles=[{
            "action": "replace",
            "target_file": "docs/specs/SPEC-EXEC.md",
            "find_text": f"(verify: {old_verify})",
            "replace_text": f"(verify: {new_verify})",
        }],
    )
    assert out["ok"] is True
    assert out["verify"] == new_verify
    assert out["verify_refreshed_after_spec_mutation"] is True
    assert out["artifact_refreshed_after_spec_mutation"] is True
    assert "refresh_spec_verify" in out["steps_completed"]
    assert "refresh_spec_artifact" in out["steps_completed"]
    from apatch.session_state import load_session_state

    active = load_session_state(str(tmp_path))
    artifact = next(a for a in active["artifacts"] if a.get("kind") == "spec")
    assert artifact["content_hash"] == out["requirement"]["content_hash"]

    class FakeTC:
        def has_trustchain(self):
            return True

        def commit_action(self, tool_id, payload):
            return True

        def iter_ledger_entries(self):
            return iter([])

    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC())
    finalized = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        finalize=True,
    )
    assert finalized["ok"] is True
    assert finalized["execution_phase"] == "complete"
    assert "verify_run" in finalized["steps_completed"]
    assert "attest" in finalized["steps_completed"]
    assert "session_end" in finalized["steps_completed"]


def test_execute_next_finalize(tmp_path, monkeypatch):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    marker = tmp_path / "done.marker"
    spec_text = SPEC_EXEC.replace(
        "(verify: python3 -c \"print('exec-ok')\")",
        f"(verify: test -f {marker})",
    )
    _write_spec(tmp_path, "SPEC-EXEC", spec_text)
    execute_next_enriched(str(tmp_path), spec="SPEC-EXEC")
    marker.write_text("ok\n", encoding="utf-8")

    class FakeTC:
        def has_trustchain(self):
            return True

        def commit_action(self, tool_id, payload):
            return True

        def iter_ledger_entries(self):
            return iter([])

    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC())

    captured = {}

    def emit(_target, _session, *, completion_summary=None, **_kwargs):
        captured["completion_summary"] = completion_summary
        return None

    monkeypatch.setattr("apatch.contribution.emit_contribution", emit)
    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        finalize=True,
        completion_summary="Created the executable result.",
    )
    assert out["ok"] is True
    assert out["execution_phase"] == "complete"
    assert "verify_run" in out["steps_completed"]
    assert "attest" in out["steps_completed"]
    assert "session_end" in out["steps_completed"]
    assert captured["completion_summary"] == "Created the executable result."


def test_mcp_execute_next_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None
    assert "apatch_execute_next" in tm._tools


def test_execute_next_requires_complete_exact_capability(tmp_path):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    out = execute_next_enriched(
        str(tmp_path), spec="SPEC-EXEC", governed_session_id="apatch_sess_missing"
    )
    assert out["ok"] is False
    assert out["error_type"] == "SESSION_BINDING_REQUIRED"


def test_stale_finalize_uses_exact_selective_rebind_before_runtime_verify(tmp_path, monkeypatch):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    calls = []
    monkeypatch.setattr(
        "apatch.spec.spec_status_workspace",
        lambda *a, **k: {
            "ok": True,
            "spec": "SPEC-EXEC",
            "source_path": "docs/specs/SPEC-EXEC.md",
            "requirements": [
                {"id": "R1", "state": "stale", "stale": True, "stale_reason": "file_drift", "verify": "pytest exact-r1"},
                {"id": "R2", "state": "stale", "stale": True, "stale_reason": "spec_text_changed", "verify": "pytest r2"},
            ],
        },
    )
    monkeypatch.setattr(
        "apatch.spec_rebind.rebind_stale_requirements",
        lambda *a, **k: calls.append(k) or {"ok": True, "spec": "SPEC-EXEC", "rebound": ["R1"], "errors": []},
    )
    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        requirement="SPEC-EXEC#R1",
        finalize=True,
        check_dependencies=False,
    )
    assert out["ok"] is True
    assert calls[0]["requirement_ids"] == ["R1"]
    assert out["selective_rebind"]["rebound"] == ["R1"]


def test_stale_finalize_reports_red_selective_rebind_as_failure(tmp_path, monkeypatch):
    _write_spec(tmp_path, "SPEC-EXEC", SPEC_EXEC)
    monkeypatch.setattr(
        "apatch.spec.spec_status_workspace",
        lambda *a, **k: {
            "ok": True,
            "spec": "SPEC-EXEC",
            "source_path": "docs/specs/SPEC-EXEC.md",
            "requirements": [
                {"id": "R1", "state": "stale", "stale": True, "stale_reason": "file_drift", "verify": "pytest red"},
            ],
        },
    )
    monkeypatch.setattr(
        "apatch.spec_rebind.rebind_stale_requirements",
        lambda *a, **k: {"ok": True, "rebound": [], "skipped_red": [{"requirement": "R1"}], "errors": []},
    )

    out = execute_next_enriched(
        str(tmp_path), spec="SPEC-EXEC", requirement="SPEC-EXEC#R1", finalize=True, check_dependencies=False
    )

    assert out["ok"] is False
    assert out["error_type"] == "VERIFY_FAILED"
    assert out["execution_phase"] == "blocked"


def test_agents_template_has_section_3j():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "AGENTS.template.md").read_text(
        encoding="utf-8"
    )
    assert "### 3J." in text or "### 3J " in text
    assert "apatch_execute_next" in text
    assert "build_*_patches.py" in text


def test_execute_next_binds_the_lane_session_when_other_lanes_are_active(tmp_path, monkeypatch):
    """RFP-036: a second active lane must not make the reused/finalize steps ambiguous."""
    from apatch.lane_context import active_lane_ids, register_active_lane

    marker = tmp_path / "marker.txt"
    marker.write_text("BEFORE\n", encoding="utf-8")
    _write_spec(
        tmp_path,
        "SPEC-EXEC",
        SPEC_EXEC.replace(
            "(verify: python3 -c \"print('exec-ok')\")",
            f"(verify: test -f {marker})",
        ),
    )
    start = execute_next_enriched(str(tmp_path), spec="SPEC-EXEC")
    assert start["ok"] is True
    register_active_lane(str(tmp_path), "sibling-lane", session_id="apatch_sess_sibling")
    assert len(active_lane_ids(str(tmp_path))) == 2

    out = execute_next_enriched(
        str(tmp_path),
        spec="SPEC-EXEC",
        needles=[
            {
                "find_text": "BEFORE",
                "replace_text": "AFTER",
                "target_file": "marker.txt",
                "label": "mark",
            }
        ],
        logs_path=str(tmp_path / "patches.jsonl"),
    )
    assert out["ok"] is True, out
    assert out["session_reused"] is True
    assert marker.read_text(encoding="utf-8").strip() == "AFTER"

    class FakeTC:
        def has_trustchain(self):
            return True

        def commit_action(self, tool_id, payload):
            return True

        def iter_ledger_entries(self):
            return iter([])

    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC())
    finalized = execute_next_enriched(str(tmp_path), spec="SPEC-EXEC", finalize=True)
    assert finalized["ok"] is True, finalized
    assert finalized["execution_phase"] == "complete"
    assert "session_end" in finalized["steps_completed"]
    assert active_lane_ids(str(tmp_path)) == ["sibling-lane"]

