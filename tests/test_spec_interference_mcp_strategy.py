"""MCP strategy param parity (SPEC-INTERFERENCE-5)."""

from __future__ import annotations

import json

import pytest


def _two_spec_fixture(tmp_path):
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


def test_mcp_interference_enriched_ok(tmp_path):
    _two_spec_fixture(tmp_path)
    from apatch.spec_interference import spec_interference_enriched

    out = spec_interference_enriched(
        str(tmp_path),
        specs=["SPEC-A", "SPEC-B"],
        include_attested=False,
    )
    assert out.get("ok") is True


def test_mcp_schedule_strategy_param(tmp_path):
    _two_spec_fixture(tmp_path)
    from apatch.spec_interference import spec_schedule_enriched

    out = spec_schedule_enriched(
        str(tmp_path),
        specs=["SPEC-A", "SPEC-B"],
        include_attested=False,
        strategy="risk_first",
    )
    assert out.get("ok") is True
    assert out.get("strategy") == "risk_first"
