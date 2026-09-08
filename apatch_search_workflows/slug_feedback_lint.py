"""Feedback-status vocabulary lint for consumer triage TSVs.

Read-only. Validates ``tests/regressions/*_feedback_triage.tsv`` (or a single
slug's file) against the canonical vocabulary in ``apatch.feedback_status``,
and the approved-contract status column against ``APPROVED_CONTRACT_STATUSES``.
Known legacy aliases get byte-exact normalization needles for the governed
apply path; unknown statuses and structural problems (missing ``status``
column) stay human findings — the vocabulary only grows through review, never
through silent acceptance.
"""

from __future__ import annotations

import glob
import os
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from apatch_search_workflows.feedback_status import (
    APPROVED_CONTRACT_STATUSES,
    CANONICAL_STATUSES,
    STATUS_ALIASES,
    canonical_status,
)
from apatch_search_workflows.slug_close import RAW_LINE_KEY, _read_tsv


def feedback_lint_workspace(
    target_dir: str = ".",
    *,
    slug: Optional[str] = None,
    triage_path: Optional[str] = None,
    approved_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Lint feedback TSV status vocabulary. Never writes files."""
    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    reg_dir = os.path.join(root, "tests", "regressions")

    if triage_path:
        targets = [_abs(root, triage_path)]
    elif slug:
        targets = [os.path.join(reg_dir, f"{_norm(slug)}_feedback_triage.tsv")]
    else:
        targets = sorted(glob.glob(os.path.join(reg_dir, "*_feedback_triage.tsv")))

    if (slug or triage_path) and targets and not os.path.isfile(targets[0]):
        return {
            "ok": False,
            "error": f"triage file not found: {_rel(root, targets[0])}",
            "error_type": "TRIAGE_NOT_FOUND",
        }

    files: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    needles: List[Dict[str, Any]] = []
    rows_total = 0
    for path in targets:
        if not os.path.isfile(path):
            continue
        report = _lint_triage_file(path, root, findings, needles)
        rows_total += int(report.get("rows") or 0)
        files.append(report)

    # Duplicate raw lines would anchor overlapping regions and abort the whole
    # apatch_generate_batch; one needle per (file, line) converges iteratively.
    seen_keys: set = set()
    deduped: List[Dict[str, Any]] = []
    for needle in needles:
        key = (needle["target_file"], needle["find_text"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(needle)
    needles = deduped

    approved_abs = _abs(
        root,
        approved_path or os.path.join("tests", "regressions", "feedback_approved_contract.tsv"),
    )
    approved_report: Optional[Dict[str, Any]] = None
    if os.path.isfile(approved_abs):
        approved_report = _lint_approved_contract(approved_abs, root, findings)

    findings.sort(key=_finding_sort_key)
    summary = {
        "files_scanned": len(files),
        "rows_scanned": rows_total,
        "alias_rows": sum(int(f.get("alias_rows") or 0) for f in files),
        "unknown_status_values": sum(1 for f in findings if f.get("id") == "unknown_status"),
        "files_missing_status_column": sum(
            1 for f in findings if f.get("id") == "missing_status_column"
        ),
        "quoting_suspect_rows": sum(1 for f in findings if f.get("id") == "quoting_mismatch"),
        "proposed_needles": len(needles),
    }
    return {
        "ok": True,
        "clean": not findings,
        "workspace": root,
        "slug": _norm(slug) if slug else None,
        "summary": summary,
        "files": files,
        "approved_contract": approved_report,
        "findings": findings,
        "proposed_needles": needles,
        "vocabulary": sorted(CANONICAL_STATUSES),
        "aliases": dict(STATUS_ALIASES),
        "agent_next": _agent_next(findings, needles),
    }


def feedback_lint_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    return enrich_tool_response(
        "apatch_slug_feedback_lint",
        feedback_lint_workspace(root, **kwargs),
        target_dir=root,
    )


def _lint_triage_file(
    path: str,
    root: str,
    findings: List[Dict[str, Any]],
    needles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    rel = _rel(root, path)
    fieldnames, rows = _read_tsv(path)
    status_counts: Counter[str] = Counter()
    unknown: Dict[str, List[str]] = {}
    alias_rows = 0
    empty_rows = 0

    if "status" not in fieldnames:
        findings.append(
            {
                "severity": "high",
                "id": "missing_status_column",
                "path": rel,
                "message": f"{rel}: header has no 'status' column ({len(rows)} rows unlintable).",
                "header": fieldnames,
                "recommended_action": "Migrate the file to the canonical triage header before closure work.",
            }
        )
        return {"path": rel, "rows": len(rows), "status_column": False, "status_counts": {}}

    status_idx = fieldnames.index("status")
    quoting_suspects = 0
    for row in rows:
        raw_value = str(row.get("status") or "").strip()
        canonical = canonical_status(raw_value)
        row_id = str(row.get("triage_id") or row.get("id") or "")
        if not raw_value:
            # csv-parsing can let an unterminated quote swallow the tabs that
            # separate later cells; when the naive tab split still shows a
            # status token, the row is mis-parsed, not untriaged.
            naive = str(row.get(RAW_LINE_KEY) or "").split("\t")
            naive_status = naive[status_idx].strip() if status_idx < len(naive) else ""
            if naive_status:
                quoting_suspects += 1
                status_counts["(unparseable)"] += 1
                findings.append(
                    {
                        "severity": "medium",
                        "id": "quoting_mismatch",
                        "path": rel,
                        "triage_id": row_id or (naive[0].strip() if naive else ""),
                        "raw_status": naive_status,
                        "message": (
                            f"{rel}: row {row_id or naive[0].strip()} has status '{naive_status}' on disk "
                            "but csv quoting makes it unparseable; fix the row quoting manually."
                        ),
                    }
                )
                continue
            status_counts["(empty)"] += 1
            empty_rows += 1
            continue
        status_counts[canonical] += 1
        if canonical not in CANONICAL_STATUSES:
            unknown.setdefault(raw_value, []).append(row_id)
            continue
        if _norm(raw_value) != canonical:
            alias_rows += 1
            needle = _alias_needle(path, root, fieldnames, status_idx, row, canonical)
            if needle:
                needles.append(needle)
                findings.append(
                    {
                        "severity": "medium",
                        "id": "alias_status",
                        "path": rel,
                        "triage_id": row_id,
                        "message": f"{rel}: {row_id or '<row>'} status '{raw_value}' is a legacy alias of '{canonical}'.",
                        "recommended_action": "Apply the normalization needle via the governed path.",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "medium",
                        "id": "alias_status_manual",
                        "path": rel,
                        "triage_id": row_id,
                        "message": (
                            f"{rel}: {row_id or '<row>'} status '{raw_value}' aliases '{canonical}' "
                            "but the row quoting prevents a byte-safe needle; fix manually."
                        ),
                    }
                )

    for value, ids in sorted(unknown.items()):
        findings.append(
            {
                "severity": "high",
                "id": "unknown_status",
                "path": rel,
                "value": value,
                "count": len(ids),
                "triage_ids": ids[:8],
                "message": f"{rel}: status '{value}' ({len(ids)} rows) is not in the canonical vocabulary.",
                "recommended_action": (
                    "Rename to a canonical status, or extend apatch.feedback_status via vocabulary review."
                ),
            }
        )
    if empty_rows:
        findings.append(
            {
                "severity": "low",
                "id": "empty_status",
                "path": rel,
                "count": empty_rows,
                "message": f"{rel}: {empty_rows} rows have an empty status (untriaged).",
            }
        )

    return {
        "path": rel,
        "rows": len(rows),
        "status_column": True,
        "status_counts": dict(sorted(status_counts.items())),
        "alias_rows": alias_rows,
        "unknown_values": sorted(unknown),
        "empty_rows": empty_rows,
        "quoting_suspect_rows": quoting_suspects,
    }


def _alias_needle(
    path: str,
    root: str,
    fieldnames: Sequence[str],
    status_idx: int,
    row: Mapping[str, str],
    canonical: str,
) -> Optional[Dict[str, Any]]:
    raw = str(row.get(RAW_LINE_KEY) or "")
    cols = raw.split("\t")
    # Byte-safe only when the naive tab split matches the parsed schema and the
    # status cell is the plain token (no csv quoting surprises in that cell).
    if len(cols) != len(fieldnames) or cols[status_idx].strip() != str(row.get("status") or "").strip():
        return None
    new_cols = list(cols)
    new_cols[status_idx] = canonical
    replacement = "\t".join(new_cols)
    if replacement == raw:
        return None
    return {
        "action": "replace",
        "target_file": _rel(root, path),
        "find_text": raw,
        "replace_text": replacement,
    }


def _lint_approved_contract(
    path: str,
    root: str,
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    rel = _rel(root, path)
    status_counts: Counter[str] = Counter()
    rows = 0
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw in handle.read().splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            cols = raw.split("\t")
            if len(cols) < 5:
                continue
            rows += 1
            status = _norm(cols[-3])
            status_counts[status or "(empty)"] += 1
            if status and status not in APPROVED_CONTRACT_STATUSES:
                findings.append(
                    {
                        "severity": "high",
                        "id": "unknown_approved_status",
                        "path": rel,
                        "value": status,
                        "record_id": cols[0],
                        "message": f"{rel}: {cols[0]} status '{status}' is not a valid approved-contract status.",
                        "recommended_action": f"Use one of: {sorted(APPROVED_CONTRACT_STATUSES)}.",
                    }
                )
    return {"path": rel, "rows": rows, "status_counts": dict(sorted(status_counts.items()))}


def _agent_next(findings: Sequence[Mapping[str, Any]], needles: Sequence[Mapping[str, Any]]) -> str:
    if not findings:
        return "Feedback status vocabulary is clean; no action needed."
    parts: List[str] = []
    if needles:
        parts.append("Apply proposed_needles (alias normalization) via apatch_generate_batch/apatch_remote_task_run.")
    if any(f.get("id") == "unknown_status" for f in findings):
        parts.append(
            "Unknown statuses need a vocabulary decision: rename rows to a canonical status "
            "or extend apatch.feedback_status via review."
        )
    if any(f.get("id") == "missing_status_column" for f in findings):
        parts.append("Migrate legacy triage files to the canonical header.")
    return " ".join(parts) or "Review findings."


def _finding_sort_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
    severity_order = {"high": 0, "medium": 1, "low": 2}
    return (
        severity_order.get(str(item.get("severity") or ""), 3),
        str(item.get("id") or ""),
        str(item.get("path") or ""),
    )


def _abs(root: str, path: str) -> str:
    expanded = os.path.expanduser(path)
    return expanded if os.path.isabs(expanded) else os.path.join(root, expanded)


def _rel(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()
