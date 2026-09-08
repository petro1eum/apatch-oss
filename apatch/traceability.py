"""Artifact traceability: coverage matrix and op_id → artifact reverse mapping (RFP-006 §6.2).

Exposed to agents via MCP ``apatch_trustchain_coverage`` and CLI
``apatch trustchain coverage``. See ``docs/mcp_setup.md`` (Artifact-anchored intent).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from apatch.artifact import entry_matches_artifact_filter, payload_artifacts

_MUTATION_ACTIONS = frozenset(
    {"strip", "compile", "phase_bundle", "apply", "checkpoint", "phase_run"}
)


def _artifact_key(art: Dict[str, Any]) -> str:
    return f"{art.get('kind')}:{art.get('id')}"


def _sort_key(row: Dict[str, Any]) -> Tuple[int, str]:
    ts = row.get("timestamp")
    if ts:
        return (0, str(ts))
    return (1, str(row.get("object_path") or ""))


def _is_mutation_row(row: Dict[str, Any]) -> bool:
    tool_id = row.get("tool_id")
    if tool_id not in ("apatch", "apatch_expert"):
        return False
    payload = row.get("payload") or {}
    action = str(payload.get("action") or "")
    if action == "bootstrap":
        return False
    if payload.get("applied_patches") is not None:
        return True
    files = payload.get("files")
    if isinstance(files, dict) and files:
        return True
    if payload.get("extracted_blocks") or payload.get("source_file"):
        return True
    if action in _MUTATION_ACTIONS and (files or payload.get("source_file")):
        return True
    return False


def _entry_role(row: Dict[str, Any]) -> str:
    tool_id = row.get("tool_id")
    payload = row.get("payload") or {}
    if tool_id == "apatch_attest":
        return "attestation"
    if payload.get("action") == "engineering_pipeline":
        return "intent"
    if _is_mutation_row(row):
        return "mutation"
    if payload_artifacts(payload) or payload.get("intent"):
        return "intent"
    return "other"


def _op_summary(row: Dict[str, Any], *, role: str, artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
    from apatch.ledger_actor import ledger_signed_by

    payload = row.get("payload") or {}
    op_id = row.get("id") or row.get("signature")
    signed_by = ledger_signed_by(row)
    return {
        "op_id": op_id,
        "role": role,
        "artifacts": artifacts,
        "tool_id": row.get("tool_id"),
        "action": payload.get("action"),
        "signature": row.get("signature"),
        "timestamp": row.get("timestamp"),
        "governed_session_id": payload.get("governed_session_id") or payload.get("session_id"),
        "intent": payload.get("intent"),
        "object_path": row.get("object_path"),
        "signed_by": signed_by,
        "key_id": row.get("key_id") or signed_by,
    }


def build_traceability_index(
    entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Walk ledger rows and build op_id → artifact mapping plus per-artifact coverage."""
    sorted_rows = sorted(entries, key=_sort_key)
    current_artifacts: List[Dict[str, Any]] = []
    op_id_index: Dict[str, Dict[str, Any]] = {}
    by_artifact: Dict[str, Dict[str, Any]] = {}

    def _ensure_bucket(key: str, art: Dict[str, Any]) -> Dict[str, Any]:
        if key not in by_artifact:
            by_artifact[key] = {
                "artifact": art,
                "intents": [],
                "mutations": [],
                "attestations": [],
            }
        return by_artifact[key]

    for row in sorted_rows:
        op_id = row.get("id") or row.get("signature")
        if not op_id:
            continue
        payload = row.get("payload") or {}
        role = _entry_role(row)
        direct = payload_artifacts(payload)

        if direct:
            current_artifacts = list(direct)

        if role == "mutation":
            assigned = direct or list(current_artifacts)
        elif role in ("intent", "attestation"):
            assigned = direct or list(current_artifacts)
        else:
            assigned = direct

        if not assigned:
            continue

        summary = _op_summary(row, role=role, artifacts=assigned)
        op_id_index[str(op_id)] = summary

        for art in assigned:
            key = _artifact_key(art)
            bucket = _ensure_bucket(key, art)
            if role == "mutation":
                bucket["mutations"].append(summary)
            elif role == "attestation":
                bucket["attestations"].append(summary)
                if payload.get("covered_by"):
                    bucket["covered_by"] = True
            elif role == "intent":
                bucket["intents"].append(summary)

    for bucket in by_artifact.values():
        muts = bucket["mutations"]
        atts = bucket["attestations"]
        cov_by = bool(bucket.get("covered_by"))
        bucket["coverage"] = {
            "has_intent": bool(bucket["intents"]),
            "has_mutations": bool(muts),
            "has_attestation": bool(atts),
            "has_covered_by": cov_by,
            "complete": bool(atts) and (bool(muts) or cov_by),
            "mutation_count": len(muts),
            "attestation_count": len(atts),
        }

    return {
        "op_id_index": op_id_index,
        "by_artifact": by_artifact,
        "artifact_count": len(by_artifact),
        "op_count": len(op_id_index),
    }


