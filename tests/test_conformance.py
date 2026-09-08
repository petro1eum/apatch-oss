"""RFP-035 — continuous conformance gate (opt-in standing contract-gate).

Phase 2 model: ``stale`` (attestation stale, verify green → advisory) is kept
distinct from ``drifted`` (live verify red → blocking). The gate blocks only on
a real red verify, never on stale bookkeeping.
"""

import json
import os
import signal
import time

import pytest

from apatch.conformance import (
    classify_attestation,
    conformance_gate,
    conformance_status,
    is_ci_safe,
    load_conformance_config,
    run_spec_verify,
)


# --- A35-E: ci_safe filter ----------------------------------------------------

def test_ci_safe_accepts_pure_pytest():
    assert is_ci_safe("python3 -m pytest tests/test_x.py::test_y -q")
    assert is_ci_safe("pytest tests/test_x.py")


def test_ci_safe_rejects_sibling_repo_and_shell_chains():
    assert not is_ci_safe("python3 -m pytest ../avatar-contract/tests/test_x.py")  # sibling repo
    assert not is_ci_safe("python3 -m pytest tests/x.py && rg -l 'tools' docs/")    # shell chain + rg
    assert not is_ci_safe("apatch rfp coverage --rfp RFP-028")                       # apatch-self/ledger
    assert not is_ci_safe("test -d patches/staging")                                 # not pytest


def test_ci_safe_runner_skips_unsafe_verifies():
    reqs = [
        {"id": "R1", "verify": "python3 -m pytest --version"},               # safe, exits 0
        {"id": "R2", "verify": "python3 -m pytest ../sibling/tests/x.py"},   # unsafe → skipped
    ]
    ran, failures, broken = run_spec_verify(".", reqs, ci_safe=True, timeout=30)
    assert ran == 1 and failures == [] and broken == []  # only the safe one ran; sibling-repo skipped


def test_run_spec_verify_separates_drifted_from_broken():
    """exit 1 = a test failed (drifted); exit >=2 = the verify can't run (broken)."""
    reqs = [
        {"id": "R0", "verify": 'python3 -c "pass"'},                      # exit 0
        {"id": "R1", "verify": 'python3 -c "import sys;sys.exit(1)"'},   # exit 1 → failure
        {"id": "R4", "verify": 'python3 -c "import sys;sys.exit(4)"'},   # exit 4 → broken
    ]
    ran, failures, broken = run_spec_verify(".", reqs, timeout=20)
    assert ran == 3
    assert failures == ["R1"]
    assert len(broken) == 1 and broken[0].startswith("R4")


def test_run_spec_verify_can_capture_failure_details():
    reqs = [
        {
            "id": "R1",
            "verify": "python3 -c \"import sys; print('out-marker'); print('err-marker', file=sys.stderr); sys.exit(1)\"",
        }
    ]
    ran, failures, broken, details = run_spec_verify(".", reqs, timeout=20, capture_details=True)
    assert ran == 1
    assert failures == ["R1"]
    assert broken == []
    assert details[0]["id"] == "R1"
    assert details[0]["kind"] == "failure"
    assert details[0]["exit_code"] == 1
    assert "out-marker" in details[0]["stdout_tail"]
    assert "err-marker" in details[0]["stderr_tail"]


