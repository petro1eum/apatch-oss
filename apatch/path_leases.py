"""Atomic path-scoped writer leases for one canonical workspace."""

from __future__ import annotations

import os
import secrets
import sys
from importlib import metadata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock, read_json_file

LEGACY_LEASE_REL = os.path.join(".apatch", "write_lease.json")
REGISTRY_REL = os.path.join(".apatch", "write_leases.json")
REGISTRY_VERSION = 2
COMPAT_OWNER = "path-lease-registry-v2"
PATH_LEASE_PROTOCOL = "apatch.path-leases.v2"
PATH_LEASE_API = (
    "acquire_path_lease",
    "release_path_leases",
    "find_covering_lease",
    "sweep_stale_leases",
)


class PathLeaseConflict(Exception):
    def __init__(self, conflicts: List[Dict[str, Any]]):
        self.conflicts = conflicts
        details = "; ".join(
            "requested {requested_path} conflicts with {held_path} "
            "(session {governed_session_id}, lease {lease_id}, pid {pid}, tool {tool})".format(
                **row
            )
            for row in conflicts
        )
        super().__init__("path write-lease conflict: " + details)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def canonical_workspace_root(root: str) -> str:
    return os.path.realpath(os.path.abspath(root))


def legacy_lease_path(root: str) -> str:
    return os.path.join(canonical_workspace_root(root), LEGACY_LEASE_REL)


def lease_registry_path(root: str) -> str:
    return os.path.join(canonical_workspace_root(root), REGISTRY_REL)


def lease_valid(cap: Dict[str, Any]) -> bool:
    expires = cap.get("expires_at")
    if not expires:
        return False
    try:
        exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if utcnow() > exp:
            return False
    except (TypeError, ValueError):
        return False
    pid = cap.get("pid")
    if pid is not None:
        try:
            os.kill(int(pid), 0)
        except (OSError, ProcessLookupError, ValueError):
            return False
    return True


def _case_sensitive(root: str) -> bool:
    """Probe the actual filesystem instead of assuming host defaults."""
    parent = os.path.join(root, ".apatch")
    os.makedirs(parent, exist_ok=True)
    token = secrets.token_hex(8)
    probe = os.path.join(parent, "CaseProbe" + token)
    alternate = os.path.join(parent, "caseprobe" + token)
    try:
        with open(probe, "xb"):
            pass
        return not os.path.exists(alternate)
    except OSError:
        return not (sys.platform.startswith("win") or sys.platform == "darwin")
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


def _realpath_with_future_tail(path: str) -> str:
    current = os.path.abspath(path)
    tail: List[str] = []
    while not os.path.lexists(current):
        parent, name = os.path.split(current)
        if parent == current:
            break
        tail.append(name)
        current = parent
    resolved = os.path.realpath(current)
    for name in reversed(tail):
        resolved = os.path.join(resolved, name)
    return os.path.normpath(resolved)


def canonicalize_paths(root: str, paths: Sequence[str]) -> List[Dict[str, str]]:
    workspace = canonical_workspace_root(root)
    case_sensitive = _case_sensitive(workspace)
    by_key: Dict[str, Dict[str, str]] = {}
    for raw in paths:
        if not raw:
            continue
        raw_text = str(raw)
        candidate = raw_text if os.path.isabs(raw_text) else os.path.join(workspace, raw_text)
        resolved = _realpath_with_future_tail(candidate)
        try:
            if os.path.commonpath([workspace, resolved]) != workspace:
                raise ValueError("lease path escapes canonical workspace: {}".format(raw_text))
        except ValueError as exc:
            raise ValueError("lease path escapes canonical workspace: {}".format(raw_text)) from exc
        rel = os.path.relpath(resolved, workspace).replace("\\", "/")
        if rel == ".":
            rel = "."
        canonical = resolved.replace("\\", "/")
        key = canonical if case_sensitive else canonical.casefold()
        by_key[key] = {
            "path": rel,
            "canonical_path": canonical,
            "canonical_key": key.rstrip("/") or "/",
        }
    return [by_key[key] for key in sorted(by_key)]


def paths_overlap(left: str, right: str) -> bool:
    left = left.rstrip("/") or "/"
    right = right.rstrip("/") or "/"
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _empty_registry(root: str) -> Dict[str, Any]:
    return {
        "version": REGISTRY_VERSION,
        "revision": 0,
        "workspace_root": canonical_workspace_root(root),
        "leases": {},
    }


def _read_registry(root: str) -> Dict[str, Any]:
    registry = read_json_file(lease_registry_path(root), _empty_registry(root))
    if registry.get("version") != REGISTRY_VERSION or not isinstance(registry.get("leases"), dict):
        return _empty_registry(root)
    return registry


