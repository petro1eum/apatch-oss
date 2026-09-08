"""Canonical feedback-status vocabulary — the single source of truth.

Every status that may appear in ``tests/regressions/<slug>_feedback_triage.tsv``
is defined here together with its lifecycle role. ``slug_close`` (replay
verdicts), ``slug_cockpit`` (open/closed counters), and ``slug_feedback_lint``
(consumer-repo validation) import these sets, so membership can no longer
drift between tools.

Vocabulary growth is deliberate: adding a status here is a reviewed
vocabulary change, not a per-bug improvisation. Legacy spellings live in
``STATUS_ALIASES`` and are normalized, never silently accepted as new words.
"""

from __future__ import annotations

from typing import Any


# Verdicts slug_close replay may suggest for a triage row.
SUGGESTED_STATUSES = {
    "catalog_gap",
    "fixed",
    "needs_review",
    "open_runtime_bug",
    "other_slug",
}

# Human closure judgments replay must not overturn when the row carries no
# positive oracle (expected_jde/expected_top).
OWNER_PRESERVED_STATUSES = {
    "accepted_analog",
    "accepted_brand_fallback",
    "accepted_feedback",
    "closed_wontfix",
    "duplicate",
    "fixed_exact",
    "invalid_feedback",
    "needs_clarification",
    "needs_review",
    "not_ours",
}

# Preserved even against a failing approved must_find oracle.
STRICT_OWNER_PRESERVED_STATUSES = {
    "accepted_feedback",
    "closed_wontfix",
    "duplicate",
}

# Statuses that count as closed in cockpit counters. ambiguous_query and
# needs_clarification are owner judgments about insufficient query identity;
# the latter requires an observable clarification outcome in the consumer.
CLOSED_FEEDBACK_STATUSES = {
    "accepted_analog",
    "accepted_brand_fallback",
    "accepted_feedback",
    "ambiguous_query",
    "catalog_gap",
    "closed_wontfix",
    "duplicate",
    "fixed",
    "fixed_exact",
    "invalid_feedback",
    "needs_clarification",
    "noise",
    "not_ours",
    "not_a_bug",
    "other_slug",
    "positive_feedback",
    "routing_non_goal",
    "wontfix",
}

# Closed by the owner rather than proven by replay; cockpit treats the CURRENT
# status as terminal even when replay suggests something open.
OWNER_TERMINAL_STATUSES = CLOSED_FEEDBACK_STATUSES - {"fixed"}

# Intake statuses stamped by feedback ingestion before human triage.
INTAKE_STATUSES = {"needs_human_review"}

# Pending acceptance obligations carried by triage rows: slug_close's
# _legacy_note_oracle treats a 'must_find' row as the acceptance oracle, so
# the status is operative vocabulary, not drift. must_find_brand_soft is the
# owner's opt-in: the brand may be soft-dropped ONLY observably
# (fallback_dropped_filters stage=brand) with the technical identity intact.
OBLIGATION_STATUSES = {"must_find", "must_find_brand_soft"}

CANONICAL_STATUSES = (
    CLOSED_FEEDBACK_STATUSES
    | SUGGESTED_STATUSES
    | OWNER_PRESERVED_STATUSES
    | INTAKE_STATUSES
    | OBLIGATION_STATUSES
)

# Status column vocabulary of tests/regressions/feedback_approved_contract.tsv.
APPROVED_CONTRACT_STATUSES = {"catalog_gap", "must_find", "must_find_brand_soft"}

# Legacy spellings -> canonical. The feedback lint proposes normalization
# needles for these; a new alias requires the same review as a new status.
STATUS_ALIASES = {
    "neighbor_slug": "other_slug",
}


def canonical_status(value: Any) -> str:
    """Normalize a raw TSV status cell to its canonical spelling."""
    norm = str(value or "").strip().lower()
    return STATUS_ALIASES.get(norm, norm)


def is_canonical_status(value: Any) -> bool:
    """True when the (alias-resolved) status belongs to the canonical set."""
    return canonical_status(value) in CANONICAL_STATUSES
