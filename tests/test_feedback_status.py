from apatch.feedback_status import (
    APPROVED_CONTRACT_STATUSES,
    CANONICAL_STATUSES,
    CLOSED_FEEDBACK_STATUSES,
    INTAKE_STATUSES,
    OBLIGATION_STATUSES,
    OWNER_PRESERVED_STATUSES,
    OWNER_TERMINAL_STATUSES,
    STATUS_ALIASES,
    STRICT_OWNER_PRESERVED_STATUSES,
    SUGGESTED_STATUSES,
    canonical_status,
    is_canonical_status,
)


def test_set_invariants_hold():
    # Cockpit terminality is derived, not hand-maintained.
    assert OWNER_TERMINAL_STATUSES == CLOSED_FEEDBACK_STATUSES - {"fixed"}
    # Strict preservation is a subset of general owner preservation.
    assert STRICT_OWNER_PRESERVED_STATUSES < OWNER_PRESERVED_STATUSES
    # Every specialized set is inside the canonical vocabulary.
    for subset in (
        SUGGESTED_STATUSES,
        OWNER_PRESERVED_STATUSES,
        CLOSED_FEEDBACK_STATUSES,
        INTAKE_STATUSES,
        OBLIGATION_STATUSES,
    ):
        assert subset <= CANONICAL_STATUSES
    assert APPROVED_CONTRACT_STATUSES == {"catalog_gap", "must_find", "must_find_brand_soft"}
    # must_find is operative triage vocabulary (slug_close._legacy_note_oracle
    # honors it), so the lint must not flag it as drift.
    assert APPROVED_CONTRACT_STATUSES <= CANONICAL_STATUSES


def test_aliases_resolve_to_canonical_statuses():
    for alias, target in STATUS_ALIASES.items():
        assert alias not in CANONICAL_STATUSES, alias
        assert target in CANONICAL_STATUSES, target
        assert canonical_status(alias) == target


def test_canonical_status_normalizes_case_and_whitespace():
    assert canonical_status(" Neighbor_Slug ") == "other_slug"
    assert canonical_status("FIXED") == "fixed"
    assert canonical_status(None) == ""
    assert canonical_status("") == ""


def test_is_canonical_status():
    assert is_canonical_status("fixed")
    assert is_canonical_status("neighbor_slug")  # via alias
    assert is_canonical_status("ambiguous_query")  # owner-terminal since upstream coverage
    assert is_canonical_status("needs_clarification")
    assert is_canonical_status("accepted_analog")
    assert is_canonical_status("accepted_brand_fallback")
    assert is_canonical_status("fixed_exact")
    assert is_canonical_status("not_ours")
    assert not is_canonical_status("total_junk_status")
    assert not is_canonical_status("")
