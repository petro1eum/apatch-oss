"""Remote policy onboarding helpers."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from apatch.remote.errors import RemoteTaskError
from apatch.remote.policy import DEFAULT_REMOTE_TASK_OPERATIONS, resolve_remote_target


def build_remote_config(
    *,
    alias: str,
    host: str,
    path: str,
    display: Optional[str] = None,
    health_url: Optional[str] = None,
    service: Optional[str] = None,
    service_kind: str = "http",
    service_unit: Optional[str] = None,
    compose_file: Optional[str] = None,
    compose_service: Optional[str] = None,
    source_handoff: bool = False,
    source_roots: Optional[Iterable[str]] = None,
    python: Optional[str] = None,
    runtime_path: Optional[str] = None,
    ssh_args: Optional[Sequence[str]] = None,
    timeout_sec: int = 600,
    health_timeout_sec: int = 60,
    allowed_hosts: Optional[Iterable[str]] = None,
    allowed_roots: Optional[Iterable[str]] = None,
    redact: bool = True,
) -> Dict[str, Any]:
    alias = _require_text(alias, "alias")
    host = _require_text(host, "host")
    path = _require_text(path, "path")
    if not path.startswith("/"):
        raise RemoteTaskError(
            "REMOTE_CONFIG_INVALID",
            "Remote path must be absolute.",
            recoverable=True,
            recommended_action="Use the absolute path to the remote git workspace.",
        )

    target: Dict[str, Any] = {
        "host": host,
        "path": path.rstrip("/") or "/",
        "display": display or alias,
        "redact": redact,
        "timeout_sec": int(timeout_sec),
        "allowed_operations": list(DEFAULT_REMOTE_TASK_OPERATIONS),
    }
    if python:
        target["python"] = python
    if ssh_args:
        target["ssh_args"] = [str(item) for item in ssh_args]
    if runtime_path:
        runtime = _require_text(runtime_path, "runtime_path")
        if not runtime.startswith("/"):
            raise RemoteTaskError("REMOTE_CONFIG_INVALID", "Runtime path must be absolute.", recoverable=True)
        target["apatch_runtime"] = {"path": runtime.rstrip("/") or "/"}

    service_requested = bool(
        health_url
        or service_kind != "http"
        or service_unit
        or compose_file
        or compose_service
    )
    if service_requested:
        service_name = service or "api"
        service_cfg: Dict[str, Any] = {
            "kind": service_kind,
            "allowed": _default_service_actions(
                service_kind,
                has_healthcheck=bool(health_url),
            ),
        }
        if health_url:
            service_cfg["healthcheck"] = {
                "url": health_url,
                "timeout_sec": int(health_timeout_sec),
            }
        if service_kind == "systemd":
            service_cfg["unit"] = _require_text(service_unit or service_name, "service_unit")
        elif service_kind == "docker_compose":
            service_cfg["service"] = compose_service or service_name
            if compose_file:
                service_cfg["file"] = compose_file
        target["services"] = {service_name: service_cfg}

    if source_handoff:
        target["source_handoff"] = {
            "enabled": True,
            "allowed_modes": ["workspace_overlay"],
            "local_roots": list(source_roots or [os.path.abspath(os.path.expanduser("."))]),
        }

    return {
        "allowed_hosts": list(allowed_hosts or [host]),
        "allowed_roots": list(allowed_roots or [target["path"]]),
        "targets": {alias: target},
    }


def write_remote_config(
    *,
    target_dir: str,
    config: Mapping[str, Any],
    out_path: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    path = out_path or os.path.join(target_dir, ".apatch", "remote.json")
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing: Dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        incoming_targets = (config.get("targets") if isinstance(config, Mapping) else {}) or {}
        existing_targets = existing.get("targets") or {}
        collisions = sorted(set(existing_targets).intersection(incoming_targets))
        if collisions and not force:
            raise RemoteTaskError(
                "REMOTE_CONFIG_EXISTS",
                "Remote alias already exists.",
                recoverable=True,
                recommended_action="Use --force to replace the alias or choose a different alias.",
            )

    merged = _merge_policy(existing, config)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return {
        "ok": True,
        "path": path,
        "aliases": sorted((merged.get("targets") or {}).keys()),
        "agent_next": "apatch remote validate --alias <alias>",
    }


def validate_remote_config(*, target_dir: str, alias: str) -> Dict[str, Any]:
    target = resolve_remote_target(alias, policy_root=target_dir)
    return {
        "ok": True,
        "alias": alias,
        "target": target.as_dict(),
        "policy_path": os.environ.get("APATCH_REMOTE_POLICY")
        or os.path.join(os.path.abspath(os.path.expanduser(target_dir)), ".apatch", "remote.json"),
    }


def _merge_policy(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    for key in ("allowed_hosts", "allowed_roots"):
        merged[key] = _unique([*(merged.get(key) or []), *(incoming.get(key) or [])])
    targets = dict(merged.get("targets") or {})
    targets.update(dict(incoming.get("targets") or {}))
    merged["targets"] = targets
    return merged


def _unique(items: Iterable[Any]) -> list:
    out = []
    seen = set()
    for item in items:
        text = str(item)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _require_text(value: str, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise RemoteTaskError(
            "REMOTE_CONFIG_INVALID",
            "{} is required.".format(field),
            recoverable=True,
            recommended_action="Provide --{}.".format(field.replace("_", "-")),
        )
    return text


def _default_service_actions(kind: str, *, has_healthcheck: bool) -> list:
    normalized = (kind or "http").strip()
    actions = []
    if normalized in {"systemd", "docker_compose"}:
        actions.extend(["status", "restart", "logs"])
    if has_healthcheck:
        actions.append("healthcheck")
    return actions
