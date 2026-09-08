"""Slug ratify — single-call ratification on one verify run.

The prod pain this op removes: closing one category re-ran the same expensive
verify (raw feedback replay) once per stale requirement plus once for the
conformance verdict. These tests count real verify executions with an
append-to-file spy command, so "exactly once" is measured, not assumed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from apatch.slug_ratify import slug_ratify_workspace


class _FakeRuntime:
    """Spy MutationRuntime: records the single governed batch attestation."""

    instances: list = []

    def __init__(self, target_dir):
        self.calls: list = []
        _FakeRuntime.instances.append(self)

    def open_session(self, intent, artifacts=None):
        self.calls.append(("open", intent, tuple(artifacts or [])))
        return {"ok": True}

    def verify_run(self, verify=None, skip_transition_check=False):
        self.calls.append(("verify", verify))
        return {"ok": True}

    def noop_attest(self, covered_by, message=None, evidence=None):
        self.calls.append(("noop", tuple(covered_by or []), message, evidence))
        return {"ok": True, "committed": True}

    def close_session(self):
        self.calls.append(("close",))
        return {"ok": True}


def _write_workspace(
    tmp_path: Path,
    *,
    verify_exit: int = 0,
    second_verify_exit: int | None = None,
    conformance_mode: str = "advisory",
):
    """SPEC-UGOL-1 with R1 file_drift-stale and optional distinct R2 verify."""
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (tmp_path / "counter.py").write_text(
        "import pathlib, sys\n"
        "with pathlib.Path('counter.log').open('a') as fh:\n"
        "    fh.write('x')\n"
        f"sys.exit({verify_exit})\n",
        encoding="utf-8",
    )
    cmd = "python3 counter.py"
    cmd2 = cmd
    if second_verify_exit is not None:
        (tmp_path / "counter2.py").write_text(
            "import pathlib, sys\n"
            "with pathlib.Path('counter2.log').open('a') as fh:\n"
            "    fh.write('y')\n"
            f"sys.exit({second_verify_exit})\n",
            encoding="utf-8",
        )
        cmd2 = "python3 counter2.py"
    (spec_dir / "SPEC-UGOL-1.md").write_text(
        f"""# SPEC-UGOL-1 - ugol category

> **apatch artifact:** `spec:SPEC-UGOL-1`

## R1 First (verify: {cmd})

