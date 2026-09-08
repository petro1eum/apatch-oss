"""RFP-009 / SPEC-RUN-1 — batch spec run orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apatch.spec_executor import ERROR_SPEC_DEPENDENCY_UNMET
from apatch.spec_run import (
    ERROR_MANIFEST_DRIFT,
    ERROR_MANIFEST_GAP,
    ERROR_SPEC_RUN_BLOCKED,
    clear_spec_run_state,
    lint_run_manifest_workspace,
    load_spec_run_state,
    spec_run_enriched,
    spec_run_workspace,
    _spec_run_abs,
)

SPEC_TWO = """# SPEC-TWO — two requirements

> **apatch artifact:** `spec:SPEC-TWO`

## R1 Create marker

(verify: test -f marker-r1.txt)

## R2 Update marker

(verify: grep -q AFTER marker-r1.txt)
"""

SPEC_CHILD = """# SPEC-CHILD — child

> **apatch artifact:** `spec:SPEC-CHILD`

**Зависимость:** [SPEC-PARENT](./SPEC-PARENT.md)

## R1 Child work

(verify: python3 -c "print(1)")
"""

SPEC_PARENT_OPEN = """# SPEC-PARENT — parent

> **apatch artifact:** `spec:SPEC-PARENT`

## R1 Parent

(verify: python3 -c "print(1)")
"""


def _write_spec(tmp_path, name: str, text: str):
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return p


def _manifest_two_rk(tmp_path, *, r2_needles=True):
    return {
        "schema_version": 1,
        "spec": "SPEC-TWO",
        "requirements": {
            "R1": {
                "needles": [
                    {
                        "action": "create",
                        "target_file": "marker-r1.txt",
                        "content": "BEFORE\n",
                    }
                ]
            },
            "R2": {
                "needles": (
                    [
                        {
                            "find_text": "BEFORE",
                            "replace_text": "AFTER",
                            "target_file": "marker-r1.txt",
                        }
                    ]
                    if r2_needles
                    else []
                ),
            },
        },
    }


class _FakeTrustChain:
    trustchain_dir = "/tmp/apatch-fake-trustchain"

    def has_trustchain(self):
        return True

    def commit_action(self, tool_id, payload):
        return True

    def iter_ledger_entries(self):
        return iter([])

    def begin_mutating_session(self, label="apatch_sess", **kwargs):
        return f"{label}_fake"

    def rollback_checkpoint(self, name):
        return True

    def count_signed_blocks(self):
        return 0

    def create_checkpoint(self, name):
        return True

    def ensure_head(self):
        return True


class _RecordingTrustChain(_FakeTrustChain):
    def __init__(self):
        self.entries = []

    def commit_action(self, tool_id, payload):
        record = {
            "id": f"fake-{len(self.entries) + 1}",
            "tool_id": tool_id,
            "payload": dict(payload or {}),
        }
        if tool_id == "apatch_attest":
            artifacts = list(record["payload"].get("artifacts") or [])
            for prior in reversed(self.entries):
                if prior["tool_id"] == "apatch" and not prior["payload"].get("artifacts"):
                    prior["payload"]["artifacts"] = artifacts
                    # Mirror the real helper's governed-session stamp; an
                    # unbound fake mutation must not qualify an attestation.
                    prior["payload"]["governed_session_id"] = record["payload"].get("session_id")
                    break
        self.entries.append(record)
        return True

    def iter_ledger_entries(self):
        return iter(list(self.entries))


def test_manifest_lint_valid_and_gap(tmp_path):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    good = _manifest_two_rk(tmp_path)
    out = lint_run_manifest_workspace(str(tmp_path), manifest=good)
    assert out["ok"] is True
    assert out["gaps"] == []

    verify_only = _manifest_two_rk(tmp_path, r2_needles=False)
    out_empty = lint_run_manifest_workspace(str(tmp_path), manifest=verify_only)
    assert out_empty["ok"] is True
    assert out_empty["gaps"] == []

    bad = _manifest_two_rk(tmp_path)
    del bad["requirements"]["R2"]
    out2 = lint_run_manifest_workspace(str(tmp_path), manifest=bad)
    assert out2["ok"] is False
    assert out2["error_type"] == ERROR_MANIFEST_GAP
    assert "R2" in (out2.get("gaps") or [])


def test_spec_run_dry_run_full_plan(tmp_path):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    out = spec_run_workspace(str(tmp_path), spec="SPEC-TWO", dry_run=True)
    assert out["ok"] is True
    assert out["execution_phase"] == "discover"
    assert len(out["pending"]) == 2
    assert out["pending"][0]["id"] == "R1"
    assert "execution_plan" in out
    assert "manifest_template" in out
    assert out["gaps"] == ["R1", "R2"]
    # needles_hint v2 (SPEC-NEEDLES-HINT-1): scaffold steers per-Rk execute_next and is
    # explicit that it does NOT auto-generate needles, rather than the old batch requirements= hint.
    assert "pending Rk" in out["agent_next"]
    assert "execute_next" in out["agent_next"]


def test_spec_run_inline_requirements_one_call(tmp_path, monkeypatch):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    reqs = _manifest_two_rk(tmp_path)["requirements"]
    out = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-TWO",
        requirements=reqs,
        reset=True,
        chunk_rk_per_call=0,
    )
    assert out["ok"] is True
    assert out.get("done") is True
    assert out["continue"] is False
    assert "AFTER" in (tmp_path / "marker-r1.txt").read_text(encoding="utf-8")


def test_spec_run_reruns_attested_requirement_when_skip_is_false(
    tmp_path, monkeypatch
):
    """An explicit attested rerun must reach mutations instead of the done shortcut."""
    spec = """# SPEC-RERUN — rerun an attested requirement

