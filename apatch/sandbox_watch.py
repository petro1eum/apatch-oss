"""P1/P1.5 — sandbox watcher: audit log + optional auto-revert."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from apatch.git_util import find_git_root, git_changed_files
from apatch.sandbox import (
    evaluate_write_policy,
    is_control_path,
    is_sandbox_enabled,
    is_path_protected,
    load_active_lease,
    load_sandbox_config,
)

VIOLATIONS_LOG_REL = os.path.join(".apatch", "sandbox_violations.jsonl")

WATCHER_OFF = "off"
WATCHER_AUDIT = "audit"
WATCHER_REVERT = "revert"


def violations_log_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), VIOLATIONS_LOG_REL)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def should_auto_revert(
    cfg: Dict[str, Any],
    auto_revert_flag: bool = False,
    *,
    respect_watcher_revert: bool = True,
) -> bool:
    """True when explicit flag or watcher policy allows auto-revert."""
    return bool(
        auto_revert_flag
        or (respect_watcher_revert and cfg.get("watcher") == WATCHER_REVERT)
    )


def _git_name_only(root: str, args: List[str]) -> Set[str]:
    git_root = find_git_root(root) or root
    try:
        proc = subprocess.run(
            ["git", "-C", git_root, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode != 0:
        return set()
    return {line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()}


def _collect_candidate_paths(root: str, base: Optional[str] = None) -> List[str]:
    """Return protected paths in the exact local or Ring-2 change set.

    Local audit (no base) observes the complete working tree, including untracked
    files. Ring-2 base mode observes only committed ``base..HEAD`` bytes plus the
    staged index. Unrelated unstaged/untracked owner work is not part of a PR gate.
    """
    root = os.path.abspath(root)
    cfg = load_sandbox_config(root)
    paths: Set[str] = set()
    if base is not None:
        changed = _git_name_only(
            root, ["diff", "--name-only", "--diff-filter=ACMRD", base, "HEAD", "--"]
        )
        changed.update(
            _git_name_only(
                root, ["diff", "--cached", "--name-only", "--diff-filter=ACMRD", "--"]
            )
        )
        return sorted(rel for rel in changed if is_path_protected(rel, cfg))

    try:
        for rel in git_changed_files(root, since="HEAD"):
            if is_path_protected(rel, cfg):
                paths.add(rel.replace("\\", "/"))
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not paths and os.path.isdir(root):
        for dirpath, _dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir.startswith("."):
                continue
            for name in filenames:
                rel = os.path.join(rel_dir, name).replace("\\", "/")
                if rel.startswith("./"):
                    rel = rel[2:]
                if is_path_protected(rel, cfg):
                    abs_p = os.path.join(root, rel)
                    if os.path.isfile(abs_p):
                        paths.add(rel)
    return sorted(paths)


def _committed_notarized_hashes(root: str) -> Dict[str, str]:
    from apatch.enforcement import (
        load_notarized_index,
        rebuild_notarized_index_from_ledger,
    )

    index = load_notarized_index(root)
    if int(index.get("version") or 0) < 3:
        index = rebuild_notarized_index_from_ledger(root)
    committed: Dict[str, str] = {}
    for rel, entry in (index.get("committed_files") or {}).items():
        if not isinstance(entry, dict):
            continue
        sha = str(entry.get("sha256") or "").strip()
        if sha:
            committed[str(rel).replace("\\", "/")] = sha
    return committed


def scan_unleased_violations(
    root: str, base: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return protected changes lacking either a live lease or committed proof."""
    if not is_sandbox_enabled(root):
        return []
    root = os.path.abspath(root)
    cfg = load_sandbox_config(root)
    committed_hashes = _committed_notarized_hashes(root)
    violations: List[Dict[str, Any]] = []
    for rel in _collect_candidate_paths(root, base=base):
        # Ring-2 (CI) does not gate control-plane policy files — those are
        # operator commits, tamper-evident via signed policy (§5.8). Ring-1
        # (agent channel) still blocks them via evaluate_write_policy.
        if is_control_path(rel):
            continue
        # With path-scoped leases the matching capability can differ per path.
        decision = evaluate_write_policy(root, rel, cfg=cfg)
        if decision.get("allowed"):
            continue
        abs_p = os.path.join(root, rel)
        sha = None
        tracked = False
        if os.path.isfile(abs_p):
            from apatch.enforcement import file_sha256

            sha = file_sha256(abs_p)
        if sha and committed_hashes.get(rel) == sha:
            continue
        gr = find_git_root(root)
        if gr:
            tracked = _is_git_tracked(gr, rel)
        violations.append(
            {
                "path": rel,
                "reason": decision.get("reason", "direct_write_blocked"),
                "sha256": sha,
                "git_tracked": tracked,
                "lease_active": bool(load_active_lease(root, path=rel)),
                "committed_proof": False,
                "detected_at": _utc_iso(),
            }
        )
    return violations