## R2 Second (verify: {cmd2})
""",
        encoding="utf-8",
    )
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "conformance.json").write_text(
        json.dumps({"enabled": True, "mode": conformance_mode}), encoding="utf-8"
    )

    marker = tmp_path / "marker.txt"
    marker.write_text("v1\n", encoding="utf-8")
    other = tmp_path / "other.txt"
    other.write_text("stable\n", encoding="utf-8")
    h_marker = hashlib.sha256(marker.read_bytes()).hexdigest()
    h_other = hashlib.sha256(other.read_bytes()).hexdigest()
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m1",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "files": {"marker.txt": {"sha256": h_marker}},
                "artifacts": [{"kind": "spec", "id": "SPEC-UGOL-1#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a1",
            "payload": {"artifacts": [{"kind": "spec", "id": "SPEC-UGOL-1#R1"}]},
        },
        {
            "tool_id": "apatch",
            "id": "op-m2",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "files": {"other.txt": {"sha256": h_other}},
                "artifacts": [{"kind": "spec", "id": "SPEC-UGOL-1#R2"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a2",
            "payload": {"artifacts": [{"kind": "spec", "id": "SPEC-UGOL-1#R2"}]},
        },
    ]
    marker.write_text("v2\n", encoding="utf-8")  # drift → R1 stale (file_drift)
    return cmd, entries


def _patch_ledger(monkeypatch, entries):
    # Import both modules before patching. spec_coverage binds
    # spec._ledger_entries at import time; importing it after the first patch
    # would make monkeypatch restore the fake as its "original" and leak into
    # later tests.
    from apatch import spec as spec_module
    from apatch import spec_coverage as coverage_module

    fake = lambda _td: (entries, True)  # noqa: E731 - test stub
    monkeypatch.setattr(spec_module, "_ledger_entries", fake)
    monkeypatch.setattr(coverage_module, "_ledger_entries", fake)


def _patch_runtime(monkeypatch):
    _FakeRuntime.instances.clear()
    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", _FakeRuntime)


def test_ratify_happy_path_verify_runs_exactly_once(tmp_path, monkeypatch):
    cmd, entries = _write_workspace(tmp_path)
    _patch_ledger(monkeypatch, entries)
    _patch_runtime(monkeypatch)

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["ok"] is True, out
    assert out["spec"] == "SPEC-UGOL-1"
    assert [s["name"] for s in out["stages"]] == ["resolve", "lint", "verify", "rebind", "gate"]
    assert all(s["ok"] for s in out["stages"])
    # ONE shared verify command, TWO requirements, plus the gate re-classification:
    # the command still executed exactly once.
    assert (tmp_path / "counter.log").read_text(encoding="utf-8") == "x"
    assert out["verify"]["commands_run"] == 1
    assert out["verify"]["green"] is True
    # the stale R1 was re-attested from the measured result — no per-Rk verify run
    assert out["reattested"] == ["R1"]
    assert out["primary_attested"] == []
    assert out["attestation"] == {"sessions_opened": 1, "ledger_commits": 1}
    assert len(_FakeRuntime.instances) == 1
    assert [c[0] for c in _FakeRuntime.instances[0].calls] == ["open", "noop", "close"]
    noop = _FakeRuntime.instances[0].calls[1]
    assert noop[1] == ("R1",)
    assert noop[3]["kind"] == "slug_ratify_shared_verify"
    assert noop[3]["commands_run"] == 1
    assert out["gate"]["verdict"] == "passed"
    assert out["files"]["contract"] is None
    assert out["files"]["triage"] is None


def test_ratify_broken_closed_verify_reference_does_not_block_open_rebind(
    tmp_path, monkeypatch
):
    _cmd, entries = _write_workspace(tmp_path, second_verify_exit=4)
    _patch_ledger(monkeypatch, entries)
    _patch_runtime(monkeypatch)

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["ok"] is True, out
    assert out["complete"] is False
    assert out["ratified_with_advisories"] is True
    assert out["verify"]["green"] is False
    assert out["verify"]["can_rebind_open"] is True
    assert out["verify"]["red"]["broken"] == ["R2:exit4"]
    assert out["reattested"] == ["R1"]
    assert out["attestation"] == {"sessions_opened": 1, "ledger_commits": 1}
    assert (tmp_path / "counter.log").read_text(encoding="utf-8") == "x"
    assert (tmp_path / "counter2.log").read_text(encoding="utf-8") == "y"
    verify_stage = next(stage for stage in out["stages"] if stage["name"] == "verify")
    rebind_stage = next(stage for stage in out["stages"] if stage["name"] == "rebind")
    assert verify_stage["ok"] is True and "advisory" in verify_stage["summary"]
    assert rebind_stage["ok"] is True
    assert "broken verify reference" in out["next_action"]


def test_ratify_verify_red_skips_rebind(tmp_path, monkeypatch):
    _cmd, entries = _write_workspace(tmp_path, verify_exit=1, conformance_mode="blocking")
    _patch_ledger(monkeypatch, entries)
    _patch_runtime(monkeypatch)

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["ok"] is False
    assert out["verify"]["green"] is False
    # one red shared command marks BOTH requirements that name it
    assert out["verify"]["red"]["failures"] == ["R1", "R2"]
    # red verify → NO rebind: no governed session opened, nothing re-attested
    assert out["reattested"] == []
    assert _FakeRuntime.instances == []
    rebind_stage = next(s for s in out["stages"] if s["name"] == "rebind")
    assert rebind_stage["ok"] is False and "skipped" in rebind_stage["summary"]
    # still at most once, even when red
    assert (tmp_path / "counter.log").read_text(encoding="utf-8") == "x"
    assert out["gate"]["verdict"] == "failed"


def test_ratify_primary_attests_all_pending_in_one_session(tmp_path, monkeypatch):
    _cmd, _entries = _write_workspace(tmp_path)
    _patch_ledger(monkeypatch, [])
    _patch_runtime(monkeypatch)

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["ok"] is True, out
    assert out["primary_attested"] == ["R1", "R2"]
    assert out["reattested"] == []
    assert out["attested"] == ["R1", "R2"]
    assert out["attestation"] == {"sessions_opened": 1, "ledger_commits": 1}
    assert len(_FakeRuntime.instances) == 1
    calls = _FakeRuntime.instances[0].calls
    assert [call[0] for call in calls] == ["open", "noop", "close"]
    assert len(calls[0][2]) == 2
    assert calls[1][1] == ("R1", "R2")
    assert (tmp_path / "counter.log").read_text(encoding="utf-8") == "x"


def test_one_batch_attestation_covers_every_requirement_artifact():
    from apatch.traceability import build_traceability_index

    artifacts = [
        {"kind": "spec", "id": "SPEC-UGOL-1#R1", "content_hash": "sha256:r1"},
        {"kind": "spec", "id": "SPEC-UGOL-1#R2", "content_hash": "sha256:r2"},
    ]
    index = build_traceability_index([
        {
            "id": "signed-batch-attestation",
            "tool_id": "apatch_attest",
            "payload": {
                "artifacts": artifacts,
                "covered_by": ["R1", "R2"],
                "evidence": {"kind": "slug_ratify_shared_verify", "commands_run": 1},
            },
        }
    ])

    for rid in ("R1", "R2"):
        bucket = index["by_artifact"][f"spec:SPEC-UGOL-1#{rid}"]
        assert bucket["coverage"]["complete"] is True
        assert bucket["coverage"]["attestation_count"] == 1


def test_ratify_resolve_failure_lists_candidates(tmp_path):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    # UGOLOK is a prefix collision, not ownership — it must be LISTED, never picked
    (spec_dir / "SPEC-UGOLOK-1.md").write_text(
        "# SPEC-UGOLOK-1 - other\n\n> **apatch artifact:** `spec:SPEC-UGOLOK-1`\n\n## R1 A (verify: true)\n",
        encoding="utf-8",
    )

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["ok"] is False
    assert out["stage"] == "resolve"
    assert "SPEC-UGOLOK-1" in out["candidates"]
    assert out.get("spec") != "SPEC-UGOLOK-1"
    assert "hint" in out


def test_ratify_dry_run_does_no_mutations(tmp_path, monkeypatch):
    cmd, entries = _write_workspace(tmp_path)
    _patch_ledger(monkeypatch, entries)
    _patch_runtime(monkeypatch)

    out = slug_ratify_workspace(str(tmp_path), "ugol", dry_run=True)

    assert out["ok"] is True
    assert out["dry_run"] is True
    assert [s["name"] for s in out["stages"]] == ["resolve", "lint", "verify"]
    # no verify executed, no governed session opened
    assert not (tmp_path / "counter.log").exists()
    assert _FakeRuntime.instances == []
    assert out["would_run"]["verify_commands"] == [cmd]
    assert out["would_run"]["open_requirements"] == ["R1"]
    assert out["would_run"]["primary_requirements"] == []
    assert out["would_run"]["stale_requirements"] == ["R1"]


def test_ratify_lint_failure_stops_before_verify(tmp_path, monkeypatch):
    _cmd, entries = _write_workspace(tmp_path)
    _patch_ledger(monkeypatch, entries)
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "ugol_feedback_triage.tsv").write_text(
        "triage_id\tquery\tstatus\nT1\tугол 90\ttotally_bogus_status\n",
        encoding="utf-8",
    )

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["ok"] is False
    assert out["stage"] == "lint"
    assert not (tmp_path / "counter.log").exists()  # fail-fast: verify never ran
    assert out["findings"]


def test_ratify_records_triage_file_and_rows(tmp_path, monkeypatch):
    _cmd, entries = _write_workspace(tmp_path)
    _patch_ledger(monkeypatch, entries)
    _patch_runtime(monkeypatch)
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "ugol_feedback_triage.tsv").write_text(
        "triage_id\tquery\tstatus\nT1\tугол 90\tfixed\nT2\tугол 45\tcatalog_gap\n",
        encoding="utf-8",
    )

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["ok"] is True, out
    triage = out["files"]["triage"]
    assert triage["path"] == "tests/regressions/ugol_feedback_triage.tsv"
    assert triage["rows"] == 2
    assert len(triage["sha256"]) == 64


def test_ratify_contract_yaml_spec_id_wins(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    _cmd, entries = _write_workspace(tmp_path)
    _patch_ledger(monkeypatch, entries)
    _patch_runtime(monkeypatch)
    contracts = tmp_path / "docs" / "specs" / "slug_contracts"
    contracts.mkdir(parents=True)
    (contracts / "ugol.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-UGOL-CUSTOM-7\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "specs" / "SPEC-UGOL-CUSTOM-7.md").write_text(
        "# SPEC-UGOL-CUSTOM-7 - custom\n\n> **apatch artifact:** `spec:SPEC-UGOL-CUSTOM-7`\n\n"
        "## R1 A (verify: python3 counter.py)\n",
        encoding="utf-8",
    )

    out = slug_ratify_workspace(str(tmp_path), "ugol")

    assert out["spec"] == "SPEC-UGOL-CUSTOM-7"
    resolve = out["stages"][0]
    assert "contract yaml" in resolve["summary"]
    assert out["files"]["contract"]["path"] == "docs/specs/slug_contracts/ugol.yaml"


def test_slug_ratify_cli_json(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    def fake_ratify(target_dir, **kwargs):
        return {
            "ok": True,
            "slug": kwargs["slug"],
            "spec": "SPEC-UGOL-1",
            "stages": [],
            "files": {"contract": None, "triage": None},
            "verify": {"commands_run": 1, "elapsed_sec": 0.1, "green": True},
            "reattested": ["R1"],
            "gate": {"verdict": "passed", "buckets": {"stale": 1}},
            "next_action": "done",
        }

    monkeypatch.setattr("apatch.slug_ratify.slug_ratify_workspace", fake_ratify)

    result = CliRunner().invoke(
        cli, ["slug", "ratify", "ugol", "--target-dir", str(tmp_path), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["spec"] == "SPEC-UGOL-1"
    assert payload["reattested"] == ["R1"]


def test_mcp_slug_ratify_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None
    assert "apatch_slug_ratify" in tm._tools
