"""Write sandbox v1 — capability lease + path policy (P0)."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Set

SANDBOX_REL = os.path.join(".apatch", "sandbox.json")
LEASE_REL = os.path.join(".apatch", "write_lease.json")

DEFAULT_PROTECTED_GLOBS = (
    "src/**",
    "app/**",
    "services/**",
    "packages/**",
    "e2e/**",
    "server/**",
    "server.js",
    "scripts/**",
    "docs/specs/**",
    "playwright.config.ts",
    "vitest.config.ts",
)

DEFAULT_ALLOW_GLOBS = (
    "manifests/**",
    "patches/**",
    "tests/**",
    "docs/*.md",
    "*.md",
    ".apatch/**",
    ".cursor/**",
    ".trustchain/**",
    ".git/**",
    "patches.jsonl",
)

# Control plane: the monitor's own policy, hooks and trust ledger. These must
# never be writable through the agent channel even though they live inside
# allow_globs (`.apatch/**`, `.cursor/**`, `.trustchain/**`) — otherwise the
# guard can be disabled by editing its own config ("key taped to the door").
# apatch's trusted runtime writes them directly (not via the Cursor Write tool
# / hook), so it is unaffected. See docs/RFP-005-reference-monitor.md.
CONTROL_GLOBS = (
    ".apatch/sandbox.json",
    ".apatch/enforcement.json",
    ".apatch/policy.lock.json",
    ".apatch/inclusion.jsonl",
    ".trustchain/**",
    ".cursor/hooks.json",
    ".cursor/hooks/**",
)


def is_control_path(rel_path: str) -> bool:
    """True if path is a monitor control-plane file (always protected).

    Uses dotfile-preserving normalization: the shared `_normalize_rel` strips
    leading dots (`.apatch` -> `apatch`), which would break matching here.
    """
    norm = rel_path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    norm = norm.lstrip("/")
    for pat in CONTROL_GLOBS:
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if norm == prefix or norm.startswith(prefix + "/"):
                return True
        elif fnmatch.fnmatch(norm, pat):
            return True
    return False

# Read-only / inspection MCP servers allowed alongside apatch_* under enforce.
# Cursor built-in browser — navigate/snapshot/screenshot for visual code review (no writes).
DEFAULT_MCP_EXTRA_SERVERS: tuple[str, ...] = ("cursor-ide-browser",)

DEFAULT_SANDBOX: Dict[str, Any] = {
    "version": 1,
    "mode": "enforce",
    "protected_globs": list(DEFAULT_PROTECTED_GLOBS),
    "allow_globs": list(DEFAULT_ALLOW_GLOBS),
    "lease_max_seconds": 300,
    "cursor_hooks": True,
    "watcher": "revert",
    "revert_untracked": True,
    "mcp_extra_servers": list(DEFAULT_MCP_EXTRA_SERVERS),
}

# Keep in sync with tests/test_mcp.py::expected and apatch/mcp/server.py tools.
APATCH_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "apatch_plan",
        "apatch_plan_batch",
        "apatch_apply",
        "apatch_apply_session",
        "apatch_scan",
        "apatch_view",
        "apatch_strip_dry_run",
        "apatch_strip",
        "apatch_phase_run",
        "apatch_rollback",
        "apatch_generate",
        "apatch_generate_batch",
        "apatch_doctor",
        "apatch_extension_list",
        "apatch_extension_inspect",
        "apatch_extension_validate",
        "apatch_extension_run",
        "apatch_init_consumer",
        "apatch_natives_check",
        "apatch_compile",
        "apatch_suggest_until",
        "apatch_arch_check",
        "apatch_impact",
        "apatch_db_check",
        "apatch_db_revision",
        "apatch_db_safety",
        "apatch_db_run",
        "apatch_refactor_run",
        "apatch_verify_semantic",
        "apatch_verify_notarization",
        "apatch_session_state",
        "apatch_spec_status",
        "apatch_spec_next",
        "apatch_execute_next",
        "apatch_spec_run",
        "apatch_spec_run_manifest_lint",
        "apatch_spec_lint",
        "apatch_rfp_lint",
        "apatch_rfp_spec_coverage",
        "apatch_simulate",
        "apatch_index_build",
        "apatch_index_query",
        "apatch_trustchain_history",
        "apatch_pipeline_run",
        "apatch_plan_graph",
        "apatch_execute_graph",
        "apatch_orchestrate",
        "apatch_replay",
        "apatch_sandbox_status",
        "apatch_sandbox_audit",
        "apatch_sandbox_ci_gate",
        "apatch_verify_anchor",
        "apatch_verify_inclusion",
        "apatch_trust_enroll",
        "apatch_policy_sign",
        "apatch_policy_verify",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def sandbox_config_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), SANDBOX_REL)


def lease_path(root: str) -> str:
    from apatch.path_leases import legacy_lease_path

    return legacy_lease_path(root)


def lease_registry_path(root: str) -> str:
    from apatch.path_leases import lease_registry_path as registry_path

    return registry_path(root)


def is_sandbox_enabled(root: str = ".") -> bool:
    path = sandbox_config_path(root)
    if not os.path.isfile(path):
        return False
    cfg = load_sandbox_config(root)
    mode = cfg.get("mode", "off")
    committed = mode not in ("off", None)

    # Kill-switch (reference-monitor: tamper-resistant, RFP-005 §audit #2).
    # APATCH_SANDBOX=0 is honored ONLY for non-enforcing dev modes (warn/audit).
    # A committed `enforce` sandbox cannot be disabled by the monitored party
    # via the environment — otherwise the "single door" has an env-sized hole.
    if os.environ.get("APATCH_SANDBOX", "").strip().lower() in ("0", "off", "false"):
        if mode == "enforce":
            return True
        return False
    return committed


def is_sandbox_enforce(root: str = ".") -> bool:
    if not is_sandbox_enabled(root):
        return False
    return load_sandbox_config(root).get("mode") == "enforce"


def load_sandbox_config(root: str = ".") -> Dict[str, Any]:
    cfg = dict(DEFAULT_SANDBOX)
    path = sandbox_config_path(root)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                cfg.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def write_sandbox_config(
    root: str,
    *,
    mode: str = "enforce",
    protected_globs: Optional[List[str]] = None,
    allow_globs: Optional[List[str]] = None,
) -> str:
    path = sandbox_config_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = dict(DEFAULT_SANDBOX)
    cfg["protected_globs"] = list(
        protected_globs if protected_globs is not None else DEFAULT_PROTECTED_GLOBS
    )
    cfg["allow_globs"] = list(
        allow_globs if allow_globs is not None else DEFAULT_ALLOW_GLOBS
    )
    cfg["mode"] = mode
    cfg["enabled_at"] = _iso(_utcnow())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def _normalize_rel(path: str) -> str:
    """Normalize separators and strip './' prefixes, preserving leading dots.

    lstrip('./') strips dot *characters* and breaks control-plane paths
    like '.apatch/sandbox.json' (it became 'apatch/sandbox.json').
    Canonical implementation lives in apatch.apatch_paths.normalize_rel.
    """
    from apatch.apatch_paths import normalize_rel

    return normalize_rel(path)


def _glob_matches(rel_path: str, pattern: str) -> bool:
    norm = _normalize_rel(rel_path)
    pat = pattern.replace("\\", "/")
    if pat.endswith("/**"):
        prefix = pat[:-3].rstrip("/")
        return norm == prefix or norm.startswith(prefix + "/")
    if "**" in pat:
        if fnmatch.fnmatch(norm, pat):
            return True
        # '**/' requires a literal '/' under fnmatch, excluding root-level files; also
        # match the de-prefixed pattern so '**/*.md' covers a root README.md.
        if pat.startswith("**/"):
            return fnmatch.fnmatch(norm, pat[3:])
        return False
    # Segment-aware: ``docs/*.md`` must not match ``docs/specs/x.md`` (py3.13+ fnmatch * crosses /).
    if "/" in pat or pat.startswith("*"):
        pat_parts = pat.split("/")
        norm_parts = norm.split("/")
        if len(pat_parts) != len(norm_parts):
            return False
        return all(fnmatch.fnmatch(n, p) for n, p in zip(norm_parts, pat_parts))
    return fnmatch.fnmatch(norm, pat)


def matches_any_glob(rel_path: str, patterns: List[str]) -> bool:
    return any(_glob_matches(rel_path, p) for p in patterns)


def is_path_allowed(rel_path: str, cfg: Dict[str, Any]) -> bool:
    allow = cfg.get("allow_globs") or list(DEFAULT_ALLOW_GLOBS)
    return matches_any_glob(rel_path, allow)


def is_path_protected(rel_path: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    if cfg is None:
        cfg = DEFAULT_SANDBOX
    if is_path_allowed(rel_path, cfg):
        return False
    protected = cfg.get("protected_globs") or list(DEFAULT_PROTECTED_GLOBS)
    return matches_any_glob(rel_path, protected)


def load_active_leases(root: str) -> List[Dict[str, Any]]:
    from apatch.path_leases import load_active_leases as load_all

    active = load_all(root)
    if active:
        return active
    # Preserve the v1 inspection API: lifecycle cleanup must be able to see a
    # dead/expired legacy capability before migrating or pruning it.
    try:
        with open(lease_path(root), encoding="utf-8") as handle:
            data = json.load(handle)
        cap = (data or {}).get("capability")
        if isinstance(cap, dict) and cap.get("holder") != "path-lease-registry-v2":
            return [cap]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def load_active_lease(
    root: str,
    governed_session_id: Optional[str] = None,
    path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    from apatch.path_leases import find_covering_lease

    if path:
        return find_covering_lease(
            root, path, governed_session_id=governed_session_id
        )
    leases = load_active_leases(root)
    if governed_session_id:
        return next(
            (
                cap
                for cap in leases
                if str(cap.get("governed_session_id") or "")
                == governed_session_id
            ),
            None,
        )
    owned = [cap for cap in leases if cap.get("pid") == os.getpid()]
    if len(owned) == 1:
        return owned[0]
    return leases[0] if len(leases) == 1 else None


def _lease_valid(cap: Dict[str, Any]) -> bool:
    from apatch.path_leases import lease_valid

    return lease_valid(cap)


def lease_covers_path(cap: Dict[str, Any], rel_path: str) -> bool:
    norm = _normalize_rel(rel_path)
    return any(
        norm == _normalize_rel(str(path))
        or norm.startswith(_normalize_rel(str(path)).rstrip("/") + "/")
        for path in cap.get("paths") or []
    )


def evaluate_write_policy(
    root: str,
    rel_path: str,
    *,
    cfg: Optional[Dict[str, Any]] = None,
    lease: Optional[Dict[str, Any]] = None,
    channel: str = "default",
) -> Dict[str, Any]:
    """Return allow/deny decision for a single path."""
    root = os.path.abspath(root)
    cfg = cfg or load_sandbox_config(root)
    mode = cfg.get("mode", "off")
    norm = _normalize_rel(rel_path)

    if mode == "off":
        return {"allowed": True, "reason": "sandbox_off", "path": norm}

    # Control-plane files are never writable through the mediated channel — not
    # even with a lease. apatch's trusted runtime writes them directly.
    # Use the original rel_path: _normalize_rel strips leading dots.
    if is_control_path(rel_path):
        if mode == "audit":
            return {
                "allowed": True,
                "reason": "audit_only",
                "path": norm,
                "would_block_in_enforce": True,
                "control_plane": True,
            }
        return {
            "allowed": False,
            "reason": "control_plane_locked",
            "path": norm,
            "hint": (
                "Monitor config/ledger is locked from the agent channel. "
                "Change policy via the apatch CLI as a trusted operator."
            ),
        }

    if not is_path_protected(norm, cfg):
        return {"allowed": True, "reason": "not_protected", "path": norm}

    if mode == "audit":
        return {
            "allowed": True,
            "reason": "audit_only",
            "path": norm,
            "would_block_in_enforce": True,
        }

    if channel != "agent_hook":
        lease = lease if lease is not None else load_active_lease(root, path=norm)
        if lease and _lease_valid(lease) and lease_covers_path(lease, norm):
            return {
                "allowed": True,
                "reason": "lease",
                "path": norm,
                "lease_id": lease.get("lease_id"),
            }

    return {
        "allowed": False,
        "reason": "direct_write_blocked",
        "path": norm,
        "hint": "Use apatch_apply_session (MCP) — direct Write/StrReplace blocked in protected zones.",
    }


def acquire_lease(
    root: str,
    paths: List[str],
    *,
    tool: str,
    session_checkpoint: Optional[str] = None,
    max_seconds: Optional[int] = None,
    governed_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.path_leases import PathLeaseConflict, acquire_path_lease

    root = os.path.realpath(os.path.abspath(root))
    cfg = load_sandbox_config(root)
    from apatch.sdd_integrity import admit_session_effect

    admit_session_effect(
        root,
        {
            "surface": "sandbox",
            "effect": "acquire",
            "paths": [str(path) for path in paths if path],
            "tool": tool,
        },
    )
    if governed_session_id is None:
        try:
            from apatch.session_state import load_session_state

            raw = load_session_state(root)
            if raw.get("session_id") and not raw.get("ended_at"):
                governed_session_id = str(raw.get("session_id"))
        except Exception:
            pass

    try:
        cap = acquire_path_lease(
            root,
            [str(path) for path in paths if path],
            tool=tool,
            governed_session_id=governed_session_id,
            session_checkpoint=session_checkpoint,
            max_seconds=int(max_seconds or cfg.get("lease_max_seconds") or 300),
        )
    except PathLeaseConflict as exc:
        raise SandboxError(
            str(exc), error_type="LEASE_CONFLICT", details={"conflicts": exc.conflicts}
        ) from exc
    return cap


def release_lease(
    root: str,
    *,
    lease_id: Optional[str] = None,
    aborted: bool = False,
    governed_session_id: Optional[str] = None,
) -> bool:
    from apatch.path_leases import release_path_leases

    root = os.path.abspath(root)
    if governed_session_id is None:
        try:
            from apatch.session_state import load_session_state

            raw = load_session_state(root)
            if raw.get("session_id") and not raw.get("ended_at"):
                governed_session_id = str(raw.get("session_id"))
        except Exception:
            pass

    return bool(
        release_path_leases(
            root,
            lease_id=lease_id,
            governed_session_id=governed_session_id,
        )
    )


def _save_lease(root: str, cap: Dict[str, Any]) -> None:
    from apatch.path_leases import save_capability

    save_capability(root, cap)


def paths_from_resolved_files(files: List[str]) -> List[str]:
    return list(dict.fromkeys(_normalize_rel(p) for p in files if p))


def _rel_to_workspace(root: str, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    root = os.path.abspath(root)
    abs_p = path if os.path.isabs(path) else os.path.join(root, path)
    try:
        return _normalize_rel(os.path.relpath(abs_p, root))
    except ValueError:
        return _normalize_rel(path)


def collect_strip_scope_paths(
    workspace: str,
    *,
    file_path: str,
    module_out_dir: Optional[str] = None,
    native_out_dir: Optional[str] = None,
    out_dir: Optional[str] = None,
    emit_barrel: Optional[str] = None,
    emit_wiring: Optional[str] = None,
) -> List[str]:
    """Paths strip may mutate under sandbox lease."""
    paths: List[str] = []
    for p in (
        file_path,
        module_out_dir,
        native_out_dir,
        out_dir,
        emit_barrel,
        emit_wiring,
    ):
        rel = _rel_to_workspace(workspace, p)
        if rel:
            paths.append(rel)
    return list(dict.fromkeys(paths))


def collect_paths_from_candidates(candidates: List[Any], target_dir: str) -> List[str]:
    from apatch.resolver import resolve_smart_path

    root = os.path.abspath(target_dir)
    paths: Set[str] = set()
    for c in candidates:
        tf = getattr(c, "target_file", None) or ""
        if not tf:
            continue
        resolved = resolve_smart_path(root, tf, getattr(c, "action_type", None))
        if resolved and os.path.isfile(resolved):
            paths.add(_normalize_rel(os.path.relpath(resolved, root)))
        else:
            paths.add(_normalize_rel(tf))
    return sorted(paths)


class SandboxError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "DIRECT_WRITE_BLOCKED",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.details = details or {}


def _current_governed_session_id(root: str) -> Optional[str]:
    try:
        from apatch.session_state import load_session_state

        state = load_session_state(root)
        if state.get("session_id") and not state.get("ended_at"):
            return str(state["session_id"])
    except Exception:
        pass
    return None


def assert_lease_for_paths(
    root: str,
    paths: List[str],
    *,
    tool: str,
    governed_session_id: Optional[str] = None,
) -> None:
    normalized = [_normalize_rel(p) for p in paths]
    session_id = governed_session_id or _current_governed_session_id(root)
    missing: List[str] = []
    for path in normalized:
        cap = load_active_lease(root, governed_session_id=session_id, path=path)
        if cap and _lease_valid(cap):
            if session_id or cap.get("pid") == os.getpid():
                continue
        missing.append(path)
    if missing:
        raise SandboxError(
            "active path lease does not cover planned write-set expansion: {}".format(
                ", ".join(missing)
            ),
            error_type="LEASE_SCOPE_VIOLATION",
            details={"missing_paths": missing, "tool": tool},
        )


@contextmanager
def sandbox_apply_scope(
    root: str,
    paths: List[str],
    *,
    tool: str,
    dry_run: bool = False,
    session_checkpoint: Optional[str] = None,
    owns_lease: bool = True,
) -> Iterator[Optional[Dict[str, Any]]]:
    """Acquire lease on enter, release on exit when owns_lease=True."""
    if dry_run or not paths:
        yield None
        return
    cap = None
    session_id = _current_governed_session_id(root)
    try:
        if owns_lease:
            cap = acquire_lease(
                root,
                paths,
                tool=tool,
                session_checkpoint=session_checkpoint,
                governed_session_id=session_id,
            )
        else:
            assert_lease_for_paths(
                root, paths, tool=tool, governed_session_id=session_id
            )
            cap = load_active_lease(root, governed_session_id=session_id)
        yield cap
    except SandboxError:
        raise
    finally:
        if owns_lease and cap:
            release_lease(root, lease_id=cap.get("lease_id"))


def sandbox_status_workspace(target_dir: str = ".") -> Dict[str, Any]:
    from apatch.sandbox_watch import count_recent_violations, violations_log_path

    root = os.path.abspath(target_dir)
    cfg = load_sandbox_config(root) if os.path.isfile(sandbox_config_path(root)) else dict(DEFAULT_SANDBOX)
    leases = load_active_leases(root)
    cap = leases[0] if len(leases) == 1 else None
    hooks_json = os.path.join(root, ".cursor", "hooks.json")
    hook_script = os.path.join(root, ".cursor", "hooks", "apatch-deny-direct-edit.sh")
    mcp_hook_script = os.path.join(root, ".cursor", "hooks", "apatch-deny-mcp-mutate.sh")
    vlog = violations_log_path(root)
    return {
        "ok": True,
        "enabled": is_sandbox_enabled(root),
        "mode": cfg.get("mode", "off"),
        "protected_globs": cfg.get("protected_globs"),
        "allow_globs": cfg.get("allow_globs"),
        "config_path": sandbox_config_path(root) if os.path.isfile(sandbox_config_path(root)) else None,
        "lease": {
            "active": bool(leases),
            "active_count": len(leases),
            "lease_id": cap.get("lease_id") if cap else None,
            "tool": cap.get("tool") if cap else None,
            "paths_count": len(cap.get("paths") or []) if cap else 0,
            "expires_at": cap.get("expires_at") if cap else None,
            "leases": [
                {
                    "lease_id": item.get("lease_id"),
                    "governed_session_id": item.get("governed_session_id"),
                    "tool": item.get("tool"),
                    "paths": item.get("paths") or [],
                    "expires_at": item.get("expires_at"),
                }
                for item in leases
            ],
        },
        "cursor_hooks_installed": (
            os.path.isfile(hooks_json)
            and os.path.isfile(hook_script)
            and os.path.isfile(mcp_hook_script)
        ),
        "watcher": cfg.get("watcher", "off"),
        "auto_revert_active": cfg.get("watcher") == "revert",
        "revert_untracked": cfg.get("revert_untracked", True),
        "violations_log": vlog if os.path.isfile(vlog) else None,
        "recent_violations": count_recent_violations(root),
        "policy": (
            "Protected: src/**, app/**, services/**, packages/**. "
            "Writes only via apatch under active lease."
        ),
    }


_MUTATION_TOOL_NAMES = frozenset(
    {"write", "strreplace", "search_replace", "applypatch", "editnotebook"}
)


def _is_mutation_tool(tool_name: str) -> bool:
    norm = tool_name.replace("-", "_").lower()
    return norm in _MUTATION_TOOL_NAMES


def _rel_from_hook_path(raw: str, *, root: str) -> str:
    """Normalize hook path to repo-relative form for policy checks."""
    raw = raw.strip().replace("\\", "/")
    root_abs = os.path.abspath(root).replace("\\", "/").rstrip("/")
    norm = os.path.normpath(raw).replace("\\", "/")
    if os.path.isabs(norm):
        try:
            rel = os.path.relpath(norm, root_abs).replace("\\", "/")
            if not rel.startswith(".."):
                return _normalize_rel(rel)
        except ValueError:
            pass
        return _normalize_rel(os.path.basename(norm))
    return _normalize_rel(raw)


def _extract_paths_from_apply_patch(tool_input: Dict[str, Any]) -> List[str]:
    import re

    paths: List[str] = []
    for key in ("patch", "input", "content", "diff"):
        body = tool_input.get(key)
        if not isinstance(body, str) or not body.strip():
            continue
        for m in re.finditer(
            r"^\*\*\* (?:Update|Add|Delete) File: (.+)$",
            body,
            re.MULTILINE,
        ):
            paths.append(m.group(1).strip())
    return paths


def _extract_paths_from_hook_input(inp: Dict[str, Any], *, root: str = ".") -> List[str]:
    paths: List[str] = []
    tool_input = inp.get("tool_input") or inp.get("input") or {}
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "target_file", "TargetFile", "filePath"):
            val = tool_input.get(key)
            if isinstance(val, str) and val.strip():
                paths.append(_rel_from_hook_path(val.strip(), root=root))
        for p in _extract_paths_from_apply_patch(tool_input):
            paths.append(_rel_from_hook_path(p, root=root))
    return paths


def evaluate_pre_tool_use(inp: Dict[str, Any], *, root: str = ".") -> Dict[str, Any]:
    """Cursor preToolUse hook: return permission JSON."""
    # No committed sandbox.json → sandbox not enabled here → do not enforce.
    if not is_sandbox_enabled(root):
        return {"permission": "allow"}
    cfg = load_sandbox_config(root)
    if cfg.get("mode") == "off":
        return {"permission": "allow"}

    tool_name = str(inp.get("tool_name") or inp.get("tool") or "")
    paths = _extract_paths_from_hook_input(inp, root=root)

    if cfg.get("mode") == "enforce" and _is_mutation_tool(tool_name) and not paths:
        return {
            "permission": "deny",
            "user_message": "Sandbox blocked direct edit: no path in tool input",
            "agent_message": (
                f"Direct {tool_name} blocked: could not extract target path "
                "(absolute paths and ApplyPatch must be parsed). "
                "Use apatch_generate → apatch_apply_session via MCP."
            ),
        }

    for rel in paths:
        # Agent channel: never honor write-lease bypass (lease is for apatch runtime only).
        decision = evaluate_write_policy(root, rel, cfg=cfg, channel="agent_hook")
        if not decision.get("allowed") and cfg.get("mode") == "enforce":
            return {
                "permission": "deny",
                "user_message": f"Sandbox blocked direct edit: {decision['path']}",
                "agent_message": (
                    f"Direct {tool_name} blocked in protected zone ({decision['path']}). "
                    "Use apatch_generate → apatch_apply_session via MCP."
                ),
            }
    return {"permission": "allow"}


# Mutating *commands* (block regardless of args — use apatch instead). Matched only
# against the UNQUOTED portion of the command so a literal "sed"/">" inside a quoted
# payload (e.g. python -c "...") is not mistaken for a shell-level mutation.
_SHELL_MUTATE_WORD_RE = (
    r"\bsed\b",
    r"\bawk\b",
    r"perl\s+-pi",
    r"\btee\b",
)

# File-output redirect: `>` or `>>` that is NOT a file-descriptor duplication.
# Matches `> file`, `>> log`, `2> err` (these create/overwrite files) but deliberately
# excludes fd-dups like `2>&1`, `>&2`, `>&-` which do not mutate the filesystem.
_SHELL_REDIRECT_RE = r">(?!&)"

# Discard-redirects to /dev/null (`2>/dev/null`, `>/dev/null`, `&>/dev/null`,
# `2>>/dev/null`) create no files — common in diagnostics, always allowed.
_DEV_NULL_REDIRECT_RE = r"(?:\d*|&)>>?\s*/dev/null\b"


def _unquoted_shell(command: str) -> str:
    """Return the command with single/double-quoted spans removed.

    Shell redirects and command words operate at the shell-syntax level, i.e. OUTSIDE
    quotes. Stripping quoted spans means a quoted payload such as
    ``python -c "a < b and c > d"`` or ``echo '2>&1 sed tee'`` no longer trips the
    mutation guard, while a real ``cmd > file`` (redirect outside quotes) still does.
    """
    out: List[str] = []
    quote: Optional[str] = None
    escaped = False
    for ch in command:
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        out.append(ch)
    return "".join(out)


def detect_shell_mutation(command: str) -> Optional[str]:
    """Return a short label for the shell file-mutation detected, else None.

    Read-only commands are NOT flagged: ``>``/``<`` inside quotes, fd redirects
    (``2>&1``, ``>&2``), discards to ``/dev/null`` (``2>/dev/null``), pipes, and
    comparisons in quoted ``-c``/``-e`` payloads.
    """
    import re

    unquoted = _unquoted_shell(command)
    for pat in _SHELL_MUTATE_WORD_RE:
        m = re.search(pat, unquoted)
        if m:
            return m.group(0).strip()
    # `2>/dev/null` and friends discard output without touching the filesystem.
    unquoted = re.sub(_DEV_NULL_REDIRECT_RE, " ", unquoted)
    if re.search(_SHELL_REDIRECT_RE, unquoted):
        return "redirect (> / >>)"
    return None

_SHELL_PACKAGE_INSTALL_RE = (
    r"(?:^|[\s;|])(?:python\d*(?:\.\d+)?\s+-m\s+)?pip\s+install\b",
    r"(?:^|[\s;|])pip\s+uninstall\b",
    r"\bnpm\s+install\b",
    r"\byarn\s+add\b",
    r"\bpnpm\s+add\b",
)

_AGENT_PIP_BLOCKED_MSG = (
    "Package install (pip/npm) is human-only under sandbox enforce. "
    "Do not retry. Ask the user to: pip install -e '.../apatch[mcp]' and restart "
    "the user-apatch MCP server in Cursor. Continue with apatch_* MCP tools if "
    "apatch_doctor.mcp_health.ok."
)


def _extract_mcp_tool_identity(inp: Dict[str, Any]) -> tuple[str, str]:
    server = str(
        inp.get("server")
        or inp.get("mcp_server")
        or inp.get("server_name")
        or inp.get("mcpServer")
        or ""
    ).strip()
    tool = str(
        inp.get("tool_name")
        or inp.get("name")
        or inp.get("mcp_tool")
        or inp.get("tool")
        or inp.get("toolName")
        or ""
    ).strip()
    if not tool and server and "/" in server:
        server, tool = server.split("/", 1)
    return server, tool


def effective_mcp_extra_servers(cfg: Dict[str, Any]) -> List[str]:
    """Union of committed extras and DEFAULT_MCP_EXTRA_SERVERS (inspection MCP)."""
    extra: List[str] = list(cfg.get("mcp_extra_servers") or [])
    for name in DEFAULT_MCP_EXTRA_SERVERS:
        if name not in extra:
            extra.append(name)
    return extra


def _is_cursor_browser_tool(server: str, tool: str) -> bool:
    """Read-only Cursor built-in browser MCP (cursor-ide-browser).

    Cursor's ``beforeMCPExecution`` hook often omits ``server`` and sends only
    ``tool_name`` (e.g. ``browser_tabs``). Those tools are inspection-only.
    """
    norm = tool.lstrip("/")
    if not norm.startswith("browser_"):
        return False
    if not server:
        return True
    server_l = server.lower()
    return server_l in ("cursor-ide-browser",) or "browser" in server_l


def _mcp_tool_allowed(cfg: Dict[str, Any], server: str, tool: str) -> bool:
    norm_tool = tool.lstrip("/")
    if norm_tool in APATCH_MCP_TOOLS or norm_tool.startswith("apatch_"):
        return True
    server_l = server.lower()
    if server_l in ("apatch", "user-apatch") or "apatch" in server_l:
        return bool(norm_tool.startswith("apatch_") or norm_tool in APATCH_MCP_TOOLS)
    if _is_cursor_browser_tool(server, tool):
        return True
    if server and server in effective_mcp_extra_servers(cfg):
        return True
    return False


def evaluate_pre_mcp(inp: Dict[str, Any], *, root: str = ".") -> Dict[str, Any]:
    """Cursor beforeMCPExecution: whitelist apatch MCP when sandbox enforce."""
    # No committed sandbox.json → sandbox not enabled here → do not gate MCP tools.
    if not is_sandbox_enabled(root):
        return {"permission": "allow"}
    cfg = load_sandbox_config(root)
    if cfg.get("mode") != "enforce":
        return {"permission": "allow"}

    server, tool = _extract_mcp_tool_identity(inp)
    if _mcp_tool_allowed(cfg, server, tool):
        return {"permission": "allow"}
    label = f"{server}/{tool}".strip("/") or tool or server or "unknown"
    return {
        "permission": "deny",
        "user_message": f"Sandbox blocked MCP tool: {label}",
        "agent_message": (
            f"MCP tool {label} blocked while sandbox enforce is active. "
            "Mutations: apatch_* MCP (apatch_apply_session, …). "
            "Visual inspection: cursor-ide-browser (allowed by default) or add server to "
            "mcp_extra_servers in .apatch/sandbox.json."
        ),
    }


def evaluate_shell_hook(inp: Dict[str, Any], *, root: str = ".") -> Dict[str, Any]:
    """Cursor beforeShellExecution: block obvious shell mutations when sandbox enforce.

    Only blocks shell-level file mutations (sed/awk/tee/perl -pi and file-output
    redirects outside quotes). Read-only commands pass: quoted ``>``/``<``, comparisons
    in ``python -c "..."``, fd redirects (``2>&1``), discards to ``/dev/null``
    (``2>/dev/null``), and pipes are all allowed.
    """
    # No committed sandbox.json → sandbox not enabled here → do not enforce.
    # Keeps hook behaviour consistent with apatch_doctor.sandbox.enabled and avoids a
    # user-level hook silently enforcing on projects that never opted in.
    if not is_sandbox_enabled(root):
        return {"permission": "allow"}
    cfg = load_sandbox_config(root)
    if cfg.get("mode") != "enforce":
        return {"permission": "allow"}

    import re

    command = str(inp.get("command") or "")
    if not command.strip():
        return {"permission": "allow"}
    unquoted = _unquoted_shell(command)
    if any(re.search(pat, unquoted, re.IGNORECASE) for pat in _SHELL_PACKAGE_INSTALL_RE):
        return {
            "permission": "deny",
            "user_message": "Sandbox blocked package install (human-only).",
            "agent_message": _AGENT_PIP_BLOCKED_MSG,
        }
    reason = detect_shell_mutation(command)
    if reason:
        return {
            "permission": "deny",
            "user_message": f"Sandbox blocked shell file mutation ({reason}).",
            "agent_message": (
                f"Shell file mutation via {reason} blocked while sandbox enforce is active. "
                "Use apatch_* MCP tools for edits (apatch_generate → apatch_apply_session). "
                "Note: this guard does NOT block read-only commands — '>'/'<' inside quotes "
                '(e.g. python -c "assert a < b"), fd redirects like 2>&1, discards like '
                "2>/dev/null, and pipes are fine, "
                "so re-run without a file-writing redirect (>/>>) or sed/awk/tee. "
                "Do not pip install or restart MCP — human only."
            ),
        }
    return {"permission": "allow"}


def hook_cli_main(argv: Optional[List[str]] = None) -> int:
    """Entry for `apatch sandbox hook-pre-tool` / `hook-shell`."""
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print('{"permission":"allow"}')
        return 0
    kind = argv[0]
    root = "."
    for i, a in enumerate(argv):
        if a == "--target-dir" and i + 1 < len(argv):
            root = argv[i + 1]
    try:
        inp = json.load(sys.stdin)
    except json.JSONDecodeError:
        inp = {}
    if kind == "hook-pre-tool":
        out = evaluate_pre_tool_use(inp, root=root)
    elif kind == "hook-shell":
        out = evaluate_shell_hook(inp, root=root)
    elif kind == "hook-pre-mcp":
        out = evaluate_pre_mcp(inp, root=root)
    else:
        out = {"permission": "allow"}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(hook_cli_main())