def test_run_spec_verify_timeout_kills_process_group(tmp_path):
    marker = tmp_path / "child.pid"
    child_code = "import time; time.sleep(30)"
    parent_code = (
        "import pathlib, subprocess, time; "
        f"p=subprocess.Popen(['python3','-c',{child_code!r}]); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); "
        "time.sleep(30)"
    )
    cmd = f"python3 -c {parent_code!r}"

    ran, failures, broken, details = run_spec_verify(
        str(tmp_path),
        [{"id": "RTO", "verify": cmd}],
        timeout=1,
        capture_details=True,
    )

    assert ran == 1
    assert failures == []
    assert broken == ["RTO:timeout"]
    assert details[0]["exit_code"] == "timeout"
    pid = int(marker.read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        pytest.fail("timed-out verify left a child process running")


def test_run_spec_verify_spec_budget_bounds_a_slow_requirement(tmp_path):
    reqs = [
        {"id": "R1", "verify": 'python3 -c "import time; time.sleep(0.2)"'},
        {"id": "R2", "verify": 'python3 -c "pass"'},
    ]

    ran, failures, broken, details = run_spec_verify(
        str(tmp_path),
        reqs,
        timeout=5,
        budget_sec=0.05,
        capture_details=True,
    )

    assert ran == 1
    assert failures == []
    assert broken == ["R1:timeout"]
    assert details[0]["exit_code"] == "timeout"
    assert "0.05" in details[0]["stderr_tail"]


def test_run_spec_verify_fail_fast_stops_after_first_red_requirement(tmp_path):
    reqs = [
        {"id": "R1", "verify": 'python3 -c "import sys; sys.exit(1)"'},
        {"id": "R2", "verify": 'python3 -c "pass"'},
    ]

    ran, failures, broken, details = run_spec_verify(
        str(tmp_path),
        reqs,
        timeout=5,
        capture_details=True,
        fail_fast=True,
    )

    assert ran == 1
    assert failures == ["R1"]
    assert broken == []
    assert details[0]["id"] == "R1"


def test_run_spec_verify_parallelizes_requirements(tmp_path):
    reqs = [
        {"id": "R1", "verify": 'python3 -c "import time; time.sleep(0.25)"'},
        {"id": "R2", "verify": 'python3 -c "import time; time.sleep(0.25)"'},
    ]

    started = time.monotonic()
    ran, failures, broken = run_spec_verify(
        str(tmp_path),
        reqs,
        timeout=5,
        requirement_jobs=2,
    )
    elapsed = time.monotonic() - started

    assert ran == 2
    assert failures == []
    assert broken == []
    assert elapsed < 0.45


def test_run_spec_verify_parallel_fail_fast_does_not_schedule_later_requirements(tmp_path):
    reqs = [
        {"id": "R1", "verify": 'python3 -c "import sys; sys.exit(1)"'},
        {"id": "R2", "verify": 'python3 -c "import time; time.sleep(0.05)"'},
        {"id": "R3", "verify": 'python3 -c "pass"'},
    ]

    ran, failures, broken = run_spec_verify(
        str(tmp_path),
        reqs,
        timeout=5,
        fail_fast=True,
        requirement_jobs=2,
    )

    assert ran == 2
    assert failures == ["R1"]
    assert broken == []


def test_broken_verify_is_advisory_not_drifted(tmp_path, monkeypatch):
    """A spec whose verify exits >=2 (renamed/missing test) is BROKEN (advisory),
    never DRIFTED — the gate must not cry wolf on a rotted verify reference."""
    _write_cfg(tmp_path, enabled=True, mode="blocking", contract={"specs": ["SPEC-ROT"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-ROT": {"ok": True, "summary": {"total": 1, "attested": 1},
                     "requirements": [{"id": "R1", "verify": "pytest x::gone"}]},
    }))
    out = conformance_status(str(tmp_path), live=True,
                             verify_runner=lambda r, reqs: (1, [], ["R1:exit4"]))
    assert out["buckets"]["broken"] == 1 and out["buckets"]["drifted"] == 0
    assert out["contract_holds"] is True  # broken is advisory, does not block


