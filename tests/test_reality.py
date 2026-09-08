"""Reality ledger (RFP-005) — observed reality as the source of truth, domain-agnostic."""
import pytest


def test_reality_record_add_and_load(tmp_path):
    from apatch.reality import add_reality_record, load_reality_records

    rid = add_reality_record(str(tmp_path), summary="login 500 on empty password",
                             source="sentry", kind="bug")
    assert rid and rid.startswith("REC-")
    # same summary+source -> idempotent (same id, no duplicate)
    add_reality_record(str(tmp_path), summary="login 500 on empty password", source="sentry")
    recs = load_reality_records(str(tmp_path))
    assert [r["id"] for r in recs].count(rid) == 1
    assert recs[0]["source"] == "sentry" and recs[0]["kind"] == "bug"


def test_reality_coverage_classifies():
    from apatch.reality import reality_coverage

    records = [
        {"id": "REC-a", "summary": "x"},
        {"id": "REC-b", "summary": "y"},
        {"id": "REC-c", "summary": "z"},
        {"id": "REC-d", "summary": "w", "status": "closed_wontfix"},
    ]
    discharge_map = {"REC-a": ["SPEC-X#R1"], "REC-b": ["SPEC-X#R2"]}
    attested = {"SPEC-X#R1"}
    cov = reality_coverage(records, discharge_map, attested)
    assert cov["covered"] == ["REC-a"]    # discharged by an attested requirement
    assert cov["pending"] == ["REC-b"]    # claimed, but R2 not attested
    assert cov["uncovered"] == ["REC-c"]  # undischarged reality debt
    assert "REC-d" not in (cov["covered"] + cov["pending"] + cov["uncovered"])  # wontfix excluded
    assert cov["ok"] is False


def test_spec_parses_discharges():
    from apatch.spec import parse_spec

    text = (
        "# SPEC-R Title\n\n"
        "## R1 fix login (verify: pytest a.py) (discharges: REC-a, REC-b)\nbody\n\n"
        "## R2 line-form\n\ndischarges: REC-c\n(verify: pytest b.py)\n"
    )
    spec = parse_spec(text)
    assert tuple(spec.requirements[0].discharges) == ("REC-a", "REC-b")
    assert tuple(spec.requirements[1].discharges) == ("REC-c",)


def test_reality_status_workspace_uncovered_then_pending(tmp_path):
    """Shared CLI+MCP assembly: a fresh record is uncovered; a discharging requirement
    (not yet attested) moves it to pending."""
    from apatch.reality import add_reality_record, reality_status_workspace

    rid = add_reality_record(str(tmp_path), summary="checkout 500 on coupon",
                             source="linear", kind="bug")
    cov = reality_status_workspace(str(tmp_path))
    assert rid in cov["uncovered"] and cov["ok"] is False

    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-CART-1.md").write_text(
        f"# SPEC-CART-1 Cart\n\n## R1 fix (verify: pytest x.py) (discharges: {rid})\nbody\n",
        encoding="utf-8")
    cov2 = reality_status_workspace(str(tmp_path))
    # claimed by a requirement, but that requirement is not attested -> pending, not debt
    assert rid in cov2["pending"] and rid not in cov2["uncovered"]