def _is_git_tracked(git_root: str, rel_path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", git_root, "ls-files", "--error-unmatch", "--", rel_path],
        capture_output=True,
        timeout=15,
    )
    return proc.returncode == 0


def _git_restore_tracked(git_root: str, rel_path: str) -> bool:
    """Restore tracked file to HEAD (index + worktree)."""
    proc = subprocess.run(
        ["git", "-C", git_root, "checkout", "HEAD", "--", rel_path],
        capture_output=True,
        timeout=30,
    )
    return proc.returncode == 0


def revert_violations(
    root: str,
    violations: List[Dict[str, Any]],
    *,
    revert_untracked: bool = True,
) -> Dict[str, Any]:
    """Revert unleased protected mutations: git restore tracked, remove untracked."""
    root = os.path.abspath(root)
    gr = find_git_root(root)
    reverted_tracked: List[str] = []
    removed_untracked: List[str] = []
    failed: List[Dict[str, Any]] = []

    for v in violations:
        rel = str(v.get("path") or "")
        if not rel:
            continue
        abs_p = os.path.join(root, rel)

        if gr and (v.get("git_tracked") or _is_git_tracked(gr, rel)):
            if _git_restore_tracked(gr, rel):
                reverted_tracked.append(rel)
            else:
                failed.append({"path": rel, "reason": "git_restore_failed"})
            continue

        if os.path.isfile(abs_p):
            if not revert_untracked:
                failed.append({"path": rel, "reason": "untracked_not_removed"})
                continue
            try:
                os.remove(abs_p)
                removed_untracked.append(rel)
            except OSError as exc:
                failed.append({"path": rel, "reason": f"unlink_failed: {exc}"})
            continue

        if gr and _git_restore_tracked(gr, rel):
            reverted_tracked.append(rel)
        else:
            failed.append({"path": rel, "reason": "missing_not_restored"})

    return {
        "reverted_tracked": reverted_tracked,
        "removed_untracked": removed_untracked,
        "failed": failed,
        "reverted_count": len(reverted_tracked) + len(removed_untracked),
    }


def _load_recent_fingerprints(root: str, limit: int = 500) -> Set[str]:
    path = violations_log_path(root)
    if not os.path.isfile(path):
        return set()
    fps: Set[str] = set()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            try:
                row = json.loads(line)
                fp = f"{row.get('path')}|{row.get('sha256')}"
                fps.add(fp)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return fps


def append_violations(root: str, violations: List[Dict[str, Any]]) -> int:
    if not violations:
        return 0
    path = violations_log_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = _load_recent_fingerprints(root)
    written = 0
    with open(path, "a", encoding="utf-8") as f:
        for v in violations:
            fp = f"{v.get('path')}|{v.get('sha256')}"
            if fp in seen:
                continue
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
            seen.add(fp)
            written += 1
    return written


