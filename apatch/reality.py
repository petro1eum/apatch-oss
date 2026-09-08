"""Reality ledger (RFP-005 §reality) — observed reality as the source of truth.

apatch is universal: a *reality record* is any externally-observed fact a project must
address — a bug report, an incident, a support ticket, a piece of user feedback, a
failing production trace, a compliance finding. The ledger is append-only; spec
requirements **discharge** records. Coverage then becomes "how much of observed reality
is closed by a green gate", not internal prose completeness — so a new bug report is
*undischarged debt by construction* until some requirement claims it, and a gate that
drifts from meaning shows up as rising ``uncovered`` reality. Reality does not drift to
match the spec, so it is the better anti-drift anchor.

This module is domain-agnostic: ``source`` and ``kind`` are free-form strings.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

REALITY_REL = os.path.join(".apatch", "reality.jsonl")
REALITY_CAP = 5000


def reality_log_path(root: str = ".") -> str:
    return os.path.join(os.path.abspath(root), REALITY_REL)


def _record_id(summary: str, source: str) -> str:
    digest = hashlib.sha256((source + "|" + summary).encode("utf-8")).hexdigest()
    return "REC-" + digest[:12]


def load_reality_records(root: str = ".") -> List[Dict[str, Any]]:
    path = reality_log_path(root)
    out: List[Dict[str, Any]] = []
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def add_reality_record(
    root: str,
    *,
    summary: str,
    id: Optional[str] = None,
    source: str = "",
    kind: str = "observation",
    status: str = "open",
) -> Optional[str]:
    """Append an observed-reality record (idempotent by id). Returns the record id.

    ``status`` ``closed_wontfix`` removes a record from the coverage obligation (it is
    an explicit, dated decision that the reality need not be discharged)."""
    if not summary:
        return None
    rid = id or _record_id(summary, source)
    existing = load_reality_records(root)
    by_id = {r.get("id"): r for r in existing}
    rec = {
        "id": rid,
        "summary": summary,
        "source": source,
        "kind": kind,
        "status": status,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    if rid in by_id:
        return rid  # idempotent re-observation — keep the original record
    records = (existing + [rec])[-REALITY_CAP:]
    path = reality_log_path(root)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return rid


def reality_coverage(
    records: List[Dict[str, Any]],
    discharge_map: Dict[str, List[str]],
    attested: Set[str],
) -> Dict[str, Any]:
    """PURE: classify observed reality against discharges + attested requirements.

    ``discharge_map`` — ``{record_id: [requirement_key, ...]}`` (which requirements
    claim to close each record). ``attested`` — the set of requirement_keys that are
    attested (green and signed). A record is:

    * ``covered``   — discharged by at least one ATTESTED requirement;
    * ``pending``   — discharged, but no discharging requirement is attested yet;
    * ``uncovered`` — no requirement discharges it (undischarged reality debt);

    Records with ``status == 'closed_wontfix'`` are excluded from the obligation.
    ``ok`` is true only when nothing is uncovered or pending — i.e. every observed
    reality is closed by a green gate.
    """
    covered: List[str] = []
    pending: List[str] = []
    uncovered: List[str] = []
    for rec in records:
        rid = rec.get("id")
        if not rid:
            continue
        if (rec.get("status") or "open") == "closed_wontfix":
            continue
        claimers = discharge_map.get(rid) or []
        if not claimers:
            uncovered.append(rid)
        elif any(c in attested for c in claimers):
            covered.append(rid)
        else:
            pending.append(rid)
    return {
        "ok": not uncovered and not pending,
        "total": len(covered) + len(pending) + len(uncovered),
        "covered": covered,
        "pending": pending,
        "uncovered": uncovered,
        "counts": {
            "covered": len(covered),
            "pending": len(pending),
            "uncovered": len(uncovered),
        },
    }



def reality_status_workspace(root: str, spec: Optional[str] = None) -> Dict[str, Any]:
    """Coverage of observed reality for a workspace, end to end.

    Loads the ledger, parses each spec's ``discharges`` lines + attested requirements
    (from the TrustChain-derived spec status), and classifies every record into
    covered / pending / uncovered (see ``reality_coverage``). Shared by the CLI
    (``apatch reality status``) and MCP (``apatch_reality(action='status')``)."""
    import glob

    records = load_reality_records(root)
    discharge_map: Dict[str, List[str]] = {}
    attested: Set[str] = set()
    spec_ids = [spec] if spec else [
        os.path.basename(p)[:-3]
        for p in sorted(glob.glob(os.path.join(root, "docs", "specs", "SPEC-*.md")))
    ]
    for sid in spec_ids:
        try:
            from apatch.spec import _load_spec, spec_status_workspace
            parsed = _load_spec(root, spec=sid)
        except Exception:
            continue
        status = spec_status_workspace(root, spec=sid)
        attested_rk = {r["id"] for r in (status.get("requirements") or [])
                       if r.get("state") == "attested"}
        for req in parsed.requirements:
            key = f"{parsed.id}#{req.id}"
            if req.id in attested_rk:
                attested.add(key)
            for rec_id in getattr(req, "discharges", ()) or ():
                discharge_map.setdefault(rec_id, []).append(key)
    cov = reality_coverage(records, discharge_map, attested)
    cov["records"] = records
    cov["discharge_map"] = discharge_map
    return cov
