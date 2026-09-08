"""SPEC-CONCEPT-VERIFY-1 — RFP-034 §B.3 concept verify: actually RUN the invariants.

`concept_status`/`concept_coverage` ask "is there a checkable invariant"; this asks
"does it pass". Each invariant `ref` is dispatched to a real apatch check (the same
primitives the canon references — edge sentinel, contract assertions, …). An unknown ref
is `broken` (a check that does not exist), never silently green (RFP-034 §3.6).

A concept is green if all its invariants pass, red if any fails, broken if any ref has no
runner (and none failed), grey if it has no invariants.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


def _check_schema_lockstep(target_dir: str) -> Tuple[bool, Any]:
    from apatch.edge_lockstep import contribution_lockstep

    v = contribution_lockstep(target_dir)
    return v["status"] == "green", (v.get("violations") or v["status"])


def _check_economic_barrier(target_dir: str) -> Tuple[bool, Any]:
    from apatch import contribution as emitter

    try:
        from avatar_contract.contribution_event import assert_economic_barrier
    except Exception as exc:  # pragma: no cover
        return False, "avatar-contract unavailable: %s" % exc
    event = emitter.build_event({"session_id": "verify-probe"}, target_dir=target_dir, ledger_rows=[])
    try:
        assert_economic_barrier(event.to_dict())
        return True, "no economic-layer keys in ContributionEvent"
    except Exception as exc:
        return False, str(exc)


# ref -> callable(target_dir) -> (ok, detail). The canon's invariants reference EXISTING
# apatch checks; this registry wires the ones we can run today.
DEFAULT_CHECKS: Dict[str, Callable[[str], Tuple[bool, Any]]] = {
    "SCHEMA-LOCKSTEP": _check_schema_lockstep,
    "economic_barrier": _check_economic_barrier,
}


def verify_concept_invariants(
    graph: Dict[str, Any],
    target_dir: str = ".",
    checks: Optional[Dict[str, Callable[[str], Tuple[bool, Any]]]] = None,
) -> Dict[str, Any]:
    """Run every concept's node + edge invariants and return per-concept verdicts.

    Returns ``{cid: {status, invariants: [{ref, verdict, detail}]}}`` where verdict is
    green / red / broken and status aggregates them (red > broken > green; grey if none).
    """
    checks = DEFAULT_CHECKS if checks is None else checks
    out: Dict[str, Any] = {}
    for cid, node in graph.get("concepts", {}).items():
        invs: List[Any] = list(node.get("invariants") or [])
        for rel in (node.get("relations") or []):
            if isinstance(rel, dict) and rel.get("invariant"):
                invs.append({"kind": "edge", "ref": rel["invariant"]})
        rows: List[Dict[str, Any]] = []
        for inv in invs:
            ref = inv.get("ref") if isinstance(inv, dict) else inv
            fn = checks.get(ref)
            if fn is None:
                rows.append({"ref": ref, "verdict": "broken",
                             "detail": "no runner registered for this ref"})
            else:
                ok, detail = fn(target_dir)
                rows.append({"ref": ref, "verdict": "green" if ok else "red", "detail": detail})
        if not rows:
            status = "grey"
        elif any(r["verdict"] == "red" for r in rows):
            status = "red"
        elif any(r["verdict"] == "broken" for r in rows):
            status = "broken"
        else:
            status = "green"
        out[cid] = {"status": status, "invariants": rows}
    return out