"""RFP-008 — Spec Executor: requirement execution orchestration (SPEC-EXECUTOR-1 R1) over RFP-007 primitives."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

ERROR_SPEC_DEPENDENCY_UNMET = "SPEC_DEPENDENCY_UNMET"

_DEP_LINE_RE = re.compile(
    r"(?:зависимост|dependency|depends\s+on|prerequisite|requires)",
    re.IGNORECASE,
)
_SPEC_ID_RE = re.compile(r"SPEC-[A-Za-z][A-Za-z0-9_-]*")


def parse_spec_dependencies(raw_text: str, current_spec_id: str) -> List[str]:
    """Upstream SPEC ids declared on dependency lines (document order, deduped)."""
    seen: set[str] = set()
    out: List[str] = []
    for line in (raw_text or "").splitlines():
        if not _DEP_LINE_RE.search(line):
            continue
        for match in _SPEC_ID_RE.finditer(line):
            sid = match.group(0)
            if sid.endswith(".md"):
                sid = sid[:-3]
            if sid == current_spec_id or sid in seen:
                continue
            seen.add(sid)
            out.append(sid)
    return out


def check_spec_dependencies(
    target_dir: str,
    spec_id: str,
    *,
    source_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return dependency matrix; ``ok`` false when any upstream spec is not done. (SPEC-EXECUTOR-1 R2)"""
    from apatch.spec import spec_status_workspace

    raw = ""
    if source_path and os.path.isfile(source_path):
        try:
            with open(source_path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            raw = ""
    upstream = parse_spec_dependencies(raw, spec_id)
    if not upstream:
        return {"ok": True, "specs": [], "unmet": []}

    rows: List[Dict[str, Any]] = []
    unmet: List[str] = []
    for dep in upstream:
        st = spec_status_workspace(target_dir, spec=dep)
        done = bool(st.get("done")) if st.get("ok") else False
        rows.append({"spec": dep, "done": done, "summary": st.get("summary")})
        if not done:
            unmet.append(dep)
    return {"ok": not unmet, "specs": rows, "unmet": unmet}


def _execution_plan(spec_id: str, req_token: str, verify: Optional[str]) -> List[str]:
    steps = [
        f"apatch_execute_next(spec={spec_id!r}, dry_run=true)",
        f"apatch_session_start(requirement={req_token!r})",
        f"apatch_execute_next(spec={spec_id!r}, needles=[...])",
    ]
    if verify:
        steps.append(f"apatch_verify_run(verify={verify!r})")
    steps.extend(
        [
            "apatch_attest(message='<public completion summary>')",
            f"apatch_execute_next(spec={spec_id!r}, finalize=true)",
            f"apatch_execute_next(spec={spec_id!r})",
        ]
    )
    return steps


def _read_spec_raw(source_path: Optional[str]) -> str:
    if not source_path or not os.path.isfile(source_path):
        return ""
    try:
        with open(source_path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _active_session_requirement(target_dir: str) -> Optional[str]:
    from apatch.session_state import load_session_state

    raw = load_session_state(target_dir)
    if not raw.get("session_id") or raw.get("ended_at"):
        return None
    for art in raw.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        if art.get("kind") != "spec":
            continue
        rid = str(art.get("id") or "")
        if "#" in rid:
            return rid
    return None


def bind_runtime_to_active_session(rt: Any, target_dir: str) -> Optional[str]:
    """Pin ``rt`` to the lane's own active governed session id (RFP-036).

    Internal executor steps must resolve the exact session they belong to. An
    unbound runtime falls back to "the only active lane", which raises
    ``SESSION_AMBIGUOUS`` as soon as a second lane is active in the workspace.
    Registry-less legacy states stay unbound so their path resolution is unchanged.
    """
    from apatch.lane_context import resolve_lane_for_session
    from apatch.session_state import load_session_state

    raw = load_session_state(target_dir)
    session_id = str(raw.get("session_id") or "")
    if not session_id or raw.get("ended_at"):
        return None
    lane_id, error = resolve_lane_for_session(target_dir, session_id, active_only=False)
    if error or not lane_id:
        return None
    rt.session_id = session_id
    return session_id


def _requirement_row(status: Dict[str, Any], req_id: str) -> Optional[Dict[str, Any]]:
    for row in status.get("requirements") or []:
        if row.get("id") == req_id:
            return row
    return None


def _needles_touch_path(
    root: str,
    path: Optional[str],
    needles: List[Dict[str, Any]],
) -> bool:
    """Return true when a mutation targets ``path`` after path normalization."""
    if not path:
        return False
    expected = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    for needle in needles:
        target = str(needle.get("target_file") or "").strip()
        if not target:
            continue
        actual = os.path.realpath(
            target if os.path.isabs(target) else os.path.join(root, target)
        )
        if actual == expected:
            return True
    return False


def _patch_log_touches_path(root: str, path: Optional[str], logs_path: str) -> bool:
    """Return true when a persisted JSONL cursor contains a mutation for ``path``."""
    if not path or not os.path.isfile(logs_path):
        return False
    expected = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    try:
        with open(logs_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                target = str(
                    row.get("target_file")
                    or row.get("path")
                    or row.get("file")
                    or ""
                ).strip()
                if not target:
                    continue
                actual = os.path.realpath(
                    target if os.path.isabs(target) else os.path.join(root, target)
                )
                if actual == expected:
                    return True
    except (OSError, json.JSONDecodeError):
        return False
    return False


def _resumable_cursor(
    apply: Dict[str, Any],
    *,
    logs_path: str,
    session_path: str,
    governed_session_id: Optional[str],
) -> Dict[str, Any]:
    progress = apply.get("progress") if isinstance(apply.get("progress"), dict) else {}
    done = int(progress.get("chunks_done") or 0)
    total = int(progress.get("chunks_total") or 0)
    return {
        "authoritative": True,
        "governed_session_id": governed_session_id,
        "session_path": session_path,
        "logs_path": logs_path,
        "chunks_done": done,
        "chunks_total": total,
        "next_chunk_index": done if done < total else None,
        "complete": bool(total and done >= total),
    }


def _blocked(
    message: str,
    *,
    error_type: str = ERROR_SPEC_DEPENDENCY_UNMET,
    execution_phase: str = "blocked",
    **extra: Any,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": message,
        "error_type": error_type,
        "recommended_action": "reduce_scope",
        "recoverable": True,
        "execution_phase": execution_phase,
        **extra,
    }


def execute_next_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    requirement: Optional[str] = None,
    dry_run: bool = False,
    needles: Optional[List[Dict[str, Any]]] = None,
    finalize: bool = False,
    completion_summary: Optional[str] = None,
    logs_path: str = "patches.jsonl",
    verify_deferred: bool = True,
    defer_verify: bool = False,
    verify_override: Optional[str] = None,
    skip_lint: bool = False,
    check_dependencies: bool = True,
    governed_session_id: Optional[str] = None,
    session_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Orchestrate one requirement cycle: discover → session → mutate → finalize."""
    from apatch.runtime.runtime import MutationRuntime
    from apatch.spec import (
        resolve_requirement,
        spec_lint_workspace,
        spec_next_workspace,
        spec_status_workspace,
    )
    from apatch.workflows import generate_patch_jsonl_batch, simulate_workspace

    from apatch.worktree_lane import prepare_execution_root

    prep = prepare_execution_root(target_dir, spec=spec)
    root = os.path.abspath(prep["path"])
    if not dry_run:
        from apatch.path_leases import writer_protocol_preflight

        protocol_error = writer_protocol_preflight(root)
        if protocol_error:
            out = dict(protocol_error)
            out["execution_phase"] = "protocol_preflight"
            out["steps_completed"] = ["protocol_preflight"]
            return out
    if bool(governed_session_id) != bool(session_token):
        return {
            "ok": False,
            "error": "governed_session_id and session_token must be provided together",
            "error_type": "SESSION_BINDING_REQUIRED",
            "recoverable": True,
        }
    if governed_session_id:
        from apatch.lane_context import bind_lane_from_kwargs

        bind_lane_from_kwargs({
            "target_dir": root,
            "governed_session_id": governed_session_id,
        })
    rt = MutationRuntime(
        root,
        session_id=governed_session_id,
        session_token=session_token,
        enforce_binding=bool(governed_session_id),
    )
    steps_completed: List[str] = []

    lint: Optional[Dict[str, Any]] = None
    if not skip_lint:
        lint = spec_lint_workspace(root, spec=spec, spec_path=spec_path)
        steps_completed.append("lint")
        if not lint.get("ok"):
            return _blocked(lint.get("error", "spec lint failed"), execution_phase="blocked")
        if not lint.get("passed"):
            return _blocked(
                "spec lint failed — fix authoring errors before execution",
                execution_phase="blocked",
                lint=lint,
                steps_completed=steps_completed,
            )

    status = spec_status_workspace(root, spec=spec, spec_path=spec_path)
    if not status.get("ok"):
        return status

    spec_id = status["spec"]
    source_path = status.get("source_path")

    deps: Optional[Dict[str, Any]] = None
    if check_dependencies:
        deps = check_spec_dependencies(root, spec_id, source_path=source_path)
        steps_completed.append("dependencies")
        if not deps.get("ok"):
            unmet = ", ".join(deps.get("unmet") or [])
            return _blocked(
                f"upstream spec dependencies not attested: {unmet}",
                details={"dependencies": deps},
                dependencies=deps,
                steps_completed=steps_completed,
            )

    req_row: Optional[Dict[str, Any]] = None
    req_id: Optional[str] = None
    verify_cmd: Optional[str] = None

    if requirement:
        resolved = resolve_requirement(root, requirement, spec=spec, spec_path=spec_path)
        if not resolved.get("ok"):
            return resolved
        req_id = resolved["requirement"]
        verify_cmd = verify_override or resolved.get("verify")
        req_row = _requirement_row(status, req_id)
        if req_row is None:
            return {"ok": False, "error": f"requirement {req_id!r} not in spec {spec_id}"}
    else:
        nxt_payload = spec_next_workspace(root, spec=spec_id, spec_path=spec_path)
        steps_completed.append("spec_next")
        if not nxt_payload.get("ok"):
            return nxt_payload
        if nxt_payload.get("done"):
            return {
                "ok": True,
                "done": True,
                "spec": spec_id,
                "execution_phase": "complete",
                "summary": nxt_payload.get("summary"),
                "hint": nxt_payload.get("hint"),
                "steps_completed": steps_completed,
                "lint": lint,
                "dependencies": deps,
            }
        req_row = nxt_payload.get("next")
        req_id = req_row["id"] if req_row else None
        verify_cmd = verify_override or (req_row or {}).get("verify")

    if not req_id or not req_row:
        return {"ok": False, "error": "no open requirement found"}

    req_token = f"{spec_id}#{req_id}"

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "execution_phase": "discover",
            "spec": spec_id,
            "requirement": req_row,
            "requirement_token": req_token,
            "verify": verify_cmd,
            "remaining": [
                r["id"]
                for r in status.get("requirements") or []
                if r.get("state") in ("pending", "in_progress", "stale")
            ],
            "summary": status.get("summary"),
            "lint": lint,
            "dependencies": deps,
            "execution_plan": _execution_plan(spec_id, req_token, verify_cmd),
            "steps_completed": steps_completed,
            "agent_next": (
                f"apatch_execute_next(spec={spec_id!r}) "
                f"or apatch_session_start(requirement={req_token!r})"
            ),
        }

    if finalize:
        steps_completed.append("finalize")
        bind_runtime_to_active_session(rt, root)
        active = _active_session_requirement(root)
        if active and active != req_token:
            return _blocked(
                f"active session is for {active!r}; cannot finalize {req_token!r}",
                error_type="SESSION_MISMATCH",
                recommended_action="use_exact_session_capability",
                steps_completed=steps_completed,
                requirement_token=req_token,
            )
        if not active:
            if not governed_session_id and req_row.get("stale") and req_row.get("stale_reason") == "file_drift":
                from apatch.spec_rebind import rebind_stale_requirements

                rebound = rebind_stale_requirements(
                    root,
                    spec=spec_id,
                    spec_path=spec_path,
                    requirement_ids=[req_id],
                )
                if rebound.get("ok") and req_id in (rebound.get("rebound") or []):
                    refreshed = spec_status_workspace(root, spec=spec_id, spec_path=spec_path)
                    return {
                        "ok": True,
                        "execution_phase": "complete",
                        "spec": spec_id,
                        "requirement_token": req_token,
                        "requirement_attested": True,
                        "requirement": _requirement_row(refreshed, req_id) or req_row,
                        "spec_status": refreshed,
                        "selective_rebind": rebound,
                        "steps_completed": steps_completed + ["selective_rebind", "verify_run", "attest", "session_end"],
                        "agent_next": f"apatch_execute_next(spec={spec_id!r})",
                    }
                out = dict(rebound)
                out["ok"] = False
                out.setdefault("error", f"selective stale rebind failed for {req_token}")
                out.setdefault(
                    "error_type",
                    "VERIFY_FAILED" if rebound.get("skipped_red") else "STALE_REBIND_FAILED",
                )
                out["execution_phase"] = "blocked"
                out["requirement_token"] = req_token
                out["steps_completed"] = steps_completed + ["selective_rebind"]
                return out
            return _blocked(
                "no active governed session owns this requirement; finalize was not started",
                error_type="SESSION_BINDING_REQUIRED",
                recommended_action="start_exact_requirement_session",
                steps_completed=steps_completed,
                requirement_token=req_token,
                agent_next=f"apatch_session_start(requirement={req_token!r}, lane={spec_id!r})",
            )
        from apatch.session_state import load_session_state

        active_state = load_session_state(root)
        if isinstance(active_state.get("sdd"), dict):
            vres = rt.sdd_verify()
            steps_completed.append("sdd_verify")
            if not vres.get("ok"):
                out = dict(vres)
                out["execution_phase"] = "blocked"
                out["steps_completed"] = steps_completed
                out["requirement_token"] = req_token
                return out
        elif verify_cmd:
            vres = rt.verify_run(verify=verify_cmd)
            steps_completed.append("verify_run")
            if not vres.get("ok"):
                out = dict(vres)
                out["execution_phase"] = "blocked"
                out["steps_completed"] = steps_completed
                out["requirement_token"] = req_token
                return out

        ares = rt.attest(message=completion_summary)
        steps_completed.append("attest")
        if not ares.get("ok"):
            out = dict(ares)
            out["execution_phase"] = "blocked"
            out["steps_completed"] = steps_completed
            return out

        end = rt.close_session()
        steps_completed.append("session_end")
        if not end.get("ok"):
            return end

        refreshed = spec_status_workspace(root, spec=spec_id, spec_path=spec_path)
        att_row = _requirement_row(refreshed, req_id) if refreshed.get("ok") else None
        attested = (att_row or {}).get("state") == "attested"
        return {
            "ok": True,
            "execution_phase": "complete",
            "spec": spec_id,
            "requirement_token": req_token,
            "requirement_attested": attested,
            "requirement": att_row or req_row,
            "spec_status": refreshed,
            "steps_completed": steps_completed,
            "agent_next": f"apatch_execute_next(spec={spec_id!r})",
        }

    active = _active_session_requirement(root)
    session_reused = False
    session_start_result: Optional[Dict[str, Any]] = None
    if active == req_token:
        session_reused = True
        steps_completed.append("session_reused")
        bind_runtime_to_active_session(rt, root)
    elif active and active != req_token:
        return _blocked(
            f"active session is for {active!r}; apatch_session_end() before {req_token!r}",
            error_type="RUNTIME_TRANSITION",
            recommended_action="retry_chunk",
        )
    else:
        resolved = resolve_requirement(root, req_token, spec=spec_id, spec_path=spec_path)
        if not resolved.get("ok"):
            return resolved
        start = rt.open_session(resolved["intent"], artifacts=[resolved["artifact"]])
        steps_completed.append("session_start")  # SPEC-EXECUTOR-1 R3
        if not start.get("ok"):
            return start
        session_start_result = start
        session_reused = False

    def _close_pre_mutation_failure(out: Dict[str, Any]) -> Dict[str, Any]:
        """Close a session opened by this call when no source mutation occurred."""
        if session_reused:
            return out
        cleanup = rt.close_session()
        steps_completed.append("session_end")
        out["session_cleanup"] = {
            "attempted": True,
            "ok": cleanup.get("ok", True),
            "reason": "pre_mutation_failure",
        }
        return out

    # A partially applied batch owns its persisted cursor. Continue it before
    # considering fresh needles so execute_next cannot diverge from apply_session.
    from apatch.apply_session import default_session_path, load_session

    pending_session_path = default_session_path(root)
    pending = load_session(pending_session_path)
    pending_chunks = (pending or {}).get("chunks") or []
    pending_index = int((pending or {}).get("chunk_index") or 0)
    reset_completed_cursor = bool(
        needles
        and pending
        and pending_chunks
        and pending_index >= len(pending_chunks)
    )
    if pending and pending_chunks and pending_index < len(pending_chunks):
        stored_logs = str(pending.get("logs_path") or "")
        if not stored_logs:
            return _blocked(
                "persisted apply cursor has no logs_path",
                error_type="APPLY_CURSOR_INVALID",
                recommended_action="recover_session",
            )
        apply = rt.apply_session(stored_logs, session_path=pending_session_path)
        steps_completed.append("resume_apply_session")
        spec_touched = _patch_log_touches_path(root, source_path, stored_logs)
        artifact_refreshed = False
        if spec_touched and apply.get("ok") and not apply.get("continue"):
            refreshed_status = spec_status_workspace(
                root, spec=spec_id, spec_path=spec_path
            )
            refreshed_req = _requirement_row(refreshed_status, req_id)
            if refreshed_req is not None:
                req_row = refreshed_req
                verify_cmd = verify_override or refreshed_req.get("verify")
            steps_completed.append("refresh_spec_verify")

            refreshed_resolution = resolve_requirement(
                root, req_token, spec=spec_id, spec_path=spec_path
            )
            if not refreshed_resolution.get("ok"):
                out = dict(refreshed_resolution)
                out["execution_phase"] = "blocked"
                out["steps_completed"] = steps_completed
                return out
            from apatch.runtime.session import set_session_intent

            rebound = set_session_intent(
                root,
                refreshed_resolution["intent"],
                artifacts=[refreshed_resolution["artifact"]],
            )
            if not rebound.get("ok"):
                out = dict(rebound)
                out["execution_phase"] = "blocked"
                out["steps_completed"] = steps_completed
                return out
            artifact_refreshed = True
            steps_completed.append("refresh_spec_artifact")
        out = dict(apply)
        out["execution_phase"] = "mutate" if apply.get("continue") else "verify"
        out["spec"] = spec_id
        out["requirement_token"] = req_token
        out["requirement"] = req_row
        out["verify"] = verify_cmd
        out["verify_refreshed_after_spec_mutation"] = bool(
            spec_touched and not apply.get("continue")
        )
        out["artifact_refreshed_after_spec_mutation"] = artifact_refreshed
        out["steps_completed"] = steps_completed
        out["session_reused"] = True
        out["logs_path"] = stored_logs
        out["resumed_from_cursor"] = True
        out["needles_ignored_for_resume"] = bool(needles)
        out["resumable_cursor"] = _resumable_cursor(
            apply,
            logs_path=stored_logs,
            session_path=pending_session_path,
            governed_session_id=str(pending.get("session_id") or "") or None,
        )
        if apply.get("continue"):
            out["agent_next"] = (
                f"apatch_execute_next(spec={spec_id!r}, requirement={req_token!r})"
            )
        else:
            out["agent_next"] = f"apatch_execute_next(spec={spec_id!r}, finalize=true)"
        return out

    if needles:
        from apatch.artifact_governance import resolve_ephemeral_logs_path

        abs_logs, _rel_logs = resolve_ephemeral_logs_path(root, logs_path)
        gen = generate_patch_jsonl_batch(
            needles=needles,
            target_dir=root,
            out_path=abs_logs,
            created_by_tool="apatch_execute_next",
        )
        steps_completed.append("generate_batch")  # SPEC-EXECUTOR-1 R4
        if not gen.get("ok"):
            out = _close_pre_mutation_failure(dict(gen))
            out["execution_phase"] = "blocked"
            out["steps_completed"] = steps_completed
            return out

        abs_logs = gen.get("out_path", abs_logs)
        sim = simulate_workspace(root, logs_path=abs_logs)
        steps_completed.append("simulate")
        if not sim.get("ok", True):
            out = _close_pre_mutation_failure(dict(sim))
            out["execution_phase"] = "blocked"
            out["steps_completed"] = steps_completed
            return out

        spec_touched = _needles_touch_path(root, source_path, needles)
        if reset_completed_cursor:
            steps_completed.append("reset_completed_apply_cursor")
        apply = rt.apply_session(
            abs_logs,
            reset=reset_completed_cursor,
            # A SPEC self-edit may replace this requirement's verify command.
            # Do not execute the stale pre-mutation command during apply; the
            # refreshed command is returned below and used by finalize.
            verify=None if (spec_touched or defer_verify) else verify_cmd,
            verify_deferred=verify_deferred,
        )
        steps_completed.append("apply_session")
        if not apply.get("ok") and not apply.get("mutation_performed"):
            out = _close_pre_mutation_failure(dict(apply))
            out["execution_phase"] = "blocked"
            out["steps_completed"] = steps_completed
            return out
        artifact_refreshed = False
        if spec_touched and apply.get("ok") and not apply.get("continue"):
            refreshed_status = spec_status_workspace(
                root, spec=spec_id, spec_path=spec_path
            )
            refreshed_req = _requirement_row(refreshed_status, req_id)
            if refreshed_req is not None:
                req_row = refreshed_req
                verify_cmd = verify_override or refreshed_req.get("verify")
            steps_completed.append("refresh_spec_verify")

            refreshed_resolution = resolve_requirement(
                root, req_token, spec=spec_id, spec_path=spec_path
            )
            if not refreshed_resolution.get("ok"):
                out = dict(refreshed_resolution)
                out["execution_phase"] = "blocked"
                out["steps_completed"] = steps_completed
                return out
            from apatch.runtime.session import set_session_intent

            rebound = set_session_intent(
                root,
                refreshed_resolution["intent"],
                artifacts=[refreshed_resolution["artifact"]],
            )
            if not rebound.get("ok"):
                out = dict(rebound)
                out["execution_phase"] = "blocked"
                out["steps_completed"] = steps_completed
                return out
            artifact_refreshed = True
            steps_completed.append("refresh_spec_artifact")

        out = dict(apply)
        out["execution_phase"] = "mutate" if apply.get("continue") else "verify"
        out["spec"] = spec_id
        out["requirement_token"] = req_token
        out["requirement"] = req_row
        out["verify"] = verify_cmd
        out["verify_refreshed_after_spec_mutation"] = bool(
            spec_touched and not apply.get("continue")
        )
        out["artifact_refreshed_after_spec_mutation"] = artifact_refreshed
        out["steps_completed"] = steps_completed
        out["session_reused"] = session_reused
        out["logs_path"] = abs_logs
        if session_start_result is not None:
            out["session"] = session_start_result.get("session")
            out["session_token"] = session_start_result.get("session_token")
            out["session_capability"] = session_start_result.get("session_capability")
        if apply.get("continue"):
            out["resumable_cursor"] = _resumable_cursor(
                apply,
                logs_path=abs_logs,
                session_path=str(apply.get("session_path") or pending_session_path),
                governed_session_id=(
                    str((session_start_result or {}).get("session", {}).get("session_id") or "")
                    or str((pending or {}).get("session_id") or "")
                    or None
                ),
            )
            out["agent_next"] = (
                f"apatch_execute_next(spec={spec_id!r}, requirement={req_token!r})"
            )
        else:
            out["agent_next"] = f"apatch_execute_next(spec={spec_id!r}, finalize=true)"
        return out

    out = {
        "ok": True,
        "execution_phase": "session",
        "spec": spec_id,
        "requirement_token": req_token,
        "requirement": req_row,
        "verify": verify_cmd,
        "session_reused": session_reused,
        "steps_completed": steps_completed,
        "lint": lint,
        "dependencies": deps,
        "agent_next": (
            f"apatch_execute_next(spec={spec_id!r}, needles=[...]) "
            f"then finalize=true"
        ),
    }
    if session_start_result is not None:
        out["session"] = session_start_result.get("session")
        out["session_token"] = session_start_result.get("session_token")
        out["session_capability"] = session_start_result.get("session_capability")
    return out


def execute_next_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    """MCP/CLI entry: run executor and attach state_update."""
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    request_id = kwargs.pop("request_id", None)
    from apatch.runtime.request_journal import run_idempotent_request

    result = run_idempotent_request(
        root,
        operation="execute_next",
        request_id=request_id,
        payload=kwargs,
        execute=lambda: execute_next_workspace(root, **kwargs),
    )
    return enrich_tool_response(
        "apatch_execute_next",
        result,
        target_dir=root,
        expected_session_id=(
            kwargs.get("governed_session_id")
            if kwargs.get("governed_session_id") and kwargs.get("session_token")
            else None
        ),
    )
