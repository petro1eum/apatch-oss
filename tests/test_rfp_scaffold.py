"""Scaffold a SPEC skeleton from an RFP Acceptance table (RFP-023, authoring side).

The scaffold must be *contract-complete by construction*: feeding it back through
rfp_spec_coverage reports zero gaps, and it parses as a valid spec (R0..Rn)."""

_RFP = """# RFP-999 Demo contract

## Acceptance

| Id | Level | Criterion |
|----|-------|-----------|
| A1 | MUST | the widget renders without error |
| A2 | MUST | the export is deterministic |
| A3 | MAY | an optional dark theme exists |
"""


def test_scaffold_is_contract_complete():
    from apatch.rfp_coverage import rfp_spec_coverage, scaffold_spec_from_rfp
    from apatch.spec import parse_spec

    res = scaffold_spec_from_rfp(_RFP, spec_id="SPEC-DEMO-1", rfp_id="RFP-999")
    assert res["ok"] and res["counts"]["scaffolded"] == 3
    md = res["spec_markdown"]
    # parses as a valid spec: R0 (traceability gate) + R1..R3
    parsed = parse_spec(md, spec_id="SPEC-DEMO-1")
    ids = [r.id for r in parsed.requirements]
    assert ids == ["R0", "R1", "R2", "R3"]
    # contract-complete by construction: 0 gaps against the same RFP
    cov = rfp_spec_coverage(_RFP, md, rfp_id="RFP-999", spec_id="SPEC-DEMO-1")
    assert cov["counts"]["gaps"] == 0 and cov["counts"]["covered"] == 3


def test_scaffold_mandatory_only_drops_may():
    from apatch.rfp_coverage import scaffold_spec_from_rfp

    res = scaffold_spec_from_rfp(_RFP, spec_id="SPEC-DEMO-1", rfp_id="RFP-999",
                                 mandatory_only=True)
    assert res["counts"]["scaffolded"] == 2 and res["counts"]["skipped_optional"] == 1
    rfp_ids = [r["rfp_id"] for r in res["requirements"]]
    assert rfp_ids == ["A1", "A2"]  # A3 (MAY) dropped


def test_scaffold_requires_spec_id_and_acceptance():
    from apatch.rfp_coverage import scaffold_spec_from_rfp

    assert scaffold_spec_from_rfp(_RFP, spec_id="")["ok"] is False
    assert scaffold_spec_from_rfp("# RFP-0 no table here", spec_id="SPEC-X")["ok"] is False


def test_scaffold_workspace_resolves_rfp_by_id(tmp_path):
    from apatch.rfp_coverage import scaffold_spec_from_rfp_workspace

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "RFP-999.md").write_text(_RFP, encoding="utf-8")
    out = scaffold_spec_from_rfp_workspace(
        str(tmp_path), rfp="RFP-999", spec_id="SPEC-DEMO-1",
        out_path="docs/specs/SPEC-DEMO-1.md")
    assert out["ok"] and out["counts"]["scaffolded"] == 3
    written = tmp_path / "docs" / "specs" / "SPEC-DEMO-1.md"
    assert written.is_file() and "## R0 RFP traceability gate" in written.read_text(encoding="utf-8")


def test_scaffold_workspace_resolves_explicit_paths(tmp_path):
    from apatch.rfp_coverage import scaffold_spec_from_rfp_workspace

    docs = tmp_path / "docs"
    docs.mkdir()
    rfp_path = docs / "custom-contract.md"
    rfp_path.write_text(_RFP, encoding="utf-8")

    absolute = scaffold_spec_from_rfp_workspace(
        str(tmp_path), rfp=str(rfp_path), spec_id="SPEC-ABSOLUTE-1"
    )
    relative = scaffold_spec_from_rfp_workspace(
        str(tmp_path), rfp="docs/custom-contract.md", spec_id="SPEC-RELATIVE-1"
    )

    assert absolute["ok"] and absolute["rfp_path"] == str(rfp_path)
    assert relative["ok"] and relative["rfp_path"] == str(rfp_path)


def test_apatch_spec_scaffold_mcp_tool(tmp_path):
    import pytest
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "RFP-999.md").write_text(_RFP, encoding="utf-8")
    scaffold = mcp_server.mcp._tool_manager._tools["apatch_spec_scaffold"].fn
    res = scaffold(target_dir=str(tmp_path), rfp="RFP-999", spec="SPEC-DEMO-1",
                   out_path="", mandatory_only=False)
    assert res["ok"] and res["counts"]["scaffolded"] == 3
    assert res["spec_markdown"].startswith("# SPEC-DEMO-1")
