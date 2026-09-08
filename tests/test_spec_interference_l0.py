"""L0 domain tag routing stress tests (RFP-014 Phase 4)."""

from __future__ import annotations

from pathlib import Path

from apatch.spec_interference import spec_interference_from_data, spec_interference_workspace
from apatch.spec_interference_l0 import l0_pair_routing


FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "l0_domain"


def _pair_row(report: dict, spec_a: str, spec_b: str) -> dict:
    for row in report.get("l0_routing", {}).get("pairs") or []:
        key = {row.get("spec_a"), row.get("spec_b")}
        if key == {spec_a, spec_b}:
            return row
    raise AssertionError(f"pair {spec_a} vs {spec_b} not in l0_routing")


def test_l0_pair_routing_orthogonal_domains():
    row = l0_pair_routing(
        "SPEC-A",
        "SPEC-B",
        ["domain:ux", "layer:L1"],
        ["domain:backend", "layer:L3"],
        {"ux_only.py"},
        {"backend_only.py"},
    )
    assert row["action"] == "skip"
    assert row["reason"] == "orthogonal_domains_no_path_overlap"
    assert row["run_l1"] is False
    assert row["run_l2"] is False


def test_l0_pair_routing_ux_backend_shared_path():
    row = l0_pair_routing(
        "SPEC-UX",
        "SPEC-BE",
        ["domain:ux", "layer:L2"],
        ["domain:backend", "layer:L2"],
        {"shared/deal_surface.py"},
        {"shared/deal_surface.py"},
    )
    assert row["action"] == "full"
    assert row["reason"] == "cross_domain_ux_backend_shared_path"
    assert row["run_l1"] is True
    assert row["run_l2"] is True


def test_l0_fixture_orthogonal_no_conflicts():
    out = spec_interference_workspace(
        str(FIXTURE),
        specs=["SPEC-L0-ORTH-UX", "SPEC-L0-ORTH-BE"],
        include_attested=False,
    )
    assert out.get("ok") is True
    row = _pair_row(out, "SPEC-L0-ORTH-UX", "SPEC-L0-ORTH-BE")
    assert row["action"] == "skip"
    assert out["summary"]["total_conflicts"] == 0
    assert out["l0_routing"]["enabled"] is True


def test_l0_fixture_ux_backend_write_write_on_shared_file():
    out = spec_interference_workspace(
        str(FIXTURE),
        specs=["SPEC-L0-UX", "SPEC-L0-BACKEND"],
        include_attested=False,
    )
    assert out.get("ok") is True
    row = _pair_row(out, "SPEC-L0-UX", "SPEC-L0-BACKEND")
    assert row["action"] == "full"
    assert row["reason"] == "cross_domain_ux_backend_shared_path"
    types = {c["type"] for c in out.get("conflicts") or []}
    assert "file_overlap" in types
    assert "write_write" in types
    assert out["summary"]["total_conflicts"] >= 2


def test_l0_planned_only_validity():
    out = spec_interference_from_data(
        str(FIXTURE),
        ["SPEC-L0-UX", "SPEC-L0-BACKEND"],
        entries=[],
        include_attested=False,
        include_planned=True,
    )
    assert out.get("validity") == "planned_only"
    assert out.get("l0_routing", {}).get("enabled") is True