> **apatch artifact:** `spec:SPEC-RERUN`

## R1 Update marker again

(verify: test -f marker.txt)
"""
    _write_spec(tmp_path, "SPEC-RERUN", spec)
    fake = _RecordingTrustChain()
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: fake
    )

    first = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-RERUN",
        requirements={
            "R1": {
                "needles": [
                    {
                        "action": "create",
                        "target_file": "marker.txt",
                        "content": "BEFORE\n",
                    }
                ]
            }
        },
        reset=True,
        chunk_rk_per_call=0,
    )
    assert first["ok"] is True
    assert first.get("done") is True

    rerun = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-RERUN",
        requirements={
            "R1": {
                "needles": [
                    {
                        "action": "replace",
                        "target_file": "marker.txt",
                        "find_text": "BEFORE",
                        "replace_text": "AFTER",
                    }
                ],
            }
        },
        reset=True,
        chunk_rk_per_call=0,
    )

    assert rerun["ok"] is True
    assert rerun.get("done") is True
    assert rerun["applied"] > 0
    assert "AFTER" in (tmp_path / "marker.txt").read_text(encoding="utf-8")
    assert "skip_attested:R1" not in (rerun.get("steps_completed") or [])


def test_spec_run_attested_explicit_skip_and_satisfied_noop(tmp_path, monkeypatch):
    spec = """# SPEC-NOOP — truthful attested maintenance

> **apatch artifact:** `spec:SPEC-NOOP`

## R1 Marker

