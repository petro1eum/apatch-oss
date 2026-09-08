"""WorkAsset lifecycle — governed promotion states and ledger-backed use records.

RFP-031 Phase 2 (`SPEC-WORK-ASSET-LIFECYCLE-1`) + Avatar Utility Contract (AUC-1
§5–§6). apatch philosophy applies: lifecycle state is DERIVED from signed ledger
events, never stored in a writable field. Promotion to ``accepted`` requires a
content-safe ``method`` (procedure + checks + contraindications — AUC-1 R-AUC-1)
whose sha256 is pinned inside the signed event; ``reuse`` counts only
ledger-backed use records (R-AUC-2). Invalid transitions are refused at write
time and deterministically ignored at fold time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

TOOL_PROMOTE = "apatch_work_asset_promote"
TOOL_USE = "apatch_work_asset_use"

STATES = ("candidate", "accepted", "rejected", "deprecated", "superseded")
# RFP-031 §5.2 governed transitions. Events anchor on the STABLE spec_id (the
# Phase-1 asset_id hash shifts when new attestations land), recording asset_id
# only as a snapshot.
TRANSITIONS = {
    ("candidate", "accepted"),
    ("candidate", "rejected"),
    ("accepted", "deprecated"),
    ("accepted", "superseded"),
}

ADAPTATIONS = ("unchanged", "adapted")

# AUC-1 R-AUC-1: a recallable method must state its procedure, its checks, and
# its contraindications/boundaries (en/ru markers accepted).
_METHOD_MARKERS = {
    "procedure": re.compile(r"procedur|процедур", re.IGNORECASE),
    "checks": re.compile(r"check|verif|провер", re.IGNORECASE),
    "contraindications": re.compile(
        r"contraindication|anti-?pattern|boundar|limitation|противопоказан|границ",
        re.IGNORECASE,
    ),
}


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def default_method_path(spec_id: str) -> str:
    """Workspace-relative default home for a promoted asset's method file."""
    return os.path.join("docs", "work_assets", f"{spec_id}.method.md")


def _assert_boundary(payload: Dict[str, Any]) -> Optional[str]:
    """Content-safety/economic boundary shared with the export contract
    (SPEC-WORK-ASSET-INDEX-1 R5): no secrets, no economics in signed payloads."""
    from apatch.work_assets import _FORBIDDEN_EXPORT_TERMS

    blob = _canonical(payload).lower()
    for term in _FORBIDDEN_EXPORT_TERMS:
        if term in blob:
            return f"forbidden term in lifecycle payload: {term}"
    return None


