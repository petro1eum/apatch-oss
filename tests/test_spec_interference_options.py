"""Tests for SPEC-INTERFERENCE-3 R2 — structural_options on L2 conflicts."""

from __future__ import annotations

import json
from pathlib import Path

from apatch.spec_interference import (
    generate_structural_options,
    l2_patch_conflicts,
    spec_interference_from_data,
)


def test_structural_options_wr_and_ww(tmp_path: Path):
    reg = tmp_path / ".apatch" / "specs"
    reg.mkdir(parents=True)
    needles_a = {
        "id": "SPEC-A",
        "requirements": {
            "R1": {
                "needles": [
                    {
                        "action": "replace",
                        "target_file": "src/shared.py",
                        "find_text": "ALPHA",
                        "replace_text": "BETA",
                        "match_mode": "literal",
                    }
                ]
            }
        },
    }
    needles_b = {
        "id": "SPEC-B",
        "requirements": {
            "R1": {
                "needles": [
                    {
                        "action": "replace",
                        "target_file": "src/shared.py",
                        "find_text": "ALPHA",
                        "replace_text": "GAMMA",
                        "match_mode": "literal",
                    }
                ]
            }
        },
    }
    (reg / "SPEC-A.json").write_text(json.dumps(needles_a), encoding="utf-8")
    (reg / "SPEC-B.json").write_text(json.dumps(needles_b), encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "shared.py").write_text("ALPHA\n", encoding="utf-8")

    report = spec_interference_from_data(
        str(tmp_path),
        ["SPEC-A", "SPEC-B"],
        entries=[],
        include_attested=False,
        include_planned=True,
    )
    wr = [c for c in report["conflicts"] if c["type"] == "write_read"]
    assert wr, "expected WR conflict A destroys B anchor"
    opts = wr[0]["structural_options"]
    assert wr[0]["confidence"] == "structural"
    option_names = {o["option"] for o in opts}
    assert "reorder" in option_names
    assert "rewrite_anchor" in option_names
    assert "merge" in option_names
    reorder = next(o for o in opts if o["option"] == "reorder")
    assert reorder["feasible"] is True
    merge = next(o for o in opts if o["option"] == "merge")
    assert merge["feasible"] == "unknown"


def test_structural_options_ww_split_region(tmp_path: Path):
    content = "line1\nline2\nline3\n"
    fpath = tmp_path / "f.py"
    fpath.write_text(content, encoding="utf-8")
    na = {
        "requirement": "R1",
        "find_text": "line1\nline2",
        "replace_text": "L12",
        "match_mode": "literal",
        "target_file": "f.py",
    }
    nb = {
        "requirement": "R2",
        "find_text": "line2\nline3",
        "replace_text": "L23",
        "match_mode": "literal",
        "target_file": "f.py",
    }
    warnings: list = []
    conflicts, _ = l2_patch_conflicts("SPEC-A", "SPEC-B", [na], [nb], str(tmp_path), warnings)
    ww = [c for c in conflicts if c["type"] == "write_write"]
    assert ww
    opts = generate_structural_options(
        ww[0],
        spec_ids=["SPEC-A", "SPEC-B"],
        wr_edges=[],
        file_content=content,
        needle_a=na,
        needle_b=nb,
    )
    assert any(o["option"] == "split_region" and o["feasible"] is True for o in opts)
