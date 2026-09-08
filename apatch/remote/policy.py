"""Policy-backed remote target aliases."""

from __future__ import annotations

import fnmatch
import json
import os
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from apatch.remote.errors import RemoteTaskError
from apatch.remote.target import RemoteTarget, build_remote_target, parse_remote_target


DEFAULT_REMOTE_TASK_OPERATIONS: Tuple[str, ...] = (
    "apatch_doctor",
    "apatch_execute_next",
    "apatch_spec_run",
    "apatch_spec_run_multi",
    "apatch_rebind_stale",
    "apatch_session_start",
    "apatch_generate_batch",
    "apatch_simulate",
    "apatch_apply_session",
    "apatch_rollback",
    "apatch_verify_run",
    "apatch_resume_session",
    "apatch_noop_attest",
    "apatch_attest",
    "apatch_commit_attested",
    "apatch_session_end",
)


def load_remote_policy(policy_root: str = ".") -> Dict[str, Any]:
    """Load local remote alias policy from APATCH_REMOTE_POLICY or .apatch/remote.json."""

    path = os.environ.get("APATCH_REMOTE_POLICY") or os.path.join(
        os.path.abspath(os.path.expanduser(policy_root)),
        ".apatch",
        "remote.json",
    )
    if not os.path.exists(path):
        return {"targets": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "Remote policy is not valid JSON.",
            recoverable=True,
            recommended_action="Fix .apatch/remote.json or APATCH_REMOTE_POLICY.",
        ) from exc
    if not isinstance(data, dict):
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "Remote policy must be a JSON object.",
            recoverable=True,
            recommended_action="Use {\"targets\": {\"alias\": {\"host\": ..., \"path\": ...}}}.",
        )
    return data


def resolve_remote_target(
    value: str,
    *,
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
) -> RemoteTarget:
    """Resolve a direct remote URI or opaque policy alias into a RemoteTarget."""

    raw = (value or "").strip()
    try:
        return parse_remote_target(raw)
    except RemoteTaskError as exc:
        if exc.error_type != "REMOTE_TARGET_INVALID":
            raise

    cfg = dict(policy) if policy is not None else load_remote_policy(policy_root)
    targets = cfg.get("targets") or cfg.get("aliases") or {}
    if not isinstance(targets, Mapping) or raw not in targets:
        raise RemoteTaskError(
            "REMOTE_ALIAS_NOT_FOUND",
            "Remote target alias is not configured.",
            recoverable=True,
            recommended_action="Add the alias to .apatch/remote.json targets.",
        )

    entry = targets[raw]
    if not isinstance(entry, Mapping):
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "Remote alias entry must be a JSON object.",
            recoverable=True,
            recommended_action="Use {\"host\": \"...\", \"path\": \"/abs/path\"}.",
        )

    host = str(entry.get("host") or "").strip()
    path = str(entry.get("path") or entry.get("root") or "").strip()
    if not host or not path:
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "Remote alias requires host and absolute path.",
            recoverable=True,
            recommended_action="Set host and path for the alias in .apatch/remote.json.",
        )

    target = build_remote_target(
        host,
        path,
        alias=raw,
        display=str(entry.get("display") or raw),
        redact=bool(entry.get("redact", True)),
        python=_optional_str(entry.get("python")),
        runtime_path=_runtime_path(entry),
        ssh_args=_optional_str_tuple(entry.get("ssh_args")),
        timeout_sec=_optional_int(entry.get("timeout_sec")),
        allow_transport_overrides=bool(entry.get("allow_transport_overrides", False)),
        allowed_operations=_allowed_operations(cfg, entry),
    )
    _validate_allowed_host(target, cfg, entry)
    _validate_allowed_root(target, cfg, entry)
    return target


def _allowed_operations(cfg: Mapping[str, Any], entry: Mapping[str, Any]) -> Optional[Tuple[str, ...]]:
    raw = entry.get("allowed_operations", cfg.get("allowed_operations", DEFAULT_REMOTE_TASK_OPERATIONS))
    if raw == "*":
        return None
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, dict)):
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "allowed_operations must be an array of operation names or '*'.",
            recoverable=True,
            recommended_action="Use the default remote task operation list or '*'.",
        )
    return tuple(str(item) for item in raw)


def _validate_allowed_host(
    target: RemoteTarget,
    cfg: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> None:
    allowed = entry.get("allowed_hosts", cfg.get("allowed_hosts"))
    if not allowed:
        return
    if _matches_any(target.host, allowed):
        return
    raise RemoteTaskError(
        "REMOTE_HOST_DENIED",
        "Remote host is outside policy allowlist.",
        recoverable=True,
        recommended_action="Choose an allowed alias or update .apatch/remote.json.",
    )


def _validate_allowed_root(
    target: RemoteTarget,
    cfg: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> None:
    allowed = entry.get("allowed_roots", cfg.get("allowed_roots"))
    if not allowed:
        return
    if _matches_any(target.path, allowed):
        return
    raise RemoteTaskError(
        "REMOTE_ROOT_DENIED",
        "Remote root is outside policy allowlist.",
        recoverable=True,
        recommended_action="Choose an allowed alias or update allowed_roots.",
    )


def _matches_any(value: str, patterns: Any) -> bool:
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, Iterable):
        return False
    return any(fnmatch.fnmatch(value, str(pattern)) for pattern in patterns)


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _runtime_path(entry: Mapping[str, Any]) -> Optional[str]:
    runtime = entry.get("apatch_runtime")
    raw = entry.get("runtime_path")
    if isinstance(runtime, Mapping):
        raw = runtime.get("path", raw)
    text = _optional_str(raw)
    if not text:
        return None
    if not text.startswith("/"):
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "apatch_runtime.path must be absolute.",
            recoverable=True,
            recommended_action="Use an absolute remote runtime path such as /home/user/.local/src/apatch_runtime.",
        )
    return "/" if text == "/" else text.rstrip("/")


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "timeout_sec must be an integer.",
            recoverable=True,
            recommended_action="Set timeout_sec to a positive integer.",
        )


def _optional_str_tuple(value: Any) -> Optional[Tuple[str, ...]]:
    if value is None:
        return None
    if not isinstance(value, list):
        raise RemoteTaskError(
            "REMOTE_POLICY_INVALID",
            "ssh_args must be an array.",
            recoverable=True,
            recommended_action="Use ssh_args as a JSON array of argv entries.",
        )
    return tuple(str(item) for item in value)
