"""CDD-G05: real disk/process budgets and real frozen verifier subprocesses."""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from apatch import sdd_integrity as sdd
from apatch.task_budget import TaskBudget, TaskBudgetError
from tests.test_sdd_verifier_closure import _fixture, _start


def envelope_with(envelope, **limits):
    return sdd.validate_task_envelope({
        **{k: v for k, v in envelope.items() if k != "document_hash"},
        "budgets": {**envelope["budgets"], **limits},
    })


def test_reservations_survive_real_process_restart_and_lane_change(tmp_path, monkeypatch):
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    envelope = envelope_with(envelope, insertions=2, deletions=1)
    budget = TaskBudget(str(tmp_path), envelope)
    first = budget.reserve_write("src/feature.py", "a\n", "a\nb\n")
    monkeypatch.setenv("APATCH_LANE", "another-lane")
    code = """import json, sys
from apatch.task_budget import TaskBudget, TaskBudgetError
b = TaskBudget(sys.argv[1], json.loads(sys.argv[2]))
try:
    b.reserve_write('src/feature.py', 'a\\nb\\n', 'a\\nb\\nc\\nd\\n')
except TaskBudgetError as e:
    print(json.dumps(e.to_dict()))
else:
    raise AssertionError('A restarted process regained spent budget')
"""
    proc = subprocess.run([sys.executable, "-c", code, str(tmp_path), json.dumps(envelope)],
                          capture_output=True, text=True, check=True)
    result = json.loads(proc.stdout)
    assert result["error_code"] == "task_insertions_exhausted"
    assert result["budget"]["insertions"] == 1
    assert result["budget"]["deadline"] == first["deadline"]
    assert TaskBudget(str(tmp_path), envelope).check()["insertions"] == 1


def test_unique_files_and_gross_deltas_are_cumulative_not_net_diff(tmp_path):
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    envelope = envelope_with(envelope, files=1, insertions=1, deletions=1)
    budget = TaskBudget(str(tmp_path), envelope)
    budget.reserve_write("src/feature.py", "a\n", "b\n")
    with pytest.raises(TaskBudgetError, match="insertions_exhausted"):
        TaskBudget(str(tmp_path), envelope).reserve_write("src/feature.py", "b\n", "a\n")
    with pytest.raises(TaskBudgetError, match="files_exhausted"):
        budget.reserve_write("src/other.py", "", "another file\n")
    state = budget.check()
    assert state["files"] == ["src/feature.py"]
    assert (state["insertions"], state["deletions"]) == (1, 1)


def test_deadline_survives_retry_and_counts_downtime(tmp_path, monkeypatch):
    import apatch.task_budget as module
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)
    initial = TaskBudget(str(tmp_path), envelope).check()
    monkeypatch.setattr(module.time, "time", lambda: 1022.0)
    resumed = TaskBudget(str(tmp_path), envelope).check()
    assert resumed["deadline"] == initial["deadline"] == 1030.0
    assert resumed["seconds_remaining"] == 8.0
    monkeypatch.setattr(module.time, "time", lambda: 1031.0)
    with pytest.raises(TaskBudgetError, match="seconds_exhausted"):
        TaskBudget(str(tmp_path), envelope).check()


@pytest.mark.parametrize("damage", ["{bad json", "{}", "[]"])
def test_corrupt_state_never_grants_a_fresh_budget(tmp_path, damage):
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    budget = TaskBudget(str(tmp_path), envelope)
    budget.check()
    budget.path.write_text(damage)
    with pytest.raises(TaskBudgetError, match="task_budget_state_"):
        TaskBudget(str(tmp_path), envelope).check()


def test_zero_seconds_keeps_one_legacy_window_and_zero_quantity_denies_write(tmp_path):
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    budget = TaskBudget(str(tmp_path), envelope_with(envelope, seconds=0, insertions=0))
    assert 299 < budget.check()["seconds_remaining"] <= 300
    with pytest.raises(TaskBudgetError, match="insertions_exhausted"):
        budget.reserve_write("src/feature.py", "", "x\n")


