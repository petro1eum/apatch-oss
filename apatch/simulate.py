"""R58 — preflight simulation (no disk writes)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from apatch.apply_session import MASS_APPLY_GUARD, plan_chunks_for_logs
from apatch.change_budget import BUDGET_PRESETS, check_budget, estimate_from_candidates, resolve_budget
from apatch.ingestor import LogIngestor
from apatch.workflows import WorkflowError, filter_patch_candidates, plan_from_logs

_DB_MODEL_HINTS = (
    "models.py",
    "model.py",
    "schema.prisma",
    "models/",
    "/migrations/",
    "alembic/",
)

from apatch.graph_execute import DEFAULT_GRAPH, build_execution_graph

_EXACT_STRATEGIES = frozenset({"exact", "exact-all", "create", "delete"})
_HINT_MIN_CONFIDENCE = 0.85
_MAX_HINT_TEXT = 240


def _rel_target_path(target_dir: str, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if os.path.isabs(path):
        try:
            return os.path.relpath(path, target_dir)
        except ValueError:
            return path
    return path


def _truncate_hint_text(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _MAX_HINT_TEXT:
        return text
    return text[:_MAX_HINT_TEXT] + "…"


def needles_hints_from_plan(
    plan: Dict[str, Any],
    candidates: List[Any],
    target_dir: str,
    *,
    min_confidence: float = _HINT_MIN_CONFIDENCE,
) -> List[Dict[str, Any]]:
    """RFP-022 needles_hint v2 — partial mutation dicts for simulate drift (advisory)."""
    by_step = {c.step_index: c for c in candidates}
    hints: List[Dict[str, Any]] = []

    for entry in plan.get("entries") or []:
        step = entry.get("step_index")
        cand = by_step.get(step)
        if not cand:
            continue

        strategy = str(entry.get("strategy") or "")
        confidence = float(entry.get("confidence") or 0.0)
        would_apply = bool(entry.get("would_apply"))
        rel = _rel_target_path(target_dir, entry.get("resolved_path") or entry.get("target_file"))

        if would_apply and strategy in _EXACT_STRATEGIES:
            continue

        if would_apply and confidence >= min_confidence:
            hints.append(
                {
                    "step_index": step,
                    "target_file": rel,
                    "drift_kind": "recovered",
                    "strategy": strategy,
                    "confidence": round(confidence, 2),
                    "needles_hint": {
                        "action": "replace",
                        "target_file": rel,
                        "find_text": _truncate_hint_text(cand.old_content),
                        "replace_text": _truncate_hint_text(cand.new_content),
                        "partial": True,
                        "advisory": True,
                    },
                    "note": (
                        "Fuzzy recovery during simulate — re-read source and use literal "
                        "find_text before apply_session"
                    ),
                }
            )
        elif not would_apply and entry.get("resolved_path"):
            partial: Dict[str, Any] = {
                "action": "replace",
                "target_file": rel,
                "partial": True,
                "advisory": True,
            }
            if cand.new_content:
                partial["replace_text"] = _truncate_hint_text(cand.new_content)
            hints.append(
                {
                    "step_index": step,
                    "target_file": rel,
                    "drift_kind": "unresolved",
                    "strategy": strategy or "no-match",
                    "confidence": round(confidence, 2),
                    "needles_hint": partial,
                    "note": "Anchor not found — read live file and author find_text from source",
                }
            )

    return hints


def _load_manifest(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise WorkflowError("simulate: manifest must be a JSON object")
    return data


def _db_model_files(paths: List[str]) -> List[str]:
    out: List[str] = []
    for p in paths:
        norm = p.replace("\\", "/").lower()
        if any(h in norm for h in _DB_MODEL_HINTS):
            out.append(p)
    return out


def _estimate_rollback_probability(
    plan: Dict[str, Any],
    estimate: Dict[str, Any],
    *,
    budget_result: Optional[Dict[str, Any]] = None,
) -> float:
    entries = plan.get("entries") or []
    risk = 0.05
    would = sum(1 for e in entries if e.get("would_apply"))
    total = plan.get("total") or len(entries) or 1
    unresolved = sum(1 for e in entries if not e.get("resolved_path"))
    low_conf = sum(
        1
        for e in entries
        if e.get("would_apply") and float(e.get("confidence") or 1.0) < 0.85
    )
    not_apply = sum(
        1 for e in entries if e.get("resolved_path") and not e.get("would_apply")
    )

    if estimate.get("files", 0) > 50:
        risk += 0.18
    elif estimate.get("files", 0) > MASS_APPLY_GUARD:
        risk += 0.10

    risk += min(0.25, not_apply * 0.04)
    risk += min(0.20, low_conf * 0.06)
    risk += min(0.15, unresolved * 0.08)

    churn = int(estimate.get("insertions", 0)) + int(estimate.get("deletions", 0))
    if churn > 2000:
        risk += 0.15
    elif churn > 500:
        risk += 0.08

    if budget_result and not budget_result.get("ok"):
        risk += 0.12

    if would == 0 and total > 0:
        risk = max(risk, 0.75)

    return round(min(0.95, risk), 2)


def _graph_from_manifest(manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    graph = build_execution_graph(manifest)
    return {"nodes": graph["nodes"], "edges": graph["edges"], "order": graph.get("order")}


def simulate_from_logs(
    logs_path: str,
    target_dir: str = ".",
    *,
    chunk_max_files: int = 5,
    replace_all: bool = False,
    only_drifted: bool = False,
    min_confidence: Optional[float] = None,
    tool: Optional[str] = None,
    keyword: Optional[str] = None,
    budget: Optional[str] = None,
    max_files: Optional[int] = None,
    max_insertions: Optional[int] = None,
    max_deletions: Optional[int] = None,
    rules_path: Optional[str] = None,
    db_profile: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dry-run risk map for a patches JSONL (no writes)."""
    target_dir = os.path.abspath(target_dir)
    if not os.path.isfile(logs_path):
        raise WorkflowError(f"simulate: logs not found: {logs_path}")

    plan = plan_from_logs(
        logs_path,
        target_dir,
        tool=tool,
        keyword=keyword,
    )
    ingestor = LogIngestor(logs_path)
    candidates = filter_patch_candidates(
        ingestor.parse(),
        tool=tool,
        keyword=keyword,
    )
    estimate = estimate_from_candidates(
        candidates,
        target_dir,
        replace_all=replace_all,
        min_confidence=min_confidence,
        only_drifted=only_drifted,
    )

    layout = plan_chunks_for_logs(
        logs_path,
        target_dir,
        chunk_max_files=chunk_max_files,
        tool=tool,
        keyword=keyword,
        only_drifted=only_drifted,
        min_confidence=min_confidence,
    )

    change_budget = resolve_budget(
        budget,
        max_files=max_files,
        max_insertions=max_insertions,
        max_deletions=max_deletions,
    )
    budget_result = None
    if change_budget:
        budget_result = check_budget(estimate, change_budget)

    entries = plan.get("entries") or []
    low_conf = [
        e.get("resolved_path") or e.get("target_file")
        for e in entries
        if e.get("would_apply") and float(e.get("confidence") or 1.0) < 0.85
    ]
    unresolved = [
        e.get("target_file")
        for e in entries
        if not e.get("resolved_path")
    ]
    file_paths = estimate.get("file_paths") or []
    db_touched = _db_model_files(file_paths)

    arch_violations = 0
    arch_rules = rules_path
    if manifest and not arch_rules:
        arch_rules = manifest.get("arch_rules_path") or "manifests/arch-rules.yaml"
    if arch_rules:
        abs_rules = arch_rules if os.path.isabs(arch_rules) else os.path.join(target_dir, arch_rules)
        if os.path.isfile(abs_rules):
            try:
                from apatch.arch_check import run_arch_check

                arch = run_arch_check(target_dir, rules_path=abs_rules)
                arch_violations = len(arch.get("violations") or [])
            except (OSError, ValueError, RuntimeError):
                pass

    profile = db_profile or (manifest or {}).get("db_profile")
    db_check_ok: Optional[bool] = None
    if profile:
        try:
            from apatch.db_check import run_db_check

            db_res = run_db_check(target_dir, profile=profile)
            db_check_ok = bool(db_res.get("ok"))
        except (OSError, ValueError, RuntimeError):
            db_check_ok = None

    rollback_p = _estimate_rollback_probability(plan, estimate, budget_result=budget_result)
    would_apply = layout.get("would_apply", 0)
    use_session = would_apply > MASS_APPLY_GUARD or layout.get("chunk_count", 0) > 1

    risk_level = "low"
    if rollback_p >= 0.5 or arch_violations > 0:
        risk_level = "high"
    elif rollback_p >= 0.25 or low_conf or db_touched:
        risk_level = "medium"

    needles_hints = needles_hints_from_plan(plan, candidates, target_dir)

    return {
        "ok": True,
        "dry_run": True,
        "logs_path": os.path.abspath(logs_path),
        "target_dir": target_dir,
        "risk_map": {
            "files_touched": estimate.get("files", 0),
            "insertions": estimate.get("insertions", 0),
            "deletions": estimate.get("deletions", 0),
            "would_apply": would_apply,
            "total_candidates": plan.get("total", 0),
            "chunks_required": layout.get("chunk_count", 0),
            "chunk_max_files": chunk_max_files,
            "unresolved_paths": len(unresolved),
            "low_confidence_applies": len(low_conf),
            "db_model_files_touched": len(db_touched),
            "db_model_paths": db_touched[:10],
            "db_check_current_ok": db_check_ok,
            "arch_violations_current": arch_violations,
            "budget_violations": (budget_result or {}).get("violations") or [],
        },
        "rollback_probability": rollback_p,
        "risk_level": risk_level,
        "recommended_path": "apatch_apply_session" if use_session else "apatch_apply",
        "next_action": (
            f"apatch_simulate passed risk={risk_level}; "
            f"run {('apatch_apply_session' if use_session else 'apatch_apply')}"
            f"(logs_path={logs_path!r}, verify=from doctor)"
        ),
        "execution_graph": _graph_from_manifest(manifest),
        "sample_warnings": [
            *(f"unresolved: {p}" for p in unresolved[:3]),
            *(f"low confidence: {p}" for p in low_conf[:3] if p),
        ],
        "needles_hints": needles_hints,
        "needles_hint_note": (
            "Advisory partial dicts for drift — not auto-applied; agent cognition required"
            if needles_hints
            else None
        ),
    }


