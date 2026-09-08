"""Chunked apply sessions: checkpoints, progress file, safe mass refactor for MCP."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Set

from apatch.enforcement import assert_can_apply
from apatch.workflows import (
    WorkflowError,
    apply_from_logs,
    plan_from_logs,
    rollback_workspace,
)

# Legacy default (pre lane-scope). Prefer default_session_path(target_dir).
DEFAULT_SESSION_PATH = os.path.join(".apatch", "apply_session.json")
DEFAULT_CHUNK_FILES = 5
MASS_APPLY_GUARD = 15


def default_session_path(target_dir: str) -> str:
    """Lane-scoped apply_session state — same isolation as spec_run / session_state."""
    from apatch.lane import lane_state_path

    return lane_state_path(target_dir, "apply_session.json")


def _session_abs(target_dir: str, session_path: Optional[str]) -> str:
    if session_path:
        if os.path.isabs(session_path):
            return session_path
        return os.path.join(os.path.abspath(target_dir), session_path)
    return default_session_path(target_dir)


def _norm_logs_path(logs_path: str, target_dir: str) -> str:
    base = os.path.abspath(os.path.expanduser(target_dir))
    if os.path.isabs(logs_path):
        return os.path.normpath(logs_path)
    return os.path.normpath(os.path.join(base, logs_path))


def _session_is_complete(session: Dict[str, Any]) -> bool:
    chunks = session.get("chunks") or []
    if not chunks:
        return False
    return int(session.get("chunk_index", 0)) >= len(chunks)


def _clear_session_file(path: str) -> None:
    if os.path.isfile(path):
        os.remove(path)


def discard_apply_session_state(
    target_dir: str,
    session_path: Optional[str] = None,
    *,
    expected_session_id: Optional[str] = None,
) -> bool:
    """Discard only chunk progress owned by the intended governed session."""
    path = _session_abs(target_dir, session_path)
    state = load_session(path)
    if state is None:
        return False
    if expected_session_id:
        owner = str(
            state.get("governed_session_id") or state.get("session_id") or ""
        )
        if owner != str(expected_session_id):
            return False
    _clear_session_file(path)
    return True


def load_session(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_session(path: str, data: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    persisted = dict(data)
    if not persisted.get("session_id"):
        state_path = os.path.join(parent, "session_state.json")
        try:
            with open(state_path, encoding="utf-8") as handle:
                lane_state = json.load(handle)
            if lane_state.get("session_id"):
                persisted["session_id"] = lane_state["session_id"]
        except (OSError, ValueError, TypeError):
            pass
    from apatch.artifact_governance import workspace_from_artifact_path

    ws = workspace_from_artifact_path(path)
    if persisted.get("session_id") and not persisted.get("runtime_namespace"):
        from apatch.runtime.namespace import runtime_namespace

        persisted["runtime_namespace"] = runtime_namespace(
            ws,
            session_id=str(persisted["session_id"]),
        )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(persisted, f, indent=2, ensure_ascii=False)
    from apatch.artifact_governance import sync_run_state_registry

    complete = _session_is_complete(persisted)
    lease_id: Optional[str] = None
    if not complete:
        sid = persisted.get("session_id")
        ckpt = persisted.get("last_checkpoint") or persisted.get("checkpoint")
        if sid and ckpt:
            lease_id = f"lease_{sid}_{ckpt}"
        elif ckpt:
            lease_id = f"lease_{ckpt}"
        elif sid:
            lease_id = f"lease_{sid}"
        else:
            lease_id = "lease_active"
    sync_run_state_registry(
        ws,
        path,
        lease_id=lease_id,
        created_by_tool="apatch_apply_session",
        reason="chunked apply session state",
        governed_session_id=persisted.get("session_id"),
    )


def dropped_plan_steps(plan_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Steps present in the patch log that will NOT be applied (would_apply
    false — anchor drifted, already applied, or unresolved target).

    A multi-file batch that silently drops one file's patches applies a
    partial change and then fails verification in a confusing way ("no
    tests ran" when the test-file patch was the one dropped). Surfacing
    these turns a silent partial-apply into a loud, actionable signal.
    """
    dropped: List[Dict[str, Any]] = []
    for entry in plan_entries:
        if entry.get("would_apply"):
            continue
        dropped.append({
            "step_index": entry.get("step_index"),
            "target_file": entry.get("target_file")
            or entry.get("resolved_path"),
            "strategy": entry.get("strategy"),
            "confidence": entry.get("confidence"),
            "reason": entry.get("reason") or "anchor not found / already applied",
        })
    return dropped