def artifact_coverage_report(
    entries: List[Dict[str, Any]],
    *,
    artifact: Optional[str] = None,
) -> Dict[str, Any]:
    """Coverage matrix for one artifact or all artifacts in the ledger."""
    index = build_traceability_index(entries)
    by_art = index["by_artifact"]

    if artifact and str(artifact).strip():
        filt = str(artifact).strip()
        if "@" in filt:
            filt = filt.split("@", 1)[0]
        matched = {
            k: v
            for k, v in by_art.items()
            if entry_matches_artifact_filter({"artifacts": [v["artifact"]]}, filt)
        }
        if not matched:
            return {
                "ok": True,
                "artifact": artifact,
                "artifacts": [],
                "intents": [],
                "mutations": [],
                "attestations": [],
                "coverage": {
                    "has_intent": False,
                    "has_mutations": False,
                    "has_attestation": False,
                    "complete": False,
                    "mutation_count": 0,
                    "attestation_count": 0,
                },
                "op_id_index": {},
                "message": "no ledger entries linked to this artifact",
            }
        key = next(iter(matched))
        bucket = matched[key]
        linked_ops = {
            op_id: rec
            for op_id, rec in index["op_id_index"].items()
            if any(
                _artifact_key(a) == key
                for a in (rec.get("artifacts") or [])
            )
        }
        return {
            "ok": True,
            "artifact": filt,
            "artifacts": [bucket["artifact"]],
            "intents": bucket["intents"],
            "mutations": bucket["mutations"],
            "attestations": bucket["attestations"],
            "coverage": bucket["coverage"],
            "op_id_index": linked_ops,
        }

    summaries = []
    for key, bucket in sorted(by_art.items()):
        summaries.append(
            {
                "artifact": bucket["artifact"],
                "artifact_key": key,
                "coverage": bucket["coverage"],
                "intents": len(bucket["intents"]),
                "mutations": len(bucket["mutations"]),
                "attestations": len(bucket["attestations"]),
            }
        )
    return {
        "ok": True,
        "artifact": None,
        "artifacts": summaries,
        "coverage_summary": {
            "total_artifacts": len(summaries),
            "complete": sum(1 for s in summaries if s["coverage"]["complete"]),
            "with_mutations_only": sum(
                1
                for s in summaries
                if s["coverage"]["has_mutations"] and not s["coverage"]["has_attestation"]
            ),
            "with_attestation_only": sum(
                1
                for s in summaries
                if s["coverage"]["has_attestation"] and not s["coverage"]["has_mutations"]
            ),
        },
        "op_id_index": index["op_id_index"],
    }


def resolve_op_id_artifacts(
    entries: List[Dict[str, Any]],
    op_id: str,
) -> Dict[str, Any]:
    """Reverse lookup: which artifact(s) does a ledger op_id belong to?"""
    index = build_traceability_index(entries)
    rec = index["op_id_index"].get(str(op_id))
    if not rec:
        return {"ok": False, "error": "op_id not found or not linked to any artifact", "op_id": op_id}
    return {"ok": True, "op_id": op_id, **rec}
