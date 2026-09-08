"""Tests for SPEC-ADHERENCE-1 (RFP-012)."""

from __future__ import annotations

import pytest

from apatch.plan_adherence import (
    adherence_for_requirement,
    build_attest_adherence,
    compute_deviation,
    spec_adherence_workspace,
)
from apatch.spec_plan import planned_files_for_rk


def _plan():
    return {
        "schema_version": 1,
        "spec": "SPEC-ADH-TEST",
        "requirements": {
            "R7": {
                "files": ["auth/service.py", "auth/models.py"],
                "needles": [],
            }
        },
    }


def test_deviation_computation():
    dev = compute_deviation(
        planned=["auth/service.py", "auth/models.py"],
        touched=["auth/service.py", "auth/models.py", "api/routes.py"],
    )
    assert dev["adherent"] is False
    assert dev["unplanned"] == ["api/routes.py"]
    assert dev["untouched_planned"] == []

    clean = compute_deviation(
        planned=["auth/service.py"],
        touched=["auth/service.py"],
    )
    assert clean["adherent"] is True


def test_attest_embeds_adherence():
    dev = compute_deviation(planned=["a.py"], touched=["a.py", "b.py"])
    block = build_attest_adherence("plan:SPEC-X@v2", dev)
    assert block["plan_id"] == "plan:SPEC-X@v2"
    assert block["adherent"] is False
    assert "b.py" in block["unplanned"]


def test_mcp_spec_adherence_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None
    assert "apatch_spec_adherence" in tm._tools


def test_plan_aware_staleness(tmp_path):
    from apatch.spec_coverage import coverage_rows
    from apatch.spec import parse_spec

    spec = parse_spec(
        """# SPEC-ADH-STALE

> **apatch artifact:** `spec:SPEC-ADH-STALE`

## R1 One

(verify: true)
""",
        spec_id="SPEC-ADH-STALE",
    )
    plan = {
        "requirements": {
            "R1": {"files": ["planned.py", "never_touched.py"], "needles": []},
        }
    }
    (tmp_path / "planned.py").write_text("ok\n", encoding="utf-8")
    (tmp_path / "never_touched.py").write_text("ok\n", encoding="utf-8")
    import hashlib

    h = hashlib.sha256((tmp_path / "planned.py").read_bytes()).hexdigest()
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "files": {"planned.py": {"sha256": h}},
                "artifacts": [{"kind": "spec", "id": "SPEC-ADH-STALE#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a",
            "payload": {
                "artifacts": [{"kind": "spec", "id": "SPEC-ADH-STALE#R1"}],
            },
        },
    ]
    rows = coverage_rows(spec, entries, str(tmp_path), plan=plan)
    r1 = next(r for r in rows if r["id"] == "R1")
    assert r1["state"] in ("attested", "stale")

    (tmp_path / "planned.py").write_text("changed\n", encoding="utf-8")
    rows2 = coverage_rows(spec, entries, str(tmp_path), plan=plan)
    r1b = next(r for r in rows2 if r["id"] == "R1")
    assert r1b["state"] == "stale"


def test_adherence_for_requirement():
    plan = _plan()
    dev = adherence_for_requirement(
        plan,
        "R7",
        [{"files": {"auth/service.py": {}, "auth/models.py": {}, "api/routes.py": {}}}],
    )
    assert dev["adherent"] is False
    assert "api/routes.py" in dev["unplanned"]

    assert planned_files_for_rk(plan, "R7") == ["auth/models.py", "auth/service.py"]