(verify: test -f marker.txt)
"""
    _write_spec(tmp_path, "SPEC-NOOP", spec)
    fake = _RecordingTrustChain()
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: fake
    )
    first = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-NOOP",
        requirements={"R1": {"needles": [{
            "action": "create", "target_file": "marker.txt", "content": "BEFORE\n",
        }]}},
        reset=True,
    )
    assert first["ok"] is True and first["applied"] > 0

    skipped = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-NOOP",
        requirements={"R1": {"skip_if_attested": True, "needles": [{
            "action": "replace", "target_file": "marker.txt",
            "find_text": "BEFORE", "replace_text": "AFTER",
        }]}},
        reset=True,
    )
    assert skipped["applied"] == 0
    assert skipped["explicitly_skipped_requirements"] == ["R1"]
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "BEFORE\n"

    satisfied = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-NOOP",
        requirements={"R1": {"needles": [{
            "action": "replace", "target_file": "marker.txt",
            "find_text": "MISSING", "replace_text": "BEFORE",
        }]}},
        reset=True,
    )
    assert satisfied["applied"] == 0
    assert satisfied["already_satisfied_requirements"] == ["R1"]


def test_already_satisfied_replace_rejects_unrelated_short_text(tmp_path):
    from apatch.spec_run import _needle_already_satisfied

    target = tmp_path / "marker.txt"
    target.write_text("prefix BEFORE suffix\n", encoding="utf-8")
    assert _needle_already_satisfied(
        str(tmp_path),
        {
            "action": "replace",
            "target_file": "marker.txt",
            "find_text": "MISSING",
            "replace_text": "BEFORE",
        },
    ) is False
    target.write_text("BEFORE\n", encoding="utf-8")
    assert _needle_already_satisfied(
        str(tmp_path),
        {
            "action": "replace",
            "target_file": "marker.txt",
            "old_content": "MISSING",
            "new_content": "BEFORE",
        },
    ) is True


def test_spec_run_state_continue_reset(tmp_path):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    manifest = _manifest_two_rk(tmp_path)
    state_path = _spec_run_abs(str(tmp_path))

    spec_run_workspace(str(tmp_path), manifest=manifest, dry_run=True)
    assert not load_spec_run_state(state_path)

    spec_run_workspace(str(tmp_path), manifest=manifest, reset=True, dry_run=True)
    clear_spec_run_state(state_path)

    mhash = lint_run_manifest_workspace(str(tmp_path), manifest=manifest)["manifest_sha256"]
    spec_run_workspace(
        str(tmp_path),
        manifest={**manifest, "spec": "SPEC-TWO"},
        reset=True,
        dry_run=False,
        chunk_rk_per_call=0,
    )
    # chunk 0 means no processing but state may be created on non-dry - actually chunk 0 processes nothing
    # Test drift instead
    spec_run_workspace(str(tmp_path), manifest=manifest, reset=True, dry_run=True)
    save_fake = {
        "spec": "SPEC-TWO",
        "manifest_sha256": "sha256:dead",
        "rk_order": ["R1", "R2"],
        "rk_index": 0,
        "per_rk": {},
        "attested": [],
    }
    from apatch.spec_run import save_spec_run_state

    save_spec_run_state(state_path, save_fake)
    drift = spec_run_workspace(str(tmp_path), manifest=manifest, resume=True)
    assert drift["ok"] is False
    assert drift["error_type"] == ERROR_MANIFEST_DRIFT


def test_spec_run_executes_two_rk_chunked(tmp_path, monkeypatch):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    manifest = _manifest_two_rk(tmp_path)

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    r1 = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-TWO",
        requirements=manifest["requirements"],
        chunk_rk_per_call=1,
        reset=True,
    )
    assert r1["ok"] is True
    assert r1["continue"] is True
    assert (tmp_path / "marker-r1.txt").read_text(encoding="utf-8").strip() == "BEFORE"
    assert r1["progress"]["rk_done"] == 1

    r2 = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-TWO",
        requirements=manifest["requirements"],
        chunk_rk_per_call=1,
    )
    assert r2["ok"] is True
    assert r2.get("done") is True
    assert r2["continue"] is False
    assert "AFTER" in (tmp_path / "marker-r1.txt").read_text(encoding="utf-8")


test_spec_run_executes_two_rk_sequence = test_spec_run_executes_two_rk_chunked


def test_spec_run_blocked_and_resume(tmp_path, monkeypatch):
    spec = SPEC_TWO.replace(
        "grep -q AFTER marker-r1.txt",
        "test -f missing-verify-file.txt",
    )
    _write_spec(tmp_path, "SPEC-TWO", spec)
    manifest = _manifest_two_rk(tmp_path)
    mpath = tmp_path / "run.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    r1 = spec_run_workspace(
        str(tmp_path), manifest_path=str(mpath), chunk_rk_per_call=1, reset=True
    )
    assert r1["ok"] is True
    assert r1["continue"] is True

    bad = spec_run_workspace(
        str(tmp_path), manifest_path=str(mpath), chunk_rk_per_call=1
    )
    assert bad["ok"] is False
    assert bad.get("requirement_token") == "SPEC-TWO#R2"
    assert load_spec_run_state(_spec_run_abs(str(tmp_path))) is not None

    spec_fixed = SPEC_TWO
    _write_spec(tmp_path, "SPEC-TWO", spec_fixed)
    (tmp_path / "marker-r1.txt").write_text("AFTER\n", encoding="utf-8")

    resume = spec_run_workspace(
        str(tmp_path), manifest_path=str(mpath), chunk_rk_per_call=1
    )
    assert resume["ok"] is True


def test_spec_run_surfaces_verify_failure_diagnostics(tmp_path, monkeypatch):
    spec = """# SPEC-DIAG — diagnostics

> **apatch artifact:** `spec:SPEC-DIAG`

