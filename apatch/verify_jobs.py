"""Async verify jobs — MCP-safe long shell verify (RFP-021 AR-2)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from apatch.tool_paths import build_subprocess_env, materialize_verify_argv, materialize_verify_command

VERIFY_JOBS_REL = ".apatch/verify_jobs"
DEFAULT_SYNC_MAX_SEC = 45


def sync_max_sec() -> int:
    raw = os.environ.get("APATCH_VERIFY_SYNC_MAX_SEC", "").strip()
    if not raw:
        return DEFAULT_SYNC_MAX_SEC
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SYNC_MAX_SEC


def jobs_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), VERIFY_JOBS_REL.replace("/", os.sep))


def job_path(root: str, job_id: str) -> str:
    return os.path.join(jobs_dir(root), f"{job_id}.json")


def job_log_path(root: str, job_id: str) -> str:
    return os.path.join(jobs_dir(root), f"{job_id}.log")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cmd_fingerprint(verify_cmd: Union[str, Sequence[str]]) -> str:
    if isinstance(verify_cmd, (list, tuple)):
        text = "\x1f".join(str(a) for a in verify_cmd)
    else:
        text = str(verify_cmd or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_job(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_job(root: str, job: Dict[str, Any]) -> str:
    os.makedirs(jobs_dir(root), exist_ok=True)
    path = job_path(root, str(job["job_id"]))
    tmp_path = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
    try:
        from apatch.artifact_governance import register_on_write

        rel = os.path.relpath(path, os.path.abspath(root)).replace("\\", "/")
        register_on_write(
            root,
            rel,
            class_name="EPHEMERAL",
            created_by_tool="apatch_verify_run",
        )
    except Exception:
        pass
    return path


def estimate_prior_duration(root: str, verify_cmd: Union[str, Sequence[str]]) -> Optional[float]:
    """Max duration_sec among completed jobs with the same verify fingerprint."""
    d = jobs_dir(root)
    if not os.path.isdir(d):
        return None
    fp = _cmd_fingerprint(verify_cmd)
    best: Optional[float] = None
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        job = _load_job(os.path.join(d, name))
        if not job or job.get("cmd_fingerprint") != fp:
            continue
        if job.get("state") not in ("passed", "failed"):
            continue
        dur = job.get("duration_sec")
        if dur is None:
            continue
        try:
            val = float(dur)
        except (TypeError, ValueError):
            continue
        best = val if best is None else max(best, val)
    return best


def should_force_async(
    root: str, verify_cmd: Union[str, Sequence[str]]
) -> Tuple[bool, str]:
    prior = estimate_prior_duration(root, verify_cmd)
    limit = sync_max_sec()
    if prior is not None and prior > limit:
        return True, f"prior_duration_sec={prior:.1f}>limit={limit}"
    return False, ""


def _spawn_verify(
    root: str, verify_cmd: Union[str, Sequence[str]]
) -> subprocess.Popen:
    env = build_subprocess_env(root)
    cwd = os.path.abspath(root)
    if isinstance(verify_cmd, (list, tuple)):
        argv = materialize_verify_argv([str(a) for a in verify_cmd if str(a) != ""], cwd)
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
    cmd = materialize_verify_command(str(verify_cmd), cwd)
    return subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )


def _spawn_verify_worker(root: str, job_id: str) -> subprocess.Popen:
    """Launch a stdio-isolated worker that owns and finalizes the verify job."""
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "apatch.verify_worker",
            "--root",
            os.path.abspath(root),
            "--job-id",
            job_id,
        ],
        cwd=os.path.abspath(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=build_subprocess_env(root),
        close_fds=True,
        start_new_session=True,
    )


def _finalize_shell_result(
    root: str,
    *,
    ok: bool,
    err: str,
    verify_cmd: Union[str, Sequence[str]],
    baseline_mode: str,
    allowed_failures: Optional[List[str]],
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.tool_paths import materialize_verify_command

    resolved_cmd = materialize_verify_command(verify_cmd, root)
    result: Dict[str, Any] = {
        "ok": ok,
        "verify_command": verify_cmd,
        "verify_command_resolved": resolved_cmd,
    }
    baseline_mode = (baseline_mode or "off").strip().lower()
    if baseline_mode == "capture":
        from apatch.verify_baseline import parse_failed_tests, save_baseline

        failures = parse_failed_tests(err)
        save_baseline(
            root,
            verify_command=resolved_cmd,
            failures=failures,
            session_id=session_id,
        )
        result["ok"] = True
        result["baseline"] = {
            "mode": "capture",
            "failures": failures,
            "verify_exit_ok": ok,
        }
        return result
    if not ok and (baseline_mode == "compare" or allowed_failures):
        from apatch.verify_baseline import compare_failures, load_baseline, parse_failed_tests

        current = parse_failed_tests(err)
        base_data = (
            load_baseline(
                root,
                session_id=session_id,
                verify_command=resolved_cmd,
            )
            if baseline_mode == "compare"
            else None
        )
        report = compare_failures(
            current,
            baseline=(base_data or {}).get("failures") or [],
            allowed_failures=allowed_failures or [],
        )
        report["mode"] = baseline_mode
        if baseline_mode == "compare":
            report["baseline_found"] = base_data is not None
        if not current:
            report["unparsed_output"] = True
        result["baseline"] = report
        if current and not report["new_failures"]:
            ok = True
            result["ok"] = True
            result["pre_existing_only"] = True
            result["note"] = (
                "verify exited non-zero but every failure is "
                "pre-existing (baseline) or allow-listed — not blocking"
            )
            result["verify_output"] = err
    if not ok:
        result["error"] = err
        result["verify_output"] = err
        from apatch.build_diagnose import enrich_verify_failure

        enrich_verify_failure(result, root, log_text=err, verify=resolved_cmd)
    return result


def start_verify_job(
    root: str,
    verify_cmd: Union[str, Sequence[str]],
    *,
    baseline: str = "off",
    allowed_failures: Optional[List[str]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    job_id = f"vjob_{int(time.time())}_{secrets.token_hex(4)}"
    job: Dict[str, Any] = {
        "job_id": job_id,
        "state": "running",
        "verify_command": verify_cmd,
        "cmd_fingerprint": _cmd_fingerprint(verify_cmd),
        "pid": None,
        "worker_pid": None,
        "started_at": _now_iso(),
        "finished_at": None,
        "duration_sec": None,
        "session_id": session_id,
        "baseline": baseline,
        "allowed_failures": list(allowed_failures or []),
        "returncode": None,
    }
    _save_job(root, job)
    worker = _spawn_verify_worker(root, job_id)

    # The worker waits for this pid publication before it may execute, so the
    # parent can never overwrite a terminal result from an ultra-fast command.
    latest = _load_job(job_path(root, job_id)) or job
    latest["pid"] = worker.pid
    latest["worker_pid"] = worker.pid
    _save_job(root, latest)
    return {
        "ok": True,
        "verify_job_id": job_id,
        "verify_job_state": "running",
        "poll": f"apatch_verify_status(job_id='{job_id}')",
        "pid": worker.pid,
    }


# Legacy in-process handles for jobs launched by an older MCP implementation.
_RUNNING: Dict[str, subprocess.Popen] = {}


def _read_process_output(proc: subprocess.Popen) -> Tuple[int, str]:
    try:
        stdout, stderr = proc.communicate(timeout=0.1)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    parts: List[str] = []
    if stdout and stdout.strip():
        parts.append(f"stdout:\n{stdout.rstrip()}")
    if stderr and stderr.strip():
        parts.append(f"stderr:\n{stderr.rstrip()}")
    err = "\n".join(parts) if parts else ""
    rc = proc.returncode if proc.returncode is not None else 1
    return rc, err


def _complete_job(
    root: str,
    job: Dict[str, Any],
    *,
    returncode: int,
    log_text: str,
) -> Dict[str, Any]:
    ok = returncode == 0
    err = log_text
    if not ok and not err:
        err = f"verify failed (exit {returncode})"
    verify_result = _finalize_shell_result(
        root,
        ok=ok,
        err=err,
        verify_cmd=job.get("verify_command") or "",
        baseline_mode=str(job.get("baseline") or "off"),
        allowed_failures=job.get("allowed_failures") or None,
        session_id=str(job.get("session_id") or "") or None,
    )
    finished = time.time()
    try:
        started = datetime.fromisoformat(str(job.get("started_at")).replace("Z", "+00:00"))
        duration = max(0.0, finished - started.timestamp())
    except (TypeError, ValueError):
        duration = None
    terminal_state = "passed" if verify_result.get("ok") else "failed"
    log_path = job_log_path(root, str(job["job_id"]))
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(log_text or "")
    except OSError:
        pass
    job.update(
        {
            "state": terminal_state,
            "finished_at": _now_iso(),
            "duration_sec": duration,
            "returncode": returncode,
            "verify_result": verify_result,
        }
    )
    # Publish terminal JSON only after the corresponding log is durable.
    _save_job(root, job)
    out = dict(verify_result)
    out["verify_job_id"] = job["job_id"]
    out["verify_job_state"] = terminal_state
    out["state"] = terminal_state
    if duration is not None:
        out["duration_sec"] = duration
    return out


def run_verify_job_worker(root: str, job_id: str) -> int:
    """Execute one registered job outside the MCP process and persist its result."""
    root = os.path.abspath(root)
    # Launch handshake: do not execute until the parent has atomically
    # published this worker pid. This prevents the parent from racing a tiny
    # command's terminal write with a stale "running" snapshot.
    deadline = time.monotonic() + 5.0
    job: Optional[Dict[str, Any]] = None
    last: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        last = _load_job(job_path(root, job_id))
        if not last:
            return 2
        if last.get("state") in ("passed", "failed"):
            return 0 if last.get("state") == "passed" else 1
        try:
            published_pid = int(last.get("worker_pid") or 0)
        except (TypeError, ValueError):
            published_pid = 0
        if published_pid == os.getpid():
            job = last
            break
        time.sleep(0.01)

    if job is None:
        if last:
            _complete_job(
                root,
                last,
                returncode=1,
                log_text=f"async verify worker launch handshake timed out for {job_id}",
            )
        return 1

    try:
        proc = _spawn_verify(root, job.get("verify_command") or "")
        stdout, stderr = proc.communicate()
        log_text = "\n".join(
            part for part in (stdout or "", stderr or "") if part
        ).strip()
        if not log_text:
            log_text = (
                f"verify failed (exit {proc.returncode})"
                if proc.returncode
                else ""
            )
        result = _complete_job(
            root,
            job,
            returncode=proc.returncode or 0,
            log_text=log_text,
        )
        return 0 if result.get("ok") else 1
    except BaseException as exc:
        _complete_job(
            root,
            job,
            returncode=1,
            log_text=f"async verify worker failed: {type(exc).__name__}: {exc}",
        )
        return 1


def latest_verify_job_for_session(
    root: str, session_id: str
) -> Optional[Dict[str, Any]]:
    """Most recent verify job for a governed session (reconnect reconcile)."""
    d = jobs_dir(root)
    if not os.path.isdir(d) or not session_id:
        return None
    latest: Optional[Dict[str, Any]] = None
    latest_key = ""
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        job = _load_job(os.path.join(d, name))
        if not job or str(job.get("session_id") or "") != str(session_id):
            continue
        key = str(job.get("finished_at") or job.get("started_at") or name)
        if key >= latest_key:
            latest_key = key
            latest = job
    return latest


def poll_verify_job(root: str, job_id: str) -> Dict[str, Any]:
    job = _load_job(job_path(root, job_id))
    if not job:
        return {"ok": False, "error": f"verify job not found: {job_id}"}

    if job.get("state") in ("passed", "failed"):
        vr = job.get("verify_result") or {}
        out = dict(vr) if isinstance(vr, dict) else {}
        out["verify_job_id"] = job_id
        out["verify_job_state"] = job.get("state")
        out["state"] = job.get("state")
        out["duration_sec"] = job.get("duration_sec")
        return out

    # Legacy in-process handles are still understood for jobs launched before
    # the detached-worker implementation was loaded.
    proc = _RUNNING.get(job_id)
    if proc is not None:
        rc = proc.poll()
        if rc is None:
            return {
                "ok": True,
                "verify_job_id": job_id,
                "verify_job_state": "running",
                "state": "running",
                "poll": f"apatch_verify_status(job_id='{job_id}')",
            }
        stdout, stderr = proc.communicate()
        log_text = "\n".join(p for p in (stdout or "", stderr or "") if p).strip()
        if not log_text:
            log_text = f"verify failed (exit {rc})" if rc else ""
        _RUNNING.pop(job_id, None)
        return _complete_job(root, job, returncode=rc or 0, log_text=log_text)

    pid = job.get("worker_pid") or job.get("pid")
    if pid:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return _complete_job(
                root,
                job,
                returncode=1,
                log_text=f"verify job {job_id} lost worker process (pid {pid})",
            )
        return {
            "ok": True,
            "verify_job_id": job_id,
            "verify_job_state": "running",
            "state": "running",
            "poll": f"apatch_verify_status(job_id='{job_id}')",
        }

    return _complete_job(
        root,
        job,
        returncode=1,
        log_text=f"verify job {job_id} has no worker pid",
    )
