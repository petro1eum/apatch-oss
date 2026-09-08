"""Plan adherence (RFP-012 / SPEC-ADHERENCE-1). R1 ok"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from apatch.spec_plan import planned_files_for_rk


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _files_from_mutation_payload(payload: Dict[str, Any]) -> Set[str]:
    files: Set[str] = set()
    raw = payload.get("files")
    if isinstance(raw, dict):
        for rel in raw:
            files.add(_normalize_rel(str(rel)))
    return files


def compute_deviation(
    *,
    planned: List[str],
    touched: List[str],
) -> Dict[str, Any]:
    planned_set = {_normalize_rel(p) for p in planned}
    touched_set = {_normalize_rel(p) for p in touched}
    unplanned = sorted(touched_set - planned_set)
    untouched = sorted(planned_set - touched_set)
    adherent = not unplanned and not untouched
    return {
        "adherent": adherent,
        "planned": sorted(planned_set),
        "touched": sorted(touched_set),
        "unplanned": unplanned,
        "untouched_planned": untouched,
    }


def adherence_for_requirement(
    plan: Dict[str, Any],
    rk: str,
    mutation_payloads: List[Dict[str, Any]],
) -> Dict[str, Any]:
    planned = planned_files_for_rk(plan, rk)
    touched: Set[str] = set()
    for pl in mutation_payloads:
        touched |= _files_from_mutation_payload(pl)
    dev = compute_deviation(planned=planned, touched=sorted(touched))
    return dev


def _mutation_payloads_for_requirement(
    entries: List[Dict[str, Any]],
    spec_id: str,
    req_id: str,
) -> List[Dict[str, Any]]:
    from apatch.traceability import build_traceability_index

    index = build_traceability_index(entries or [])
    key = f"spec:{spec_id}#{req_id}"
    bucket = (index.get("by_artifact") or {}).get(key) or {}
    payloads: List[Dict[str, Any]] = []
    for mut in bucket.get("mutations") or []:
        op_id = mut.get("op_id")
        row = next(
            (e for e in entries if (e.get("id") or e.get("signature")) == op_id),
            None,
        )
        if row:
            payloads.append(row.get("payload") or {})
    return payloads


def spec_adherence_workspace(  # R3
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    plan_ref: str = "latest",
) -> Dict[str, Any]:
    from apatch.spec import _ledger_entries, _load_spec
    from apatch.spec_plan import resolve_plan_version

    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    root = __import__("os").path.abspath(target_dir)
    plan, version, err = resolve_plan_version(root, parsed.id, plan_ref)
    if err or not plan:
        return {"ok": False, "error": err or "plan not found"}
    entries, _ledger_active = _ledger_entries(target_dir)
    rows = []
    adherent_n = 0
    for req in parsed.requirements:
        payloads = _mutation_payloads_for_requirement(entries, parsed.id, req.id)
        dev = adherence_for_requirement(plan, req.id, payloads)
        if dev["adherent"]:
            adherent_n += 1
        rows.append({"id": req.id, **dev})
    return {
        "ok": True,
        "spec": parsed.id,
        "plan_version": version,
        "requirements": rows,
        "summary": {
            "rk_total": len(rows),
            "adherent": adherent_n,
            "deviated": len(rows) - adherent_n,
        },
    }


def spec_adherence_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = __import__("os").path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_adherence",
        spec_adherence_workspace(root, **kwargs),
        target_dir=root,
    )


def build_attest_adherence(  # R2
    plan_id: str,
    deviation: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "plan_id": plan_id,
        "adherent": deviation.get("adherent", False),
        "unplanned": deviation.get("unplanned") or [],
        "untouched_planned": deviation.get("untouched_planned") or [],
    }