## R1 Failing verification

(verify: python3 -m pytest tests/test_example.py -q)
"""
    _write_spec(tmp_path, "SPEC-DIAG", spec)

    def fail_requirement(*_args, **_kwargs):
        return {
            "ok": False,
            "error": "pytest failed",
            "error_type": "VERIFY_FAILED",
            "recommended_action": "fix_forward",
            "verify_rollback": True,
            "rollback_performed": True,
            "verify_output": "FAILED tests/test_example.py::test_case - AssertionError",
            "diagnostics": [{"kind": "pytest", "message": "AssertionError"}],
            "diagnostic_count": 1,
            "diagnostics_artifact": ".apatch/diagnostics/session.json",
            "chunk_result": {"verify_rollback": True, "rollback_performed": True},
            "checkpoint": "apatch_sess_chunk",
            "requirement_token": "SPEC-DIAG#R1",
            "steps_completed": ["apply_session"],
        }

    monkeypatch.setattr("apatch.spec_run._run_one_requirement", fail_requirement)
    out = spec_run_workspace(
        str(tmp_path),
        spec="SPEC-DIAG",
        requirements={"R1": {"needles": []}},
        reset=True,
    )

    assert out["ok"] is False
    assert out["error_type"] == "VERIFY_FAILED"
    assert out["recommended_action"] == "fix_forward"
    assert out["rollback_performed"] is True
    assert out["diagnostic_count"] == 1
    assert out["diagnostics"][0]["kind"] == "pytest"
    assert "FAILED tests/test_example.py" in out["verify_output"]
    assert "apatch_resume_session" in out["resume_hint"]
    assert "apatch_rollback" not in out["resume_hint"]


def test_spec_run_dependency_blocked(tmp_path):
    _write_spec(tmp_path, "SPEC-PARENT", SPEC_PARENT_OPEN)
    _write_spec(tmp_path, "SPEC-CHILD", SPEC_CHILD)
    manifest = {
        "schema_version": 1,
        "spec": "SPEC-CHILD",
        "requirements": {"R1": {"needles": [{"action": "create", "target_file": "x.txt", "content": "a"}]}},
    }
    out = spec_run_workspace(str(tmp_path), manifest=manifest, dry_run=True)
    assert out["ok"] is False
    assert out["error_type"] == ERROR_SPEC_DEPENDENCY_UNMET


def test_mcp_spec_run_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None
    assert "apatch_spec_run" in tm._tools
    assert "apatch_spec_run_manifest_lint" in tm._tools


def test_agents_template_has_section_3k():
    text = (Path(__file__).parent.parent / "docs" / "AGENTS.template.md").read_text(
        encoding="utf-8"
    )
    assert "### 3K." in text or "### 3K " in text
    assert "apatch_spec_run" in text
    assert "attestation.md" in text
    assert "целевом артефакте" in text or "target artifact" in text.lower()


def test_spec_run_uses_manifest_verify_override(tmp_path, monkeypatch):
    spec = """# SPEC-VERIFY-OVERRIDE — use run-specific verification

> **apatch artifact:** `spec:SPEC-VERIFY-OVERRIDE`

## R1 Create marker

(verify: test -f impossible.txt)
"""
    _write_spec(tmp_path, "SPEC-VERIFY-OVERRIDE", spec)
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    captured = {}

    def emit(_target, _session, *, completion_summary=None, **_kwargs):
        captured["completion_summary"] = completion_summary
        return None

    monkeypatch.setattr("apatch.contribution.emit_contribution", emit)
    out = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-VERIFY-OVERRIDE",
        requirements={
            "R1": {
                "completion_summary": "Created the verified marker.",
                "verify_override": "test -f marker.txt",
                "needles": [
                    {
                        "action": "create",
                        "target_file": "marker.txt",
                        "content": "ok\n",
                    }
                ],
            }
        },
        reset=True,
        chunk_rk_per_call=0,
    )

    assert out["ok"] is True
    assert out.get("done") is True
    assert captured["completion_summary"] == "Created the verified marker."
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "ok\n"


def test_spec_run_verify_only_empty_needles_attests_when_green(tmp_path, monkeypatch):
    """Explicit needles=[] means verify-only, not a manifest gap."""
    spec = """# SPEC-VERIFY-ONLY — verify-only attestation

> **apatch artifact:** `spec:SPEC-VERIFY-ONLY`

