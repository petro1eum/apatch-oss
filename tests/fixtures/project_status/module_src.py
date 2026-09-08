"""Unified project status DTO (RFP-020 / SPEC-PROJECT-STATUS-1)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1


def _load_spec_registry(root: str, spec_id: str) -> Dict[str, Any]:
    path = os.path.join(root, ".apatch", "specs", f"{spec_id}.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _discover_spec_ids(root: str) -> List[str]:
    from apatch.spec_interference import _discover_spec_ids

    return _discover_spec_ids(root)


def _ledger_once(target_dir: str) -> Tuple[List[Dict[str, Any]], bool]:
    from apatch.spec import _ledger_entries

    return _ledger_entries(target_dir)


def _resolve_lane(root: str) -> Dict[str, Any]:
    from apatch.lane import resolve_lane

    return resolve_lane(root).to_dict()


def _active_session(root: str) -> Optional[Dict[str, Any]]:
    from apatch.sandbox import sandbox_status_workspace
    from apatch.session_state import load_session_state

    st = load_session_state(root)
    lease = sandbox_status_workspace(root).get("lease") or {}
    phase = st.get("phase")
    if lease.get("active"):
        return {"session_id": st.get("checkpoint"), "phase": phase, "lease": lease}
    if phase and phase not in ("idle", "complete"):
        return {"session_id": st.get("checkpoint"), "phase": phase}
    return None


def _hygiene(root: str) -> Dict[str, Any]:
    from apatch.artifact_governance import build_doctor_hygiene

    return build_doctor_hygiene(root)


def _policy(root: str) -> Dict[str, Any]:
    from apatch.enforcement import is_enforcement_enabled, resolve_governed_mode
    from apatch.trustchain_helper import TrustChainHelper

    tc = TrustChainHelper(root, auto_init=False)
    return {
        "trustchain_active": tc.has_trustchain(),
        "enforcement_active": is_enforcement_enabled(root),
        "governed_mode": resolve_governed_mode(root),
    }


def _activity_from_index(index: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for op_id, rec in (index.get("op_id_index") or {}).items():
        rows.append(
            {
                "op_id": op_id,
                "role": rec.get("role"),
                "tool_id": rec.get("tool_id"),
                "timestamp": rec.get("timestamp"),
                "signed_by": rec.get("signed_by"),
                "intent": rec.get("intent"),
                "artifacts": rec.get("artifacts") or [],
                "governed_session_id": rec.get("governed_session_id"),
            }
        )
    rows.sort(
        key=lambda r: (str(r.get("timestamp") or ""), str(r.get("op_id") or "")),
        reverse=True,
    )
    return rows[:limit]


def _specs_block(
    root: str,
    spec_ids: List[str],
    entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    from apatch.spec import _load_spec, spec_status_from_entries

    specs: List[Dict[str, Any]] = []
    for sid in spec_ids:
        try:
            parsed = _load_spec(root, spec=sid)
            status = spec_status_from_entries(parsed, entries)
            registry = _load_spec_registry(root, sid)
            specs.append(
                {
                    "id": sid,
                    "title": status.get("title"),
                    "source_path": status.get("source_path"),
                    "summary": status.get("summary"),
                    "requirements": status.get("requirements"),
                    "done": status.get("done"),
                    "planned_needles": registry.get("requirements") or {},
                }
            )
        except (FileNotFoundError, ValueError):
            continue
    return specs


def _program_summary(specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = attested = stale = 0
    for spec in specs:
        sm = spec.get("summary") or {}
        total += int(sm.get("total") or 0)
        attested += int(sm.get("attested") or 0)
        stale += int(sm.get("stale") or 0)
    return {
        "spec_count": len(specs),
        "total_requirements": total,
        "attested": attested,
        "stale_count": stale,
        "percent_complete": round(100.0 * attested / total, 1) if total else 0.0,
    }


def _conflicts_block(
    root: str,
    spec_ids: List[str],
    entries: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if len(spec_ids) < 2:
        return None
    from apatch.spec_interference import spec_interference_from_data

    out = spec_interference_from_data(root, spec_ids, entries, level=2)
    if not out.get("ok"):
        return {"ok": False, "error": out.get("error")}
    return {
        "graph": out.get("conflict_graph"),
        "safe_order": out.get("safe_order"),
        "risk_score": out.get("risk_score"),
        "conflicts": out.get("conflicts"),
        "summary": out.get("summary"),
    }


def project_status_workspace(target_dir: str = ".") -> Dict[str, Any]:
    """Read-only unified project status for CLI/HTML/MD views (RFP-020)."""
    root = os.path.abspath(target_dir)
    entries, ledger_active = _ledger_once(root)
    from apatch.traceability import build_traceability_index

    trace_index = build_traceability_index(entries or [])
    spec_ids = _discover_spec_ids(root)
    specs = _specs_block(root, spec_ids, entries)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "workspace": root,
        "lane": _resolve_lane(root),
        "ledger_active": ledger_active,
        "specs": specs,
        "summary": _program_summary(specs),
        "conflicts": _conflicts_block(root, spec_ids, entries),
        "active_session": _active_session(root),
        "hygiene": _hygiene(root),
        "policy": _policy(root),
        "activity": _activity_from_index(trace_index),
    }


def project_status_enriched(target_dir: str = ".") -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_project_status",
        project_status_workspace(root),
        target_dir=root,
    )
