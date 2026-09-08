"""SPEC-HYGIENE-2 — GC report CLI & MCP."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

pytest.importorskip("mcp", reason="mcp extra not installed")


def test_gc_cli_dry_run_default(tmp_path):
    (tmp_path / "patches-test.jsonl").write_text("{}\n", encoding="utf-8")
    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["gc", "--target-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "degraded" in result.output or "clean" in result.output
    assert "dry-run" in result.output.lower() or "reclaimable" in result.output.lower()


def test_gc_cli_json(tmp_path):
    (tmp_path / "patches-foo.jsonl").write_text("{}\n", encoding="utf-8")
    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["gc", "--target-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "classified" in payload
    assert "issues" in payload
    assert payload["recommendation"] == "apatch gc --dry-run"


def test_gc_cli_safe_runs(tmp_path):
    from apatch.artifact_governance import register_on_write
    from apatch.cli import cli

    ws = str(tmp_path)
    register_on_write(
        ws,
        "patches-cli-safe.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_test",
        reason="cli safe fixture",
    )
    (tmp_path / "patches-cli-safe.jsonl").write_text("{}", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli, ["gc", "--target-dir", ws, "--safe", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload.get("deleted_count", 0) >= 1


def test_mcp_gc_report(tmp_path):
    from apatch.mcp.server import apatch_gc

    (tmp_path / "patches-mcp.jsonl").write_text("{}\n", encoding="utf-8")
    out = apatch_gc(target_dir=str(tmp_path), mode="report")
    assert out["ok"] is True
    assert out["classified"]["EPHEMERAL"] >= 1
    assert out["state_update"] is not None
    assert out["recommendation"] == "apatch gc --dry-run"


def test_mcp_gc_safe_rotate(tmp_path):
    from apatch.artifact_governance import register_on_write
    from apatch.mcp.server import apatch_gc

    ws = str(tmp_path)
    register_on_write(
        ws,
        "patches-mcp-safe.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_test",
        reason="mcp safe fixture",
    )
    (tmp_path / "patches-mcp-safe.jsonl").write_text("{}", encoding="utf-8")

    safe = apatch_gc(target_dir=ws, mode="safe")
    assert safe["ok"] is True
    assert safe["deleted_count"] >= 1
    assert safe["reclaimed_bytes"] >= 0
    assert safe["state_update"] is not None

    register_on_write(
        ws,
        "patches-mcp-rotate.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_test",
        reason="mcp rotate fixture",
    )
    (tmp_path / "patches-mcp-rotate.jsonl").write_text("{}", encoding="utf-8")
    rotate = apatch_gc(target_dir=ws, mode="rotate")
    assert rotate["ok"] is True
    assert rotate["deleted_count"] >= 1
    assert rotate["state_update"] is not None


def test_gc_cli_mcp_parity(tmp_path):
    from apatch.cli import cli
    from apatch.gc import run_gc
    from apatch.mcp.server import apatch_gc

    (tmp_path / "patches-parity.jsonl").write_text("{}\n", encoding="utf-8")
    ws = str(tmp_path)

    runner = CliRunner()
    cli_result = runner.invoke(cli, ["gc", "--target-dir", ws, "--json"])
    cli_payload = json.loads(cli_result.output)

    mcp_payload = apatch_gc(target_dir=ws, mode="report")
    assert cli_payload["classified"] == mcp_payload["classified"]
    assert cli_payload["issues"] == mcp_payload["issues"]


def test_gc_report_zero_unclassified_apatch_writes(tmp_path):
    from apatch.artifact_governance import gc_report, register_on_write
    from apatch.session_state import save_session_state

    ws = str(tmp_path)
    save_session_state(ws, {"phase": "idle"})
    register_on_write(
        ws,
        "patches.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_generate",
        reason="fixture",
    )
    (tmp_path / "patches.jsonl").write_text("{}\n", encoding="utf-8")

    report = gc_report(ws)
    assert report["classified"].get("UNCLASSIFIED", 0) == 0
    assert report["inferred_count"] == 0
    assert report["status"] == "clean"


def test_gc_protects_control_plane_files_and_atomic_locks(tmp_path):
    from apatch.artifact_governance import (
        ArtifactClass,
        collect_gc_safe_candidates,
        collect_registered_orphan_ephemeral,
        infer_class_for_path,
        load_registry,
        register_inferred_artifacts,
        register_on_write,
    )

    ws = str(tmp_path)
    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    lock_rel = ".apatch/session_state.json.lock"
    (tmp_path / lock_rel).write_text("", encoding="utf-8")
    register_on_write(
        ws,
        lock_rel,
        class_name="EPHEMERAL",
        created_by_tool="legacy_reconcile",
        reason="legacy lock misclassification",
        gc_allowed=True,
    )

    reality_rel = ".apatch/reality.jsonl"
    (tmp_path / reality_rel).write_text("{}\n", encoding="utf-8")
    register_on_write(
        ws,
        reality_rel,
        class_name="EPHEMERAL",
        created_by_tool="legacy_reconcile",
        reason="legacy reality misclassification",
        gc_allowed=True,
    )

    assert infer_class_for_path(reality_rel) == ArtifactClass.LEDGER.value
    assert infer_class_for_path(".apatch/conformance.json") == ArtifactClass.STATE.value
    assert infer_class_for_path(".apatch/remote.json") == ArtifactClass.STATE.value
    assert lock_rel not in collect_registered_orphan_ephemeral(ws)

    delete, skipped, _ = collect_gc_safe_candidates(ws)
    assert lock_rel not in delete
    assert reality_rel not in delete
    assert lock_rel in skipped
    assert reality_rel in skipped

    result = register_inferred_artifacts(ws)
    assert result["reclassified"] == [reality_rel]
    reality = load_registry(ws)[reality_rel]
    assert reality.class_name == ArtifactClass.LEDGER.value
    assert reality.gc_allowed is False
    assert reality.replay_critical is True


def test_gc_report_classifies_legacy_apatch_artifacts(tmp_path):
    from apatch.artifact_governance import gc_report

    fixtures = [
        ".apatch/specs/SPEC-FOO.json",
        ".apatch/diagnostics/apatch_sess_123.json",
        ".apatch/staging/diagnostics/schema.py",
        ".apatch/mcp.json",
        ".apatch/mcp_tool_fingerprint.json",
        ".apatch/notarized_index.json",
        ".apatch/replay_log.json",
        ".apatch/mcp_apply_report.json",
        ".apatch/report.html",
        ".apatch/spec-run-ledger-actor-manifest.json",
        ".apatch/spec-run-ledger-actor-reattest.json",
        ".apatch/spec_run_ux_requirements.json",
        ".apatch/spec-verify-session-id-needles.json",
        ".apatch/build_verify_session_id_needles.py",
        ".apatch/linkfix.jsonl",
    ]
    for rel in fixtures:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    report = gc_report(str(tmp_path))
    assert report["classified"].get("UNCLASSIFIED", 0) == 0
    assert report["inferred_count"] == len(fixtures)


def test_gc_safe_respects_gc_allowed(tmp_path):
    from apatch.artifact_governance import gc_safe, register_on_write

    ws = str(tmp_path)
    register_on_write(
        ws,
        "patches-del.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_test",
        reason="deletable fixture",
    )
    (tmp_path / "patches-del.jsonl").write_text("{}", encoding="utf-8")
    register_on_write(
        ws,
        "patches-keep.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_test",
        reason="blocked fixture",
        gc_allowed=False,
    )
    (tmp_path / "patches-keep.jsonl").write_text("{}", encoding="utf-8")

    result = gc_safe(ws)
    assert result["ok"] is True
    assert "patches-del.jsonl" in result["deleted"]
    assert "patches-keep.jsonl" not in result["deleted"]
    assert not (tmp_path / "patches-del.jsonl").exists()
    assert (tmp_path / "patches-keep.jsonl").exists()


def test_gc_reconcile_releases_finished_lane_run_state(tmp_path):
    from apatch.artifact_governance import (
        gc_safe,
        load_registry,
        register_inferred_artifacts,
        register_on_write,
    )

    ws = str(tmp_path)
    lane = tmp_path / ".apatch" / "lanes" / "finished"
    lane.mkdir(parents=True)
    (lane / "session_state.json").write_text(
        json.dumps(
            {
                "session_id": "apatch_sess_finished",
                "ended_at": "2026-07-27T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    apply_state = lane / "apply_session.json"
    apply_state.write_text(json.dumps({"chunk_index": 1, "chunks": [[1], [2]]}), encoding="utf-8")
    rel = ".apatch/lanes/finished/apply_session.json"
    register_on_write(
        ws,
        rel,
        class_name="RUN_STATE",
        created_by_tool="legacy_apply_session",
        reason="legacy run-state fixture",
        run_lease_id="lease_stale",
        gc_allowed=False,
        replay_critical=True,
    )

    result = register_inferred_artifacts(ws)

    assert result["reconciled_run_states"] == [rel]
    entry = load_registry(ws)[rel]
    assert entry.run_lease_id is None
    assert entry.gc_allowed is True
    assert entry.lineage["governed_session_id"] == "apatch_sess_finished"
    assert rel in gc_safe(ws)["deleted"]
    assert not apply_state.exists()


def test_gc_safe_static_replay_block(tmp_path):
    from apatch.artifact_governance import gc_safe, register_on_write

    ws = str(tmp_path)
    apatch = tmp_path / ".apatch"
    apatch.mkdir(parents=True)
    events = apatch / "events.jsonl"
    events.write_text("{}", encoding="utf-8")
    register_on_write(
        ws,
        ".apatch/events.jsonl",
        class_name="LEDGER",
        created_by_tool="apatch_test",
        reason="decoy registry lie",
        gc_allowed=True,
        replay_critical=False,
    )
    result = gc_safe(ws)
    assert ".apatch/events.jsonl" not in result["deleted"]
    assert events.exists()


def test_gc_rotate_history_debug(tmp_path):
    import os
    import time

    from apatch.artifact_governance import gc_report, gc_rotate, register_on_write

    ws = str(tmp_path)
    backups = tmp_path / ".apatch" / "backups"
    backups.mkdir(parents=True)
    for i in range(52):
        rel = f".apatch/backups/chunk_{i}.bak"
        path = tmp_path / rel
        path.write_text("x", encoding="utf-8")
        os.utime(path, (float(i), float(i)))
        register_on_write(
            ws,
            rel,
            class_name="HISTORY",
            created_by_tool="apatch_test",
            reason="backup fixture",
        )

    debug = tmp_path / ".apatch" / "mcp_stderr.log"
    debug.write_text("log line", encoding="utf-8")
    old = time.time() - (8 * 24 * 3600)
    os.utime(debug, (old, old))

    result = gc_rotate(ws)
    assert result["ok"] is True
    assert len(list(backups.iterdir())) <= 50

    report = gc_report(ws)
    assert report["classified"]["HISTORY"] <= 50
    assert not any(issue["type"] == "HISTORY_OVERFLOW" for issue in report["issues"])
    assert not debug.exists()
    assert result["deleted_count"] >= 3


def test_doctor_hygiene_compacts_operational_debt_and_returns_repairs(tmp_path):
    from apatch.artifact_governance import build_doctor_hygiene, register_on_write

    ws = str(tmp_path)
    for index in range(55):
        rel = f".apatch/backups/old-{index:03d}.bak"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        register_on_write(
            ws,
            rel,
            class_name="HISTORY",
            created_by_tool="test",
            reason="history overflow fixture",
        )
    (tmp_path / "patches-inferred.jsonl").write_text("{}\n", encoding="utf-8")
    registered_orphan = tmp_path / ".apatch" / "tmp" / "ended" / "patches.jsonl"
    registered_orphan.parent.mkdir(parents=True, exist_ok=True)
    registered_orphan.write_text("{}\n", encoding="utf-8")
    register_on_write(
        ws,
        ".apatch/tmp/ended/patches.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="test",
        reason="ended session fixture",
        gc_allowed=True,
        replay_critical=False,
    )

    hygiene = build_doctor_hygiene(ws)

    by_type = {issue["type"]: issue for issue in hygiene["issues"]}
    assert {"HISTORY_OVERFLOW", "INFERRED_ARTIFACTS", "ORPHAN_EPHEMERAL"} <= set(by_type)
    assert len([i for i in hygiene["issues"] if i["type"] == "ORPHAN_EPHEMERAL"]) == 1
    assert all(len(issue.get("paths") or []) <= 10 for issue in hygiene["issues"])
    actions = {row["issue_type"]: row["calls"] for row in hygiene["repair_actions"]}
    assert actions["HISTORY_OVERFLOW"] == [{"tool": "apatch_gc", "mode": "rotate"}]
    assert actions["INFERRED_ARTIFACTS"] == [{"tool": "apatch_gc", "mode": "reconcile"}]
    assert actions["ORPHAN_EPHEMERAL"][-1] == {"tool": "apatch_gc", "mode": "safe"}
    assert len(json.dumps(hygiene)) < 8000


def test_inference_sunset_auto_reconcile(tmp_path):
    from apatch.artifact_governance import (
        assert_governed_hygiene_allowed,
        save_governed_ops_counter,
    )
    from apatch.doctor import run_doctor

    ws = str(tmp_path)
    (tmp_path / "patches-inferred.jsonl").write_text("{}", encoding="utf-8")
    save_governed_ops_counter(ws, 100)

    assert_governed_hygiene_allowed(ws)

    doc = run_doctor(ws)
    assert doc["hygiene"]["inferred_count"] == 0
    assert doc["hygiene"]["status"] != "critical"


def test_inference_sunset_blocks_when_reconcile_fails(tmp_path, monkeypatch):
    from unittest import mock

    from apatch.artifact_governance import (
        assert_governed_hygiene_allowed,
        save_governed_ops_counter,
    )
    from apatch.doctor import run_doctor
    from apatch.runtime.errors import HygieneCriticalError
    from apatch.runtime.runtime import MutationRuntime

    ws = str(tmp_path)
    (tmp_path / "patches-inferred.jsonl").write_text("{}", encoding="utf-8")
    save_governed_ops_counter(ws, 100)

    with mock.patch(
        "apatch.artifact_governance.register_inferred_artifacts",
        return_value={"ok": True, "registered_count": 0, "registered": []},
    ):
        with pytest.raises(HygieneCriticalError):
            assert_governed_hygiene_allowed(ws)

    doc = run_doctor(ws)
    assert doc["hygiene"]["status"] == "critical"

    patches = tmp_path / "patches-empty.jsonl"
    patches.write_text("", encoding="utf-8")
    with mock.patch(
        "apatch.artifact_governance.register_inferred_artifacts",
        return_value={"ok": True, "registered_count": 0, "registered": []},
    ):
        result = MutationRuntime(ws).apply_session(str(patches))
    assert result["ok"] is False
    assert result.get("error_type") == "HYGIENE_CRITICAL"
