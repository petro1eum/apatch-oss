"""Tests for SPEC-INTERFERENCE-3 R1 — MVCC validity on interference reports."""

from __future__ import annotations

import json
from pathlib import Path

from apatch.spec_interference import (
    attach_mvcc_fields,
    classify_data_domains,
    compute_input_hashes,
    compute_interference_validity,
    spec_interference_from_data,
    spec_schedule_workspace,
)


def _registry_needles(tmp_path: Path, spec_id: str, *, find: str, replace: str) -> None:
    reg_dir = tmp_path / ".apatch" / "specs"
    reg_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": spec_id,
        "requirements": {
            "R1": {
                "needles": [
                    {
                        "action": "replace",
                        "target_file": "src/shared.py",
                        "find_text": find,
                        "replace_text": replace,
                        "match_mode": "literal",
                    }
                ]
            }
        },
    }
    (reg_dir / f"{spec_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_input_hashes_and_validity(tmp_path: Path):
    _registry_needles(tmp_path, "SPEC-A", find="ALPHA", replace="BETA")
    _registry_needles(tmp_path, "SPEC-B", find="GAMMA", replace="DELTA")
    src = tmp_path / "src"
    src.mkdir()
    (src / "shared.py").write_text("ALPHA\nGAMMA\n", encoding="utf-8")

    report = spec_interference_from_data(
        str(tmp_path),
        ["SPEC-A", "SPEC-B"],
        entries=[],
        include_attested=False,
        include_planned=True,
    )

    assert report["ok"] is True
    assert "computed_at" in report
    assert report["computed_at"].endswith("+00:00")
    assert set(report["input_hashes"]) == {"SPEC-A", "SPEC-B"}
    assert report["input_hashes"]["SPEC-A"].startswith("sha256:")
    assert report["validity"] == "planned_only"
    assert report["data_domains"]["SPEC-A"] == ["planned"]
    assert report["data_domains"]["SPEC-B"] == ["planned"]

    # Stable hash for unchanged needles
    again = spec_interference_from_data(
        str(tmp_path),
        ["SPEC-A", "SPEC-B"],
        entries=[],
        include_attested=False,
        include_planned=True,
    )
    assert again["input_hashes"] == report["input_hashes"]


def test_stale_when_previous_hashes_differ(tmp_path: Path):
    needles_by_spec = {
        "SPEC-A": [{"requirement": "R1", "target_file": "a.py", "find_text": "x", "replace_text": "y"}],
    }
    data_sources = {"SPEC-A": ["registry"]}
    live = compute_input_hashes(needles_by_spec)
    stale_report = attach_mvcc_fields(
        {"ok": True, "warnings": []},
        needles_by_spec=needles_by_spec,
        data_sources=data_sources,
        previous_input_hashes={"SPEC-A": "sha256:deadbeef"},
    )
    assert stale_report["validity"] == "stale"
    assert stale_report["recommended_action"] == "re_run_interference"
    assert any("input_hashes_changed" in w for w in stale_report["warnings"])

    current = compute_interference_validity(
        classify_data_domains(data_sources),
        input_hashes=live,
        previous_input_hashes=live,
    )
    assert current == "planned_only"


def test_schedule_propagates_mvcc(tmp_path: Path):
    _registry_needles(tmp_path, "SPEC-A", find="ONE", replace="TWO")
    _registry_needles(tmp_path, "SPEC-B", find="THREE", replace="FOUR")
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    for sid in ("SPEC-A", "SPEC-B"):
        (docs / f"{sid}.md").write_text(
            f"# {sid}\n\n> **apatch artifact:** `spec:{sid}`\n\n## R1 t\n\n(verify: true)\n",
            encoding="utf-8",
        )
    src = tmp_path / "src"
    src.mkdir()
    (src / "shared.py").write_text("ONE\nTHREE\n", encoding="utf-8")

    out = spec_schedule_workspace(str(tmp_path), specs=["SPEC-A", "SPEC-B"])
    assert out["ok"] is True
    sched = out["schedule"]
    assert sched["interference_validity"] == "planned_only"
    assert "SPEC-A" in sched["input_hashes"]
    assert sched["computed_at"]