def test_a_scaffold_placeholder_is_never_under_contract(tmp_path):
    """The standing gate must not go red on an authoring template.

    The registry keeps the PARSED spec id, so `SPEC-TEMPLATE.md` is registered under
    its placeholder heading `SPEC-<ID>`. The name-based exclusion missed that, and the
    whole-contract gate reported a template as a drifted contract — its verify,
    `pytest tests/test_<id>_r1.py`, cannot pass by construction.
    """
    from apatch.conformance import gated_specs

    registry = tmp_path / ".apatch" / "specs"
    registry.mkdir(parents=True)
    for name in ("SPEC-REAL-1.json", "SPEC-TEMPLATE.json", "SPEC-<ID>.json"):
        (registry / name).write_text("{}", encoding="utf-8")

    assert gated_specs(str(tmp_path), {"contract": {}}) == ["SPEC-REAL-1"]


def test_gated_specs_falls_back_to_docs_specs(tmp_path):
    """When the registry cache (.apatch/specs) is empty (fresh CI checkout), the
    contract is derived from committed docs/specs/SPEC-*.md — not 0 specs."""
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "specs" / "SPEC-A-1.md").write_text("# SPEC-A-1\n", encoding="utf-8")
    (tmp_path / "docs" / "specs" / "SPEC-B-2.md").write_text("# SPEC-B-2\n", encoding="utf-8")
    from apatch.conformance import gated_specs
    assert gated_specs(str(tmp_path), {"contract": {}}) == ["SPEC-A-1", "SPEC-B-2"]


def test_gated_specs_excludes_templates(tmp_path):
    """SPEC-TEMPLATE.md is an authoring scaffold with placeholder verifies
    (`tests/test_<id>_r1.py`) — it must never be gated as a real contract,
    or the gate cries wolf on a spec that always 'fails'."""
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    for name in ("SPEC-REAL-1.md", "SPEC-TEMPLATE.md", "SPEC-template-2.md"):
        (tmp_path / "docs" / "specs" / name).write_text(f"# {name}\n", encoding="utf-8")
    from apatch.conformance import gated_specs
    assert gated_specs(str(tmp_path), {"contract": {}}) == ["SPEC-REAL-1"]


def _write_cfg(root, **cfg):
    d = root / ".apatch"
    d.mkdir(parents=True, exist_ok=True)
    (d / "conformance.json").write_text(json.dumps(cfg), encoding="utf-8")


def _status(**by_spec):
    """A fake spec_status_workspace driven by a {spec: result} map."""
    return lambda root, spec=None, **k: by_spec[spec]


# --- A35-C: attestation classification never yields drifted --------------------

def test_classify_conformant():
    assert classify_attestation({"total": 3, "attested": 3, "stale": 0, "blocked": 0}) == "conformant"


def test_attestation_stale_is_stale_not_drifted():
    # the dogfood lesson: stale attestation with green verify must NOT be drifted
    assert classify_attestation({"total": 3, "attested": 3, "stale": 1}) == "stale"


def test_classify_unproven_when_nothing_attested():
    assert classify_attestation({"total": 2, "attested": 0, "pending": 2}) == "unproven"


def test_classify_in_progress_partial():
    assert classify_attestation({"total": 4, "attested": 2, "pending": 2}) == "in_progress"


# --- A35-A: opt-in -------------------------------------------------------------

def test_disabled_without_config(tmp_path):
    out = conformance_status(str(tmp_path))
    assert out["ok"] is True and out["enabled"] is False
    assert "disabled" in out["skipped"]
    assert out["workspace"] == str(tmp_path.resolve())
    assert out["config_path"].endswith(".apatch/conformance.json")


def test_gate_skips_when_disabled(tmp_path):
    out = conformance_gate(str(tmp_path))
    assert out["gate"] == "skipped" and out["ok"] is True
    assert out["workspace"] == str(tmp_path.resolve())


# --- A35-B/C/D: buckets, no live verify ---------------------------------------

