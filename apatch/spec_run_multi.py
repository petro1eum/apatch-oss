"""Multi-spec run orchestrator (RFP-014 Phase 3 / SPEC-INTERFERENCE-3 R6).

MCP: ``apatch_spec_run_multi`` · CLI: ``apatch spec run-multi``.
See ``docs/AGENTS.template.md`` §3L Phase 3 and ``docs/RFP-014-spec-interference-detection.md``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

ERROR_SPEC_SCHEDULE_BLOCKED = "SPEC_SCHEDULE_BLOCKED"
ERROR_SPEC_CROSS_VERIFY_FAILED = "SPEC_CROSS_VERIFY_FAILED"


def _checkpoint_list(result: Dict[str, Any]) -> List[str]:
    values = result.get("checkpoints") or []
    if isinstance(values, str):
        values = [values]
    out: List[str] = []
    for value in values:
        checkpoint = str(value or "").strip()
        if checkpoint and checkpoint not in out:
            out.append(checkpoint)
    last = str(result.get("last_checkpoint") or result.get("checkpoint") or "").strip()
    if last and last not in out:
        out.append(last)
    return out


def _rollback_checkpoints(root: str, checkpoints: Dict[str, Any]) -> bool:
    from apatch.workflows import rollback_workspace

    ok = True
    for sid in reversed(list(checkpoints)):
        for checkpoint in reversed(
            _checkpoint_list({"checkpoints": checkpoints.get(sid)})
        ):
            rollback = rollback_workspace(root, checkpoint)
            ok = ok and bool(rollback.get("ok", False))
    return ok


def _inline_needles(requirements: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    needles: List[Dict[str, Any]] = []
    for entry in (requirements or {}).values():
        if not isinstance(entry, dict):
            continue
        needles.extend(
            needle
            for needle in (entry.get("needles") or [])
            if isinstance(needle, dict)
        )
    return needles


def _cross_verify_blocks(
    root: str,
    source: str,
    victim: str,
    *,
    source_requirements: Optional[Dict[str, Any]] = None,
) -> tuple[bool, Dict[str, Any]]:
    from apatch.spec_cross_verify import cross_verify_pair

    if source_requirements is not None:
        needles = _inline_needles(source_requirements)
        data_sources = ["inline_requirements"]
    else:
        from apatch.spec import _ledger_entries
        from apatch.spec_interference import load_needles_for_spec

        entries, _ledger_active = _ledger_entries(root)
        needles, data_sources = load_needles_for_spec(
            root,
            source,
            entries,
            include_planned=True,
            include_attested=False,
        )

    if not needles:
        check = {
            "source_spec": source,
            "victim_spec": victim,
            "skipped": True,
            "reason": "no needles for source spec",
            "data_sources": data_sources,
        }
        cv: Dict[str, Any] = {
            "ok": True,
            "specs": [source, victim],
            "pairs_tested": 1,
            "checks": [check],
            "semantic_conflicts": [],
            "all_passed": True,
        }
    else:
        check = cross_verify_pair(root, source, victim, needles)
        check["data_sources"] = data_sources
        cv = {
            "ok": True,
            "specs": [source, victim],
            "pairs_tested": 1,
            "checks": [check],
            "semantic_conflicts": list(check.get("semantic_conflicts") or []),
            "all_passed": bool(check.get("all_passed")),
        }

    blocked = not bool(cv.get("all_passed"))
    if blocked:
        cv = {
            **cv,
            "error_type": ERROR_SPEC_CROSS_VERIFY_FAILED,
            "recommended_action": "refactor_needles",
        }
    return blocked, cv


def _run_spec_to_completion(
    root: str,
    spec_id: str,
    *,
    requirements: Optional[Dict[str, Any]],
    peer_specs: Optional[List[str]],
    allow_partial: bool,
    spec_run_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    from apatch.spec_run import clear_spec_run_state, spec_run_workspace, _spec_run_abs

    kw = dict(spec_run_kwargs)
    kw.setdefault("allow_partial", allow_partial)
    base_logs = kw.pop("logs_path", "patches-spec-run-multi.jsonl")
    if base_logs.endswith(".jsonl"):
        spec_logs = base_logs.replace(".jsonl", f"-{spec_id.lower()}.jsonl")
    else:
        spec_logs = f"{base_logs}-{spec_id.lower()}.jsonl"

    from apatch.apply_session import default_session_path

    clear_spec_run_state(_spec_run_abs(root))
    apply_sess = default_session_path(root)
    if os.path.isfile(apply_sess):
        os.remove(apply_sess)

    run = spec_run_workspace(
        root,
        spec=spec_id,
        requirements=requirements,
        peer_specs=peer_specs,
        interference_check=bool(peer_specs),
        reset=True,
        logs_path=spec_logs,
        **kw,
    )
    applied_total = int(run.get("applied") or 0)
    already_satisfied = list(run.get("already_satisfied_requirements") or [])
    explicitly_skipped = list(run.get("explicitly_skipped_requirements") or [])
    resume_kwargs = dict(spec_run_kwargs)
    resume_kwargs.setdefault("allow_partial", allow_partial)
    while run.get("ok") and run.get("continue"):
        run = spec_run_workspace(
            root,
            spec=spec_id,
            requirements=requirements,
            peer_specs=peer_specs,
            interference_check=bool(peer_specs),
            resume=True,
            **resume_kwargs,
        )
        applied_total += int(run.get("applied") or 0)
        for rid in run.get("already_satisfied_requirements") or []:
            if rid not in already_satisfied:
                already_satisfied.append(rid)
        for rid in run.get("explicitly_skipped_requirements") or []:
            if rid not in explicitly_skipped:
                explicitly_skipped.append(rid)
    run["applied"] = applied_total
    run["already_satisfied_requirements"] = already_satisfied
    run["explicitly_skipped_requirements"] = explicitly_skipped
    return run


def spec_run_multi_workspace(
    target_dir: str = ".",
    *,
    specs: Optional[List[str]] = None,
    requirements: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_verify: bool = False,
    re_interference: bool = True,
    execution_mode: str = "serial",
    maintenance_verify: Optional[str] = None,
    verify_jobs: int = 8,
    verify_timeout: float = 120.0,
    maintenance_chunk_max_files: int = 100,
    **spec_run_kwargs: Any,
) -> Dict[str, Any]:
    """Run N specs in schedule order with optional cross-verify gates (RFP-014 R6).

    Args:
        target_dir: workspace root.
        specs: two or more spec ids (order computed via ``spec_schedule``).
        requirements: optional ``{spec_id: {Rk: {needles: [...]}}}``. An explicitly
            supplied per-spec map is the bounded work list: unrelated open Rk are
            skipped and reported. Omitted specs still use the strict registry manifest.
        cross_verify: Level-3 gate before each next spec (apply predecessor → verify
            victim → rollback workspace).
        re_interference: re-run L1/L2 on remaining specs after each success; passes
            ``peer_specs=remaining`` into nested ``spec_run``.
        **spec_run_kwargs: forwarded to ``spec_run`` (``logs_path``, ``verify_deferred``, …).

    Returns:
        On success: ``ok``, ``completed_specs``, ``order``, ``steps``, ``final_interference``.
        On failure: ``failed_at``, ``error_type`` (``SPEC_SCHEDULE_BLOCKED``,
        ``SPEC_CROSS_VERIFY_FAILED``, or nested ``spec_run`` error), ``recommended_action``,
        ``rolled_back`` when cross-verify fails mid-run.

    Nested ``spec_run`` loops until ``continue=false`` per spec; ``MutationRuntime.close_session``
    between specs.
    """
    from apatch.spec_interference import spec_interference_workspace, spec_schedule_workspace

    root = os.path.abspath(target_dir)
    spec_list = [str(s) for s in (specs or []) if s]
    if len(spec_list) < 2:
        return {
            "ok": False,
            "error": "spec_run_multi requires at least two spec ids",
            "error_type": "SPEC_RUN_BLOCKED",
            "recommended_action": "reduce_scope",
        }

    steps: List[Dict[str, Any]] = []
    completed: List[str] = []
    checkpoints: Dict[str, List[str]] = {}
    applied_total = 0
    already_satisfied_requirements: List[str] = []
    explicitly_skipped_requirements: List[str] = []
    req_map = requirements or {}

    sched = spec_schedule_workspace(
        root,
        specs=spec_list,
        planned_by_spec=req_map or None,
    )
    steps.append({"step": "schedule", "result": sched})
    if not sched.get("ok"):
        return {
            "ok": False,
            "error": sched.get("error") or "schedule failed",
            "steps": steps,
            "schedule": sched,
        }
    if not sched.get("schedulable"):
        return {
            "ok": False,
            "error": "schedule not schedulable",
            "error_type": ERROR_SPEC_SCHEDULE_BLOCKED,
            "recommended_action": "resolve_conflicts",
            "schedule": sched,
            "steps": steps,
        }

    order = list(sched.get("order") or sched.get("safe_order") or [])

    mode = str(execution_mode or "serial").strip().lower()
    if mode == "shared_maintenance":
        if cross_verify:
            return {
                "ok": False,
                "error_type": "SPEC_SHARED_MAINTENANCE_INVALID",
                "error": "shared_maintenance cannot combine with cross_verify in v1",
                "schedule": sched,
                "steps": steps,
            }
        from apatch.shared_maintenance import shared_maintenance_workspace

        shared = shared_maintenance_workspace(
            root,
            specs=order,
            requirements=req_map,
            schedule=sched,
            maintenance_verify=maintenance_verify,
            verify_jobs=verify_jobs,
            verify_timeout=verify_timeout,
            chunk_max_files=maintenance_chunk_max_files,
        )
        shared_step = {key: value for key, value in shared.items() if key != "steps"}
        steps.append({"step": "shared_maintenance", "result": shared_step})
        shared.setdefault("order", order)
        shared.setdefault("steps", steps)
        return shared
    if mode != "serial":
        return {
            "ok": False,
            "error_type": "SPEC_RUN_BLOCKED",
            "error": f"unsupported spec_run_multi execution_mode {execution_mode!r}",
            "schedule": sched,
            "steps": steps,
        }

    for idx, sid in enumerate(order):
        remaining = [s for s in order[idx + 1 :]]

        spec_reqs = req_map.get(sid)
        if spec_reqs is None:
            from apatch.spec_registry import load_spec_registry

            reg = load_spec_registry(root, sid)
            if reg:
                spec_reqs = reg.get("requirements")

        # Cross-verify the exact source work list while its old anchors still
        # exist. Test every later spec so an empty intermediate work list cannot
        # hide a semantic conflict. Each sandbox restores before governed apply.
        if cross_verify and remaining:
            for victim in remaining:
                blocked, cv = _cross_verify_blocks(
                    root,
                    sid,
                    victim,
                    source_requirements=spec_reqs,
                )
                steps.append(
                    {"step": "cross_verify", "pair": [sid, victim], "result": cv}
                )
                if blocked:
                    rolled = _rollback_checkpoints(root, checkpoints)
                    return {
                        "ok": False,
                        "error": "cross-verify failed before applying source spec",
                        "error_type": cv.get("error_type") or ERROR_SPEC_CROSS_VERIFY_FAILED,
                        "recommended_action": cv.get("recommended_action") or "refactor_needles",
                        "failed_at": victim,
                        "completed_specs": list(completed),
                        "steps": steps,
                        "rolled_back": rolled,
                        "workspace_restored": rolled,
                        "cross_verify": cv,
                    }

        run = _run_spec_to_completion(
            root,
            sid,
            requirements=spec_reqs,
            peer_specs=remaining if re_interference else None,
            allow_partial=sid in req_map,
            spec_run_kwargs=spec_run_kwargs,
        )
        steps.append({"step": "spec_run", "spec": sid, "result": run})
        if not run.get("ok"):
            completed_rolled_back = _rollback_checkpoints(root, checkpoints)
            chunk_result = run.get("chunk_result") or {}
            nested_rollback_done = bool(
                run.get("rollback_performed")
                or chunk_result.get("rollback_performed")
            )
            failing_checkpoints = _checkpoint_list(run)
            if nested_rollback_done:
                already_rolled = str(
                    run.get("checkpoint") or run.get("last_checkpoint") or ""
                )
                if already_rolled in failing_checkpoints:
                    failing_checkpoints.remove(already_rolled)
            prior_failing_rolled_back = _rollback_checkpoints(
                root,
                {sid: failing_checkpoints},
            )
            had_apply = any(
                "apply_session" in str(step)
                for step in (run.get("steps_completed") or [])
            )
            failing_rolled_back = (
                prior_failing_rolled_back
                and (nested_rollback_done or bool(failing_checkpoints) or not had_apply)
            )
            passthrough_fields = (
                "recoverable",
                "verify_rollback",
                "rollback_performed",
                "checkpoint",
                "last_checkpoint",
                "checkpoints",
                "chunk_result",
                "diagnostics",
                "diagnostic_count",
                "diagnostics_artifact",
                "verify_output",
                "agent_next",
                "resume_hint",
                "rollback_hint",
                "baseline",
            )
            return {
                "ok": False,
                "failed_at": sid,
                "completed_specs": list(completed),
                "steps": steps,
                "error": run.get("error"),
                "error_type": run.get("error_type"),
                "recommended_action": run.get("recommended_action"),
                "completed_specs_rolled_back": completed_rolled_back,
                "failing_spec_rolled_back": failing_rolled_back,
                **{key: run[key] for key in passthrough_fields if key in run},
            }

        applied_total += int(run.get("applied") or 0)
        for rid in run.get("already_satisfied_requirements") or []:
            token = str(rid) if "#" in str(rid) else f"{sid}#{rid}"
            if token not in already_satisfied_requirements:
                already_satisfied_requirements.append(token)
        for rid in run.get("explicitly_skipped_requirements") or []:
            token = str(rid) if "#" in str(rid) else f"{sid}#{rid}"
            if token not in explicitly_skipped_requirements:
                explicitly_skipped_requirements.append(token)
        run_checkpoints = _checkpoint_list(run)
        had_apply = any(
            "apply_session" in str(step)
            for step in (run.get("steps_completed") or [])
        )
        if had_apply and not run_checkpoints:
            return {
                "ok": False,
                "error": "successful spec_run did not expose a rollback checkpoint",
                "error_type": "SPEC_RUN_BLOCKED",
                "recommended_action": "retry_chunk",
                "failed_at": sid,
                "completed_specs": list(completed),
                "steps": steps,
                "workspace_restored": False,
                "checkpoint_missing_for": sid,
            }

        completed.append(sid)
        if run_checkpoints:
            checkpoints[sid] = run_checkpoints

        from apatch.runtime.runtime import MutationRuntime

        MutationRuntime(root).close_session()

        if re_interference and remaining:
            ich = spec_interference_workspace(root, specs=[sid] + remaining, level=2)
            steps.append({"step": "re_interference", "specs": [sid] + remaining, "result": ich})

    final_ic = spec_interference_workspace(root, specs=spec_list, level=2) if len(spec_list) >= 2 else None

    return {
        "ok": True,
        "completed_specs": completed,
        "order": order,
        "steps": steps,
        "final_interference": final_ic,
        "applied": applied_total,
        "already_satisfied_requirements": already_satisfied_requirements,
        "explicitly_skipped_requirements": explicitly_skipped_requirements,
        "continue": False,
        "agent_next": "All specs in multi-run completed.",
    }


def spec_run_multi_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root_dir = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_run_multi",
        spec_run_multi_workspace(root_dir, **kwargs),
        target_dir=root_dir,
    )