def simulate_workspace(
    target_dir: str = ".",
    *,
    logs_path: Optional[str] = None,
    manifest_path: Optional[str] = None,
    chunk_max_files: int = 5,
    replace_all: bool = False,
    only_drifted: bool = False,
    min_confidence: Optional[float] = None,
    budget: Optional[str] = None,
    max_files: Optional[int] = None,
    max_insertions: Optional[int] = None,
    max_deletions: Optional[int] = None,
) -> Dict[str, Any]:
    """Entry: simulate from JSONL and/or engineering-pipeline manifest."""
    root = os.path.abspath(target_dir)
    manifest: Optional[Dict[str, Any]] = None
    if manifest_path:
        abs_manifest = manifest_path if os.path.isabs(manifest_path) else os.path.join(root, manifest_path)
        manifest = _load_manifest(abs_manifest)
        if not logs_path:
            rel = manifest.get("patches_jsonl") or manifest.get("logs_path")
            if rel:
                logs_path = rel if os.path.isabs(rel) else os.path.join(root, rel)

    if not logs_path:
        raise WorkflowError("simulate: provide logs_path or manifest_path with patches_jsonl")

    abs_logs = logs_path if os.path.isabs(logs_path) else os.path.join(root, logs_path)
    cb = manifest.get("change_budget") if manifest else None
    return simulate_from_logs(
        abs_logs,
        root,
        chunk_max_files=chunk_max_files,
        replace_all=replace_all,
        only_drifted=only_drifted,
        min_confidence=min_confidence,
        budget=budget,
        max_files=max_files or (cb or {}).get("max_files"),
        max_insertions=max_insertions or (cb or {}).get("max_insertions"),
        max_deletions=max_deletions or (cb or {}).get("max_deletions"),
        db_profile=(manifest or {}).get("db_profile"),
        manifest=manifest,
    )
