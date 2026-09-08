"""Policy-controlled remote service actions."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from typing import Any, Dict, Mapping, Optional

from apatch.remote.errors import RemoteTaskError
from apatch.remote.policy import load_remote_policy, resolve_remote_target
from apatch.remote.target import RemoteTarget


SERVICE_ACTIONS = {"status", "restart", "logs", "healthcheck", "exec"}
_DEFAULT_SERVICE_COMMAND_TIMEOUT_SEC = 120
_REMOTE_TRANSPORT_TIMEOUT_CAP_SEC = 240
_REMOTE_TRANSPORT_TIMEOUT_GRACE_SEC = 15
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
# exec — best-effort read-only diagnostic channel: reject obvious mutating commands.
# Policy-defined command actions are the intended path for maintenance operations.
_EXEC_DESTRUCTIVE_RE = re.compile(
    r"(?<![\w-])(rm|rmdir|mv|dd|mkfs\w*|shutdown|reboot|halt|poweroff|kill|pkill|"
    r"killall|truncate|shred|fdisk|parted|wipefs|tee|chmod|chown|chgrp)(?![\w-])"
)


def _assert_exec_read_only(command: str) -> None:
    if not isinstance(command, str) or not command.strip():
        raise RemoteTaskError(
            "REMOTE_EXEC_COMMAND_MISSING",
            "exec action requires a non-empty command.",
            recoverable=True,
            recommended_action="Pass command='<diagnostic shell command>' or use a policy-defined command action.",
        )
    if _EXEC_DESTRUCTIVE_RE.search(command):
        raise RemoteTaskError(
            "REMOTE_EXEC_NOT_READ_ONLY",
            "exec is a guarded diagnostic channel; the command looks state-mutating.",
            recoverable=True,
            recommended_action=(
                "Use a diagnostic command, apatch_remote_task_run for governed mutations, "
                "or a policy-defined service command for maintenance."
            ),
        )


def plan_service_action(
    *,
    alias: str,
    service: str,
    action: str,
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
    lines: int = 80,
    command: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a redacted, policy-approved remote service action plan."""

    cfg = dict(policy) if policy is not None else load_remote_policy(policy_root)
    target = resolve_remote_target(alias, policy_root=policy_root, policy=cfg)
    plan = _prepare_service_plan(
        target=target,
        cfg=cfg,
        alias=alias,
        service=service,
        action=action,
        lines=lines,
        command=command,
    )
    return _redact_plan(target, plan)


def _plan_timeout_sec(plan: Mapping[str, Any]) -> int:
    raw = plan.get("timeout_sec") or _DEFAULT_SERVICE_COMMAND_TIMEOUT_SEC
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        timeout = _DEFAULT_SERVICE_COMMAND_TIMEOUT_SEC
    return max(1, timeout)


def _transport_timeout_sec(target: RemoteTarget, plan: Mapping[str, Any]) -> int:
    policy_timeout = max(1, int(target.timeout_sec or 600))
    action_timeout = _plan_timeout_sec(plan)
    return min(
        policy_timeout,
        action_timeout + _REMOTE_TRANSPORT_TIMEOUT_GRACE_SEC,
        _REMOTE_TRANSPORT_TIMEOUT_CAP_SEC,
    )


def execute_service_action(
    *,
    alias: str,
    service: str,
    action: str,
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
    lines: int = 80,
    command: Optional[str] = None,
    transport: Optional["SshServiceTransport"] = None,
) -> Dict[str, Any]:
    cfg = dict(policy) if policy is not None else load_remote_policy(policy_root)
    target = resolve_remote_target(alias, policy_root=policy_root, policy=cfg)
    plan = _prepare_service_plan(
        target=target,
        cfg=cfg,
        alias=alias,
        service=service,
        action=action,
        lines=lines,
        command=command,
    )
    from apatch.sdd_integrity import admit_session_effect

    admit_session_effect(
        policy_root,
        {
            "surface": "service",
            "effect": action,
            "service": service,
            "target": alias,
        },
    )
    if transport is None:
        transport = SshServiceTransport(
            python=target.python or "python3",
            ssh_args=list(target.ssh_args) if target.ssh_args else None,
            timeout_sec=_transport_timeout_sec(target, plan),
        )
    result = transport.call(target, plan)
    return _redact_plan(target, result)