def _annotate_violations_with_revert(
    violations: List[Dict[str, Any]],
    revert_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    tracked = set(revert_result.get("reverted_tracked") or [])
    untracked = set(revert_result.get("removed_untracked") or [])
    out: List[Dict[str, Any]] = []
    for v in violations:
        row = dict(v)
        p = row.get("path")
        if p in tracked:
            row["revert_action"] = "git_restore"
            row["reverted_at"] = _utc_iso()
        elif p in untracked:
            row["revert_action"] = "removed_untracked"
            row["reverted_at"] = _utc_iso()
        out.append(row)
    return out


def run_sandbox_watch_once(
    root: str = ".",
    *,
    auto_revert: bool = False,
    force: bool = False,
    base: Optional[str] = None,
    report_only: bool = False,
    respect_watcher_revert: bool = True,
) -> Dict[str, Any]:
    """Single audit pass with optional auto-revert.

    ``base`` switches to PR-diff mode (changes vs that ref) — used by the CI
    gate to enforce over shared history. ``respect_watcher_revert=False`` is
    for explicit audit surfaces where a caller asked for report-only behavior
    even if sandbox.json has ``watcher: revert``.
    """
    root = os.path.abspath(root)
    cfg = load_sandbox_config(root)
    watcher_mode = cfg.get("watcher", WATCHER_OFF)
    # report_only (CI gate): never revert; violations must fail the gate,
    # not be silently healed by the config watcher.
    do_revert = False if report_only else should_auto_revert(
        cfg,
        auto_revert,
        respect_watcher_revert=respect_watcher_revert,
    )

    if not force and watcher_mode == WATCHER_OFF and not do_revert:
        return {
            "ok": True,
            "skipped": True,
            "reason": "watcher_off",
            "hint": "Set sandbox.json watcher to 'audit'/'revert' or use apatch sandbox audit",
        }

    violations = scan_unleased_violations(root, base=base)
    new_count = append_violations(root, violations)

    revert_result: Dict[str, Any] = {
        "reverted_tracked": [],
        "removed_untracked": [],
        "failed": [],
        "reverted_count": 0,
    }
    if do_revert and violations:
        revert_untracked = cfg.get("revert_untracked", True)
        revert_result = revert_violations(
            root,
            violations,
            revert_untracked=bool(revert_untracked),
        )
        reverted_rows = _annotate_violations_with_revert(
            [v for v in violations if v.get("path") in set(revert_result.get("reverted_tracked") or [])
             | set(revert_result.get("removed_untracked") or [])],
            revert_result,
        )
        if reverted_rows:
            append_violations(root, reverted_rows)

    all_reverted = (
        do_revert
        and violations
        and not revert_result.get("failed")
        and int(revert_result.get("reverted_count") or 0) == len(violations)
    )

    return {
        "ok": len(violations) == 0 or all_reverted,
        "mode": cfg.get("mode"),
        "watcher": watcher_mode,
        "scanned_violations": len(violations),
        "new_log_entries": new_count,
        "violations": violations[:20],
        "auto_revert": do_revert,
        "reverted_tracked": revert_result.get("reverted_tracked") or [],
        "removed_untracked": revert_result.get("removed_untracked") or [],
        "revert_failed": revert_result.get("failed") or [],
        "reverted_count": revert_result.get("reverted_count", 0),
        "violations_log": violations_log_path(root),
    }


def run_sandbox_watch_loop(
    root: str = ".",
    *,
    interval: float = 2.0,
    max_iterations: Optional[int] = None,
    auto_revert: bool = False,
) -> Dict[str, Any]:
    """Poll workspace for unleased protected mutations."""
    root = os.path.abspath(root)
    cfg = load_sandbox_config(root)
    do_revert = should_auto_revert(cfg, auto_revert)
    if cfg.get("watcher") not in (WATCHER_AUDIT, WATCHER_REVERT) and not do_revert:
        return {
            "ok": False,
            "error": "watcher not enabled; set sandbox.json watcher to 'audit' or 'revert'",
        }

    total_new = 0
    total_violations = 0
    total_reverted = 0
    iterations = 0
    try:
        while True:
            result = run_sandbox_watch_once(
                root,
                auto_revert=do_revert,
                force=True,
            )
            if not result.get("skipped"):
                total_new += int(result.get("new_log_entries") or 0)
                total_violations += int(result.get("scanned_violations") or 0)
                total_reverted += int(result.get("reverted_count") or 0)
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass

    return {
        "ok": True,
        "iterations": iterations,
        "auto_revert": do_revert,
        "watcher": cfg.get("watcher"),
        "total_new_log_entries": total_new,
        "total_violations_seen": total_violations,
        "total_reverted": total_reverted,
        "violations_log": violations_log_path(root),
    }


def run_sandbox_ci_gate(
    root: str = ".", base: Optional[str] = None
) -> Dict[str, Any]:
    """CI/PR gate: audit unleased protected writes + optional notarization check.

    Pass ``base`` (e.g. ``origin/main``) on a PR so the gate inspects the change
    set being merged — the authoritative Ring-2 boundary. This catches mutations
    that bypassed the local Ring-1 hook (e.g. ``git commit --no-verify``).
    """
    root = os.path.abspath(root)
    from apatch.enforcement import is_enforcement_enabled, verify_paths_notarized
    from apatch.sandbox import is_sandbox_enabled

    sandbox_on = is_sandbox_enabled(root)
    enforce_on = is_enforcement_enabled(root)
    if not sandbox_on and not enforce_on:
        return {
            "ok": True,
            "skipped": True,
            "gate": "skipped",
            "reason": "no .apatch/sandbox.json or enforcement.json",
        }

    candidate_paths = _collect_candidate_paths(root, base=base)
    audit = run_sandbox_watch_once(
        root, force=True, auto_revert=False, base=base, report_only=True
    )
    out: Dict[str, Any] = {
        "ok": True,
        "gate": "passed",
        "base": base,
        "sandbox_enabled": sandbox_on,
        "enforcement_enabled": enforce_on,
        "candidate_paths": candidate_paths,
        "audit": audit,
    }
    if not audit.get("ok") and not audit.get("skipped"):
        out["ok"] = False
        out["gate"] = "failed"
        out["reason"] = "unleased protected mutations detected"
        return out

    if enforce_on:
        notary = verify_paths_notarized(root, candidate_paths)
        out["notarization"] = notary
        if not notary.get("ok"):
            out["ok"] = False
            out["gate"] = "failed"
            out["reason"] = "working tree not notarized"

        # Signed policy (RFP-005 §0.3): if the monitor config is signed, it must
        # still match the signed manifest. Drift / bad signature = tampered
        # control plane → fail. Unsigned policy is not failed here (opt-in).
        from apatch.policy_lock import verify_policy

        policy = verify_policy(root)
        if policy.get("signed"):
            out["policy"] = policy
            if not policy.get("ok"):
                out["ok"] = False
                out["gate"] = "failed"
                out["reason"] = (
                    "policy drift" if policy.get("has_drift") else "policy signature invalid"
                )

        # External inclusion (RFP-005 §5.10): recorded ledger ops must be
        # provably committed in the public append-only log. Opt-in — skips
        # cleanly when nothing is anchored externally.
        from apatch.inclusion import verify_inclusion

        inclusion = verify_inclusion(root)
        if not inclusion.get("skipped"):
            out["inclusion"] = inclusion
            if not inclusion.get("ok"):
                out["ok"] = False
                out["gate"] = "failed"
                out["reason"] = "external inclusion failed"
    return out


def count_recent_violations(root: str, limit: int = 50) -> int:
    path = violations_log_path(root)
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return min(limit, sum(1 for _ in f))
    except OSError:
        return 0