def _legacy_capability(root: str) -> Optional[Dict[str, Any]]:
    data = read_json_file(legacy_lease_path(root), {})
    cap = data.get("capability")
    if not isinstance(cap, dict) or cap.get("holder") == COMPAT_OWNER:
        return None
    return cap


def _compat_capability(registry: Dict[str, Any]) -> Dict[str, Any]:
    live = [cap for cap in registry.get("leases", {}).values() if lease_valid(cap)]
    expires_at = max(
        (str(cap.get("expires_at") or "") for cap in live),
        default=iso(utcnow()),
    )
    return {
        "lease_id": "lease_registry_v2_guard",
        "holder": COMPAT_OWNER,
        "infrastructure_guard": True,
        "minimum_writer_protocol": REGISTRY_VERSION,
        "governed_session_id": "APATCH_UPGRADE_REQUIRED",
        "tool": "apatch_protocol_v2_upgrade_required",
        "paths": ["."],
        "issued_at": iso(utcnow()),
        # A v1 client can remove this capability itself after every v2 writer
        # has expired. Never leave an infrastructure guard with an infinite TTL.
        "expires_at": expires_at,
        "registry_revision": registry.get("revision", 0),
    }


def _sync_compat_sentinel(root: str, registry: Dict[str, Any]) -> None:
    legacy = legacy_lease_path(root)
    leases = list((registry.get("leases") or {}).values())
    migrated = [cap for cap in leases if cap.get("legacy_migrated")]
    if migrated:
        # Do not replace a live v1 owner's capability with infrastructure
        # state. The old process must be able to extend/release its own lease.
        original = dict(migrated[0].get("legacy_capability") or migrated[0])
        original.pop("canonical_paths", None)
        original.pop("legacy_capability", None)
        original.pop("legacy_migrated", None)
        atomic_write_json(legacy, {"version": 1, "capability": original})
    elif leases:
        atomic_write_json(legacy, {"version": 1, "capability": _compat_capability(registry)})
    else:
        try:
            os.remove(legacy)
        except OSError:
            pass


def _migrate_legacy(root: str, registry: Dict[str, Any]) -> bool:
    cap = _legacy_capability(root)
    leases = registry.get("leases") or {}
    migrated_ids = [
        lease_id
        for lease_id, existing in leases.items()
        if isinstance(existing, dict) and existing.get("legacy_migrated")
    ]
    if cap and not lease_valid(cap):
        try:
            os.remove(legacy_lease_path(root))
        except OSError:
            pass
        cap = None
    if not cap:
        changed = False
        for lease_id in migrated_ids:
            leases.pop(lease_id, None)
            changed = True
        return changed

    migrated = dict(cap)
    migrated["legacy_migrated"] = True
    migrated["legacy_capability"] = dict(cap)
    # A live v1 process knows nothing about path concurrency. Keep its original
    # workspace-wide exclusion until it releases, exits, or expires.
    canonical = canonicalize_paths(root, ["."])
    migrated["paths"] = ["."]
    migrated["canonical_paths"] = canonical
    lease_id = str(migrated.get("lease_id") or "lease_legacy_{}".format(secrets.token_hex(8)))
    migrated["lease_id"] = lease_id
    changed = registry["leases"].get(lease_id) != migrated
    registry["leases"][lease_id] = migrated
    for stale_id in migrated_ids:
        if stale_id != lease_id:
            registry["leases"].pop(stale_id, None)
            changed = True
    return changed


def _prune(registry: Dict[str, Any]) -> bool:
    leases = registry.get("leases") or {}
    kept = {lease_id: cap for lease_id, cap in leases.items() if isinstance(cap, dict) and lease_valid(cap)}
    changed = len(kept) != len(leases)
    registry["leases"] = kept
    return changed


