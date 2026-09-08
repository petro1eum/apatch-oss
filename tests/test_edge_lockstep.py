"""SPEC-EDGE-LOCKSTEP-1 — ContributionEvent schema lockstep (RFP-034 first circle).

No-mock: imports the real apatch emitter and the real avatar-contract schema and
asserts the sentinel's verdict on the *current* tree.
"""
import pytest

# The lockstep edge verifies against the sibling avatar-contract repo. It is not
# pip-installable, so these cross-repo tests self-skip when it is absent (CI).
pytest.importorskip("avatar_contract")

from apatch.edge_lockstep import lockstep_verdict, contribution_lockstep, tracker_lockstep


def test_r1_anchors_and_version():
    """R1: sentinel names edge, concept, both realized_by anchors, and each side's version."""
    out = contribution_lockstep(".")
    assert out["edge"] == "SCHEMA-LOCKSTEP"
    assert out["concept"] == "cpt_contribution_event"
    assert len(out["anchors"]) == 2
    assert "apatch/contribution.py" in out["anchors"][0]
    assert "avatar_contract" in out["anchors"][1]
    assert set(out["schema_version"]) == {"emitter", "contract"}


def test_r2_version_lockstep():
    """R2: the two anchors agree on schema_version (version dimension in lockstep)."""
    out = contribution_lockstep(".")
    assert out["status"] != "broken", out["violations"]
    sv = out["schema_version"]
    assert sv["emitter"] == sv["contract"]
    assert not any("schema_version drift" in v for v in out["violations"])


def test_r3_live_edge_in_lockstep():
    """R3: after the coordinated v2 migration (emitter kind "contribution" -> "fact"),
    the live edge is in lockstep — the sentinel is green, no violations, emitter speaks
    the canonical kind. The loop closed: machine found drift, human fixed, machine green."""
    out = contribution_lockstep(".")
    assert out["status"] == "green", out["violations"]
    assert out["ok"] is True
    assert out["violations"] == []
    assert out["probed"]["kind"] == "fact"


def test_r4_green_when_aligned():
    """R4: verdict discriminates — aligned pair is green, any drifted dimension is red."""
    green = lockstep_verdict(2, 2, "fact", "attested",
                             {"fact", "claim"}, {"claimed", "attested", "verified"})
    assert green["status"] == "green"
    assert green["ok"] is True
    assert green["violations"] == []
    assert lockstep_verdict(1, 2, "fact", "attested", {"fact"}, {"attested"})["status"] == "red"


def test_r5_tracker_third_anchor():
    """R5: the third anchor (HC_Tracker SQLAlchemy mirror) is checked statically, no HC
    import. A mirror that renames kind->shape or drops schema_version is red with named
    violations; an aligned mirror is green. (Live cross-repo wiring is out of scope.)"""
    drifted = (
        "class ContributionEvent(Base):\n"
        "    event_id: Mapped[str] = mapped_column(String)\n"
        "    avatar_id: Mapped[str] = mapped_column(String)\n"
        "    source: Mapped[str] = mapped_column(String)\n"
        "    trust_level: Mapped[str] = mapped_column(String)\n"
        "    shape: Mapped[str] = mapped_column(String)\n"
        "    idempotency_key: Mapped[str] = mapped_column(String)\n"
    )
    out = tracker_lockstep(drifted)
    assert out["status"] == "red"
    assert any("shape" in v for v in out["violations"]), out["violations"]
    assert any("schema_version" in v for v in out["violations"]), out["violations"]

    aligned = (
        "class ContributionEvent(Base):\n"
        "    schema_version: Mapped[int] = mapped_column(Integer)\n"
        "    event_id: Mapped[str] = mapped_column(String)\n"
        "    idempotency_key: Mapped[str] = mapped_column(String)\n"
        "    avatar_id: Mapped[str] = mapped_column(String)\n"
        "    kind: Mapped[str] = mapped_column(String)\n"
        "    source: Mapped[str] = mapped_column(String)\n"
        "    trust_level: Mapped[str] = mapped_column(String)\n"
    )
    assert tracker_lockstep(aligned)["status"] == "green"
    # model absent => broken, never silently green
    assert tracker_lockstep("class Other(Base):\n    x: Mapped[int]\n")["status"] == "broken"