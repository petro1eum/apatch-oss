"""Tests for RFP→SPEC coverage (RFP-023 / SPEC-RFP-COVERAGE-1)."""

from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("apatch.rfp_coverage", reason="rfp_coverage module not installed yet")

from apatch.rfp_coverage import (
    parse_rfp_acceptance,
    parse_spec_traceability,
    parse_spec_requirement_ids,
    rfp_lint,
    rfp_lint_workspace,
    rfp_spec_coverage,
    rfp_spec_coverage_workspace,
    sibling_spec_waiver_target,
    spec_has_rfp_traceability,
)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "rfp_coverage")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_r1_parse_rfp_acceptance():
    text = _read("RFP-TEST-1.md")
    out = parse_rfp_acceptance(text)
    assert out["ok"] is True
    ids = [r["id"] for r in out["rows"]]
    assert ids == ["T1-A", "T1-B", "T1-C"]


def test_r2_parse_spec_traceability():
    text = _read("SPEC-TEST-1.md")
    out = parse_spec_traceability(text)
    assert out["ok"] is True
    assert out["map"]["T1-A"]["spec_rk"] == "R1"


def test_r3_coverage_must_gap_and_waiver():
    rfp = _read("RFP-TEST-1.md")
    ok = rfp_spec_coverage(rfp, _read("SPEC-TEST-1.md"), rfp_id="RFP-TEST-1", spec_id="SPEC-TEST-1")
    assert ok["passed"] is True
    bad = rfp_spec_coverage(rfp, _read("SPEC-GAP-1.md"), rfp_id="RFP-TEST-1", spec_id="SPEC-GAP-1")
    assert bad["passed"] is False
    assert any(g["id"] == "T1-C" for g in bad["gaps"])


def test_r4_rfp_lint_missing_acceptance():
    out = rfp_lint("# RFP-999 — No table\n")
    assert out["passed"] is False


def test_r5_fixture_pair():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cov = rfp_spec_coverage_workspace(root, rfp="RFP-TEST-1", spec="SPEC-TEST-1")
    assert cov["passed"] is True


def test_r0_self_coverage_rfp_023():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-023-rfp-spec-coverage.md"), encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", "SPEC-RFP-COVERAGE-1.md"), encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-023", spec_id="SPEC-RFP-COVERAGE-1")
    assert out["passed"] is True


def test_r7_spec_run_blocks_on_coverage_gap(tmp_path):
    from apatch import spec_run

    fx = FIXTURES
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    shutil.copy(os.path.join(fx, "SPEC-GAP-1.md"), docs / "SPEC-GAP-1.md")
    shutil.copy(os.path.join(fx, "RFP-TEST-1.md"), tmp_path / "docs" / "RFP-TEST-1.md")
    out = spec_run.spec_run_workspace(
        str(tmp_path), spec="SPEC-GAP-1", dry_run=True, skip_lint=True, rfp="RFP-TEST-1"
    )
    assert out.get("ok") is False or "coverage" in str(out.get("error", "")).lower()


def test_spec_has_rfp_traceability():
    assert spec_has_rfp_traceability(_read("SPEC-TEST-1.md"))


def test_unknown_spec_rk_fails_coverage():
    rfp = _read("RFP-TEST-1.md")
    spec = _read("SPEC-TEST-1.md").replace("| T1-C | R2 | covered |", "| T1-C | R99 | covered |")
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-TEST-1", spec_id="SPEC-TEST-1")
    assert out["passed"] is False
    assert any(e.get("code") == "unknown_spec_rk" for e in out.get("errors") or [])


def test_sibling_spec_waiver_target():
    assert sibling_spec_waiver_target("waiver: implemented in SPEC-CONTRACT-EDGES-1") == "SPEC-CONTRACT-EDGES-1"
    assert sibling_spec_waiver_target("waiver: deferred to v2") is None


def test_rfp_022_single_and_aggregate():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    specs = [
        "SPEC-DIAGNOSTIC-GRAPH-1",
        "SPEC-CONTRACT-EDGES-1",
        "SPEC-KNOWLEDGE-GRAPH-1",
    ]
    for sid in specs:
        cov = rfp_spec_coverage_workspace(root, rfp="RFP-022", spec=sid)
        assert cov["passed"] is True, f"{sid} failed: {cov.get('gaps')} {cov.get('errors')}"
    agg = rfp_spec_coverage_workspace(root, rfp="RFP-022", specs=",".join(specs))
    assert agg.get("mode") == "aggregate"
    assert agg["passed"] is True
    assert len(agg.get("covered") or []) == 11
    assert len(agg.get("gaps") or []) == 0


def test_parse_spec_requirement_ids_from_fixture():
    spec = _read("SPEC-TEST-1.md")
    ids = parse_spec_requirement_ids(spec, "SPEC-TEST-1")
    assert ids == {"R1", "R2"}
