"""SPEC-CONCEPT-MERGE-1 — RFP-034 §B.2/§3.3 the human-confirmed merge.

Dedup (SPEC-CONCEPT-DEDUP-1) raises merge *candidates*; it never merges. This module is the
other half: a human confirms "A and B are the same — keep B", and the machine records the
decision and *collapses* the graph — drops the merged node, rewrites every reference to the
survivor, folds the merged node's name/aliases/realized_by into the survivor, and remembers
what was absorbed. The decision is a human-owned store; the collapse is a pure function.

Recording the merge closes the loop: once `A same_as B` is on record, dedup stops raising
the pair, and the rendered graph shows one node, not two.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

STORE_REL = os.path.join(".apatch", "concept_merges.json")


def _store_path(target_dir: str) -> str:
    return os.path.join(target_dir, STORE_REL)


def merges(target_dir: str = ".") -> Dict[str, Dict[str, Any]]:
    """Return ``{dropped_id: {into, by, note}}`` of recorded merges (empty if none)."""
    path = _store_path(target_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh) or {}


def _write(target_dir: str, data: Dict[str, Dict[str, Any]]) -> None:
    path = _store_path(target_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)


def record_merge(dropped: str, into: str, target_dir: str = ".",
                 by: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Record ``dropped same_as into`` (a human decision). Returns the store."""
    if dropped == into:
        raise ValueError("cannot merge a concept into itself")
    data = merges(target_dir)
    data[dropped] = {"into": into, "by": by, "note": note}
    _write(target_dir, data)
    return data


def unmerge(dropped: str, target_dir: str = ".") -> bool:
    """Undo a recorded merge (a human decision). Returns True if one existed."""
    data = merges(target_dir)
    existed = data.pop(dropped, None) is not None
    _write(target_dir, data)
    return existed


def merge_map(target_dir: str = ".") -> Dict[str, str]:
    """``{dropped: into}`` for collapse_graph / dedup exclusion."""
    return {k: v["into"] for k, v in merges(target_dir).items()}


def _survivor(cid: str, mm: Dict[str, str]) -> str:
    seen = set()
    while cid in mm and cid not in seen:
        seen.add(cid)
        cid = mm[cid]
    return cid


def collapse_graph(graph: Dict[str, Any], mm: Dict[str, str]) -> Dict[str, Any]:
    """Collapse merged concepts into their survivors (pure). ``mm`` is ``{dropped: into}``.

    Survivor absorbs the dropped node's name (as alias), aliases and realized_by; every
    relation/claim reference to a dropped node is rewritten to the survivor; self-loops
    created by the merge are dropped; the survivor records ``absorbed: [dropped, ...]``.
    Returns a fresh graph; the input is untouched. ``mm`` empty → returns the input.
    """
    concepts = graph.get("concepts", {})
    claims = graph.get("claims", {})
    if not mm:
        return graph

    out: Dict[str, Any] = {}
    for cid, n in concepts.items():
        if _survivor(cid, mm) == cid:
            out[cid] = {**n,
                        "aliases": list(n.get("aliases") or []),
                        "realized_by": list(n.get("realized_by") or []),
                        "relations": list(n.get("relations") or []),
                        "absorbed": list(n.get("absorbed") or [])}

    for cid, n in concepts.items():
        s = _survivor(cid, mm)
        if s == cid or s not in out:
            continue
        surv = out[s]
        surv["absorbed"].append(cid)
        if n.get("name"):
            surv["aliases"].append(n["name"])
        surv["aliases"] += list(n.get("aliases") or [])
        for rb in n.get("realized_by") or []:
            if rb not in surv["realized_by"]:
                surv["realized_by"].append(rb)
        surv["relations"] += list(n.get("relations") or [])

    for cid, surv in out.items():
        seen = set()
        rewritten: List[Any] = []
        for rel in surv["relations"]:
            if isinstance(rel, dict) and rel.get("to"):
                to = _survivor(rel["to"], mm)
                if to == cid:
                    continue  # self-loop introduced by the merge
                key = (rel.get("rel"), to)
                if key in seen:
                    continue
                seen.add(key)
                rewritten.append({**rel, "to": to})
            else:
                rewritten.append(rel)
        surv["relations"] = rewritten
        surv["aliases"] = sorted({a for a in surv["aliases"] if a})
        surv["absorbed"] = sorted(set(surv["absorbed"]))

    new_claims: Dict[str, Any] = {}
    for sid, c in claims.items():
        about = sorted({_survivor(x, mm) for x in (c.get("about") or [])})
        new_claims[sid] = {**c, "about": about}

    return {"schema_version": graph.get("schema_version", 1),
            "concepts": out, "claims": new_claims, "errors": []}