def _build_chunks(
    plan_entries: List[Dict[str, Any]],
    *,
    chunk_max_files: int,
) -> List[List[int]]:
    """Split steps without ever dividing one target file across chunks.

    Each file spans the interval from its first to last step. Overlapping
    intervals form an atomic group, preserving log order even for A -> B -> A.
    Adjacent atomic groups are then packed by their unique file count.
    """
    steps_ordered: List[int] = []
    step_files: Dict[int, Set[str]] = {}
    for entry in plan_entries:
        if not entry.get("would_apply"):
            continue
        step = int(entry["step_index"])
        path = entry.get("resolved_path") or entry.get("target_file") or ""
        if step not in step_files:
            steps_ordered.append(step)
            step_files[step] = set()
        if path:
            step_files[step].add(path)

    if not steps_ordered:
        return []

    # Unknown targets still consume one chunk slot but cannot connect steps.
    for step in steps_ordered:
        if not step_files[step]:
            step_files[step].add(f"__step__:{step}")

    last_position: Dict[str, int] = {}
    for position, step in enumerate(steps_ordered):
        for path in step_files[step]:
            last_position[path] = position

    atomic_groups: List[List[int]] = []
    group_files: List[Set[str]] = []
    start = 0
    while start < len(steps_ordered):
        end = start
        files: Set[str] = set()
        cursor = start
        while cursor <= end:
            step = steps_ordered[cursor]
            files.update(step_files[step])
            end = max(end, *(last_position[path] for path in step_files[step]))
            cursor += 1
        atomic_groups.append(steps_ordered[start : end + 1])
        group_files.append(files)
        start = end + 1

    chunks: List[List[int]] = []
    current_steps: List[int] = []
    current_files: Set[str] = set()
    for steps, files in zip(atomic_groups, group_files):
        combined_files = current_files | files
        if current_steps and len(combined_files) > chunk_max_files:
            chunks.append(current_steps)
            current_steps = []
            current_files = set()
        current_steps.extend(steps)
        current_files.update(files)
    if current_steps:
        chunks.append(current_steps)
    return chunks