def _prepare_service_plan(
    *,
    target: RemoteTarget,
    cfg: Mapping[str, Any],
    alias: str,
    service: str,
    action: str,
    lines: int,
    command: Optional[str] = None,
) -> Dict[str, Any]:
    action = (action or "").strip()
    if action not in SERVICE_ACTIONS:
        _assert_safe_custom_action(action)

    target_entry = ((cfg.get("targets") or {}).get(alias) or {})
    services = target_entry.get("services") or {}
    service_cfg = services.get(service)
    if not isinstance(service_cfg, Mapping):
        raise RemoteTaskError(
            "REMOTE_SERVICE_NOT_FOUND",
            "Remote service alias is not configured.",
            recoverable=True,
            recommended_action="Add the service to .apatch/remote.json or run apatch remote init.",
        )
    allowed = set(service_cfg.get("allowed") or [])
    if action not in allowed:
        raise RemoteTaskError(
            "REMOTE_SERVICE_ACTION_DENIED",
            "Remote service policy does not allow this action.",
            recoverable=True,
            recommended_action="Update service.allowed explicitly if this action is intended.",
        )
    if action not in SERVICE_ACTIONS and not _has_policy_command(service_cfg, action):
        raise RemoteTaskError(
            "REMOTE_SERVICE_ACTION_INVALID",
            "Unsupported remote service action.",
            recoverable=True,
            recommended_action=(
                "Use one of: status, restart, logs, healthcheck, exec, or add a "
                "policy-defined command under services.<name>.commands.<action>."
            ),
        )

    plan = _build_plan(target, service, service_cfg, action, lines=lines, command=command)
    plan["ok"] = True
    plan["dry_run"] = True
    plan["target"] = target.as_dict()
    plan["service"] = service
    plan["action"] = action
    return plan


