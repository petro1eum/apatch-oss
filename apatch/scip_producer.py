"""SCIP Phase 2 (RFP-033 A33-G) — produce the .scip index out of band and surface
cross-file reference impact in verify/status output.

Phase 1 (apatch/scip_ingest.py) ingests a .scip and computes impact; this module is the
*producer* (run scip-python) plus the end-to-end glue that feeds the impact engine the
changed symbols (git diff) and the attested requirements' guarded-file symbols. Everything
here is **advisory** — it never marks a requirement stale, blocks attestation, or adds a
hard runtime dependency: when scip-python or the index is absent it degrades to a no-op.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

SCIP_REL = os.path.join(".apatch", "scip", "index.scip")


def scip_python_available() -> bool:
    return shutil.which("scip-python") is not None


def produce_scip_index(root: str, *, timeout: int = 600) -> Dict[str, Any]:
    """Run scip-python out of band → ``.apatch/scip/index.scip``. Graceful: returns
    ``ok: False`` with a note when scip-python is absent or fails; never raises."""
    root = os.path.abspath(root)
    if not scip_python_available():
        return {"ok": False, "available": False, "indexer": "scip-python",
                "note": "scip-python not installed (out-of-band producer) — "
                        "`pip install scip-python`; Phase-1 ingestion still reads any .scip"}
    dest = os.path.join(root, SCIP_REL)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        proc = subprocess.run(
            ["scip-python", "index", "--output", dest, "."],
            cwd=root, capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "available": True, "indexer": "scip-python",
                "note": f"scip-python failed: {exc}"}
    ok = proc.returncode == 0 and os.path.isfile(dest)
    note = "ok" if ok else (proc.stderr or b"").decode("utf-8", "replace")[:300]
    return {"ok": ok, "available": True, "indexer": "scip-python",
            "path": dest if ok else None, "rc": proc.returncode, "note": note}


def changed_symbols_since(root: str, since: str = "HEAD") -> List[Tuple[str, str]]:
    """``[(rel_path, symbol_name)]`` for symbols whose lines changed since ``since``
    (parsed from ``git diff -U0``). Best-effort: returns ``[]`` on any git/parse error."""
    from apatch.symbol_anchor import symbols_for_line_range

    root = os.path.abspath(root)
    out: List[Tuple[str, str]] = []
    seen = set()
    try:
        diff = subprocess.run(
            ["git", "-C", root, "diff", "-U0", since, "--", "*.py"],
            capture_output=True, timeout=30,
        )
        if diff.returncode != 0:
            return out
        text = diff.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return out
    cur_file: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            cur_file = line[6:].strip()
        elif line.startswith("@@") and cur_file:
            try:
                plus = line.split("+", 1)[1].split(" ", 1)[0]  # "c,d"
                parts = plus.split(",")
                start = int(parts[0])
                count = int(parts[1]) if len(parts) > 1 else 1
            except (ValueError, IndexError):
                continue
            if count <= 0:
                count = 1
            ap = os.path.join(root, cur_file)
            if not os.path.isfile(ap):
                continue
            for sym in symbols_for_line_range(ap, start, start + count - 1):
                key = (cur_file, sym)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    return out


def attested_requirement_anchors(
    root: str, spec: Optional[str] = None
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """``{SPEC#Rk: {rel_path: {symbol_name: ""}}}`` for each ATTESTED requirement, from the
    symbols in its guarded files (inferred from the requirement's ``verify`` command).

    An over-approximation (all symbols in the guarded files), which is correct for an
    advisory cross-file warning — it flags "an attested requirement's guarded code
    references the symbol you changed elsewhere"."""
    from apatch.spec import _load_spec, spec_status_workspace
    from apatch.spec_needles_scaffold import infer_target_files_from_verify
    from apatch.symbol_anchor import extract_file_symbols

    root = os.path.abspath(root)
    anchors: Dict[str, Dict[str, Dict[str, str]]] = {}
    if spec:
        spec_ids = [spec]
    else:
        spec_ids = [os.path.basename(p)[:-3]
                    for p in sorted(glob.glob(os.path.join(root, "docs", "specs", "SPEC-*.md")))]
    for sid in spec_ids:
        try:
            parsed = _load_spec(root, spec=sid)
            status = spec_status_workspace(root, spec=sid)
        except Exception:
            continue
        attested = {r["id"] for r in (status.get("requirements") or [])
                    if r.get("state") == "attested"}
        for req in parsed.requirements:
            if req.id not in attested:
                continue
            files = set(infer_target_files_from_verify(req.verify or ""))
            file_syms: Dict[str, Dict[str, str]] = {}
            for rel in files:
                if not rel.endswith(".py"):
                    continue
                ap = os.path.join(root, rel)
                if not os.path.isfile(ap):
                    continue
                syms = extract_file_symbols(ap)
                if syms:
                    file_syms[rel] = {name: "" for name in syms}
            if file_syms:
                anchors[f"{parsed.id}#{req.id}"] = file_syms
    return anchors


def scip_impact_workspace(
    root: str,
    *,
    since: str = "HEAD",
    spec: Optional[str] = None,
    anchors: Optional[Dict[str, Dict[str, Dict[str, str]]]] = None,
    allow_native: bool = True,
) -> Dict[str, Any]:
    """End-to-end advisory impact: which attested requirements reference, cross-file, a
    symbol changed since ``since``. Uses a precise ``.scip`` index when present, else a
    native in-process import-resolved graph (``allow_native``) so it works with no external
    indexer. Advisory only \u2014 never blocks or marks stale; ``model_present: False`` is a
    no-op. Pass ``allow_native=False`` on hot paths (verify) to use only a precomputed .scip."""
    from apatch.scip_ingest import load_scip_model, scip_impacted_requirements

    root = os.path.abspath(root)
    model = load_scip_model(root)
    source = "scip"
    if model is None and allow_native:
        from apatch.native_refs import native_reference_model
        model = native_reference_model(root)
        source = "native"
    if model is None:
        return {"ok": True, "model_present": False, "index_present": False, "advisory": True,
                "impact": [], "note": "no .scip index here (run `apatch scip index`)"}
    changed = changed_symbols_since(root, since)
    req_anchors = anchors if anchors is not None else attested_requirement_anchors(root, spec)
    impact = scip_impacted_requirements(changed, req_anchors, model, root)
    return {"ok": True, "model_present": True, "model_source": source,
            "index_present": source == "scip", "advisory": True, "since": since,
            "changed_symbols": [f"{p}::{s}" for p, s in changed], "impact": impact,
            "note": f"{len(impact)} attested requirement(s) reference a changed symbol "
                    f"(advisory, source={source})"}