def _lease_paths_from_entries(entries: List[Dict[str, Any]]) -> List[str]:
    paths: List[str] = []
    seen: Set[str] = set()
    for entry in entries:
        if not entry.get("would_apply"):
            continue
        path = entry.get("resolved_path") or entry.get("target_file") or ""
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def plan_chunks_for_logs(
    logs_path: str,
    target_dir: str = ".",
    *,
    chunk_max_files: int = DEFAULT_CHUNK_FILES,
    tool: Optional[str] = None,
    keyword: Optional[str] = None,
    only_drifted: bool = False,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """Dry-run plan and return chunk boundaries (step index lists)."""
    plan = plan_from_logs(
        logs_path,
        target_dir,
        tool=tool,
        keyword=keyword,
    )
    entries = plan.get("entries") or []
    if only_drifted or min_confidence is not None:
        filtered: List[Dict[str, Any]] = []
        for e in entries:
            if only_drifted and e.get("strategy") == "exact":
                continue
            if min_confidence is not None and float(e.get("confidence") or 0) < min_confidence:
                continue
            filtered.append(e)
        entries = filtered
    chunks = _build_chunks(entries, chunk_max_files=chunk_max_files)
    dropped = dropped_plan_steps(entries)
    return {
        "logs_path": logs_path,
        "target_dir": os.path.abspath(target_dir),
        "total_candidates": plan.get("total", 0),
        "would_apply": sum(1 for e in entries if e.get("would_apply")),
        "chunk_max_files": chunk_max_files,
        "chunks": chunks,
        "chunk_count": len(chunks),
        "lease_paths": _lease_paths_from_entries(entries),
        # Steps in the log that will NOT be applied — surfaced so a
        # multi-file batch never silently drops one file's patches.
        "dropped_steps": dropped,
    }


def should_require_session(
    candidate_count: int,
    *,
    steps: Optional[str] = None,
    range_str: Optional[str] = None,
    budget: Optional[Dict[str, int]] = None,
    dry_run: bool = False,
) -> bool:
    if dry_run or steps or range_str or budget:
        return False
    return candidate_count > MASS_APPLY_GUARD


def run_apply_session(
    logs_path: str,
    target_dir: str = ".",
    *,
    session_path: Optional[str] = None,
    verify: Optional[str] = None,
    chunk_max_files: int = DEFAULT_CHUNK_FILES,
    replace_all: bool = False,
    only_drifted: bool = False,
    min_confidence: Optional[float] = None,
    verify_deferred: bool = True,
    no_trustchain: bool = False,
    tool: Optional[str] = None,
    keyword: Optional[str] = None,
    reset: bool = False,
    abort: bool = False,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Apply JSONL in bounded chunks with checkpoint after each chunk.

    Call repeatedly until ``continue`` is false. State is persisted in session_path.
    """
    block = assert_can_apply(target_dir, no_trustchain=no_trustchain)
    if block:
        raise WorkflowError(block)

    abs_session = _session_abs(target_dir, session_path)
    abs_logs = _norm_logs_path(logs_path, target_dir)
    if abort:
        session = load_session(abs_session)
        ckpt = (session or {}).get("last_checkpoint")
        rollback_info = None
        if ckpt:
            rollback_info = rollback_workspace(target_dir, ckpt)
        try:
            from apatch.sandbox import release_lease

            release_lease(
                target_dir,
                lease_id=(session or {}).get("lease_id"),
                governed_session_id=(session or {}).get("governed_session_id"),
                aborted=True,
            )
        except Exception:
            pass
        if os.path.isfile(abs_session):
            os.remove(abs_session)
        return {
            "ok": True,
            "aborted": True,
            "checkpoint": ckpt,
            "rollback": rollback_info,
            "continue": False,
            "agent_next": "Session cleared. Fix the issue, regenerate patches.jsonl, call apatch_apply_session(reset=true).",
        }

    session = None if reset else load_session(abs_session)
    if session:
        stored_dir = session.get("target_dir") or target_dir
        stored_logs = _norm_logs_path(session.get("logs_path") or "", stored_dir)
        if stored_logs != abs_logs:
            if _session_is_complete(session):
                _clear_session_file(abs_session)
                session = None
            else:
                done = int(session.get("chunk_index", 0))
                total = len(session.get("chunks") or [])
                raise WorkflowError(
                    "apply_session: in-progress session for "
                    f"{session.get('logs_path')!r} ({done}/{total} chunks); "
                    "pass reset=true to abandon or abort=true to rollback"
                )

    if session is None:
        layout = plan_chunks_for_logs(
            logs_path,
            target_dir,
            chunk_max_files=chunk_max_files,
            tool=tool,
            keyword=keyword,
            only_drifted=only_drifted,
            min_confidence=min_confidence,
        )
        chunks = layout["chunks"]
        dropped = layout.get("dropped_steps") or []
        if not chunks:
            return {
                "ok": False,
                "error": "apply_session: plan found no applicable chunks"
                + (
                    f" — {len(dropped)} log step(s) will not apply "
                    f"(anchors not found / already applied): "
                    + ", ".join(
                        f"#{d['step_index']} {d['target_file']}"
                        for d in dropped
                    )
                    if dropped else ""
                ),
                "dropped_steps": dropped,
                "layout": layout,
                "continue": False,
            }
        from apatch.sdd_integrity import admit_session_effect

        admit_session_effect(
            target_dir,
            {
                "surface": "mutation",
                "effect": "write",
                "paths": list(layout.get("lease_paths") or []),
                "plan_match": not bool(dropped),
            },
        )
        verify_baseline = None
        if verify:
            from apatch.session_state import load_session_state
            from apatch.verify_baseline import capture_baseline

            governed = load_session_state(target_dir)
            verify_baseline = capture_baseline(
                target_dir,
                verify_command=verify,
                session_id=str(governed.get("session_id") or "") or None,
            )
        session = {
            "logs_path": abs_logs,
            "target_dir": os.path.abspath(target_dir),
            "verify": verify,
            "chunk_max_files": chunk_max_files,
            "replace_all": replace_all,
            "only_drifted": only_drifted,
            "min_confidence": min_confidence,
            "verify_deferred": verify_deferred,
            "verify_baseline": verify_baseline,
            "no_trustchain": no_trustchain,
            "tool": tool,
            "keyword": keyword,
            "chunks": chunks,
            "chunk_index": 0,
            "checkpoints": [],
            "last_checkpoint": None,
            "chunk_reports": [],
            "sandbox_lease": False,
            "lease_paths": list(layout.get("lease_paths") or []),
            # Loud record of log steps the plan will NOT apply, so a
            # multi-file batch cannot silently drop one file's patches.
            "dropped_steps": dropped,
        }
        try:
            from apatch.session_state import load_session_state

            governed = load_session_state(target_dir)
            if governed.get("session_id") and not governed.get("ended_at"):
                session["governed_session_id"] = str(governed["session_id"])
        except Exception:
            pass
        save_session(abs_session, session)

    chunks = session["chunks"]
    idx = int(session["chunk_index"])
    if idx >= len(chunks):
        if session.get("sandbox_lease"):
            try:
                from apatch.sandbox import release_lease

                release_lease(
                    target_dir,
                    lease_id=session.get("lease_id"),
                    governed_session_id=session.get("governed_session_id"),
                )
            except Exception:
                pass
        return {
            "ok": True,
            "phase": "done",
            "status": "SESSION_ALREADY_COMPLETE",
            "continue": False,
            "session_path": abs_session,
            "checkpoint": session.get("last_checkpoint"),
            "checkpoints": session.get("checkpoints", []),
            "progress": {
                "chunks_done": len(chunks),
                "chunks_total": len(chunks),
                "percent": 100,
            },
            "agent_next": (
                "Session already complete — nothing applied. Pass reset=true to "
                "re-apply a changed patch set, or proceed to verify/attest."
            ),
        }

    # Admission is part of every resumable call. A process that crashed loses
    # its PID-bound lease; the exact governed session can deterministically
    # reacquire the persisted write-set without consulting patches.jsonl.
    try:
        from apatch.sandbox import SandboxError, acquire_lease

        lease_paths = list(session.get("lease_paths") or [])
        if not lease_paths:
            lease_paths = list(
                plan_chunks_for_logs(
                    session["logs_path"],
                    target_dir,
                    chunk_max_files=int(session.get("chunk_max_files") or chunk_max_files),
                    tool=session.get("tool"),
                    keyword=session.get("keyword"),
                    only_drifted=bool(session.get("only_drifted")),
                    min_confidence=session.get("min_confidence"),
                ).get("lease_paths")
                or []
            )
            session["lease_paths"] = lease_paths
        cap = acquire_lease(
            target_dir,
            lease_paths,
            tool="apatch_apply_session",
            governed_session_id=session.get("governed_session_id"),
        )
        session["sandbox_lease"] = True
        session["lease_id"] = cap.get("lease_id")
        save_session(abs_session, session)
    except SandboxError as exc:
        raise WorkflowError(str(exc)) from exc

    step_list = chunks[idx]
    steps = ",".join(str(s) for s in step_list)
    chunk_report = os.path.join(
        os.path.dirname(abs_session),
        f"apply_session_chunk_{idx + 1}.json",
    )

    deferred_verify = bool(session.get("verify_deferred", verify_deferred))
    final_chunk = idx == len(chunks) - 1
    configured_verify = session.get("verify", verify)
    # Deferred verification is a whole apply-session guarantee, not merely a
    # per-chunk guarantee. Running a repository-wide command after an early
    # chunk creates false failures whenever code and its tests land in
    # different chunks. Keep the command for the final chunk; the TUI then
    # runs it once after every candidate in the complete session is present.
    chunk_verify = configured_verify if final_chunk or not deferred_verify else None

    result = apply_from_logs(
        logs_path,
        target_dir,
        tool=session.get("tool"),
        keyword=session.get("keyword"),
        steps=steps,
        replace_all=session.get("replace_all", replace_all),
        min_confidence=session.get("min_confidence", min_confidence),
        only_drifted=session.get("only_drifted", only_drifted),
        verify=chunk_verify,
        verify_deferred=deferred_verify,
        verify_baseline=session.get("verify_baseline"),
        no_trustchain=session.get("no_trustchain", no_trustchain),
        report_path=chunk_report,
        quiet=quiet,
        sandbox_owns_lease=False,
    )

    ckpt = result.get("checkpoint")
    if ckpt:
        session["last_checkpoint"] = ckpt
        session["checkpoints"] = list(session.get("checkpoints") or []) + [ckpt]
    if result.get("verify_rollback"):
        # Verify failed and the chunk was rolled back: do NOT advance the session.
        # A resumed "completed" session would silently apply nothing (no-op) while
        # reporting ok=true. Clear state so the next call re-applies the chunk.
        if session.get("sandbox_lease"):
            try:
                from apatch.sandbox import release_lease

                release_lease(
                    target_dir,
                    lease_id=session.get("lease_id"),
                    governed_session_id=session.get("governed_session_id"),
                )
            except Exception:
                pass
        if os.path.isfile(abs_session):
            os.remove(abs_session)
        failure_out = {
            "ok": False,
            "phase": "failed",
            "continue": False,
            "session_path": abs_session,
            "checkpoint": ckpt or session.get("last_checkpoint"),
            "error_type": "VERIFY_FAILED",
            "recoverable": True,
            "recommended_action": "fix_forward",
            "verify_rollback": True,
            "rollback_performed": True,
            "error": "verify failed; chunk already rolled back and apply cursor cleared for fix-forward",
            "verify_output": result.get("verify_output", ""),
            "chunk_result": {
                "applied": result.get("applied", 0),
                "failed": result.get("failed", 0),
                "skipped": result.get("skipped", 0),
                "rolled_back": result.get("rolled_back", 0),
                "verify_rollback": True,
                "rollback_performed": True,
                "report_path": result.get("report_path"),
            },
            "agent_next": "Call apatch_resume_session, apply corrected needles, then re-verify and attest the same governed session.",
            "rollback_hint": "not required: chunk files already restored",
        }
        if failure_out.get("verify_output"):
            from apatch.build_diagnose import enrich_verify_failure

            enrich_verify_failure(
                failure_out,
                target_dir,
                log_text=failure_out["verify_output"],
                verify=session.get("verify", verify),
            )
        return failure_out
    session["chunk_index"] = idx + 1
    session["chunk_reports"] = list(session.get("chunk_reports") or []) + [chunk_report]
    save_session(abs_session, session)

    done = session["chunk_index"]
    total = len(chunks)
    continue_apply = done < total
    agent_next = (
        f"Call apatch_apply_session(session_path={abs_session!r}) "
        f"again — chunk {done}/{total} done."
        if continue_apply
        else "All chunks applied. Verify tests; on failure apatch_rollback(session_id=checkpoint)."
    )

    out = {
        "ok": result.get("ok", False) and not result.get("verify_rollback"),
        "phase": "apply" if continue_apply else "done",
        "continue": continue_apply,
        "session_path": abs_session,
        "checkpoint": ckpt or session.get("last_checkpoint"),
        "checkpoints": session.get("checkpoints", []),
        "chunk_index": idx,
        "chunk_steps": step_list,
        "chunk_result": {
            "applied": result.get("applied", 0),
            "failed": result.get("failed", 0),
            "skipped": result.get("skipped", 0),
            "rolled_back": result.get("rolled_back", 0),
            "verify_rollback": result.get("verify_rollback", False),
            "report_path": result.get("report_path"),
        },
        "progress": {
            "chunks_done": done,
            "chunks_total": total,
            "percent": int(100 * done / total) if total else 100,
        },
        "agent_next": agent_next,
        "rollback_hint": (
            f"apatch_rollback(session_id={ckpt!r})"
            if ckpt
            else "apatch_rollback() for latest checkpoint"
        ),
    }
    _dropped = session.get("dropped_steps") or []
    if _dropped:
        # Never let a partial multi-file apply pass unnoticed: the caller
        # asked for these steps and the plan is not applying them.
        out["dropped_steps"] = _dropped
        out["warnings"] = list(out.get("warnings") or []) + [
            f"{len(_dropped)} log step(s) were NOT applied (anchor not found "
            "/ already applied): "
            + ", ".join(f"#{d['step_index']} {d['target_file']}" for d in _dropped)
            + " — the patch log is stale or a prior partial apply lingered; "
            "regenerate patches from current needles (fresh logs_path)."
        ]
    if not continue_apply and out.get("ok"):
        try:
            from apatch.mcp_health import build_tooling_refresh

            refresh = build_tooling_refresh(target_dir)
            if refresh.get("applies"):
                out["tooling_refresh"] = refresh
                out["agent_next"] = (
                    "apatch source changed: send user tooling_refresh.human_steps "
                    "and wait for MCP restart before testing new tools."
                )
        except Exception:
            pass
        try:
            from apatch.mcp_health import self_edit_restart_signal

            _applied = []
            try:
                with open(abs_logs, encoding="utf-8") as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if not _line:
                            continue
                        _rec = json.loads(_line)
                        if not isinstance(_rec, dict):
                            continue
                        for _k in ("target_file", "path", "file", "resolved_path"):
                            if _rec.get(_k):
                                _applied.append(_rec[_k])
            except Exception:
                pass
            _sig = self_edit_restart_signal(target_dir, _applied)
            if _sig:
                out["restart_required"] = True
                out["self_edit"] = _sig
        except Exception:
            pass
    return out