def test_concurrent_processes_cannot_spend_the_same_remaining_line(tmp_path):
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    envelope = envelope_with(envelope, insertions=1)
    code = """import json, sys
from apatch.task_budget import TaskBudget, TaskBudgetError
try:
    TaskBudget(sys.argv[1], json.loads(sys.argv[2])).reserve_write('src/feature.py', '', 'x\\n')
except TaskBudgetError as e:
    print(e.reason)
else:
    print('allowed')
"""
    argv = [sys.executable, "-c", code, str(tmp_path), json.dumps(envelope)]
    processes = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
    outcomes = []
    for proc in processes:
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, stderr
        outcomes.append(stdout.strip())
    assert sorted(outcomes) == ["allowed", "task_insertions_exhausted"]
    assert TaskBudget(str(tmp_path), envelope).check()["insertions"] == 1


def test_clock_regression_fails_closed(tmp_path, monkeypatch):
    import apatch.task_budget as module
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    budget = TaskBudget(str(tmp_path), envelope)
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)
    budget.check()
    monkeypatch.setattr(module.time, "time", lambda: 999.0)
    with pytest.raises(TaskBudgetError, match="task_budget_clock_regressed"):
        budget.check()


def test_broad_write_allowlist_cannot_overwrite_budget_control_state(tmp_path):
    contract, envelope, _, _ = _fixture(tmp_path, material=False)
    envelope = sdd.validate_task_envelope({
        **{k: v for k, v in envelope.items() if k != "document_hash"},
        "allowed_writes": ["**"],
    })
    capability = sdd.issue_capability(contract, envelope, actor_id="performer", role="implementation")
    result = sdd.admit_effect(capability, {"surface": "mutation", "effect": "write",
                             "path": ".apatch/task_budgets/ledger.json"})
    assert result["decision"] == "denied"
    assert "task_budget_control_state" in result["reasons"]


def test_multiple_real_subprocesses_share_one_deadline(tmp_path):
    _, envelope, _, _ = _fixture(tmp_path, material=False)
    budget = TaskBudget(str(tmp_path), envelope_with(envelope, seconds=2))
    command = [sys.executable, "-c", "import time; time.sleep(1.2)"]
    started = time.monotonic()
    first = sdd._run_frozen_argv(str(tmp_path), command, timeout=2, budget=budget)
    second = sdd._run_frozen_argv(str(tmp_path), command, timeout=2, budget=budget)
    assert first["ok"] is True
    assert second["ok"] is False and second["timed_out"] is True
    assert time.monotonic() - started < 2.7
    with pytest.raises(TaskBudgetError, match="seconds_exhausted"):
        budget.check()


def test_mutant_timeout_restores_file_and_restart_cannot_restart_checks(tmp_path, monkeypatch):
    from apatch.runtime.session import end_session
    contract, envelope, source, _ = _fixture(tmp_path, judge_body=(
        "def test_feature():\n"
        "    import time\n"
        "    value = Path('src/feature.py').read_text()\n"
        "    if 'apatch frozen-verifier mutant' in value: time.sleep(5)\n"
        "    assert 'VALUE = \"green\"' in value\n"
    ))
    envelope = envelope_with(envelope, seconds=2)
    original = source.read_bytes(), source.stat().st_mode
    _start(tmp_path, contract, envelope)
    result = sdd.run_frozen_session_verification(str(tmp_path))
    assert result["ok"] is False
    assert result["error_type"] == "SDD_TASK_BUDGET_EXHAUSTED"
    assert result["falsification"]["restored_green"] is False
    assert (source.read_bytes(), source.stat().st_mode) == original
    end_session(str(tmp_path), require_binding=False)
    _start(tmp_path, contract, envelope)
    monkeypatch.setattr(sdd.subprocess, "run", lambda *a, **k: pytest.fail("Expired task ran again"))
    retried = sdd.run_frozen_session_verification(str(tmp_path))
    assert retried["error_code"] == "task_seconds_exhausted"
    assert (source.read_bytes(), source.stat().st_mode) == original
