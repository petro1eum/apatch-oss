"""One-screen slug cockpit over intake, feedback replay, and conformance state.

This module intentionally orchestrates existing read-only surfaces instead of
inventing another diagnostics model. It answers the operator question:
"what is not green for this slug, why, and what should I do next?"
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from apatch_search_workflows.feedback_status import (
    CLOSED_FEEDBACK_STATUSES,
    OWNER_TERMINAL_STATUSES,
    canonical_status,
)
from apatch_search_workflows.repair_map import load_repair_map, route
from apatch_search_workflows.slug_close import DEFAULT_API_URL, ApiFunc, slug_close_workspace
from apatch_search_workflows.slug_feedback_lint import feedback_lint_workspace
from apatch_search_workflows.slug_intake import slug_intake_workspace


def slug_cockpit_workspace(
    target_dir: str = ".",
    *,
    slug: str,
    aliases: Optional[Sequence[str]] = None,
    manifest_path: Optional[str] = None,
    live: bool = False,
    evidence_limit: int = 25,
    feedback_limit: int = 0,
    api_url: str = DEFAULT_API_URL,
    triage_path: Optional[str] = None,
    approved_path: Optional[str] = None,
    size: int = 5,
    timeout: float = 30.0,
    api_func: Optional[ApiFunc] = None,
) -> Dict[str, Any]:
    """Build a compact read-only cockpit for slug work.

    ``live`` is deliberately opt-in: it may run scoped conformance verifies via
    ``slug_intake``. Feedback replay always uses the live search API because the
    whole point is query-first closure of real customer strings.
    """
    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    intake = slug_intake_workspace(
        root,
        slug=slug,
        aliases=aliases or (),
        manifest_path=manifest_path,
        live=live,
        limit=evidence_limit,
    )
    close = slug_close_workspace(
        root,
        slug=slug,
        api_url=api_url,
        triage_path=triage_path,
        approved_path=approved_path,
        size=size,
        timeout=timeout,
        limit=feedback_limit,
        include_rows=False,
        api_func=api_func,
    )

    if not intake.get("ok"):
        return {
            "ok": False,
            "workspace": root,
            "slug": slug,
            "error": intake.get("error"),
            "error_type": intake.get("error_type") or "INTAKE_FAILED",
            "intake": intake,
            "feedback": _compact_close(close),
            "agent_next": "Fix slug intake first; cockpit cannot classify the slug without spec/data context.",
        }

    compact_close = _compact_close(close)
    operational = _operational_status(
        root,
        slug=str(intake.get("slug") or slug),
        intake=intake,
        live=live,
        timeout=timeout,
    )
    lint = _compact_lint(
        _safe_feedback_lint(
            root,
            slug=slug,
            triage_path=triage_path,
            approved_path=approved_path,
        )
    )
    repair = load_repair_map(root)
    repair_info = _attach_repair_routes(compact_close, repair)
    diagnosis = _diagnosis(intake, compact_close, operational)
    action_items = _action_items(
        intake,
        compact_close,
        diagnosis,
        lint,
        repair,
        repair_info,
        operational,
    )
    health = _health(intake, compact_close, action_items, operational)

    return {
        "ok": True,
        "workspace": root,
        "slug": intake.get("slug") or slug,
        "aliases": intake.get("aliases") or [],
        "live": bool(live),
        "health": health,
        "summary": _summary(intake, compact_close, diagnosis, lint, operational),
        "action_items": action_items,
        "diagnosis": diagnosis,
        "operational": operational,
        "specs": _compact_specs(intake),
        "required_gates": intake.get("required_gates") or {},
        "conformance": intake.get("conformance") or {},
        "feedback": compact_close,
        "feedback_lint": lint,
        "repair_map": {
            "present": repair.get("present"),
            "path": repair.get("path"),
            "rules": len(repair.get("rules") or []),
            "errors": repair.get("errors") or [],
            "matched": repair_info.get("matched"),
            "unmatched_causes": repair_info.get("unmatched"),
        },
        "gaps": intake.get("gaps") or [],
        "next_commands": _next_commands(intake, compact_close, lint, slug=str(slug), live=live),
        "agent_next": _agent_next(health, action_items, compact_close, operational),
    }


def slug_cockpit_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    return enrich_tool_response(
        "apatch_slug_cockpit",
        slug_cockpit_workspace(root, **kwargs),
        target_dir=root,
    )


def _operational_status(
    root: str,
    *,
    slug: str,
    intake: Mapping[str, Any],
    live: bool,
    timeout: float,
) -> Dict[str, Any]:
    """Run the consumer's explicit operational-status hook and normalize its DTO.

    Exit 1 is accepted when the JSON is valid: domain evidence debt may make the
    producer red while runtime work is still complete. Exit >=2 means the hook
    itself is broken. The cockpit never infers completion from labels or SPEC
    enrollment; the consumer must return the standard fields below.
    """
    commands = ((intake.get("hooks") or {}).get("commands") or {})
    command = str(commands.get("operational_status") or "").strip()
    base = {
        "configured": bool(command),
        "executed": False,
        "ok": True,
        "command": command or None,
        "work_status": "not_configured" if not command else "not_run",
        "runtime_work_complete": None,
        "runtime_reopen_required": False,
        "reopen_reasons": [],
        "runtime_defect_codes": [],
        "evidence_debt_codes": [],
        "verification_key": None,
        "evidence_only": False,
    }
    if not command:
        return base
    if not live:
        base["note"] = "Run slug cockpit with live=true to execute operational_status."
        return base

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout or 30.0)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            **base,
            "executed": True,
            "ok": False,
            "work_status": "hook_broken",
            "error_type": "OPERATIONAL_STATUS_TIMEOUT",
            "error": f"operational_status exceeded {max(1.0, float(timeout or 30.0)):g}s",
        }
    except OSError as exc:
        return {
            **base,
            "executed": True,
            "ok": False,
            "work_status": "hook_broken",
            "error_type": "OPERATIONAL_STATUS_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
        }

    executed = {**base, "executed": True, "exit_code": int(proc.returncode)}
    if proc.returncode not in {0, 1}:
        return {
            **executed,
            "ok": False,
            "work_status": "hook_broken",
            "error_type": "OPERATIONAL_STATUS_FAILED",
            "error": f"operational_status exited {proc.returncode}",
            "stderr_tail": (proc.stderr or "")[-2000:],
        }
    try:
        payload = json.loads(proc.stdout or "")
    except (TypeError, json.JSONDecodeError) as exc:
        return {
            **executed,
            "ok": False,
            "work_status": "hook_broken",
            "error_type": "OPERATIONAL_STATUS_INVALID_JSON",
            "error": str(exc),
            "stdout_tail": (proc.stdout or "")[-2000:],
        }
    row = _operational_row(payload, slug)
    if row is None or not isinstance(row.get("runtime_work_complete"), bool):
        return {
            **executed,
            "ok": False,
            "work_status": "hook_broken",
            "error_type": "OPERATIONAL_STATUS_INVALID_SCHEMA",
            "error": "Expected runtime_work_complete boolean in a direct object or matching results/rows item.",
        }

    def _strings(key: str) -> List[str]:
        value = row.get(key) or []
        if not isinstance(value, list):
            return [str(value)]
        return [str(item) for item in value if str(item)]

    complete = row["runtime_work_complete"] is True
    work_status = str(row.get("work_status") or ("runtime_verified" if complete else "not_verified"))
    reopen_reasons = _strings("reopen_reasons")
    runtime_defects = _strings("runtime_defect_codes")
    evidence_debt = _strings("evidence_debt_codes")
    reopen_required = bool(
        reopen_reasons
        or runtime_defects
        or work_status in {"needs_runtime_work", "verification_incomplete"}
    )
    return {
        **executed,
        "ok": True,
        "work_status": work_status,
        "runtime_work_complete": complete,
        "runtime_reopen_required": reopen_required,
        "reopen_reasons": reopen_reasons,
        "runtime_defect_codes": runtime_defects,
        "evidence_debt_codes": evidence_debt,
        "verification_key": row.get("verification_key"),
        "evidence_only": complete and bool(evidence_debt) and not reopen_required,
        "advisory_exit": proc.returncode == 1,
    }


def _operational_row(payload: Any, slug: str) -> Optional[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return None
    if "runtime_work_complete" in payload:
        return payload
    for key in ("results", "rows"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        matches = [
            row
            for row in rows
            if isinstance(row, Mapping) and str(row.get("slug") or "") == slug
        ]
        if matches:
            return matches[0]
        if len(rows) == 1 and isinstance(rows[0], Mapping):
            return rows[0]
    return None


def _compact_specs(intake: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for spec in intake.get("specs") or []:
        summary = spec.get("summary") or {}
        pending = [
            req.get("id")
            for req in spec.get("requirements") or []
            if req.get("state") in {"pending", "in_progress", "stale"}
        ]
        out.append(
            {
                "id": spec.get("id"),
                "done": bool(spec.get("done")),
                "attested": summary.get("attested"),
                "total": summary.get("total"),
                "stale": summary.get("stale"),
                "pending": pending,
                "source_path": spec.get("source_path"),
            }
        )
    return out


def _compact_close(close: Mapping[str, Any]) -> Dict[str, Any]:
    if not close.get("ok"):
        return {
            "ok": False,
            "error": close.get("error"),
            "error_type": close.get("error_type"),
            "rows_scanned": 0,
            "summary": {},
            "non_fixed": [],
            "approved_conflicts": [],
            "proposed_needles": [],
            "agent_next": close.get("agent_next"),
        }
    open_feedback: List[Dict[str, Any]] = []
    classified_non_fixed: List[Dict[str, Any]] = []
    root_causes: Counter[str] = Counter()
    graph_issues: Counter[str] = Counter()
    for row in close.get("suggestions") or []:
        root_cause = str(row.get("root_cause") or "unknown")
        root_causes[root_cause] += 1
        live = row.get("live") or {}
        graph_issue = str(live.get("graph_primary_issue") or "")
        if graph_issue:
            graph_issues[graph_issue] += 1
        current_status = str(row.get("current_status") or "")
        suggested_status = str(row.get("suggested_status") or "")
        if suggested_status == "fixed":
            continue
        record = {
            "triage_id": row.get("triage_id"),
            "query": row.get("query"),
            "current_status": current_status,
            "suggested_status": suggested_status,
            "suggested_slug": row.get("suggested_slug"),
            "suggested_jde": row.get("suggested_jde"),
            "root_cause": root_cause,
            "top_jde": live.get("top_jde"),
            "top_name": live.get("top_name"),
            "graph_primary_issue": live.get("graph_primary_issue"),
            "evidence": (row.get("evidence") or [])[:6],
        }
        if canonical_status(current_status) in OWNER_TERMINAL_STATUSES:
            record["owner_terminal_status"] = True
            classified_non_fixed.append(record)
        elif suggested_status in CLOSED_FEEDBACK_STATUSES:
            classified_non_fixed.append(record)
        else:
            open_feedback.append(record)
    return {
        "ok": True,
        "api_url": close.get("api_url"),
        "triage_path": close.get("triage_path"),
        "approved_path": close.get("approved_path"),
        "rows_scanned": close.get("rows_scanned"),
        "summary": close.get("summary") or {},
        "non_fixed": open_feedback,
        "open_feedback": open_feedback,
        "classified_non_fixed": classified_non_fixed,
        "root_cause_counts": dict(sorted(root_causes.items())),
        "graph_issue_counts": dict(sorted(graph_issues.items())),
        "approved_conflicts": close.get("approved_conflicts") or [],
        "proposed_needles": close.get("proposed_needles") or [],
        "approved_needles": close.get("approved_needles") or [],
        "agent_next": close.get("agent_next"),
    }


def _safe_feedback_lint(
    root: str,
    *,
    slug: str,
    triage_path: Optional[str],
    approved_path: Optional[str],
) -> Dict[str, Any]:
    """Vocabulary lint must never break the cockpit; failures become a report."""
    try:
        return feedback_lint_workspace(
            root,
            slug=slug,
            triage_path=triage_path,
            approved_path=approved_path,
        )
    except Exception as exc:  # noqa: BLE001 - cockpit stays read-only and robust
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "error_type": "FEEDBACK_LINT_FAILED"}


def _compact_lint(lint: Mapping[str, Any]) -> Dict[str, Any]:
    if not lint.get("ok"):
        return {
            "ok": False,
            "clean": None,
            "error": lint.get("error"),
            "error_type": lint.get("error_type"),
            "summary": {},
            "findings": [],
        }
    return {
        "ok": True,
        "clean": bool(lint.get("clean")),
        "summary": lint.get("summary") or {},
        "findings": (lint.get("findings") or [])[:12],
        "proposed_needles": len(lint.get("proposed_needles") or []),
    }


def _diagnosis(
    intake: Mapping[str, Any],
    close: Mapping[str, Any],
    operational: Mapping[str, Any],
) -> Dict[str, Any]:
    gates = intake.get("required_gates") or {}
    query_first = gates.get("query_first") or {}
    data_model = gates.get("data_model") or {}
    conformance = intake.get("conformance") or {}
    buckets = conformance.get("buckets") or {}
    return {
        "query_first_present": bool(query_first.get("present")),
        "data_model_present": bool(data_model.get("present")),
        "reality_uncovered": len(((gates.get("reality_coverage") or {}).get("uncovered") or [])),
        "reality_pending": len(((gates.get("reality_coverage") or {}).get("pending") or [])),
        "conformance_buckets": buckets,
        "feedback_status_counts": (close.get("summary") or {}).get("suggested_status_counts") or {},
        "feedback_root_cause_counts": close.get("root_cause_counts") or {},
        "graph_issue_counts": close.get("graph_issue_counts") or {},
        "approved_conflicts": len(close.get("approved_conflicts") or []),
        "open_feedback": len(close.get("open_feedback") or []),
        "classified_non_fixed": len(close.get("classified_non_fixed") or []),
        "non_fixed_feedback": len(close.get("open_feedback") or []),
        "work_status": operational.get("work_status"),
        "runtime_work_complete": operational.get("runtime_work_complete"),
        "runtime_reopen_required": operational.get("runtime_reopen_required"),
        "evidence_debt": len(operational.get("evidence_debt_codes") or []),
    }


def _summary(
    intake: Mapping[str, Any],
    close: Mapping[str, Any],
    diagnosis: Mapping[str, Any],
    lint: Mapping[str, Any],
    operational: Mapping[str, Any],
) -> Dict[str, Any]:
    specs = _compact_specs(intake)
    return {
        "specs": len(specs),
        "specs_done": sum(1 for spec in specs if spec.get("done")),
        "gaps": len(intake.get("gaps") or []),
        "feedback_rows": close.get("rows_scanned") or 0,
        "open_feedback": diagnosis.get("open_feedback") or 0,
        "classified_non_fixed": diagnosis.get("classified_non_fixed") or 0,
        "non_fixed_feedback": diagnosis.get("non_fixed_feedback") or 0,
        "approved_conflicts": diagnosis.get("approved_conflicts") or 0,
        "vocabulary_clean": lint.get("clean"),
        "query_first_present": diagnosis.get("query_first_present"),
        "data_model_present": diagnosis.get("data_model_present"),
        "work_status": operational.get("work_status"),
        "runtime_work_complete": operational.get("runtime_work_complete"),
        "runtime_reopen_required": operational.get("runtime_reopen_required"),
        "evidence_debt": len(operational.get("evidence_debt_codes") or []),
    }


def _attach_repair_routes(close: Mapping[str, Any], repair: Mapping[str, Any]) -> Dict[str, Any]:
    """Attach 'where does this fix go' pointers to open records; report gaps."""
    if not repair.get("present") or repair.get("errors") or not repair.get("rules"):
        return {"matched": 0, "unmatched": []}
    matched = 0
    unmatched: List[str] = []
    for record in list(close.get("open_feedback") or []) + list(close.get("approved_conflicts") or []):
        keys = {
            "root_cause": record.get("root_cause"),
            "graph_primary_issue": record.get("graph_primary_issue"),
        }
        matches = route(repair, keys)
        if matches:
            record["repair"] = matches[0]
            if len(matches) > 1:
                record["repair_alternatives"] = [m["rule_id"] for m in matches[1:]]
            matched += 1
        else:
            cause = str(record.get("root_cause") or record.get("graph_primary_issue") or "unknown")
            if cause not in unmatched:
                unmatched.append(cause)
    return {"matched": matched, "unmatched": unmatched}


def _repair_action(record: Mapping[str, Any], default: str) -> str:
    repair = record.get("repair")
    if not isinstance(repair, Mapping):
        return default
    surfaces = ", ".join(repair.get("edit_surfaces") or [])
    return f"[{repair.get('rule_id')}] {repair.get('action')} (surfaces: {surfaces})"


def _action_items(
    intake: Mapping[str, Any],
    close: Mapping[str, Any],
    diagnosis: Mapping[str, Any],
    lint: Mapping[str, Any],
    repair: Mapping[str, Any],
    repair_info: Mapping[str, Any],
    operational: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if operational.get("configured") and operational.get("executed"):
        if not operational.get("ok"):
            items.append(
                {
                    "severity": "medium",
                    "id": "operational_status_unavailable",
                    "message": operational.get("error") or "Operational status hook failed.",
                    "recommended_action": "Fix the hook or its JSON contract; do not infer that runtime search is broken.",
                }
            )
        elif operational.get("runtime_reopen_required"):
            items.append(
                {
                    "severity": "high",
                    "id": "runtime_reopen_required",
                    "message": "The operational audit found a runtime defect or incomplete runtime coverage.",
                    "reopen_reasons": operational.get("reopen_reasons") or [],
                    "runtime_defect_codes": operational.get("runtime_defect_codes") or [],
                    "recommended_action": "Reopen only the reported runtime defect; preserve already verified contract behavior.",
                }
            )
        elif operational.get("runtime_work_complete") and operational.get("evidence_debt_codes"):
            items.append(
                {
                    "severity": "low",
                    "id": "evidence_debt_only",
                    "message": "Runtime work is complete; only evidence/proof debt remains.",
                    "evidence_debt_codes": operational.get("evidence_debt_codes") or [],
                    "recommended_action": "Improve evidence separately; do not rebuild or reopen the category runtime.",
                }
            )
        elif operational.get("runtime_work_complete") is False:
            items.append(
                {
                    "severity": "medium",
                    "id": "runtime_verification_incomplete",
                    "message": "Operational completion has not been proven.",
                    "recommended_action": "Complete the missing live verification before deciding whether code changes are needed.",
                }
            )
    feedback_rows = int(close.get("rows_scanned") or 0)
    if repair.get("errors"):
        items.append(
            {
                "severity": "medium",
                "id": "repair_map_invalid",
                "message": f"Repair map {repair.get('path')} is invalid: {'; '.join(repair['errors'][:3])}",
                "recommended_action": "Fix the repair map through review; routing is disabled until it is valid.",
            }
        )
    for cause in repair_info.get("unmatched") or []:
        items.append(
            {
                "severity": "medium",
                "id": "repair_map_gap",
                "message": f"No repair route for diagnosis '{cause}'.",
                "recommended_action": (
                    "Extend the repair map through review or file a platform RFC; "
                    "do not improvise a new file/function for this failure class."
                ),
            }
        )
    for finding in lint.get("findings") or []:
        severity = str(finding.get("severity") or "medium")
        if severity == "low":
            continue
        items.append(
            {
                "severity": severity,
                "id": f"vocabulary_{finding.get('id')}",
                "message": finding.get("message"),
                "recommended_action": finding.get("recommended_action")
                or "Run apatch slug feedback-lint and apply the normalization needles via the governed path.",
            }
        )
    if lint.get("ok") is False:
        items.append(
            {
                "severity": "medium",
                "id": "feedback_lint_unavailable",
                "message": f"Feedback vocabulary lint failed: {lint.get('error')}",
                "recommended_action": "Fix triage path or lint failure; vocabulary state is unknown.",
            }
        )
    for conflict in close.get("approved_conflicts") or []:
        items.append(
            {
                "severity": "high",
                "id": "approved_conflict",
                "message": "Approved feedback contract conflicts with current live replay.",
                "query": conflict.get("query"),
                "root_cause": conflict.get("root_cause"),
                "recommended_action": _repair_action(
                    conflict,
                    "Fix runtime or correct the approved contract before attesting feedback closure.",
                ),
            }
        )
    for row in close.get("open_feedback") or []:
        severity = "high" if row.get("suggested_status") == "open_runtime_bug" else "medium"
        items.append(
            {
                "severity": severity,
                "id": "feedback_not_fixed",
                "message": f"Live feedback row is {row.get('suggested_status')} ({row.get('root_cause')}).",
                "triage_id": row.get("triage_id"),
                "query": row.get("query"),
                "top_jde": row.get("top_jde"),
                "top_name": row.get("top_name"),
                "recommended_action": _repair_action(
                    row,
                    "Use the graph/evidence for this query, then update the owning SPEC and live gate.",
                ),
            }
        )
    for gap in intake.get("gaps") or []:
        if gap.get("id") == "missing_reality" and feedback_rows > 0:
            continue
        items.append(
            {
                "severity": gap.get("severity") or "medium",
                "id": gap.get("id"),
                "message": gap.get("message"),
                "recommended_action": gap.get("recommended_action"),
            }
        )
    conformance = intake.get("conformance") or {}
    for row in conformance.get("per_spec") or []:
        bucket = row.get("conformance")
        if bucket == "stale":
            items.append(
                {
                    "severity": "medium",
                    "id": "conformance_stale",
                    "message": f"{row.get('spec')} is green-but-stale; rebind attestation.",
                    "spec": row.get("spec"),
                    "recommended_action": row.get("recommended_cli")
                    or f"apatch spec rebind-stale --spec {row.get('spec')} --target-dir .",
                }
            )
        elif bucket in {"drifted", "broken", "unproven", "in_progress"}:
            items.append(
                {
                    "severity": "high" if bucket == "drifted" else "medium",
                    "id": f"conformance_{bucket}",
                    "message": f"{row.get('spec')} conformance is {bucket}.",
                    "spec": row.get("spec"),
                    "recommended_action": row.get("recommended_action") or "Run scoped conformance/status for this spec.",
                }
            )
    return sorted(items, key=_action_sort_key)


def _action_sort_key(item: Mapping[str, Any]) -> tuple[int, str]:
    severity_order = {"high": 0, "medium": 1, "low": 2}
    return (severity_order.get(str(item.get("severity") or ""), 3), str(item.get("id") or ""))


def _health(
    intake: Mapping[str, Any],
    close: Mapping[str, Any],
    action_items: Sequence[Mapping[str, Any]],
    operational: Mapping[str, Any],
) -> Dict[str, Any]:
    high = [item for item in action_items if item.get("severity") == "high"]
    medium = [item for item in action_items if item.get("severity") == "medium"]
    close_ok = bool(close.get("ok"))
    runtime_reopen = bool(operational.get("runtime_reopen_required"))
    runtime_complete = (
        operational.get("runtime_work_complete") is True and not runtime_reopen
    )
    close_blocks = not close_ok and not runtime_complete
    if high or close_blocks:
        state = "red"
    elif medium or not close_ok:
        state = "yellow"
    else:
        state = "green"
    reasons = [str(item.get("id")) for item in action_items[:8] if item.get("id")]
    if not close_ok:
        reasons.insert(0, str(close.get("error_type") or "feedback_replay_failed"))
    return {
        "state": state,
        "runtime_state": (
            "reopen_required"
            if runtime_reopen
            else "complete"
            if runtime_complete
            else "unknown"
        ),
        "high": len(high),
        "medium": len(medium),
        "reasons": reasons,
    }


def _next_commands(
    intake: Mapping[str, Any],
    close: Mapping[str, Any],
    lint: Mapping[str, Any],
    *,
    slug: str,
    live: bool,
) -> List[str]:
    commands = [
        f"apatch slug cockpit {slug} --target-dir . --json",
        f"apatch slug close {slug} --target-dir . --compact --json",
    ]
    if lint.get("clean") is False:
        commands.append(f"apatch slug feedback-lint {slug} --target-dir . --json")
    if not live:
        commands.append(f"apatch slug intake {slug} --target-dir . --live --json")
    for row in (intake.get("conformance") or {}).get("per_spec") or []:
        if row.get("conformance") == "stale":
            commands.append(row.get("recommended_cli") or f"apatch spec rebind-stale --spec {row.get('spec')} --target-dir .")
    if close.get("proposed_needles"):
        commands.append("Apply proposed_needles through apatch_remote_task_run/apatch_generate_batch after review.")
    return commands


def _agent_next(
    health: Mapping[str, Any],
    action_items: Sequence[Mapping[str, Any]],
    close: Mapping[str, Any],
    operational: Mapping[str, Any],
) -> str:
    fresh_runtime_ids = {"approved_conflict", "feedback_not_fixed", "runtime_reopen_required"}
    fresh_runtime_issue = any(item.get("id") in fresh_runtime_ids for item in action_items)
    if (
        operational.get("runtime_work_complete") is True
        and not operational.get("runtime_reopen_required")
        and not fresh_runtime_issue
    ):
        debt = len(operational.get("evidence_debt_codes") or [])
        suffix = f" Address the {debt} evidence item(s) separately." if debt else ""
        return "Runtime work is already complete; do not rebuild or reopen this slug." + suffix
    if health.get("state") == "green":
        return "Slug cockpit is green; run scoped live conformance before commit if this slug was changed."
    if action_items:
        first = action_items[0]
        return f"Start with {first.get('id')}: {first.get('recommended_action') or first.get('message')}"
    if not close.get("ok"):
        return "Feedback replay is unavailable; fix triage path/API before marking the slug green."
    return "Review cockpit diagnostics, then update the owning SPEC and rerun cockpit."
