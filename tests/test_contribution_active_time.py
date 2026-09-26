"""SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1 R9 — emitted contributions carry measured work time.

``apatch_attest`` emits the ContributionEvent before ``session_end``, so the event's
``duration_sec`` is the sub-second ceremony (SPEC-CONTRIB-TIMESHEET-1 R8). Governed
evidence claims ``active_sec`` first; these tests pin that it is the measured span from
session start through every session ledger operation, excluding breaks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from apatch import contribution as C
from apatch import timesheet as T

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {"key_id": "k" * 32, "agent_id": "tester", "ca": "legacy", "trust_level": "claimed"}
PROJECT = {"id": "project-1", "name": "demo", "remote": None}


def _row(stamp: str, op: str) -> dict:
    return {"id": op, "timestamp": stamp,
            "payload": {"governed_session_id": "apatch_sess_active", "files": {"a.py": {}},
                        "insertions": 1, "deletions": 0}}


def _event(rows, *, started="2026-09-26T10:00:00+00:00", ended=None):
    return C.build_event(
        {"session_id": "apatch_sess_active", "artifacts": [], "started_at": started, "ended_at": ended},
        identity=IDENTITY, project=PROJECT, ledger_rows=rows,
        created_at="2026-09-26T12:00:00+00:00",
    ).to_dict()


def test_r9_active_time_spans_start_through_ledger_operations():
    event = _event([_row("2026-09-26T10:00:05+00:00", "op1"), _row("2026-09-26T10:00:20+00:00", "op2")])
    assert event["session"]["active_sec"] == 20.0
    assert event["session"]["duration_sec"] == 0.0  # attest-before-end ceremony is unchanged
    assert event["session"]["ended_at"] is None


def test_r9_breaks_longer_than_the_idle_gap_are_not_claimed():
    rows = [_row("2026-09-26T10:10:00+00:00", "op1"),
            _row("2026-09-26T11:10:00+00:00", "op2"),  # 60 minute break
            _row("2026-09-26T11:15:00+00:00", "op3")]
    assert C.ACTIVE_IDLE_GAP_MIN == 30.0
    assert _event(rows)["session"]["active_sec"] == 600.0 + 300.0


def test_r9_measurement_is_deterministic_and_zero_without_a_span():
    rows = [_row("2026-09-26T10:00:30+00:00", "op1")]
    assert _event(rows) == _event(rows)
    assert _event([], started=None)["session"]["active_sec"] == 0.0
    assert _event([_row("2026-09-26T10:00:30+00:00", "op1")], started=None)["session"]["active_sec"] == 0.0


def test_r9_timesheet_hours_ignore_the_new_field():
    measured = _event([_row("2026-09-26T10:30:00+00:00", "op1")])
    legacy = json.loads(json.dumps(measured))
    legacy["session"]["active_sec"] = None
    assert T.aggregate([measured]) == T.aggregate([legacy])


_E2E = r"""
import json, sys, time
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import start_session
from apatch.session_state import PHASE_COMPLETE, load_session_state, save_session_state
from apatch.trustchain_helper import TrustChainHelper

workspace = sys.argv[1]
start_session(workspace, "measured active time")
session_id = load_session_state(workspace)["session_id"]
time.sleep(1.2)
chain = TrustChainHelper(workspace, auto_init=True)
assert chain.commit_action("apatch_apply_session", {"governed_session_id": session_id,
                                                    "files": {"src/app.py": {}}, "insertions": 3, "deletions": 1})
time.sleep(1.2)
state = load_session_state(workspace)
state["phase"] = PHASE_COMPLETE
save_session_state(workspace, state)
result = MutationRuntime(workspace).attest(message="measured active time")
print(json.dumps({"receipt": result.get("contribution_receipt"), "session_id": session_id}))
"""


def test_r9_attest_before_session_end_claims_measured_seconds(tmp_path):
    workspace, home, store = tmp_path / "workspace", tmp_path / "home", tmp_path / "store"
    workspace.mkdir()
    home.mkdir()
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT") if key in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), APATCH_CONTRIB_STORE=str(store),
               PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    probe = subprocess.run([sys.executable, "-c", _E2E, str(workspace)], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=180)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    report = json.loads(probe.stdout.strip().splitlines()[-1])
    assert report["receipt"]["status"] == "emitted", report
    [path] = list(store.glob("*/*.json"))
    session = json.loads(path.read_text(encoding="utf-8"))["session"]
    assert session["session_id"] == report["session_id"] and session["ended_at"] is None
    assert session["active_sec"] >= 2.0, session
    claimed = int(round(float(session["active_sec"])))
    assert claimed >= 2, "governed evidence would claim no work time"
