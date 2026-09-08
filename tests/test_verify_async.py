"""Async verify jobs (RFP-021 AR-2 / SPEC-VERIFY-ASYNC-1)."""

from __future__ import annotations

import json
import sys
import time

import pytest

from apatch.runtime.runtime import MutationRuntime
from apatch.verify_jobs import (
    _save_job,
    estimate_prior_duration,
    job_log_path,
    job_path,
    poll_verify_job,
    should_force_async,
    start_verify_job,
    sync_max_sec,
)


def test_job_file_roundtrip(tmp_path):
    job = {
        "job_id": "vjob_test_abc",
        "state": "running",
        "verify_command": [sys.executable, "-c", "print('hi')"],
        "cmd_fingerprint": "deadbeef",
        "pid": 1,
        "started_at": "2026-06-12T00:00:00+00:00",
        "finished_at": None,
        "duration_sec": None,
        "session_id": None,
        "baseline": "off",
        "allowed_failures": [],
        "returncode": None,
    }
    _save_job(str(tmp_path), job)
    loaded = __import__("json").load(open(job_path(str(tmp_path), "vjob_test_abc"), encoding="utf-8"))
    assert loaded["job_id"] == "vjob_test_abc"
    assert loaded["state"] == "running"


def test_verify_run_async_returns_job_id(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    cmd = [sys.executable, "-c", "import time; time.sleep(0.3); print('ok')"]
    res = rt.verify_run(verify=cmd, async_mode=True, skip_transition_check=True)
    assert res["ok"] is True
    assert res.get("verify_job_id")
    assert res.get("verify_job_state") == "running"
    assert "apatch_verify_status" in res.get("poll", "")


def test_verify_status_poll_pass_and_fail(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    ok_cmd = [sys.executable, "-c", "print('done')"]
    job = rt.verify_run(verify=ok_cmd, async_mode=True, skip_transition_check=True)
    jid = job["verify_job_id"]
    deadline = time.time() + 5
    terminal = None
    while time.time() < deadline:
        terminal = rt.verify_job_status(jid)
        if terminal.get("state") != "running":
            break
        time.sleep(0.05)
    assert terminal is not None
    assert terminal.get("verify_job_state") == "passed"
    assert terminal.get("ok") is True

    fail_cmd = [
        sys.executable,
        "-c",
        "print('FAILED tests/x.py::t - boom'); import sys; sys.exit(1)",
    ]
    job2 = rt.verify_run(verify=fail_cmd, async_mode=True, skip_transition_check=True)
    jid2 = job2["verify_job_id"]
    deadline = time.time() + 5
    failed = None
    while time.time() < deadline:
        failed = rt.verify_job_status(jid2)
        if failed.get("state") != "running":
            break
        time.sleep(0.05)
    assert failed.get("verify_job_state") == "failed"
    assert failed.get("ok") is False


def test_forced_async_from_prior_duration(tmp_path, monkeypatch):
    root = str(tmp_path)
    cmd = [sys.executable, "-c", "import time; time.sleep(1.2)"]
    monkeypatch.setenv("APATCH_VERIFY_SYNC_MAX_SEC", "1")
    rt = MutationRuntime(root)
    first = rt.verify_run(verify=cmd, async_mode=True, skip_transition_check=True)
    jid = first["verify_job_id"]
    deadline = time.time() + 8
    while time.time() < deadline:
        st = rt.verify_job_status(jid)
        if st.get("state") != "running":
            break
        time.sleep(0.05)
    prior = estimate_prior_duration(root, cmd)
    assert prior is not None
    assert prior > sync_max_sec()
    forced, reason = should_force_async(root, cmd)
    assert forced is True
    assert "prior_duration_sec" in reason
    promoted = rt.verify_run(verify=cmd, async_mode=False, skip_transition_check=True)
    assert promoted.get("async_forced") is True
    assert promoted.get("verify_job_state") == "running"


def test_async_baseline_compare_pre_existing(tmp_path):
    rt = MutationRuntime(str(tmp_path))

    def failing(node):
        return [
            sys.executable,
            "-c",
            f"print('FAILED {node} - boom'); import sys; sys.exit(1)",
        ]

    cap = rt.verify_run(
        verify=failing("tests/old.py::test_legacy"),
        baseline="capture",
        skip_transition_check=True,
    )
    assert cap["ok"] is True

    job = rt.verify_run(
        verify=failing("tests/old.py::test_legacy"),
        baseline="compare",
        async_mode=True,
        skip_transition_check=True,
    )
    jid = job["verify_job_id"]
    deadline = time.time() + 5
    result = None
    while time.time() < deadline:
        result = rt.verify_job_status(jid)
        if result.get("state") != "running":
            break
        time.sleep(0.05)
    assert result.get("ok") is True
    assert result.get("pre_existing_only") is True
    assert result["baseline"]["new_failures"] == []


def test_cli_verify_run_async_flag(tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    runner = CliRunner()
    cmd = f'{sys.executable} -c "print(1)"'
    result = runner.invoke(
        cli,
        [
            "verify",
            "run",
            "--async",
            "--verify",
            cmd,
            "--json",
            "--target-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    data = __import__("json").loads(result.output)
    assert data.get("verify_job_id")
    assert data.get("verify_job_state") == "running"


def _wait_for_terminal(rt, job_id, timeout=10):
    deadline = time.time() + timeout
    result = None
    while time.time() < deadline:
        result = rt.verify_job_status(job_id)
        if result.get("state") != "running":
            return result
        time.sleep(0.05)
    return result or {}


def test_async_large_output_does_not_block_worker_pipe(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    size = 512 * 1024
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys; "
            f"sys.stdout.write('o' * {size}); "
            f"sys.stderr.write('e' * {size})"
        ),
    ]
    job = rt.verify_run(verify=cmd, async_mode=True, skip_transition_check=True)
    terminal = _wait_for_terminal(rt, job["verify_job_id"])
    assert terminal.get("verify_job_state") == "passed"
    log = open(
        job_log_path(str(tmp_path), job["verify_job_id"]),
        encoding="utf-8",
    ).read()
    assert len(log) >= size * 2


def test_async_job_survives_loss_of_mcp_process_handle(tmp_path):
    import apatch.verify_jobs as verify_jobs

    rt = MutationRuntime(str(tmp_path))
    cmd = [
        sys.executable,
        "-c",
        "import time; time.sleep(0.3); print('restart-safe')",
    ]
    job = rt.verify_run(verify=cmd, async_mode=True, skip_transition_check=True)
    verify_jobs._RUNNING.pop(job["verify_job_id"], None)
    terminal = _wait_for_terminal(rt, job["verify_job_id"])
    assert terminal.get("verify_job_state") == "passed"


def test_lost_worker_is_persisted_as_terminal_failure(tmp_path):
    root = str(tmp_path)
    job_id = "vjob_lost_worker"
    _save_job(root, {
        "job_id": job_id,
        "state": "running",
        "verify_command": [sys.executable, "-c", "print('never')"],
        "cmd_fingerprint": "lost",
        "pid": 99999999,
        "worker_pid": 99999999,
        "started_at": "2026-06-12T00:00:00+00:00",
        "finished_at": None,
        "duration_sec": None,
        "session_id": None,
        "baseline": "off",
        "allowed_failures": [],
        "returncode": None,
    })
    result = poll_verify_job(root, job_id)
    assert result.get("verify_job_state") == "failed"
    saved = json.load(open(job_path(root, job_id), encoding="utf-8"))
    assert saved["state"] == "failed"
    assert saved["returncode"] == 1


def test_ultra_fast_async_jobs_cannot_regress_terminal_state(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    jobs = [
        rt.verify_run(
            verify=[sys.executable, "-c", "pass"],
            async_mode=True,
            skip_transition_check=True,
        )
        for _ in range(12)
    ]
    for job in jobs:
        terminal = _wait_for_terminal(rt, job["verify_job_id"])
        assert terminal.get("verify_job_state") == "passed"
        saved = json.load(
            open(job_path(str(tmp_path), job["verify_job_id"]), encoding="utf-8")
        )
        assert saved["state"] == "passed"


def test_async_verify_returns_before_subprocess_finishes(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    slow = [sys.executable, "-c", "import time; time.sleep(2); print('ok')"]
    t0 = time.time()
    res = rt.verify_run(verify=slow, async_mode=True, skip_transition_check=True)
    elapsed = time.time() - t0
    assert elapsed < 1.0
    assert res.get("verify_job_state") == "running"
    jid = res["verify_job_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        st = rt.verify_job_status(jid)
        if st.get("state") != "running":
            break
        time.sleep(0.1)
    assert st.get("verify_job_state") == "passed"
