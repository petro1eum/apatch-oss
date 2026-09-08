"""GC reconcile — register inferred artifacts (P1 registry cleanup)."""

from __future__ import annotations

import json
import os

from apatch.artifact_governance import (
    build_doctor_hygiene,
    gc_report,
    load_registry,
    register_inferred_artifacts,
)
from apatch.gc import run_gc


def test_register_inferred_artifacts(tmp_path):
    ws = str(tmp_path)
    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    orphan = tmp_path / "patches-orphan.jsonl"
    orphan.write_text("{}\n", encoding="utf-8")

    assert gc_report(ws)["inferred_count"] == 1
    out = register_inferred_artifacts(ws)
    assert out["registered_count"] == 1
    assert "patches-orphan.jsonl" in load_registry(ws)
    assert gc_report(ws)["inferred_count"] == 0


def test_gc_reconcile_mode(tmp_path):
    ws = str(tmp_path)
    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    (tmp_path / "patches-test.jsonl").write_text("{}\n", encoding="utf-8")

    result = run_gc(ws, mode="reconcile", dry_run=False)
    assert result["ok"]
    assert result["mode"] == "reconcile"
    assert result["registered_count"] == 1
    assert result["inferred_count"] == 0


def test_gc_reconcile_persists_without_explicit_dry_run(tmp_path):
    # Regression: MCP apatch_gc(mode='reconcile') calls run_gc WITHOUT dry_run.
    # Reconcile is an action mode and must persist the registry write, not preview.
    ws = str(tmp_path)
    (tmp_path / ".apatch").mkdir()
    (tmp_path / "patches-test.jsonl").write_text("{}\n", encoding="utf-8")

    result = run_gc(ws, mode="reconcile")  # no dry_run -> must still persist
    assert result["ok"]
    assert result["dry_run"] is False
    assert result["registered_count"] == 1
    assert result["inferred_count"] == 0
    assert gc_report(ws)["inferred_count"] == 0


def test_resolve_ephemeral_logs_path_in_spec_run(tmp_path):
    """Regression: spec_run simulate must find JSONL after generate_batch routing."""
    from apatch.artifact_governance import resolve_ephemeral_logs_path
    from apatch.session_state import save_session_state

    ws = str(tmp_path)
    os.makedirs(os.path.join(ws, ".apatch"), exist_ok=True)
    save_session_state(
        ws,
        {"session_id": "apatch_sess_test", "phase": "apply"},
    )
    abs_out, rel = resolve_ephemeral_logs_path(ws, "patches-spec-run-r1.jsonl")
    assert rel == ".apatch/tmp/apatch_sess_test/patches-spec-run-r1.jsonl"
    assert abs_out.endswith("patches-spec-run-r1.jsonl")
