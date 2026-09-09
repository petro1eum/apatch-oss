"""Requirement coverage and staleness (RFP-010 / SPEC-COVERAGE-1).\n\nLedger-derived file sets per requirement (R1). SPEC-COVERAGE-1 R1 re-attest. R2 staleness re-attest. R1 final. R2 final."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from apatch.apatch_paths import normalize_rel as _normalize_rel
from apatch.spec import (
    Spec,
    _ledger_entries,
    _load_spec,
    _requirement_state,
    parse_spec_file,
    spec_status_from_entries,
)
from apatch.traceability import build_traceability_index


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _files_from_mutation_row(
    row: Dict[str, Any],
    *,
    artifact_key: Optional[str] = None,
) -> Dict[str, str]:
    """Extract rel_path -> sha256, honoring an optional signed Rk partition."""
    payload = row.get("payload") or {}
    files = payload.get("files")
    allowed = None
    partition = payload.get("artifact_files")
    if artifact_key and isinstance(partition, dict):
        raw_allowed = partition.get(artifact_key)
        if not isinstance(raw_allowed, list):
            return {}
        allowed = {_normalize_rel(str(path)) for path in raw_allowed}
    out: Dict[str, str] = {}
    if isinstance(files, dict):
        for rel, meta in files.items():
            rel_n = _normalize_rel(str(rel))
            if not rel_n or (allowed is not None and rel_n not in allowed):
                continue
            if isinstance(meta, dict):
                sha = meta.get("sha256") or meta.get("hash")
                if sha:
                    out[rel_n] = str(sha)
    return out


def _latest_attestation_session(
    bucket: Dict[str, Any],
    entries: List[Dict[str, Any]],
) -> Optional[str]:
    """Governed session id from the most recent attestation for this requirement."""
    latest_ts: Optional[str] = None
    latest_session: Optional[str] = None
    for att in bucket.get("attestations") or []:
        ts = att.get("timestamp")
        if ts is not None and latest_ts is not None and str(ts) <= str(latest_ts):
            continue
        if ts is not None:
            latest_ts = str(ts)
        session_id = att.get("governed_session_id")
        op_id = att.get("op_id")
        if op_id and not session_id:
            row = next(
                (e for e in entries if (e.get("id") or e.get("signature")) == op_id),
                None,
            )
            if row:
                pl = row.get("payload") or {}
                session_id = pl.get("session_id") or pl.get("governed_session_id")
        if session_id:
            latest_session = str(session_id)
    return latest_session


def requirement_file_sets(
    entries: List[Dict[str, Any]],
    spec_id: str,
) -> Dict[str, Dict[str, Any]]:
    """Derive file references without mistaking missing evidence for freshness.

    A later fileless attestation does not prove a new file set. Retain the last
    recorded references for diagnosis, explicitly marked incomplete; never hash
    the current workspace to manufacture historical evidence.
    """
    index = build_traceability_index(entries or [])
    by_art = index.get("by_artifact") or {}
    out: Dict[str, Dict[str, Any]] = {}
    by_id = {str(e.get("id") or e.get("signature")): e for e in entries or []}
    from apatch.traceability import _sort_key
    positions = {str(e.get("id") or e.get("signature")): i
                 for i, e in enumerate(sorted(entries or [], key=_sort_key))}

    def mutation_history(bucket, before=None):
        cutoff = positions.get(str(before.get("op_id")), -1) if before else len(positions)
        return [str(m.get("op_id")) for m in bucket.get("mutations") or []
                if positions.get(str(m.get("op_id")), len(positions)) < cutoff]

    def session_files(bucket, attestation, artifact_key):
        session = attestation.get("governed_session_id")
        files: Dict[str, str] = {}
        op_ids: List[str] = []
        complete = True
        observed = False
        for mutation in bucket.get("mutations") or []:
            if mutation.get("governed_session_id") != session:
                continue
            att_time, mut_time = attestation.get("timestamp"), mutation.get("timestamp")
            if att_time is not None and mut_time is not None and str(mut_time) > str(att_time):
                continue
            observed = True
            op_id = str(mutation.get("op_id") or "")
            row = by_id.get(op_id)
            if row:
                extracted = _files_from_mutation_row(row, artifact_key=artifact_key)
                payload = row.get("payload") or {}
                raw_files = payload.get("files")
                expected = {_normalize_rel(str(p)) for p in raw_files} if isinstance(raw_files, dict) else set()
                partition = payload.get("artifact_files")
                if isinstance(partition, dict):
                    allowed = partition.get(artifact_key)
                    expected &= {_normalize_rel(str(p)) for p in allowed} if isinstance(allowed, list) else set()
                complete = complete and bool(expected) and expected.issubset(extracted)
                if extracted:
                    files.update(extracted)
                    op_ids.append(op_id)
            else:
                complete = False
        return files, op_ids, complete and bool(files), observed

    for key, bucket in by_art.items():
        if not key.startswith("spec:"):
            continue
        rest = key[5:]
        if "#" not in rest:
            continue
        parent, rk = rest.split("#", 1)
        if parent != spec_id or not rk:
            continue

        if not bucket.get("attestations") or not bucket.get("mutations"):
            continue

        from apatch.spec_reverification import valid_signed_reverification

        attestations = bucket["attestations"]
        latest = attestations[-1]
        reference = latest
        files, op_ids = {}, []
        complete = reference_complete = False
        verify_sha256 = None
        for attestation in attestations:
            current, mutation_ids, valid, observed = session_files(bucket, attestation, key)
            row = by_id.get(str(attestation.get("op_id")), {})
            payload = row.get("payload") or {}
            proofs = payload.get("file_reverification") or {}
            proof = proofs.get(key) if isinstance(proofs, dict) else None
            anchor_hash = next((
                item.get("content_hash") for item in payload.get("artifacts", [])
                if isinstance(item, dict) and item.get("kind") == "spec"
                and item.get("id") == key[5:]
            ), None)
            complete = False
            verify_sha256 = None
            if observed:
                files, op_ids, reference = current, mutation_ids, attestation
                complete = reference_complete = valid
            elif reference_complete and valid_signed_reverification(
                proof, artifact=key, artifact_hash=anchor_hash,
                reference=reference.get("op_id"), files=files, op_ids=op_ids,
                mutation_history=mutation_history(bucket, attestation),
            ):
                files, reference = dict(proof["files"]), attestation
                complete = reference_complete = True
                verify_sha256 = proof["verify_sha256"]

        history = mutation_history(bucket)
        reference_history = mutation_history(bucket, reference)
        complete = complete and history == reference_history
        out[rk] = {
            "mutation_history_op_ids": history,
            "reference_mutation_history_op_ids": reference_history,
            "files": files,
            "session_id": latest.get("governed_session_id"),
            "op_ids": op_ids,
            "attested_at": latest.get("timestamp"),
            "attestation_op_id": latest.get("op_id"),
            "reference_session_id": reference.get("governed_session_id"),
            "reference_attestation_op_id": reference.get("op_id"),
            "reference_complete": reference_complete,
            "evidence_status": "complete" if complete else "incomplete",
            "reverification_verify_sha256": verify_sha256,
        }
    return out


def reference_hashes(file_set: Dict[str, Any]) -> Dict[str, str]:
    files = file_set.get("files") if isinstance(file_set, dict) else file_set
    if not isinstance(files, dict):
        return {}
    return dict(files)


def compute_drift(
    file_set: Dict[str, str],
    workspace_root: str,
) -> List[str]:
    """Return rel paths whose current sha256 differs from attestation reference (R2)."""
    drifted: List[str] = []
    root = os.path.abspath(workspace_root)
    for rel, ref_sha in file_set.items():
        abs_path = os.path.join(root, rel)
        if not os.path.isfile(abs_path):
            drifted.append(rel)
            continue
        current = _sha256_file(abs_path)
        if current != ref_sha:
            drifted.append(rel)
    return sorted(drifted)


def _global_file_history(entries: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Map rel path -> op_ids that touched it (mutation rows)."""
    hist: Dict[str, List[str]] = {}
    for row in entries or []:
        op_id = row.get("id") or row.get("signature")
        for rel in _files_from_mutation_row(row):
            hist.setdefault(rel, [])
            if op_id and op_id not in hist[rel]:
                hist[rel].append(str(op_id))
    return hist


