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
    from apatch.trust_identity import anchor_status
    from apatch.trustchain_helper import TrustChainHelper

    tc = TrustChainHelper(root, auto_init=False)
    anchor = anchor_status(root)
    return {
        "trustchain_active": tc.has_trustchain(),
        "enforcement_active": is_enforcement_enabled(root),
        "governed_mode": resolve_governed_mode(root),
        "trust_anchor": {
            "agent_id": anchor.get("agent_id"),
            "secure": anchor.get("secure"),
        },
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


def _diagnostics_summary(root: str) -> Optional[Dict[str, Any]]:
    from apatch.knowledge_graph import diagnostics_summary_workspace

    return diagnostics_summary_workspace(root)


def _asset_summary(root: str, trace_index: Dict[str, Any]) -> Dict[str, Any]:
    """RFP-025 AF-7: embed the Avatar asset_summary, reusing the already-built
    traceability index — no second ledger walk (perf)."""
    try:
        from apatch.avatar_compiler import asset_summary_from_index

        return asset_summary_from_index(trace_index, root)
    except Exception:
        return {}


def _capabilities_summary(root: str) -> Dict[str, Any]:
    """RFP-037 A37-M: what the avatar provably does (top classes, reliability,
    uncertainty) — never how much data was collected."""
    try:
        from apatch.avatar_evidence import capabilities_summary

        return capabilities_summary(root)
    except Exception:
        return {}


def project_status_workspace(
    target_dir: str = ".", *, view: str = "full"
) -> Dict[str, Any]:
    """Read-only unified project status for CLI/HTML/MD views (RFP-020).

    ``view='compact'`` returns a token-bounded DTO (per-spec summaries +
    stale/pending requirement ids, conflict counts); the full requirement rows
    and conflict graph are omitted. ``view='full'`` (default) returns the
    complete DTO used by CLI/HTML/MD renderers.
    """
    from apatch.session_status import active_session_alias, build_session_block

    root = os.path.abspath(target_dir)
    entries, ledger_active = _ledger_once(root)
    from apatch.traceability import build_traceability_index

    trace_index = build_traceability_index(entries or [])
    spec_ids = _discover_spec_ids(root)
    specs = _specs_block(root, spec_ids, entries)
    session = build_session_block(root)
    dto: Dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "extensions": ["session_blocker_v1"],
        "workspace": root,
        "lane": _resolve_lane(root),
        "ledger_active": ledger_active,
        "specs": specs,
        "summary": _program_summary(specs),
        "conflicts": _conflicts_block(root, spec_ids, entries),
        "session": session,
        "active_session": active_session_alias(session) or _active_session(root),
        "hygiene": _hygiene(root),
        "policy": _policy(root),
        "activity": _activity_from_index(trace_index),
        "diagnostics_summary": _diagnostics_summary(root),
        "asset_summary": _asset_summary(root, trace_index),
        "capabilities_summary": _capabilities_summary(root),
    }
    if str(view or "full").lower() == "compact":
        return _compact_view(dto)
    return dto


def _compact_view(dto: Dict[str, Any]) -> Dict[str, Any]:
    """Token-bounded status: per-spec summaries + stale/pending ids (no full
    requirement rows), conflict counts (no full graph)."""
    specs = [
        {
            "id": s.get("id"),
            "title": s.get("title"),
            "summary": s.get("summary"),
            "done": s.get("done"),
            "stale": [r["id"] for r in (s.get("requirements") or []) if r.get("stale")],
            "pending": [
                r["id"]
                for r in (s.get("requirements") or [])
                if r.get("state") == "pending"
            ],
        }
        for s in dto.get("specs") or []
    ]
    conflicts = dto.get("conflicts")
    if isinstance(conflicts, dict):
        conflicts = {
            "summary": conflicts.get("summary"),
            "risk_score": conflicts.get("risk_score"),
            "safe_order": conflicts.get("safe_order"),
        }
    out = dict(dto)
    out["specs"] = specs
    out["conflicts"] = conflicts
    # secondary heavy blocks: not needed for an at-a-glance status
    if dto.get("asset_summary"):
        out["asset_summary"] = {"omitted_in_compact": True}
    activity = dto.get("activity")
    if isinstance(activity, list) and len(activity) > 5:
        out["activity"] = activity[:5]
        out["activity_truncated"] = len(activity)
    out["view"] = "compact"
    return out


def project_status_enriched(
    target_dir: str = ".", *, view: str = "full"
) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_project_status",
        project_status_workspace(root, view=view),
        target_dir=root,
    )