def validate_method_file(target_dir: str, method_path: str) -> Dict[str, Any]:
    """R-AUC-1 gate: the method file must exist, be non-empty, carry procedure /
    checks / contraindication sections, and pass the content-safety boundary."""
    root = os.path.abspath(target_dir)
    rel = method_path
    path = rel if os.path.isabs(rel) else os.path.join(root, rel)
    errors: List[str] = []
    if not os.path.isfile(path):
        return {"ok": False, "errors": [f"method file not found: {rel}"],
                "path": rel, "sha256": None}
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        errors.append("method file is empty")
    for name, marker in _METHOD_MARKERS.items():
        if not marker.search(text):
            errors.append(f"method file missing required section: {name}")
    from apatch.work_assets import _FORBIDDEN_EXPORT_TERMS

    lowered = text.lower()
    for term in _FORBIDDEN_EXPORT_TERMS:
        if term in lowered:
            errors.append(f"method file violates content-safety boundary: {term}")
    rel_out = os.path.relpath(path, root)
    return {
        "ok": not errors,
        "errors": errors,
        "path": rel_out,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _sorted_events(entries: Iterable[Dict[str, Any]], tool_id: str) -> List[Dict[str, Any]]:
    rows = [e for e in entries or []
            if e.get("tool_id") == tool_id and isinstance(e.get("payload"), dict)]
    return sorted(rows, key=lambda e: (str(e.get("timestamp") or ""), str(e.get("id") or "")))


def lifecycle_fold(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Replay promotion events (timestamp, id order); apply only valid
    transitions from the folded current state — invalid/forged events are
    ignored deterministically. Last valid event wins."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in _sorted_events(entries, TOOL_PROMOTE):
        payload = row["payload"]
        spec_id = payload.get("spec_id")
        to_state = payload.get("to_state")
        if not spec_id or to_state not in STATES:
            continue
        cur = out.setdefault(spec_id, {
            "state": "candidate", "method": None,
            "superseded_by": None, "since": None, "event_count": 0,
        })
        if (cur["state"], to_state) not in TRANSITIONS:
            continue
        cur["state"] = to_state
        cur["since"] = row.get("timestamp")
        cur["event_count"] += 1
        if to_state == "accepted":
            method = payload.get("method") or {}
            cur["method"] = (
                {"path": method.get("path"), "sha256": method.get("sha256")}
                if method.get("sha256") else None
            )
        if to_state == "superseded":
            cur["superseded_by"] = payload.get("superseded_by")
    return out


def reuse_fold(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """R-AUC-2: reuse comes only from signed use records, never a constant."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in _sorted_events(entries, TOOL_USE):
        spec_id = row["payload"].get("spec_id")
        if not spec_id:
            continue
        cur = out.setdefault(spec_id, {"count": 0, "last_used_at": None})
        cur["count"] += 1
        cur["last_used_at"] = row.get("timestamp") or cur["last_used_at"]
    return out


def apply_lifecycle_overlay(assets: List[Dict[str, Any]],
                            entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Decorate Phase-1 candidate assets with ledger-derived lifecycle state,
    reuse counters, the pinned method ref, and the AUC-1 §5 recallable flag
    (accepted ∧ method → participates in recall; otherwise visible-only)."""
    entries = list(entries or [])
    life = lifecycle_fold(entries)
    reuse = reuse_fold(entries)
    out: List[Dict[str, Any]] = []
    for asset in assets:
        spec_id = None
        for ref in asset.get("spec_refs") or []:
            body = ref.split(":", 1)[1] if ":" in ref else ref
            spec_id = body.split("#", 1)[0]
            break
        decorated = dict(asset)
        state_row = life.get(spec_id) or {}
        state = state_row.get("state") or decorated.get("lifecycle") or "candidate"
        method = state_row.get("method")
        decorated["lifecycle"] = state
        decorated["reuse"] = dict(reuse.get(spec_id) or {"count": 0, "last_used_at": None})
        decorated["method_ref"] = method
        decorated["recallable"] = bool(state == "accepted" and method and method.get("sha256"))
        if state_row.get("superseded_by"):
            decorated["superseded_by"] = state_row["superseded_by"]
        out.append(decorated)
    return out


def _commit(target_dir: str, tool_id: str, payload: Dict[str, Any]) -> bool:
    from apatch.trustchain_helper import TrustChainHelper

    tc = TrustChainHelper(os.path.abspath(target_dir), auto_init=True)
    return bool(tc.commit_action(tool_id, payload))


def promote_work_asset(target_dir: str = ".", spec_id: str = "", to_state: str = "",
                       *, method_path: Optional[str] = None, reason: str = "",
                       superseded_by: Optional[str] = None,
                       asset_id: Optional[str] = None) -> Dict[str, Any]:
    """Governed promotion: validate the RFP §5.2 transition against the folded
    current state, enforce R-AUC-1 for acceptance, then commit a signed event."""
    from apatch.work_assets import _ledger_entries

    root = os.path.abspath(target_dir)
    errors: List[str] = []
    if not spec_id:
        errors.append("spec_id is required")
    if to_state not in STATES or to_state == "candidate":
        errors.append(f"to_state must be one of {sorted(set(STATES) - {'candidate'})}")
    current = "candidate"
    if not errors:
        current = (lifecycle_fold(_ledger_entries(root)).get(spec_id) or {}).get("state", "candidate")
        if (current, to_state) not in TRANSITIONS:
            errors.append(f"invalid transition: {current} -> {to_state} (RFP-031 §5.2)")
    method: Optional[Dict[str, Any]] = None
    if not errors and to_state == "accepted":
        rel = method_path or default_method_path(spec_id)
        check = validate_method_file(root, rel)
        if not check["ok"]:
            errors.extend(check["errors"])
        else:
            method = {"path": check["path"], "sha256": check["sha256"]}
    if not errors and to_state == "superseded" and not superseded_by:
        errors.append("superseded requires superseded_by")
    if errors:
        return {"ok": False, "committed": False, "spec_id": spec_id,
                "from_state": current, "to_state": to_state, "errors": errors}

    payload: Dict[str, Any] = {
        "action": "work_asset_promote",  # key required so ledger parsers lift the payload
        "spec_id": spec_id,
        "from_state": current,
        "to_state": to_state,
        "reason": reason or "",
    }
    if asset_id:
        payload["asset_id"] = asset_id
    if method:
        payload["method"] = method
    if superseded_by:
        payload["superseded_by"] = superseded_by
    boundary = _assert_boundary(payload)
    if boundary:
        return {"ok": False, "committed": False, "spec_id": spec_id,
                "from_state": current, "to_state": to_state, "errors": [boundary]}
    committed = _commit(root, TOOL_PROMOTE, payload)
    return {
        "ok": committed, "committed": committed, "spec_id": spec_id,
        "from_state": current, "to_state": to_state, "event": payload,
        "errors": [] if committed else
        ["ledger commit failed (trustchain unavailable or policy denied)"],
    }


def record_work_asset_use(target_dir: str = ".", spec_id: str = "", *,
                          verification: Optional[Dict[str, Any]] = None,
                          adaptation: str = "unchanged",
                          spec_refs: Optional[List[str]] = None,
                          proof_ref: Optional[str] = None,
                          asset_id: Optional[str] = None,
                          session_id: Optional[str] = None) -> Dict[str, Any]:
    """Signed use record (RFP §5.4 / A31-F): asset + session + verification
    result + applied-unchanged-vs-adapted flag. No economics fields, ever."""
    errors: List[str] = []
    if not spec_id:
        errors.append("spec_id is required")
    if adaptation not in ADAPTATIONS:
        errors.append(f"adaptation must be one of {list(ADAPTATIONS)}")
    if not isinstance(verification, dict) or "ok" not in verification:
        errors.append("verification requires at least {'ok': bool} (plus 'command')")
    if errors:
        return {"ok": False, "committed": False, "spec_id": spec_id, "errors": errors}

    payload: Dict[str, Any] = {
        "action": "work_asset_use",
        "spec_id": spec_id,
        "adaptation": adaptation,
        "verification": {
            "command": str(verification.get("command") or ""),
            "ok": bool(verification.get("ok")),
        },
    }
    if asset_id:
        payload["asset_id"] = asset_id
    if session_id:
        payload["session_id"] = session_id
    if spec_refs:
        payload["spec_refs"] = list(spec_refs)
    if proof_ref:
        payload["proof_ref"] = proof_ref
    boundary = _assert_boundary(payload)
    if boundary:
        return {"ok": False, "committed": False, "spec_id": spec_id, "errors": [boundary]}
    committed = _commit(target_dir, TOOL_USE, payload)
    return {
        "ok": committed, "committed": committed, "spec_id": spec_id, "event": payload,
        "errors": [] if committed else
        ["ledger commit failed (trustchain unavailable or policy denied)"],
    }