def apply_file_drift_to_status(
    row: Dict[str, Any],
    *,
    file_set: Optional[Dict[str, Any]] = None,
    drifted: Optional[List[str]] = None,
    stale_reason: Optional[str] = None,
) -> Dict[str, Any]:
    out = dict(row)
    if drifted:
        out["state"] = "stale"
        out["stale"] = True
        out["stale_reason"] = stale_reason or "file_drift"
        out["drifted"] = list(drifted)
    if file_set:
        out["files"] = list((file_set.get("files") or {}).keys())
        out["file_hashes"] = dict(file_set.get("files") or {})
    return out


def coverage_rows(
    spec: Spec,
    entries: List[Dict[str, Any]],
    workspace_root: str,
    *,
    plan: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    status = spec_status_from_entries(spec, entries)
    file_sets = requirement_file_sets(entries, spec.id)
    rows: List[Dict[str, Any]] = []

    for req_row in status.get("requirements") or []:
        rid = req_row["id"]
        row = dict(req_row)
        fs = file_sets.get(rid)
        if fs:
            row["files"] = sorted((fs.get("files") or {}).keys())
            row["file_hashes"] = dict(fs.get("files") or {})
            row["session_id"] = fs.get("session_id")
            row["op_ids"] = list(fs.get("op_ids") or [])
            row["mutation_history_op_ids"] = list(fs.get("mutation_history_op_ids") or [])
            row["reference_mutation_history_op_ids"] = list(fs.get("reference_mutation_history_op_ids") or [])
            row["attested_at"] = fs.get("attested_at")
            row["evidence_status"] = fs.get("evidence_status")
            row["reference_complete"] = fs.get("reference_complete", False)
            verify_hash = fs.get("reverification_verify_sha256")
            if verify_hash and verify_hash != hashlib.sha256(
                str(row.get("verify") or "").encode("utf-8")
            ).hexdigest():
                fs = dict(fs, evidence_status="incomplete")
                row["evidence_status"] = "incomplete"
                row["reference_complete"] = False
            row["reference_session_id"] = fs.get("reference_session_id")
            row["attestation_op_id"] = fs.get("attestation_op_id")
            row["reference_attestation_op_id"] = fs.get("reference_attestation_op_id")
            if fs.get("evidence_status") == "incomplete" and row.get("state") == "attested":
                row["state"] = "stale"
                row["stale"] = True
                row["stale_reason"] = "evidence_incomplete"


        canonical_files = None
        if plan:  # R4 plan-aware staleness
            from apatch.spec_plan import planned_files_for_rk

            canonical_files = planned_files_for_rk(plan, rid)
            if canonical_files and fs:
                ref = fs.get("files") or {}
                drifted = compute_drift(ref, workspace_root)
                for pf in canonical_files:
                    if pf not in ref:
                        drifted.append(pf)
                drifted = sorted(set(drifted))
                if drifted and row.get("state") == "attested":
                    row = apply_file_drift_to_status(
                        row,
                        file_set=fs,
                        drifted=drifted,
                        stale_reason="file_drift",
                    )

        if row.get("state") == "attested" and fs:
            drifted = compute_drift(fs.get("files") or {}, workspace_root)
            if drifted:
                row = apply_file_drift_to_status(
                    row,
                    file_set=fs,
                    drifted=drifted,
                    stale_reason="file_drift",
                )
        elif row.get("state") == "stale":
            if fs:
                drifted = compute_drift(fs.get("files") or {}, workspace_root)
                if drifted:
                    row = apply_file_drift_to_status(
                        row,
                        file_set=fs,
                        drifted=drifted,
                        stale_reason="file_drift",
                    )
                elif not row.get("stale_reason"):
                    row["stale_reason"] = "spec_text_changed"
            elif row.get("stale") and not row.get("stale_reason"):
                row["stale_reason"] = "spec_text_changed"

        if row.get("state") == "pending":
            row.setdefault("drifted", [])
            row.setdefault("files", [])
        rows.append(row)
    return rows


def spec_coverage_from_entries(
    spec: Spec,
    entries: List[Dict[str, Any]],
    workspace_root: str,
) -> Dict[str, Any]:
    rows = coverage_rows(spec, entries, workspace_root)
    summary = {
        "total": len(rows),
        "attested": sum(1 for r in rows if r.get("state") == "attested"),
        "stale": sum(1 for r in rows if r.get("state") == "stale"),
        "pending": sum(1 for r in rows if r.get("state") == "pending"),
        "in_progress": sum(1 for r in rows if r.get("state") == "in_progress"),
    }
    return {
        "ok": True,
        "spec": spec.id,
        "requirements": rows,
        "summary": summary,
    }


def spec_coverage_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    entries, ledger_active = _ledger_entries(target_dir)
    out = spec_coverage_from_entries(parsed, entries, target_dir)
    out["ledger_active"] = ledger_active
    return out


def spec_status_with_coverage(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    """spec_status enriched with file-drift stale semantics (SPEC-COVERAGE-1 R4)."""
    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    entries, ledger_active = _ledger_entries(target_dir)
    rows = coverage_rows(parsed, entries, target_dir)
    buckets: Dict[str, List[str]] = {
        "pending": [],
        "in_progress": [],
        "attested": [],
        "stale": [],
        "blocked": [],
    }
    for r in rows:
        buckets.setdefault(r.get("state", "pending"), []).append(r["id"])
    total = len(rows)
    attested_only = len(buckets["attested"])
    stale_count = len(buckets["stale"])
    return {
        "ok": True,
        "spec": parsed.id,
        "title": parsed.title,
        "source_path": parsed.source_path,
        "spec_content_hash": parsed.content_hash,
        "requirements": rows,
        "summary": {
            "total": total,
            "attested": attested_only,
            "in_progress": len(buckets["in_progress"]),
            "pending": len(buckets["pending"]),
            "stale": stale_count,
            "blocked": len(buckets["blocked"]),
            "percent_complete": round(100.0 * attested_only / total, 1) if total else 0.0,
        },
        "complete": buckets["attested"],
        "in_progress": buckets["in_progress"],
        "pending": buckets["pending"],
        "stale": buckets["stale"],
        "blocked": buckets["blocked"],
        "done": total > 0 and attested_only == total and stale_count == 0,
        "warnings": parsed.warnings,
        "ledger_active": ledger_active,
    }


def spec_coverage_enriched(target_dir: str = ".", **kwargs):
    from apatch.session_state import enrich_tool_response

    root_dir = __import__("os").path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_coverage",
        spec_coverage_workspace(root_dir, **kwargs),
        target_dir=root_dir,
    )
