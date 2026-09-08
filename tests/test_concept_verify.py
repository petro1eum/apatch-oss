"""SPEC-CONCEPT-VERIFY-1 — run node/edge invariants for real (RFP-034 §B.3). No-mock."""
import os

import pytest

from apatch.concept_compile import compile_concept_graph
from apatch.concept_verify import verify_concept_invariants

CONCEPTS = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "avatar-layer.md")


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_runs_real_checks_green():
    """R1: the avatar ContributionEvent invariants actually execute — the SCHEMA-LOCKSTEP
    sentinel runs and the economic barrier is asserted — and pass (status green)."""
    pytest.importorskip("avatar_contract")  # cross-repo anchor, absent in CI
    ce = verify_concept_invariants(_graph(), ".")["cpt_contribution_event"]
    assert ce["status"] == "green", ce
    refs = {r["ref"]: r["verdict"] for r in ce["invariants"]}
    assert refs.get("SCHEMA-LOCKSTEP") == "green"
    assert refs.get("economic_barrier") == "green"


def test_r2_no_invariant_is_grey():
    """R2: a concept with no invariants is grey (counted, not green)."""
    assert verify_concept_invariants(_graph(), ".")["cpt_signed_fact"]["status"] == "grey"


def test_r3_unknown_ref_is_broken():
    """R3: an invariant whose ref has no runner is broken — a check that does not exist
    is never silently green (§3.6)."""
    g = compile_concept_graph(
        "<!-- @cid:cpt_x -->\n```concept\nname: X\ninvariants:\n"
        "  - {kind: test, ref: NO_SUCH_CHECK}\n```\nX.\n")
    res = verify_concept_invariants(g, ".")["cpt_x"]
    assert res["status"] == "broken"
    assert res["invariants"][0]["verdict"] == "broken"


def test_r4_failing_check_is_red():
    """R4: a check that fails makes the concept red (with detail) — verify reflects reality."""
    g = compile_concept_graph(
        "<!-- @cid:cpt_y -->\n```concept\nname: Y\ninvariants:\n"
        "  - {kind: test, ref: BOOM}\n```\nY.\n")
    res = verify_concept_invariants(g, ".", checks={"BOOM": lambda td: (False, "boom")})["cpt_y"]
    assert res["status"] == "red"
    assert res["invariants"][0]["verdict"] == "red"
    assert res["invariants"][0]["detail"] == "boom"