def test_status_reports_all_buckets_at_once(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, mode="advisory")
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-A": {"ok": True, "summary": {"total": 2, "attested": 2}},
        "SPEC-B": {"ok": True, "summary": {"total": 2, "attested": 2, "stale": 1}},  # stale, not drifted
        "SPEC-C": {"ok": False, "error": "no spec"},  # unproven hole
    }))
    out = conformance_status(str(tmp_path), specs=["SPEC-A", "SPEC-B", "SPEC-C"])
    assert out["buckets"]["conformant"] == 1
    assert out["buckets"]["stale"] == 1
    assert out["buckets"]["unproven"] == 1
    assert out["buckets"]["drifted"] == 0
    # stale alone does NOT break the contract (no cry wolf)
    assert out["contract_holds"] is True


def test_conformance_status_cli_can_limit_to_one_spec(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from apatch.cli import cli

    _write_cfg(tmp_path, enabled=True, mode="advisory", contract={"specs": ["SPEC-A", "SPEC-B"]})
    seen = []

    def fake_status(root, spec=None, **kwargs):
        seen.append(spec)
        return {"ok": True, "summary": {"total": 1, "attested": 1}, "requirements": []}

    monkeypatch.setattr("apatch.spec.spec_status_workspace", fake_status)
    res = CliRunner().invoke(
        cli,
        ["conformance", "status", "--target-dir", str(tmp_path), "--spec", "SPEC-B", "--json"],
    )

    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["gated"] == 1
    assert out["per_spec"][0]["spec"] == "SPEC-B"
    assert seen == ["SPEC-B"]


def test_conformance_status_cli_no_live_overrides_config(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from apatch.cli import cli

    _write_cfg(tmp_path, enabled=True, live_verify=True, contract={"specs": ["SPEC-A"]})

    def fake_status(root, spec=None, **kwargs):
        return {
            "ok": True,
            "summary": {"total": 1, "attested": 1},
            "requirements": [{"id": "R1", "verify": "false"}],
        }

    monkeypatch.setattr("apatch.spec.spec_status_workspace", fake_status)
    res = CliRunner().invoke(
        cli,
        ["conformance", "status", "--target-dir", str(tmp_path), "--spec", "SPEC-A", "--no-live", "--json"],
    )

    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["live"] is False
    assert out["verified_live"] == 0
    assert out["buckets"]["conformant"] == 1
    assert out["buckets"]["drifted"] == 0


# --- A35-C: live verify is what distinguishes stale from drifted ---------------

def test_live_verify_green_keeps_stale(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, mode="blocking", contract={"specs": ["SPEC-B"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-B": {"ok": True, "summary": {"total": 1, "attested": 1, "stale": 1},
                   "requirements": [{"id": "R1", "verify": "true"}]},
    }))
    out = conformance_status(str(tmp_path), live=True, verify_runner=lambda r, reqs: (1, []))
    assert out["buckets"]["stale"] == 1 and out["buckets"]["drifted"] == 0
    assert out["per_spec"][0]["recommended_mcp"] == "apatch_rebind_stale(target_dir='.', spec='SPEC-B')"
    assert out["per_spec"][0]["recommended_cli"] == "apatch spec rebind-stale --spec SPEC-B --target-dir ."
    assert "MCP:" in out["per_spec"][0]["recommended_action"]
    assert "CLI:" in out["per_spec"][0]["recommended_action"]
    assert out["contract_holds"] is True


def test_live_verify_red_is_drifted(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, mode="blocking", contract={"specs": ["SPEC-B"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-B": {"ok": True, "summary": {"total": 1, "attested": 1, "stale": 1},
                   "requirements": [{"id": "R1", "verify": "false"}]},
    }))
    out = conformance_status(str(tmp_path), live=True, verify_runner=lambda r, reqs: (1, ["R1"]))
    assert out["buckets"]["drifted"] == 1
    assert out["drifted"] == ["SPEC-B"]
    assert out["contract_holds"] is False