## R1 Ready marker

(verify: test -f ready.txt)
"""
    _write_spec(tmp_path, "SPEC-VERIFY-ONLY", spec)
    (tmp_path / "ready.txt").write_text("ok\n", encoding="utf-8")

    fake = _RecordingTrustChain()
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: fake
    )

    out = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-VERIFY-ONLY",
        requirements={"R1": {"needles": []}},
        reset=True,
        chunk_rk_per_call=0,
    )
    assert out["ok"] is True
    assert out.get("done") is True
    assert out["continue"] is False
    last = out.get("last_rk_result") or {}
    assert last.get("skipped_mutations") is True
    assert last.get("requirement_attested") is True
    assert "noop_attest" in (last.get("steps_completed") or [])
    assert "verify_precheck_passed" in (out.get("steps_completed") or [])
    status = out.get("spec_status") or {}
    assert status.get("done") is True
    assert (status.get("summary") or {}).get("attested") == 1
    assert (status.get("requirements") or [{}])[0].get("state") == "attested"


def test_spec_run_verify_only_empty_needles_blocks_when_red(tmp_path, monkeypatch):
    """A verify-only Rk must not attest a red requirement."""
    spec = """# SPEC-VERIFY-RED — verify-only attestation

> **apatch artifact:** `spec:SPEC-VERIFY-RED`

## R1 Missing marker

(verify: test -f missing.txt)
"""
    _write_spec(tmp_path, "SPEC-VERIFY-RED", spec)

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    out = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-VERIFY-RED",
        requirements={"R1": {"needles": []}},
        reset=True,
        chunk_rk_per_call=0,
    )
    assert out["ok"] is False
    assert out["error_type"] == ERROR_SPEC_RUN_BLOCKED
    assert out["requirement_token"] == "SPEC-VERIFY-RED#R1"
    assert "verify_precheck_failed" in (out.get("steps_completed") or [])


def test_spec_run_verify_precheck_does_not_skip_when_needles_set(tmp_path, monkeypatch):
    """Doc-only Rk: green verify must not skip mutations when needles are provided."""
    spec = """# SPEC-DOC — doc-only attestation

> **apatch artifact:** `spec:SPEC-DOC`

## R1 Playbook exists

(verify: test -f docs/playbook.md)
"""
    _write_spec(tmp_path, "SPEC-DOC", spec)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "playbook.md").write_text("# Playbook\n", encoding="utf-8")

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    reqs = {
        "R1": {
            "needles": [
                {
                    "action": "replace",
                    "target_file": "docs/playbook.md",
                    "find_text": "# Playbook",
                    "replace_text": "# Playbook (attested)",
                }
            ]
        }
    }
    out = spec_run_enriched(
        str(tmp_path),
        spec="SPEC-DOC",
        requirements=reqs,
        reset=True,
        chunk_rk_per_call=0,
    )
    assert out["ok"] is True
    assert out.get("done") is True
    text = (tmp_path / "docs" / "playbook.md").read_text(encoding="utf-8")
    assert "(attested)" in text
    assert "verify_precheck_passed" not in (out.get("steps_completed") or [])


def test_spec_run_completes_when_another_lane_is_active(tmp_path, monkeypatch):
    """RFP-036: spec_run pins every internal step to its own lane session.

    The MCP server binds the SPEC lane from the tool arguments; a sibling lane
    that stays active in the registry must not make the cycle ambiguous.
    """
    from apatch.lane_context import active_lane_ids, bind_lane_from_kwargs, register_active_lane

    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )
    register_active_lane(str(tmp_path), "sibling-lane", session_id="apatch_sess_sibling")
    bind_lane_from_kwargs({"target_dir": str(tmp_path), "spec": "SPEC-TWO"})
    try:
        out = spec_run_enriched(
            str(tmp_path),
            spec="SPEC-TWO",
            requirements=_manifest_two_rk(tmp_path)["requirements"],
            reset=True,
            chunk_rk_per_call=0,
        )
    finally:
        bind_lane_from_kwargs({})
    assert out["ok"] is True, out
    assert out.get("done") is True
    assert "AFTER" in (tmp_path / "marker-r1.txt").read_text(encoding="utf-8")
    assert (tmp_path / ".apatch" / "lanes" / "SPEC-TWO" / "session_state.json").is_file()
    assert active_lane_ids(str(tmp_path)) == ["sibling-lane"]