@contextmanager
def registry_transaction(root: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    workspace = canonical_workspace_root(root)
    # The legacy lock is always first. That makes admission atomic against a
    # still-running v1 writer, while the registry lock protects v2 transactions.
    with exclusive_file_lock(legacy_lease_path(workspace)):
        with exclusive_file_lock(lease_registry_path(workspace)):
            registry = _read_registry(workspace)
            changed = _migrate_legacy(workspace, registry)
            changed = _prune(registry) or changed
            if changed:
                registry["revision"] = int(registry.get("revision") or 0) + 1
            before = int(registry.get("revision") or 0)
            try:
                yield workspace, registry
            finally:
                if changed or int(registry.get("revision") or 0) != before:
                    atomic_write_json(lease_registry_path(workspace), registry)
                _sync_compat_sentinel(workspace, registry)


def load_active_leases(root: str) -> List[Dict[str, Any]]:
    registry = _read_registry(root)
    active = [dict(cap) for cap in registry.get("leases", {}).values() if lease_valid(cap)]
    if active:
        return sorted(active, key=lambda cap: str(cap.get("lease_id") or ""))
    legacy = _legacy_capability(root)
    return [legacy] if legacy and lease_valid(legacy) else []


def sweep_stale_leases(root: str) -> List[str]:
    before_registry = _read_registry(root)
    before = set((before_registry.get("leases") or {}).keys())
    legacy = _legacy_capability(root)
    if legacy:
        before.add(str(legacy.get("lease_id") or "legacy"))
    with registry_transaction(root):
        pass
    after = {
        str(cap.get("lease_id") or "legacy") for cap in load_active_leases(root)
    }
    return sorted(before - after)


def acquire_path_lease(
    root: str,
    paths: Sequence[str],
    *,
    tool: str,
    governed_session_id: Optional[str],
    session_checkpoint: Optional[str] = None,
    max_seconds: int = 300,
    pid: Optional[int] = None,
) -> Dict[str, Any]:
    requested_pid = int(pid if pid is not None else os.getpid())
    with registry_transaction(root) as (workspace, registry):
        requested = canonicalize_paths(workspace, paths)
        if not requested:
            raise ValueError("path lease requires at least one planned path")
        owner = str(governed_session_id or "")
        owned: Optional[Dict[str, Any]] = None
        conflicts: List[Dict[str, Any]] = []
        for existing in registry["leases"].values():
            same_owner = bool(owner and str(existing.get("governed_session_id") or "") == owner)
            same_pid_legacy = not owner and existing.get("pid") == requested_pid
            if same_owner or same_pid_legacy:
                owned = existing
                continue
            for wanted in requested:
                for held in existing.get("canonical_paths") or canonicalize_paths(
                    workspace, existing.get("paths") or ["."]
                ):
                    if paths_overlap(wanted["canonical_key"], held["canonical_key"]):
                        conflicts.append(
                            {
                                "requested_path": wanted["path"],
                                "requested_canonical_path": wanted["canonical_path"],
                                "held_path": held["path"],
                                "held_canonical_path": held["canonical_path"],
                                "lease_id": existing.get("lease_id"),
                                "governed_session_id": existing.get("governed_session_id") or "legacy",
                                "pid": existing.get("pid"),
                                "tool": existing.get("tool") or "?",
                            }
                        )
        if conflicts:
            raise PathLeaseConflict(conflicts)

        now = utcnow()
        if owned is None:
            owned = {
                "lease_id": "lease_{}".format(secrets.token_hex(8)),
                "holder": "apatch",
                "pid": requested_pid,
                "issued_at": iso(now),
            }
            registry["leases"][owned["lease_id"]] = owned
        merged = {
            item["canonical_key"]: item
            for item in list(owned.get("canonical_paths") or []) + requested
        }
        owned["canonical_paths"] = [merged[key] for key in sorted(merged)]
        owned["paths"] = [item["path"] for item in owned["canonical_paths"]]
        owned["tool"] = tool
        owned["pid"] = requested_pid
        owned["expires_at"] = iso(now + timedelta(seconds=max_seconds))
        if owner:
            owned["governed_session_id"] = owner
        if session_checkpoint:
            owned["session_checkpoint"] = session_checkpoint
        registry["revision"] = int(registry.get("revision") or 0) + 1
        return dict(owned)


def release_path_leases(
    root: str,
    *,
    lease_id: Optional[str] = None,
    governed_session_id: Optional[str] = None,
    pid: Optional[int] = None,
) -> List[str]:
    requested_pid = int(pid if pid is not None else os.getpid())
    removed: List[str] = []
    with registry_transaction(root) as (_workspace, registry):
        for current_id, cap in list(registry["leases"].items()):
            if lease_id and current_id != lease_id:
                continue
            owner = str(cap.get("governed_session_id") or "")
            if governed_session_id:
                if owner != governed_session_id:
                    continue
            elif not lease_id and cap.get("pid") != requested_pid:
                continue
            elif lease_id and not governed_session_id and lease_valid(cap) and cap.get("pid") not in (None, requested_pid):
                continue
            removed.append(current_id)
            del registry["leases"][current_id]
        if removed:
            registry["revision"] = int(registry.get("revision") or 0) + 1
    return removed


def save_capability(root: str, cap: Dict[str, Any]) -> None:
    """Replace one v2 capability; retained for tests and recovery tooling."""
    with registry_transaction(root) as (workspace, registry):
        stored = dict(cap)
        lease_id = str(stored.get("lease_id") or "lease_{}".format(secrets.token_hex(8)))
        stored["lease_id"] = lease_id
        if not stored.get("canonical_paths"):
            stored["canonical_paths"] = canonicalize_paths(workspace, stored.get("paths") or ["."])
        stored["paths"] = [item["path"] for item in stored["canonical_paths"]]
        registry["leases"][lease_id] = stored
        registry["revision"] = int(registry.get("revision") or 0) + 1


def find_covering_lease(
    root: str,
    path: str,
    *,
    governed_session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    wanted = canonicalize_paths(root, [path])
    if not wanted:
        return None
    key = wanted[0]["canonical_key"]
    for cap in load_active_leases(root):
        if governed_session_id and str(cap.get("governed_session_id") or "") != governed_session_id:
            continue
        held_paths = cap.get("canonical_paths") or canonicalize_paths(root, cap.get("paths") or [])
        if any(paths_overlap(key, held["canonical_key"]) for held in held_paths):
            return cap
    return None


def registry_enabled(root: str) -> bool:
    data = _read_registry(root)
    return data.get("version") == REGISTRY_VERSION and os.path.isfile(lease_registry_path(root))


def _installed_apatch_version() -> Optional[str]:
    try:
        return metadata.version("apatch")
    except metadata.PackageNotFoundError:
        return None


def writer_protocol_status(
    root: str,
    *,
    current_protocol: int = REGISTRY_VERSION,
    runtime_version: Optional[str] = None,
    installed_version: Optional[str] = None,
    enforce_runtime_match: Optional[bool] = None,
) -> Dict[str, Any]:
    """Describe writer compatibility without acquiring a lease or creating a session."""
    from apatch import __version__

    workspace = canonical_workspace_root(root)
    registry = _read_registry(workspace)
    legacy = read_json_file(legacy_lease_path(workspace), {})
    guard = legacy.get("capability") if isinstance(legacy, dict) else None
    guard = guard if isinstance(guard, dict) and guard.get("holder") == COMPAT_OWNER else None
    guard_active = bool(guard and lease_valid(guard))
    required_protocol = max(
        REGISTRY_VERSION,
        int(registry.get("version") or 0),
        int((guard or {}).get("minimum_writer_protocol") or 0),
    )
    loaded = str(runtime_version or __version__)
    installed = installed_version if installed_version is not None else _installed_apatch_version()
    check_runtime = (
        os.environ.get("APATCH_CANONICAL_RUNTIME") == "1"
        if enforce_runtime_match is None
        else bool(enforce_runtime_match)
    )
    protocol_ok = int(current_protocol) >= required_protocol
    runtime_ok = not check_runtime or not installed or loaded == installed
    ready = protocol_ok and runtime_ok
    reload_required = not ready
    status: Dict[str, Any] = {
        "ok": ready,
        "ready": ready,
        "protocol": PATH_LEASE_PROTOCOL,
        "writer_protocol_version": int(current_protocol),
        "workspace_required_protocol": required_protocol,
        "registry_version": int(registry.get("version") or REGISTRY_VERSION),
        "registry_revision": int(registry.get("revision") or 0),
        "registry_path": lease_registry_path(workspace),
        "disjoint_concurrency": True,
        "path_lease_api": list(PATH_LEASE_API),
        "guard": {
            "present": bool(guard),
            "active": guard_active,
            "infrastructure": bool((guard or {}).get("infrastructure_guard")),
            "minimum_writer_protocol": int(
                (guard or {}).get("minimum_writer_protocol") or 0
            ),
            "expires_at": (guard or {}).get("expires_at"),
        },
        "runtime": {
            "apatch_version": loaded,
            "installed_version": installed,
            "apatch_path": os.path.realpath(__import__("apatch").__file__),
            "python_executable": os.path.realpath(sys.executable),
            "pid": os.getpid(),
            "canonical": check_runtime,
        },
        "reload_required": reload_required,
    }
    if ready:
        status["summary"] = (
            "writer protocol v2 ready; infrastructure guard is ignored by v2 "
            "admission and disjoint path leases may run concurrently"
        )
        return status

    if not protocol_ok:
        error_type = "MCP_WRITER_PROTOCOL_MISMATCH"
        error = (
            f"running MCP writer protocol v{current_protocol} is older than workspace "
            f"protocol v{required_protocol}; restart APatch MCP before mutation"
        )
    else:
        error_type = "MCP_RUNTIME_VERSION_MISMATCH"
        error = (
            f"running APatch MCP {loaded} differs from installed APatch {installed}; "
            "restart APatch MCP before mutation"
        )
    status.update(
        {
            "ok": False,
            "error_type": error_type,
            "error": error,
            "mutation_performed": False,
            "session_started": False,
            "recoverable": True,
            "recommended_action": "restart_mcp_and_retry",
            "human_steps": [
                "Restart the APatch MCP server in the coding client.",
                (
                    "Run apatch_doctor and verify writer_protocol.ready=true, "
                    f"writer_protocol_version>={required_protocol}, and reload_required=false."
                ),
                "Retry the original governed mutation; do not delete write_lease.json manually.",
            ],
        }
    )
    return status


def writer_protocol_preflight(root: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """Return an actionable error when this runtime cannot safely mutate ``root``."""
    status = writer_protocol_status(root, **kwargs)
    return None if status.get("ready") else status