def test_live_verify_red_surfaces_command_output_details(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, mode="blocking", contract={"specs": ["SPEC-B"]})
    cmd = "python3 -c \"import sys; print('visible-stdout'); print('visible-stderr', file=sys.stderr); sys.exit(1)\""
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-B": {"ok": True, "summary": {"total": 1, "attested": 1},
                   "requirements": [{"id": "R1", "verify": cmd}]},
    }))
    out = conformance_status(str(tmp_path), live=True)
    detail = out["per_spec"][0]["verify_details"][0]
    assert out["drifted"] == ["SPEC-B"]
    assert detail["id"] == "R1"
    assert detail["cmd"] == cmd
    assert "visible-stdout" in detail["stdout_tail"]
    assert "visible-stderr" in detail["stderr_tail"]


# --- A35-F: gate modes ---------------------------------------------------------

def test_gate_blocking_fails_only_on_live_red(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, mode="blocking", live_verify=True, contract={"specs": ["SPEC-B"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-B": {"ok": True, "summary": {"total": 1, "attested": 1, "stale": 1},
                   "requirements": [{"id": "R1", "verify": "false"}]},
    }))
    monkeypatch.setattr("apatch.conformance.run_spec_verify", lambda r, reqs, **k: (1, ["R1"]))
    out = conformance_gate(str(tmp_path), live=True)
    assert out["gate"] == "failed" and out["ok"] is False
    assert "drifted" in out["reason"]


def test_block_on_is_the_projects_choice(tmp_path, monkeypatch):
    """Default blocks only on drifted; a project can opt unproven/stale into the
    blocking policy. apatch gives the lever; the project decides (R1)."""
    monkeypatch.setattr("apatch.spec.spec_status_workspace",
                        lambda root, spec=None, **k: {"ok": False, "error": "no spec"})  # → unproven
    # default policy: unproven is advisory, contract still holds
    _write_cfg(tmp_path, enabled=True)
    assert conformance_status(str(tmp_path), specs=["SPEC-X"])["contract_holds"] is True
    # project opts unproven into blocking → now it fails
    _write_cfg(tmp_path, enabled=True, block_on=["drifted", "unproven"])
    out = conformance_status(str(tmp_path), specs=["SPEC-X"])
    assert out["block_on"] == ["drifted", "unproven"]
    assert out["contract_holds"] is False


def test_gate_passes_with_only_stale_advisory(tmp_path, monkeypatch):
    # stale bookkeeping must pass the gate, surfaced as an advisory
    _write_cfg(tmp_path, enabled=True, mode="blocking", contract={"specs": ["SPEC-B"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-B": {"ok": True, "summary": {"total": 1, "attested": 1, "stale": 1},
                   "requirements": [{"id": "R1", "verify": "true"}]},
    }))
    out = conformance_gate(str(tmp_path), live=True)  # verify_runner default; "true" exits 0
    assert out["gate"] == "passed" and out["ok"] is True
    assert out["advisories"]["stale"] == 1


# --- A35-H: baseline-aware ----------------------------------------------------

def test_baseline_aware_skips_untouched_specs(tmp_path, monkeypatch):
    """With a base, an untouched spec is NOT re-verified — even a red runner is
    not called for it, so it keeps its attestation state (fast at scale)."""
    _write_cfg(tmp_path, enabled=True, mode="blocking", contract={"specs": ["SPEC-X"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-X": {"ok": True, "summary": {"total": 1, "attested": 1},
                   "requirements": [{"id": "R1", "verify": "false", "files": ["src/untouched.py"]}]},
    }))
    # base set; the only changed file is unrelated → SPEC-X is out of scope
    monkeypatch.setattr("apatch.conformance.changed_files", lambda root, base: {"src/other.py"})

    def _boom(root, reqs):
        raise AssertionError("verify must not run for an untouched spec")

    out = conformance_status(str(tmp_path), live=True, base="origin/main", verify_runner=_boom)
    assert out["verified_live"] == 0
    assert out["buckets"]["conformant"] == 1 and out["buckets"]["drifted"] == 0


