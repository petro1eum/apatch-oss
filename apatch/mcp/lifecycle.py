"""MCP stdio process lifecycle — lease cleanup on shutdown, stale lease sweep."""

from __future__ import annotations

import atexit
import os
import signal
import threading
from typing import Any, Dict, List, Set

_HOOKS_REGISTERED = False
_HOOK_LOCK = threading.Lock()
_TOUCHED_WORKSPACES: Set[str] = set()
_SWEPT_WORKSPACES: Set[str] = set()


def sweep_stale_lease(root: str) -> Dict[str, Any]:
    """Prune expired/dead path leases without touching unrelated live owners."""
    from apatch.path_leases import sweep_stale_leases
    from apatch.sandbox import _lease_valid, load_active_leases

    root = os.path.abspath(root)
    if root in _SWEPT_WORKSPACES:
        return {"root": root, "swept": False, "reason": "already_swept"}
    _SWEPT_WORKSPACES.add(root)

    before = load_active_leases(root)
    released = sweep_stale_leases(root)
    if not released and before and all(_lease_valid(cap) for cap in before):
        return {
            "root": root,
            "swept": False,
            "reason": "lease_valid",
            "pid": before[0].get("pid"),
        }
    return {
        "root": root,
        "swept": bool(released),
        "reason": "stale_lease_removed" if released else "no_stale_leases",
        "lease_ids": released,
    }


def touch_workspace(root: str) -> Dict[str, Any]:
    """Track workspace for shutdown cleanup; sweep stale lease once per process."""
    root = os.path.abspath(root)
    with _HOOK_LOCK:
        first_touch = root not in _TOUCHED_WORKSPACES
        _TOUCHED_WORKSPACES.add(root)
    result = sweep_stale_lease(root)
    if first_touch:
        try:
            from apatch.mcp_health import write_mcp_fingerprint

            write_mcp_fingerprint(root)
        except Exception:
            pass
    return result


def sweep_registered_workspaces() -> List[Dict[str, Any]]:
    """Reconcile stale lease state for every human-registered local workspace."""
    try:
        from apatch.mcp.workspace_registry import load_workspace_registry

        data = load_workspace_registry()
    except Exception:
        return []
    results: List[Dict[str, Any]] = []
    for entry in (data.get("workspaces") or {}).values():
        if not isinstance(entry, dict):
            continue
        root = os.path.realpath(os.path.abspath(str(entry.get("path") or "")))
        if root and os.path.isdir(os.path.join(root, ".apatch")):
            results.append(sweep_stale_lease(root))
    return results


def release_leases_for_current_pid() -> List[Dict[str, Any]]:
    """Release sandbox leases held by this MCP process."""
    from apatch.sandbox import load_active_leases, release_lease

    pid = os.getpid()
    results: List[Dict[str, Any]] = []
    with _HOOK_LOCK:
        roots = list(_TOUCHED_WORKSPACES)
    for root in roots:
        for cap in load_active_leases(root):
            if cap.get("pid") != pid:
                continue
            released = release_lease(root, lease_id=cap.get("lease_id"))
            results.append(
                {
                    "root": root,
                    "released": bool(released),
                    "lease_id": cap.get("lease_id"),
                }
            )
    return results


def _shutdown_handler() -> None:
    release_leases_for_current_pid()


def register_mcp_lifecycle_hooks() -> None:
    """Register atexit and signal handlers once per MCP process."""
    global _HOOKS_REGISTERED
    with _HOOK_LOCK:
        if _HOOKS_REGISTERED:
            return
        _HOOKS_REGISTERED = True
    atexit.register(_shutdown_handler)
    for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if signum is None:
            continue
        try:
            signal.signal(signum, _signal_handler)
        except (ValueError, OSError):
            pass


def _signal_handler(signum: int, _frame: Any) -> None:
    _shutdown_handler()
    raise SystemExit(128 + signum)


def on_mcp_startup() -> None:
    """Called from ``apatch.mcp.launcher`` before serving stdio requests."""
    register_mcp_lifecycle_hooks()
    from apatch.mcp.bound_workspace import bind_mcp_workspace

    root = bind_mcp_workspace() or os.getcwd()
    if os.path.isdir(os.path.join(root, ".apatch")):
        sweep_stale_lease(root)
        try:
            from apatch.mcp_health import write_mcp_fingerprint

            write_mcp_fingerprint(root)
        except Exception:
            pass
    sweep_registered_workspaces()
