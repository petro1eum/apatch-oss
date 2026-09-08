"""Tests for apatch_spec_schedule (RFP-014 Phase 1.5)."""

from __future__ import annotations

import pytest


def test_mcp_spec_schedule_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    if tm is None:
        pytest.skip("FastMCP tool manager unavailable")
    assert "apatch_spec_schedule" in tm._tools


def test_schedule_enriched_shape():
    from apatch.spec_interference import spec_schedule_workspace

    out = spec_schedule_workspace(
        ".",
        specs=["SPEC-LEDGER-ACTOR-1", "SPEC-COVERAGE-1"],
        level=1,
    )
    assert out.get("ok") is True
    assert "schedule" in out
    assert "safe_order" in out["schedule"]
    assert "risk_score" in out["schedule"]


def test_enriched_schedule_risk_per_step(tmp_path):
    import json
    from pathlib import Path

    from apatch.spec_interference import spec_schedule_workspace

    reg = tmp_path / ".apatch" / "specs"
    reg.mkdir(parents=True)
    for sid, find, replace in [("SPEC-A", "AAA", "BBB"), ("SPEC-B", "CCC", "DDD")]:
        payload = {
            "id": sid,
            "requirements": {
                "R1": {
                    "needles": [
                        {
                            "action": "replace",
                            "target_file": "src/x.py",
                            "find_text": find,
                            "replace_text": replace,
                            "match_mode": "literal",
                        }
                    ]
                }
            },
        }
        (reg / f"{sid}.json").write_text(json.dumps(payload), encoding="utf-8")
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    for sid in ("SPEC-A", "SPEC-B"):
        (docs / f"{sid}.md").write_text(
            f"# {sid}\n> **apatch artifact:** `spec:{sid}`\n## R1 t\n(verify: true)\n",
            encoding="utf-8",
        )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/x.py").write_text("AAA\nCCC\n", encoding="utf-8")

    out = spec_schedule_workspace(str(tmp_path), specs=["SPEC-A", "SPEC-B"], include_attested=False)
    assert out["ok"] is True
    assert out["schedulable"] is True
    assert out["order"] == out["safe_order"]
    assert out["interference_hash"].startswith("sha256:")
    assert out["interference_validity"] == "planned_only"
    assert out["strategy"] == "safe"
    steps = out["risk_per_step"]
    assert len(steps) == 2
    assert all("spec" in s and "risk" in s and "depends_on" in s for s in steps)

    risk_first = spec_schedule_workspace(
        str(tmp_path), specs=["SPEC-A", "SPEC-B"], include_attested=False, strategy="risk_first"
    )
    assert risk_first["strategy"] == "risk_first"
    assert risk_first["order"]
