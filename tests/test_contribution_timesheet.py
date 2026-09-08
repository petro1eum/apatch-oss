"""Meta: RFP-026 -> SPEC-CONTRIB-TIMESHEET-1 self-coverage (SPEC R0)."""
import os

import pytest

pytest.importorskip("apatch.rfp_coverage")
from apatch.rfp_coverage import rfp_spec_coverage


def test_r0_self_coverage_rfp_026():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-026-contribution-timesheet.md"),
              encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", "SPEC-CONTRIB-TIMESHEET-1.md"),
              encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-026",
                            spec_id="SPEC-CONTRIB-TIMESHEET-1")
    assert out["passed"] is True
    assert not out.get("gaps")