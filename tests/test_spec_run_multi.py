"""SPEC-INTERFERENCE-3 R6/R7 — apatch_spec_run_multi."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from apatch.spec_registry import update_spec_registry

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "cross_verify"
MODULE_REL = "shared/module.py"


class _FakeTrustChain:
    trustchain_dir = "/tmp/apatch-fake-trustchain"
    _counter = 0

    def has_trustchain(self):
        return True

    def commit_action(self, tool_id, payload):
        return True

    def iter_ledger_entries(self):
        return iter([])

    def begin_mutating_session(self, label="apatch_sess", **kwargs):
        type(self)._counter += 1
        return f"{label}_fake_{type(self)._counter}"

    def rollback_checkpoint(self, name):
        return True

    def count_signed_blocks(self):
        return 0

    def create_checkpoint(self, name):
        return True

    def ensure_head(self):
        return True


def _copy_fixture_tree(tmp_path: Path) -> Path:
    dest = tmp_path / "cv"
    shutil.copytree(FIXTURE_ROOT, dest, ignore=shutil.ignore_patterns(".apatch"))
    reg_src = FIXTURE_ROOT / "registry"
    if reg_src.is_dir():
        reg_dest = dest / ".apatch" / "specs"
        reg_dest.mkdir(parents=True, exist_ok=True)
        for f in reg_src.glob("*.json"):
            shutil.copy2(f, reg_dest / f.name)
    return dest


def _setup_two_independent_specs(tmp_path: Path) -> None:
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    for sid, fname in [("SPEC-M1", "m1.txt"), ("SPEC-M2", "m2.txt")]:
        (docs / f"{sid}.md").write_text(
            f"# {sid}\n> **apatch artifact:** `spec:{sid}`\n## R1 create\n(verify: test -f {fname})\n",
            encoding="utf-8",
        )
        update_spec_registry(
            str(tmp_path),
            sid,
            {
                "requirements": {
                    "R1": {
                        "needles": [
                            {
                                "action": "create",
                                "target_file": fname,
                                "content": f"{sid}\n",
                            }
                        ]
                    }
                }
            },
        )


def test_shared_maintenance_failure_result_is_json_serializable(tmp_path, monkeypatch):
    from apatch.spec_run_multi import spec_run_multi_workspace
    import apatch.shared_maintenance as maintenance
    import apatch.spec_interference as interference

    schedule = {
        "ok": True,
        "schedulable": True,
        "order": ["SPEC-A", "SPEC-B"],
        "safe_order": ["SPEC-A", "SPEC-B"],
    }
    monkeypatch.setattr(
        interference,
        "spec_schedule_workspace",
        lambda *args, **kwargs: schedule,
    )
    monkeypatch.setattr(
        maintenance,
        "shared_maintenance_workspace",
        lambda *args, **kwargs: {
            "ok": False,
            "error_type": "SPEC_SHARED_MAINTENANCE_INVALID",
            "error": "preflight rejected",
        },
    )

    result = spec_run_multi_workspace(
        str(tmp_path),
        specs=["SPEC-A", "SPEC-B"],
        requirements={"SPEC-A": {}, "SPEC-B": {}},
        execution_mode="shared_maintenance",
    )

    assert result["ok"] is False
    assert result["steps"][-1]["result"] is not result
    json.dumps(result)


def test_shared_maintenance_preflight_partitions_files_and_rejects_shared_target(tmp_path):
    from apatch.shared_maintenance import (
        ERROR_PLAN,
        ERROR_SHARED_FILE,
        prepare_shared_maintenance,
    )

    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    for sid in ("SPEC-A", "SPEC-B"):
        (docs / f"{sid}.md").write_text(
            f"# {sid}\n"
            f"> **apatch artifact:** `spec:{sid}`\n"
            "## R1 mapping\n"
            "(verify: true)\n",
            encoding="utf-8",
        )
    requirements = {
        "SPEC-A": {"R1": {"needles": [{
            "action": "replace", "target_file": "categories/a/query.py",
            "find_text": "old", "replace_text": "new",
        }]}},
        "SPEC-B": {"R1": {"needles": [{
            "action": "replace", "target_file": "categories/b/query.py",
            "find_text": "old", "replace_text": "new",
        }]}},
    }
    prepared = prepare_shared_maintenance(
        str(tmp_path), specs=["SPEC-A", "SPEC-B"], requirements=requirements,
    )
    assert prepared["ok"] is True
    assert prepared["artifact_files"] == {
        "spec:SPEC-A#R1": ["categories/a/query.py"],
        "spec:SPEC-B#R1": ["categories/b/query.py"],
    }

    target = requirements["SPEC-A"]["R1"]["needles"][0]
    target["target_file"] = "docs/specs/slug_contracts/a.yaml"
    slug_contract = prepare_shared_maintenance(
        str(tmp_path), specs=["SPEC-A", "SPEC-B"], requirements=requirements,
    )
    assert slug_contract["ok"] is True
    assert slug_contract["artifact_files"]["spec:SPEC-A#R1"] == [
        "docs/specs/slug_contracts/a.yaml",
    ]

    for unsafe_target in (
        "docs/specs/SPEC-A.md",
        "docs/specs/slug_contracts//a.yaml",
        "docs/specs/slug_contracts/../SPEC-A.yaml",
        "docs/specs/slug_contracts/.hidden.yaml",
        "docs/specs/slug_contracts/a.YAML",
    ):
        target["target_file"] = unsafe_target
        rejected = prepare_shared_maintenance(
            str(tmp_path), specs=["SPEC-A", "SPEC-B"], requirements=requirements,
        )
        assert rejected["ok"] is False
        assert rejected["error_type"] == ERROR_PLAN

    target["target_file"] = "categories/a/query.py"
    requirements["SPEC-A"]["R1"]["skip_if_attested"] = True
    explicit_skip = prepare_shared_maintenance(
        str(tmp_path), specs=["SPEC-A", "SPEC-B"], requirements=requirements,
    )
    assert explicit_skip["ok"] is False
    assert explicit_skip["error_type"] == "SPEC_SHARED_MAINTENANCE_INVALID"
    requirements["SPEC-A"]["R1"].pop("skip_if_attested")
    requirements["SPEC-B"]["R1"]["needles"][0]["target_file"] = "categories/a/query.py"
    collision = prepare_shared_maintenance(
        str(tmp_path), specs=["SPEC-A", "SPEC-B"], requirements=requirements,
    )
    assert collision["ok"] is False
    assert collision["error_type"] == ERROR_SHARED_FILE


def test_run_multi_explicit_requirements_are_bounded_work_lists(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    requirements = {}
    for sid, prefix in (("SPEC-M1", "m1"), ("SPEC-M2", "m2")):
        (docs / f"{sid}.md").write_text(
            f"# {sid}\n"
            f"> **apatch artifact:** `spec:{sid}`\n"
            f"## R1 deferred\n(verify: test -f {prefix}-deferred.txt)\n"
            f"## R2 selected\n(verify: test -f {prefix}-selected.txt)\n",
            encoding="utf-8",
        )
        requirements[sid] = {
            "R2": {
                "needles": [{
                    "action": "create",
                    "target_file": f"{prefix}-selected.txt",
                    "content": f"{sid}\n",
                }],
            },
        }

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    from apatch.spec_run_multi import spec_run_multi_workspace

    out = spec_run_multi_workspace(
        str(tmp_path),
        specs=["SPEC-M1", "SPEC-M2"],
        requirements=requirements,
        re_interference=False,
    )

    assert out["ok"] is True
    assert out["completed_specs"] == ["SPEC-M1", "SPEC-M2"]
    assert (tmp_path / "m1-selected.txt").is_file()
    assert (tmp_path / "m2-selected.txt").is_file()
    assert not (tmp_path / "m1-deferred.txt").exists()
    assert not (tmp_path / "m2-deferred.txt").exists()
    spec_steps = [step for step in out["steps"] if step["step"] == "spec_run"]
    assert [step["result"]["skipped_pending"] for step in spec_steps] == [["R1"], ["R1"]]
    assert out["applied"] == 2


def test_run_spec_to_completion_accumulates_apply_and_noop_outcomes(tmp_path, monkeypatch):
    from apatch.spec_run_multi import _run_spec_to_completion

    ticks = iter([
        {
            "ok": True,
            "continue": True,
            "applied": 2,
            "already_satisfied_requirements": ["R1"],
        },
        {
            "ok": True,
            "continue": False,
            "applied": 0,
            "already_satisfied_requirements": ["R2"],
            "explicitly_skipped_requirements": ["R3"],
        },
    ])
    monkeypatch.setattr(
        "apatch.spec_run.spec_run_workspace",
        lambda *_a, **_k: next(ticks),
    )

    out = _run_spec_to_completion(
        str(tmp_path),
        "SPEC-A",
        requirements={},
        peer_specs=None,
        allow_partial=True,
        spec_run_kwargs={},
    )

    assert out["applied"] == 2
    assert out["already_satisfied_requirements"] == ["R1", "R2"]
    assert out["explicitly_skipped_requirements"] == ["R3"]


def test_run_multi_aggregates_requirement_outcomes(tmp_path, monkeypatch):
    from apatch.spec_run_multi import spec_run_multi_workspace

    monkeypatch.setattr(
        "apatch.spec_interference.spec_schedule_workspace",
        lambda *_a, **_k: {
            "ok": True,
            "schedulable": True,
            "order": ["SPEC-A", "SPEC-B"],
        },
    )
    monkeypatch.setattr(
        "apatch.spec_interference.spec_interference_workspace",
        lambda *_a, **_k: {"ok": True, "has_cycle": False},
    )
    monkeypatch.setattr(
        "apatch.spec_run_multi._run_spec_to_completion",
        lambda _root, sid, **_k: {
            "ok": True,
            "continue": False,
            "applied": 1 if sid == "SPEC-A" else 0,
            "already_satisfied_requirements": ["R1"] if sid == "SPEC-B" else [],
            "explicitly_skipped_requirements": ["R2"] if sid == "SPEC-A" else [],
        },
    )

    out = spec_run_multi_workspace(
        str(tmp_path),
        specs=["SPEC-A", "SPEC-B"],
        re_interference=False,
    )

    assert out["applied"] == 1
    assert out["already_satisfied_requirements"] == ["SPEC-B#R1"]
    assert out["explicitly_skipped_requirements"] == ["SPEC-A#R2"]


def test_run_multi_safe_order(tmp_path, monkeypatch):
    from apatch.spec_run_multi import spec_run_multi_workspace

    _setup_two_independent_specs(tmp_path)
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    out = spec_run_multi_workspace(
        str(tmp_path),
        specs=["SPEC-M1", "SPEC-M2"],
        re_interference=False,
    )
    assert out["ok"] is True
    assert out["completed_specs"] == ["SPEC-M1", "SPEC-M2"]
    assert (tmp_path / "m1.txt").is_file()
    assert (tmp_path / "m2.txt").is_file()


def test_fixture_stops_on_cross_verify(tmp_path, monkeypatch):
    from apatch.spec_run_multi import spec_run_multi_workspace

    root = _copy_fixture_tree(tmp_path)
    update_spec_registry(
        str(root),
        "SPEC-CV-SOURCE",
        {
            "requirements": {
                "R1": {
                    "needles": [
                        {
                            "target_file": MODULE_REL,
                            "find_text": "ORIGINAL",
                            "replace_text": "RENAMED",
                            "match_mode": "literal",
                        }
                    ]
                }
            }
        },
    )
    mod = root / MODULE_REL
    before = mod.read_text(encoding="utf-8")

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    out = spec_run_multi_workspace(
        str(root),
        specs=["SPEC-CV-SOURCE", "SPEC-CV-VICTIM"],
        cross_verify=True,
        re_interference=False,
        requirements={
            "SPEC-CV-SOURCE": {
                "R1": {
                    "needles": [
                        {
                            "target_file": MODULE_REL,
                            "find_text": "ORIGINAL",
                            "replace_text": "RENAMED",
                            "match_mode": "literal",
                        }
                    ]
                }
            },
            "SPEC-CV-VICTIM": {"R1": {"needles": []}},
        },
    )
    assert out["ok"] is False
    assert out["error_type"] == "SPEC_CROSS_VERIFY_FAILED"
    assert out["failed_at"] == "SPEC-CV-VICTIM"
    assert out["completed_specs"] == []
    assert [step["step"] for step in out["steps"]] == ["schedule", "cross_verify"]
    check = out["cross_verify"]["checks"][0]
    assert check["data_sources"] == ["inline_requirements"]
    assert check["apply"]["failed"] == []
    assert check["semantic_conflicts"]
    assert mod.read_text(encoding="utf-8") == before


def test_multi_failure_restores_all_completed_checkpoints(tmp_path, monkeypatch):
    from apatch.spec_run_multi import spec_run_multi_workspace

    _setup_two_independent_specs(tmp_path)
    spec_b = tmp_path / "docs" / "specs" / "SPEC-M2.md"
    spec_b.write_text(
        "# SPEC-M2\n"
        "> **apatch artifact:** `spec:SPEC-M2`\n"
        "## R1 create\n"
        "(verify: false)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )

    out = spec_run_multi_workspace(
        str(tmp_path),
        specs=["SPEC-M1", "SPEC-M2"],
        re_interference=False,
    )

    assert out["ok"] is False
    assert out["failed_at"] == "SPEC-M2"
    assert out["completed_specs_rolled_back"] is True
    assert out["failing_spec_rolled_back"] is True
    assert not (tmp_path / "m1.txt").exists()
    assert not (tmp_path / "m2.txt").exists()


def test_apply_backup_session_uses_returned_checkpoint(tmp_path, monkeypatch):
    from apatch.backup import BackupManager
    from apatch.ingestor import PatchCandidate
    from apatch.tui import InteractiveTUI
    from apatch.workflows import rollback_workspace

    target = tmp_path / "value.txt"
    target.write_text("before", encoding="utf-8")
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: _FakeTrustChain()
    )
    tui = InteractiveTUI(
        [
            PatchCandidate(
                step_index=1,
                tool_name="replace_file_content",
                target_file="value.txt",
                old_content="before",
                new_content="after",
            )
        ],
        str(tmp_path),
        non_interactive=True,
        quiet=True,
    )
    tui.run_apply_loop()

    assert tui.tc_checkpoint_name
    assert tui.backup_mgr.session_id == tui.tc_checkpoint_name
    assert BackupManager.list_session_files(
        str(tmp_path), tui.tc_checkpoint_name
    )["count"] == 1
    rollback = rollback_workspace(str(tmp_path), tui.tc_checkpoint_name)
    assert rollback["ok"] is True
    assert target.read_text(encoding="utf-8") == "before"


def test_missing_checkpoint_never_reports_workspace_restored(tmp_path, monkeypatch):
    from apatch.spec_run_multi import spec_run_multi_workspace

    calls = []

    monkeypatch.setattr(
        "apatch.spec_interference.spec_schedule_workspace",
        lambda *_a, **_k: {
            "ok": True,
            "schedulable": True,
            "order": ["SPEC-A-1", "SPEC-B-1"],
        },
    )

    def fake_run(_root, sid, **_kwargs):
        calls.append(sid)
        return {
            "ok": True,
            "steps_completed": ["apply_session"],
        }

    monkeypatch.setattr(
        "apatch.spec_run_multi._run_spec_to_completion",
        fake_run,
    )

    out = spec_run_multi_workspace(
        str(tmp_path),
        specs=["SPEC-A-1", "SPEC-B-1"],
        re_interference=False,
    )

    assert out["ok"] is False
    assert out["error_type"] == "SPEC_RUN_BLOCKED"
    assert out["checkpoint_missing_for"] == "SPEC-A-1"
    assert out["workspace_restored"] is False
    assert calls == ["SPEC-A-1"]


def test_nested_verify_failure_is_preserved_without_second_rollback(tmp_path, monkeypatch):
    from apatch.spec_run_multi import spec_run_multi_workspace

    monkeypatch.setattr(
        "apatch.spec_interference.spec_schedule_workspace",
        lambda *_a, **_k: {
            "ok": True,
            "schedulable": True,
            "order": ["SPEC-A-1", "SPEC-B-1"],
        },
    )
    monkeypatch.setattr(
        "apatch.spec_run_multi._run_spec_to_completion",
        lambda *_a, **_k: {
            "ok": False,
            "error": "live contract failed",
            "error_type": "VERIFY_FAILED",
            "recoverable": True,
            "recommended_action": "fix_forward",
            "verify_rollback": True,
            "rollback_performed": True,
            "last_checkpoint": "already-restored",
            "verify_output": "1 failed",
            "diagnostics": [{"kind": "pytest"}],
        },
    )
    monkeypatch.setattr(
        "apatch.spec_run_multi._rollback_checkpoints",
        lambda *_a, **_k: True,
    )

    def fail_on_second_rollback(*_a, **_k):
        raise AssertionError("nested rollback must not run twice")

    monkeypatch.setattr("apatch.workflows.rollback_workspace", fail_on_second_rollback)

    out = spec_run_multi_workspace(
        str(tmp_path),
        specs=["SPEC-A-1", "SPEC-B-1"],
        re_interference=False,
    )

    assert out["ok"] is False
    assert out["error_type"] == "VERIFY_FAILED"
    assert out["recommended_action"] == "fix_forward"
    assert out["rollback_performed"] is True
    assert out["verify_output"] == "1 failed"
    assert out["failing_spec_rolled_back"] is True


def test_mcp_spec_run_multi_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    if tm is None:
        pytest.skip("FastMCP tool manager unavailable")
    assert "apatch_spec_run_multi" in tm._tools
