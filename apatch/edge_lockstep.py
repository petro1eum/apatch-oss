"""SPEC-EDGE-LOCKSTEP-1 — first edge invariant (RFP-034 §3.6/§3.9 dogfood).

Edge invariant ``SCHEMA-LOCKSTEP`` over the concept ``cpt_contribution_event``: the
Avatar Architecture Canon Rule 1 demands ONE ``ContributionEvent`` contract — "not two
schemas that match by convention". The contract lives in code three times:

  - ``apatch/contribution.py``                                 (Layer 1 emitter)
  - ``avatar_contract/contribution_event.py`` (avatar-contract) (canonical schema)
  - HC_Tracker SQLAlchemy mirror                                (cross-repo, later layer)

This guards the first two stay in lockstep. It is RFP-034's "new muscle": a check over
a RELATIONSHIP between two code anchors, not over one. It reuses the contract's own
published vocabulary (``SCHEMA_VERSION`` / ``KINDS`` / ``TRUST_LEVELS``) as the oracle —
no new predicate language (RFP-034 non-goal).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

EDGE = "SCHEMA-LOCKSTEP"
CONCEPT = "cpt_contribution_event"
ANCHORS = [
    "apatch/contribution.py:ContributionEvent",
    "avatar-contract/avatar_contract/contribution_event.py:ContributionEvent",
]

# Third copy (cross-repo, optional): HC_Tracker's SQLAlchemy persistence mirror.
TRACKER_ANCHOR = "HC_Tracker/backend/app/models.py:ContributionEvent"

# Core fields the persistence mirror MUST carry under the contract's own names.
CORE_REQUIRED = frozenset({
    "schema_version", "event_id", "idempotency_key", "avatar_id",
    "kind", "source", "trust_level",
})


def lockstep_verdict(
    emitter_version: int,
    contract_version: int,
    kind: str,
    trust_level: str,
    kinds: Iterable[str],
    trust_levels: Iterable[str],
) -> Dict[str, Any]:
    """Pure comparison core — no imports, fully testable.

    A relationship invariant: an event the emitter actually produces must speak the
    contract's *current* vocabulary (not a tolerated legacy alias).
    """
    kinds = set(kinds)
    trust_levels = set(trust_levels)
    violations: List[str] = []
    if emitter_version != contract_version:
        violations.append(
            "schema_version drift: emitter={} contract={}".format(emitter_version, contract_version))
    if kind not in kinds:
        violations.append(
            "kind drift: emitter emits {!r}, contract vocab is {}".format(kind, sorted(kinds)))
    if trust_level not in trust_levels:
        violations.append(
            "trust_level drift: emitter emits {!r}, contract allows {}".format(
                trust_level, sorted(trust_levels)))
    return {"ok": not violations, "status": "green" if not violations else "red",
            "violations": violations}


def tracker_columns(models_text: str, class_name: str = "ContributionEvent"):
    """Statically extract a SQLAlchemy model's mapped column names from source text.

    A pure read of the class body — NO import of the HC app (cross-repo, heavy).
    Returns a set of column names, or None if the class is not found.
    """
    import re

    m = re.search(r"^class\s+" + re.escape(class_name) + r"\b.*?:", models_text, re.M)
    if not m:
        return None
    body = models_text[m.end():]
    nxt = re.search(r"^class\s+\w+", body, re.M)
    if nxt:
        body = body[: nxt.start()]
    return set(re.findall(r"^\s+(\w+):\s*Mapped\[", body, re.M))


def tracker_lockstep(models_text: str) -> Dict[str, Any]:
    """Third-anchor edge check (pure, over SQLAlchemy source text): the persistence
    mirror must carry the contract's CORE fields under the contract's own names.

    Catches field-name drift (e.g. ``kind`` persisted as ``shape``) and dropped fields
    (e.g. a missing ``schema_version`` column). Returns green / red (with violations) /
    broken (model not found) — never silently green.
    """
    cols = tracker_columns(models_text)
    if cols is None:
        return {"status": "broken",
                "violations": ["ContributionEvent model not found in tracker models"]}
    violations = []
    for fld in sorted(CORE_REQUIRED - cols):
        if fld == "kind" and "shape" in cols:
            violations.append(
                "field-name drift: contract 'kind' is persisted as 'shape' in the tracker mirror")
        else:
            violations.append("missing core field: tracker mirror has no '{}' column".format(fld))
    return {"ok": not violations, "status": "green" if not violations else "red",
            "violations": violations, "tracker_columns": sorted(cols)}


def contribution_lockstep(target_dir: str = ".") -> Dict[str, Any]:
    """Live SCHEMA-LOCKSTEP edge invariant between the two ContributionEvent anchors.

    Returns a status dict: ``green`` (in lockstep), ``red`` (drifted, with
    ``violations``), or ``broken`` (an anchor is unreachable — never silently green).
    """
    from apatch import contribution as emitter

    try:
        from avatar_contract.contribution_event import (
            SCHEMA_VERSION as CONTRACT_VERSION,
            KINDS,
            TRUST_LEVELS,
        )
    except Exception as exc:  # missing anchor => broken, not green (silence forbidden)
        return {
            "ok": False, "status": "broken", "edge": EDGE, "concept": CONCEPT,
            "anchors": ANCHORS,
            "violations": ["avatar-contract anchor not importable: {}".format(exc)],
        }

    event = emitter.build_event(
        {"session_id": "lockstep-probe", "intent": "schema-lockstep probe"},
        target_dir=target_dir,
        ledger_rows=[],
    )
    emitted_version = event.schema_version
    verdict = lockstep_verdict(
        emitted_version, CONTRACT_VERSION, event.kind, event.trust_level,
        KINDS, TRUST_LEVELS,
    )
    verdict.update({
        "edge": EDGE,
        "concept": CONCEPT,
        "anchors": ANCHORS,
        "schema_version": {"emitter": emitted_version, "contract": CONTRACT_VERSION},
        "probed": {"kind": event.kind, "trust_level": event.trust_level},
    })
    return verdict