def test_baseline_aware_verifies_touched_specs(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, mode="blocking", contract={"specs": ["SPEC-X"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-X": {"ok": True, "summary": {"total": 1, "attested": 1},
                   "requirements": [{"id": "R1", "verify": "false", "files": ["src/touched.py"]}]},
    }))
    monkeypatch.setattr("apatch.conformance.changed_files", lambda root, base: {"src/touched.py"})
    out = conformance_status(str(tmp_path), live=True, base="origin/main",
                             verify_runner=lambda root, reqs: (1, ["R1"]))
    assert out["verified_live"] == 1
    assert out["buckets"]["drifted"] == 1


def test_live_conformance_parallelizes_specs(tmp_path, monkeypatch):
    import threading

    _write_cfg(tmp_path, enabled=True, live_verify=True, contract={"specs": ["SPEC-A", "SPEC-B"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-A": {"ok": True, "summary": {"total": 1, "attested": 1},
                   "requirements": [{"id": "R1", "verify": "true"}]},
        "SPEC-B": {"ok": True, "summary": {"total": 1, "attested": 1},
                   "requirements": [{"id": "R1", "verify": "true"}]},
    }))

    lock = threading.Lock()
    active = 0
    max_active = 0

    def runner(root, reqs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return (1, [], [])

    out = conformance_status(str(tmp_path), live=True, jobs=2, verify_runner=runner)

    assert out["verified_live"] == 2
    assert out["jobs"] == 2
    assert max_active == 2
    assert out["buckets"]["conformant"] == 2


def test_conformance_reports_runner_exception_as_broken(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, live_verify=True, contract={"specs": ["SPEC-X"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-X": {"ok": True, "summary": {"total": 1, "attested": 1},
                   "requirements": [{"id": "R1", "verify": "true"}]},
    }))

    def runner(root, reqs):
        raise RuntimeError("boom")

    out = conformance_status(str(tmp_path), live=True, verify_runner=runner)

    assert out["buckets"]["broken"] == 1
    assert out["broken"] == ["SPEC-X"]
    assert out["per_spec"][0]["verify_broken"] == ["runner:RuntimeError"]
    assert "boom" in out["per_spec"][0]["verify_details"][0]["stderr_tail"]


def test_conformance_uses_configured_jobs_timeout_and_budget(tmp_path, monkeypatch):
    _write_cfg(
        tmp_path,
        enabled=True,
        live_verify=True,
        jobs=3,
        requirement_jobs=2,
        verify_timeout_sec=7,
        spec_budget_sec=11,
        contract={"specs": ["SPEC-X"]},
    )
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-X": {"ok": True, "summary": {"total": 1, "attested": 1},
                   "requirements": [{"id": "R1", "verify": "true"}]},
    }))

    out = conformance_status(str(tmp_path), verify_runner=lambda root, reqs: (1, [], []))

    assert out["jobs"] == 3
    assert out["requirement_jobs"] == 2
    assert out["verify_timeout_sec"] == 7
    assert out["spec_budget_sec"] == 11


def test_conformance_fail_fast_defaults_true_but_can_be_disabled(tmp_path, monkeypatch):
    _write_cfg(tmp_path, enabled=True, live_verify=True, contract={"specs": ["SPEC-X"]})
    monkeypatch.setattr("apatch.spec.spec_status_workspace", _status(**{
        "SPEC-X": {"ok": True, "summary": {"total": 2, "attested": 2},
                   "requirements": [
                       {"id": "R1", "verify": 'python3 -c "import sys; sys.exit(1)"'},
                       {"id": "R2", "verify": 'python3 -c "pass"'},
                   ]},
    }))

    fast = conformance_status(str(tmp_path), live=True, jobs=1)
    exhaustive = conformance_status(str(tmp_path), live=True, jobs=1, fail_fast=False)

    assert fast["fail_fast"] is True
    assert fast["per_spec"][0]["verify_ran"] == 1
    assert exhaustive["fail_fast"] is False
    assert exhaustive["per_spec"][0]["verify_ran"] == 2
