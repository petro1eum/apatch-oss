"""Avatar Recall — intent-based WorkAsset suggestion and RecallBundle assembly.

RFP-031 Phase 3 (`SPEC-WORK-ASSET-SUGGEST-1`, A31-E) + Avatar Utility Contract
(AUC-1 §3–§4): given a new task's intent, find the owner's proven, recallable
methods and hand the agent a compact, money-free RecallBundle — "have I solved
something like this before?" answered from signed evidence.

Read-only by construction: this module NEVER commits ledger events, never
mutates files. The bundle's ``next_action`` always routes into a normal
governed session (suggestions must not bypass sessions/verify/attest).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
BUNDLE_KIND = "recall_bundle"
# AUC-1 §4: compactness budget for a RecallBundle (canonical JSON, bytes).
BUNDLE_MAX_BYTES = 16 * 1024

_GUARDED_NEXT_TOOL = "apatch_session_start"


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _intent_terms(intent: str) -> List[str]:
    return [t.lower() for t in re.findall(r"[A-Za-zА-Яа-я0-9_:-]{2,}", intent or "")]


def _next_action(asset: Dict[str, Any]) -> Dict[str, Any]:
    """Guarded next step (A31-E): enter a normal governed session carrying the
    asset as an artifact — never a direct mutation."""
    refs = [f"work_asset:{asset.get('asset_id')}"]
    for ref in asset.get("spec_refs") or []:
        spec = ref.split("#", 1)[0]
        if spec not in refs:
            refs.append(spec)
        break
    return {"tool": _GUARDED_NEXT_TOOL, "artifacts": refs}


def suggest_work_assets(target_dir: str = ".", intent: str = "",
                        limit: Optional[int] = 3) -> Dict[str, Any]:
    """Deterministic, explainable suggestion over RECALLABLE assets only
    (AUC-1 §5: accepted + pinned method participate in recall; everything else
    is visible-only). Ranking: matched intent terms, then ledger-backed reuse,
    then attestation evidence — same ledger + same intent → same order."""
    from apatch.work_assets import _score_asset, build_work_asset_index

    index = build_work_asset_index(target_dir)
    terms = _intent_terms(intent)
    results: List[Dict[str, Any]] = []
    for asset in index.get("assets") or []:
        if not asset.get("recallable"):
            continue
        score, reasons = _score_asset(asset, terms)
        if terms and not score:
            continue
        results.append({
            "asset_id": asset.get("asset_id"),
            "spec_id": (asset.get("spec_refs") or [""])[0].split(":", 1)[-1].split("#", 1)[0],
            "title": asset.get("title"),
            "score": round(score / len(terms), 4) if terms else 0.0,
            "reasons": reasons,
            "reuse": dict(asset.get("reuse") or {}),
            "next": _next_action(asset),
        })
    results.sort(key=lambda row: (
        -row["score"],
        -(row["reuse"].get("count") or 0),
        row["asset_id"] or "",
    ))
    if limit is not None and limit >= 0:
        results = results[:limit]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "work_asset_suggestions",
        "intent": intent or "",
        "count": len(results),
        "results": results,
    }


def _find_asset(index: Dict[str, Any], asset_id: str) -> Optional[Dict[str, Any]]:
    for asset in index.get("assets") or []:
        if asset.get("asset_id") == asset_id:
            return asset
    return None


def _method_block(root: str, asset: Dict[str, Any]) -> Dict[str, Any]:
    """Load the pinned method with an INTEGRITY check: the file on disk must
    still hash to the sha256 signed at acceptance — a drifted method is refused,
    never silently served."""
    ref = asset.get("method_ref") or {}
    rel, pinned = ref.get("path"), ref.get("sha256")
    if not rel or not pinned:
        raise ValueError("asset has no pinned method (not recallable)")
    path = rel if os.path.isabs(rel) else os.path.join(root, rel)
    if not os.path.isfile(path):
        raise ValueError(f"method file missing: {rel}")
    with open(path, "rb") as fh:
        raw = fh.read()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != pinned:
        raise ValueError(
            "method integrity check failed: file drifted since acceptance "
            f"(pinned {pinned[:12]}…, actual {actual[:12]}…) — re-promote to re-pin"
        )
    return {
        "mode": "inline",
        "path": rel,
        "sha256": pinned,
        "content": raw.decode("utf-8", errors="replace"),
    }


def build_recall_bundle(target_dir: str = ".", asset_id: str = "",
                        intent: str = "") -> Dict[str, Any]:
    """Assemble the AUC-1 §4 RecallBundle for one recallable asset:
    why_fit / method / context / evidence / rights / next_action.
    Money-free, content-safe, deterministic, ≤ BUNDLE_MAX_BYTES (with a
    pointer fallback when the inline method would blow the budget)."""
    from apatch.work_assets import _FORBIDDEN_EXPORT_TERMS, _score_asset, build_work_asset_index

    root = os.path.abspath(target_dir)
    index = build_work_asset_index(root)
    asset = _find_asset(index, asset_id)
    if asset is None:
        return {"ok": False, "error_type": "WORK_ASSET_NOT_FOUND",
                "error": f"unknown asset: {asset_id}"}
    if not asset.get("recallable"):
        return {"ok": False, "error_type": "NOT_RECALLABLE",
                "error": "asset is visible-only: recall requires accepted state "
                         "+ pinned method (AUC-1 §5)"}
    try:
        method = _method_block(root, asset)
    except ValueError as exc:
        return {"ok": False, "error_type": "METHOD_INTEGRITY", "error": str(exc)}

    terms = _intent_terms(intent)
    score, reasons = _score_asset(asset, terms) if terms else (0, [])
    bundle: Dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "kind": BUNDLE_KIND,
        "asset_id": asset.get("asset_id"),
        "title": asset.get("title"),
        "why_fit": {
            "intent": intent or "",
            "matched": reasons,
            "problem_signature": asset.get("problem_signature"),
        },
        "method": method,
        "context": {
            "applicability": asset.get("applicability"),
            "confidentiality_class": asset.get("confidentiality_class"),
        },
        "evidence": {
            "verification_refs": asset.get("verification_refs") or [],
            "proof_refs": asset.get("proof_refs") or [],
            "reuse": dict(asset.get("reuse") or {}),
            "outputs": asset.get("outputs") or [],
        },
        "rights": {
            "portability_policy": asset.get("portability_policy"),
            "dependency_lens": asset.get("dependency_lens"),
        },
        "next_action": _next_action(asset),
    }

    # Compactness budget (AUC-1 §4): fall back to a pointer before failing.
    if len(_canonical(bundle).encode("utf-8")) > BUNDLE_MAX_BYTES:
        bundle["method"] = {k: v for k, v in method.items() if k != "content"}
        bundle["method"]["mode"] = "pointer"
        if len(_canonical(bundle).encode("utf-8")) > BUNDLE_MAX_BYTES:
            return {"ok": False, "error_type": "BUNDLE_TOO_LARGE",
                    "error": f"bundle exceeds {BUNDLE_MAX_BYTES} bytes even as pointer"}

    # Content-safety / economic boundary over the WHOLE bundle (incl. method text).
    blob = _canonical(bundle).lower()
    for term in _FORBIDDEN_EXPORT_TERMS:
        if term in blob:
            return {"ok": False, "error_type": "BOUNDARY_VIOLATION",
                    "error": f"forbidden term in recall bundle: {term}"}
    return bundle
