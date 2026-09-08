"""SSH-backed remote transport for apatch task orchestration."""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any, Dict, Mapping, Optional, Sequence

from apatch.remote.target import RemoteTarget


class SshRemoteTransport:
    """Run remote apatch operations through one JSON envelope per internal step."""

    def __init__(
        self,
        *,
        python: str = "python3",
        ssh_args: Optional[Sequence[str]] = None,
        timeout_sec: int = 600,
    ) -> None:
        self.python = python
        self.ssh_args = list(ssh_args or ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
        self.timeout_sec = timeout_sec

    def call(
        self,
        target: RemoteTarget,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> Dict[str, Any]:
        payload = {"operation": operation, "arguments": dict(arguments)}
        command = _remote_command(target.path, self.python, runtime_path=target.runtime_path)
        argv = ["ssh", *self.ssh_args, target.host, command]

        try:
            proc = subprocess.run(
                argv,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout_sec,
                check=False,
            )
        except FileNotFoundError:
            return _error(
                "SSH_CLIENT_MISSING",
                "ssh executable was not found on the local machine.",
                operation=operation,
                recoverable=True,
                recommended_action="Install OpenSSH client or provide an environment with ssh in PATH.",
            )
        except subprocess.TimeoutExpired as exc:
            return _error(
                "REMOTE_TIMEOUT",
                "Remote operation timed out.",
                operation=operation,
                recoverable=True,
                recommended_action="reconcile_remote_state",
                stdout=_coerce_output(exc.stdout),
                stderr=_coerce_output(exc.stderr),
            )

        parsed = _parse_json(proc.stdout)
        if proc.returncode != 0:
            if parsed:
                parsed.setdefault("ok", False)
                parsed.setdefault("error_type", "REMOTE_COMMAND_FAILED")
                parsed.setdefault("message", "Remote operation failed.")
                parsed["ssh_returncode"] = proc.returncode
                parsed["stderr"] = proc.stderr
                parsed["operation"] = operation
                return parsed
            return _error(
                _remote_command_error_type(proc.stderr),
                "Remote operation failed before returning JSON.",
                operation=operation,
                recoverable=True,
                recommended_action=_remote_command_recommended_action(proc.stderr),
                ssh_returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )

        if parsed is None:
            return _error(
                "REMOTE_JSON_INVALID",
                "Remote operation did not return valid JSON.",
                operation=operation,
                recoverable=True,
                recommended_action="Ensure remote Python can import apatch and no startup hook writes to stdout.",
                stdout=proc.stdout,
                stderr=proc.stderr,
            )

        parsed.setdefault("ok", True)
        parsed["operation"] = operation
        parsed["ssh_returncode"] = proc.returncode
        return parsed


def _remote_command(target_path: str, python: str, *, runtime_path: Optional[str] = None) -> str:
    env = ""
    if runtime_path:
        env = "PYTHONPATH={}:$PYTHONPATH ".format(shlex.quote(runtime_path))
    return "cd {} && {}{} -m apatch.remote.worker".format(
        shlex.quote(target_path),
        env,
        shlex.quote(python),
    )


def _remote_command_error_type(stderr: str) -> str:
    if "No module named" in (stderr or "") and "apatch" in stderr:
        return "REMOTE_APATCH_MISSING"
    return "REMOTE_COMMAND_FAILED"


def _remote_command_recommended_action(stderr: str) -> str:
    if _remote_command_error_type(stderr) == "REMOTE_APATCH_MISSING":
        return (
            "Bootstrap the apatch runtime through the local broker and configure "
            "targets.<alias>.apatch_runtime.path; do not fall back to raw scp/tar|ssh."
        )
    return "Inspect stderr and verify remote apatch installation."


def _parse_json(stdout: str) -> Optional[Dict[str, Any]]:
    """Parse clean worker JSON or the final JSON line from a noisy legacy worker."""
    text = (stdout or "").strip()
    if not text:
        return None

    candidates = [text]
    if "\n" in text:
        candidates.extend(
            line.strip()
            for line in reversed(text.splitlines())
            if line.strip().startswith(("{", "["))
        )
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else {"ok": True, "result": payload}
    return None


def _coerce_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _error(error_type: str, message: str, *, operation: str, recoverable: bool, **extra: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
        "operation": operation,
        "recoverable": recoverable,
    }
    result.update({k: v for k, v in extra.items() if v is not None})
    return result


_REMOTE_EXECUTOR_SCRIPT = r'''
import json
import sys


def _fail(error_type, message, **extra):
    result = {"ok": False, "error_type": error_type, "message": message}
    result.update({k: v for k, v in extra.items() if v is not None})
    return result


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run(payload):
    operation = payload.get("operation")
    args = payload.get("arguments") or {}
    target_dir = "."

    if operation == "apatch_doctor":
        from apatch.doctor import run_doctor

        return run_doctor(target_dir)

    if operation == "apatch_session_start":
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(target_dir).open_session(
            args.get("intent") or "remote apatch task",
            artifacts=args.get("artifacts"),
        )

    if operation == "apatch_generate_batch":
        from apatch.workflows import generate_patch_jsonl_batch

        plan = args.get("plan") or {}
        needles = plan.get("needles") or args.get("needles")
        if not isinstance(needles, list):
            return _fail("REMOTE_PLAN_INVALID", "apatch_generate_batch requires plan.needles as a list.")
        out_path = plan.get("out_path") or args.get("out_path") or ".apatch/remote/patches.jsonl"
        result = generate_patch_jsonl_batch(
            needles=needles,
            target_dir=target_dir,
            out_path=out_path,
            append=bool(plan.get("append") or args.get("append")),
            default_glob_pattern=str(plan.get("glob_pattern") or "**/*"),
            default_match_mode=str(plan.get("match_mode") or "literal"),
            default_replace_all=bool(plan.get("replace_all")),
            created_by_tool="apatch_remote_task_run",
        )
        if result.get("ok"):
            result["logs_path"] = result.get("out_path_rel") or result.get("out_path")
        return result

    if operation == "apatch_simulate":
        from apatch.workflows import simulate_workspace

        plan = args.get("plan") or {}
        logs_path = args.get("logs_path") or plan.get("logs_path") or plan.get("out_path") or ".apatch/remote/patches.jsonl"
        return simulate_workspace(
            target_dir,
            logs_path=logs_path,
            chunk_max_files=_as_int(plan.get("chunk_max_files") or args.get("chunk_max_files"), 5),
            replace_all=bool(plan.get("replace_all") or args.get("replace_all")),
            only_drifted=bool(plan.get("only_drifted") or args.get("only_drifted")),
            min_confidence=plan.get("min_confidence") or args.get("min_confidence"),
            budget=plan.get("budget") or args.get("budget"),
            max_files=plan.get("max_files") or args.get("max_files"),
            max_insertions=plan.get("max_insertions") or args.get("max_insertions"),
            max_deletions=plan.get("max_deletions") or args.get("max_deletions"),
        )

    if operation == "apatch_apply_session":
        from apatch.runtime.runtime import MutationRuntime

        plan = args.get("plan") or {}
        logs_path = args.get("logs_path") or plan.get("logs_path") or plan.get("out_path") or ".apatch/remote/patches.jsonl"
        return MutationRuntime(target_dir).apply_session(
            logs_path,
            session_path=plan.get("session_path") or args.get("session_path"),
            verify=None,
            chunk_max_files=_as_int(plan.get("chunk_max_files") or args.get("chunk_max_files"), 5),
            replace_all=bool(plan.get("replace_all") or args.get("replace_all")),
            only_drifted=bool(plan.get("only_drifted") or args.get("only_drifted")),
            min_confidence=plan.get("min_confidence") or args.get("min_confidence"),
            verify_deferred=bool(plan.get("verify_deferred", True)),
            no_trustchain=bool(plan.get("no_trustchain") or args.get("no_trustchain")),
            tool=plan.get("tool") or args.get("tool"),
            keyword=plan.get("keyword") or args.get("keyword"),
            reset=bool(plan.get("reset") or args.get("reset")),
            quiet=True,
        )

    if operation == "apatch_verify_run":
        from apatch.runtime.runtime import MutationRuntime

        verify = args.get("verify")
        if isinstance(verify, dict):
            return MutationRuntime(target_dir).verify_run(
                verify=verify.get("command"),
                semantic=bool(verify.get("semantic")),
                notarization=bool(verify.get("notarization")),
                pipeline_manifest=verify.get("pipeline_manifest"),
                dry_run=bool(verify.get("dry_run")),
                rules_path=verify.get("rules_path"),
                since=verify.get("since") or "HEAD",
                staged=bool(verify.get("staged")),
                working_tree=bool(verify.get("working_tree")),
                baseline=verify.get("baseline") or "off",
                allowed_failures=verify.get("allowed_failures"),
                async_mode=bool(verify.get("async_mode")),
            )
        return MutationRuntime(target_dir).verify_run(verify=verify)

    if operation == "apatch_attest":
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(target_dir).attest(message=args.get("message") or args.get("intent"))

    if operation == "apatch_session_end":
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(target_dir).close_session()

    return _fail("REMOTE_OPERATION_UNSUPPORTED", "Unsupported remote operation.", operation=operation)


def main():
    try:
        payload = json.load(sys.stdin)
        result = _run(payload)
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": "REMOTE_EXCEPTION",
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''