class SshServiceTransport:
    """Execute a pre-approved service plan on the remote host."""

    def __init__(
        self,
        *,
        python: str = "python3",
        ssh_args: Optional[list] = None,
        timeout_sec: int = 600,
    ) -> None:
        self.python = python
        self.ssh_args = list(ssh_args or ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
        self.timeout_sec = timeout_sec

    def call(self, target: RemoteTarget, plan: Mapping[str, Any]) -> Dict[str, Any]:
        payload = {"plan": dict(plan)}
        command = "cd {} && {} -c {}".format(
            shlex.quote(target.path),
            shlex.quote(self.python),
            shlex.quote(_REMOTE_SERVICE_SCRIPT),
        )
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
            return _error("SSH_CLIENT_MISSING", "ssh executable was not found.", plan=plan)
        except subprocess.TimeoutExpired as exc:
            return _error(
                "REMOTE_SERVICE_TIMEOUT",
                "Remote service action timed out.",
                plan=plan,
                stdout=_coerce_output(exc.stdout),
                stderr=_coerce_output(exc.stderr),
            )
        try:
            parsed = json.loads((proc.stdout or "").strip())
        except json.JSONDecodeError:
            parsed = None
        if proc.returncode != 0 and not parsed:
            return _error(
                "REMOTE_SERVICE_COMMAND_FAILED",
                "Remote service action failed before returning JSON.",
                plan=plan,
                ssh_returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        if not isinstance(parsed, dict):
            return _error(
                "REMOTE_SERVICE_JSON_INVALID",
                "Remote service action did not return valid JSON.",
                plan=plan,
                ssh_returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        parsed.setdefault("ok", proc.returncode == 0)
        parsed["ssh_returncode"] = proc.returncode
        return _annotate_service_result(plan, parsed)


def _build_plan(
    target: RemoteTarget,
    service: str,
    service_cfg: Mapping[str, Any],
    action: str,
    *,
    lines: int,
    command: Optional[str] = None,
) -> Dict[str, Any]:
    kind = str(service_cfg.get("kind") or "http")
    if action == "exec":
        # Diagnostic: run an operator-supplied command, return stdout.
        # Gated by service.allowed containing "exec"; obvious mutating commands are rejected.
        # For maintenance, prefer policy-defined command actions instead of exec.
        _assert_exec_read_only(command or "")
        shell = _safe_name(str(service_cfg.get("shell") or "bash"), "shell")
        return {
            "kind": "command",
            "operation": "command",
            "argv": [shell, "-lc", str(command)],
            "timeout_sec": _plan_timeout_sec(service_cfg),
        }
    if action == "healthcheck":
        health = service_cfg.get("healthcheck") or {}
        url = health.get("url")
        if not url:
            raise RemoteTaskError(
                "REMOTE_SERVICE_HEALTH_MISSING",
                "Service has no healthcheck URL.",
                recoverable=True,
                recommended_action="Add services.<name>.healthcheck.url to remote policy.",
            )
        return {
            "kind": kind,
            "operation": "http_healthcheck",
            "url": str(url),
            "timeout_sec": int(health.get("timeout_sec") or 60),
        }
    if kind == "systemd":
        unit = _safe_name(str(service_cfg.get("unit") or service), "systemd unit")
        if action == "status":
            argv = ["systemctl", "status", unit, "--no-pager"]
        elif action == "restart":
            argv = ["systemctl", "restart", unit]
        elif action == "logs":
            argv = ["journalctl", "-u", unit, "--no-pager", "-n", str(int(lines))]
        else:
            raise _unsupported(kind, action)
        return {
            "kind": kind,
            "operation": "command",
            "argv": argv,
            "timeout_sec": _plan_timeout_sec(service_cfg),
        }
    if kind == "docker_compose":
        compose_service = _safe_name(str(service_cfg.get("service") or service), "compose service")
        argv = ["docker", "compose"]
        compose_file = service_cfg.get("file")
        if compose_file:
            argv.extend(["-f", str(compose_file)])
        if action == "status":
            argv.extend(["ps", compose_service])
        elif action == "restart":
            argv.extend(["restart", compose_service])
        elif action == "logs":
            argv.extend(["logs", "--tail", str(int(lines)), compose_service])
        else:
            raise _unsupported(kind, action)
        return {
            "kind": kind,
            "operation": "command",
            "argv": argv,
            "timeout_sec": _plan_timeout_sec(service_cfg),
        }
    if kind == "command":
        commands = service_cfg.get("commands") or {}
        template = commands.get(action)
        if not template:
            raise _unsupported(kind, action)
        command_str = str(template).replace("{lines}", str(int(lines)))
        shell = _safe_name(str(service_cfg.get("shell") or "bash"), "shell")
        return {
            "kind": kind,
            "operation": "command",
            "argv": [shell, "-lc", command_str],
            "timeout_sec": _plan_timeout_sec(service_cfg),
        }
    raise RemoteTaskError(
        "REMOTE_SERVICE_KIND_UNSUPPORTED",
        "Remote service kind is not supported.",
        recoverable=True,
        recommended_action="Use kind=http, systemd, docker_compose, or command.",
    )


def _safe_name(value: str, label: str) -> str:
    if not value or not _SAFE_NAME_RE.match(value):
        raise RemoteTaskError(
            "REMOTE_SERVICE_CONFIG_INVALID",
            "{} contains unsafe characters.".format(label),
            recoverable=True,
            recommended_action="Use a simple service/unit name from policy.",
        )
    return value


def _assert_safe_custom_action(action: str) -> None:
    if not action or not _SAFE_NAME_RE.match(action):
        raise RemoteTaskError(
            "REMOTE_SERVICE_ACTION_INVALID",
            "Unsupported remote service action.",
            recoverable=True,
            recommended_action="Use a simple policy-defined action name such as cleanup_macos_metadata.",
        )


def _has_policy_command(service_cfg: Mapping[str, Any], action: str) -> bool:
    if str(service_cfg.get("kind") or "http") != "command":
        return False
    commands = service_cfg.get("commands") or {}
    return isinstance(commands, Mapping) and action in commands


def _unsupported(kind: str, action: str) -> RemoteTaskError:
    return RemoteTaskError(
        "REMOTE_SERVICE_ACTION_UNSUPPORTED",
        "{} does not support {}.".format(kind, action),
        recoverable=True,
        recommended_action="Choose an action supported by this service kind.",
    )


def _redact_plan(target: RemoteTarget, value: Any) -> Any:
    if not target.redact:
        return value
    label = target.display or target.alias or "remote"
    if isinstance(value, str):
        out = value
        for needle in (target.uri, target.path, target.host):
            if needle:
                out = out.replace(needle, label)
        return out
    if isinstance(value, Mapping):
        return {key: _redact_plan(target, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_plan(target, item) for item in value]
    return value


def _annotate_service_result(plan: Mapping[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("ok") is not False:
        return result
    operation = str(plan.get("operation") or "service_action")
    default_error = (
        "REMOTE_SERVICE_COMMAND_FAILED"
        if operation == "command"
        else "REMOTE_SERVICE_ACTION_FAILED"
    )
    result.setdefault("error_type", default_error)
    result.setdefault("message", "Remote service action returned ok:false.")
    result.setdefault("recoverable", True)
    result.setdefault(
        "recommended_action",
        "Inspect stdout/stderr/status and retry the service action; no governed mutation ran.",
    )
    return result


def _error(error_type: str, message: str, *, plan: Mapping[str, Any], **extra: Any) -> Dict[str, Any]:
    result = {
        "ok": False,
        "error_type": error_type,
        "message": message,
        "plan": dict(plan),
        "recoverable": True,
        "recommended_action": "Inspect stdout/stderr/status and retry the service action; no governed mutation ran.",
    }
    result.update(extra)
    return result


def _coerce_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


_REMOTE_SERVICE_SCRIPT = r'''
import json
import subprocess
import sys
import urllib.request


def _limited(value, limit=12000):
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    return text[:limit]


def main():
    payload = json.load(sys.stdin)
    plan = payload.get("plan") or {}
    operation = plan.get("operation")
    if operation == "http_healthcheck":
        url = plan.get("url")
        timeout = int(plan.get("timeout_sec") or 60)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read(4096)
                status = int(getattr(resp, "status", 200))
            ok = 200 <= status < 500
            sys.stdout.write(json.dumps({"ok": ok, "status": status, "body": _limited(body, 4096)}))
        except Exception as exc:
            sys.stdout.write(json.dumps({
                "ok": False,
                "error_type": "REMOTE_SERVICE_HEALTH_FAILED",
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }))
        return
    if operation == "command":
        argv = plan.get("argv")
        if not isinstance(argv, list) or not argv:
            sys.stdout.write(json.dumps({
                "ok": False,
                "error_type": "REMOTE_SERVICE_COMMAND_INVALID",
                "message": "service argv must be a non-empty list",
            }))
            return
        timeout = int(plan.get("timeout_sec") or 120)
        try:
            proc = subprocess.run(
                [str(item) for item in argv],
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            sys.stdout.write(json.dumps({
                "ok": False,
                "error_type": "REMOTE_SERVICE_COMMAND_TIMEOUT",
                "message": "remote service command timed out",
                "stdout": _limited(exc.stdout),
                "stderr": _limited(exc.stderr),
            }))
            return
        sys.stdout.write(json.dumps({
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": _limited(proc.stdout),
            "stderr": _limited(proc.stderr),
        }))
        return
    sys.stdout.write(json.dumps({
        "ok": False,
        "error_type": "REMOTE_SERVICE_OPERATION_UNSUPPORTED",
        "message": "unsupported service operation",
    }))


if __name__ == "__main__":
    main()
'''
