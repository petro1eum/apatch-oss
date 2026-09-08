"""Tests for SPEC-INTERFERENCE-1 (RFP-014 Phase 1)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from apatch.spec_interference import (
    build_conflict_graph,
    compute_risk_score,
    detect_cycles,
    is_wr_conflict,
    is_ww_conflict,
    l1_file_overlaps,
    spec_interference_from_data,
    spec_interference_workspace,
    topological_safe_order,
)


def _ledger(tmp_path: Path, entries):
    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    for i, data in enumerate(entries):
        payload = {
            "value": {
                "tool_id": data["tool_id"],
                "data": data["payload"],
                "signature": data.get("signature", f"sig{i}"),
                "id": data.get("id", f"op-{i}"),
                "timestamp": data.get("timestamp", f"2026-06-10T10:00:{i:02d}Z"),
            }
        }
        (tc_dir / f"entry{i}.json").write_text(json.dumps(payload), encoding="utf-8")


def _overlap_entries(shared_file: str = "src/shared.py"):
    base = {
        "tool_id": "apatch",
        "id": "op-a-m",
        "timestamp": "2026-06-10T10:00:01Z",
        "payload": {
            "action": "apply",
            "applied_patches": 1,
            "governed_session_id": "sess-a",
            "files": {shared_file: {"sha256": "aaa"}},
            "artifacts": [{"kind": "spec", "id": "SPEC-A#R1"}],
        },
    }
    b_mut = {
        "tool_id": "apatch",
        "id": "op-b-m",
        "timestamp": "2026-06-10T10:00:03Z",
        "payload": {
            "action": "apply",
            "applied_patches": 1,
            "governed_session_id": "sess-b",
            "files": {shared_file: {"sha256": "bbb"}},
            "artifacts": [{"kind": "spec", "id": "SPEC-B#R1"}],
        },
    }
    a_att = {
        "tool_id": "apatch_attest",
        "id": "op-a-a",
        "timestamp": "2026-06-10T10:00:02Z",
        "payload": {
            "session_id": "sess-a",
            "artifacts": [{"kind": "spec", "id": "SPEC-A#R1", "content_hash": "sha256:a"}],
        },
    }
    b_att = {
        "tool_id": "apatch_attest",
        "id": "op-b-a",
        "timestamp": "2026-06-10T10:00:04Z",
        "payload": {
            "session_id": "sess-b",
            "artifacts": [{"kind": "spec", "id": "SPEC-B#R1", "content_hash": "sha256:b"}],
        },
    }
    return [base, a_att, b_mut, b_att]


def test_l1_file_overlap():
    entries = _overlap_entries("apatch/spec_coverage.py")
    conflicts = l1_file_overlaps(["SPEC-A", "SPEC-B"], entries)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "file_overlap"
    assert conflicts[0]["file"] == "apatch/spec_coverage.py"


def test_wr_conflict_literal():
    content = "alpha\nbeta\ngamma\n"
    na = {"find_text": "alpha", "replace_text": "ALPHA", "match_mode": "literal"}
    nb = {"find_text": "beta", "replace_text": "BETA", "match_mode": "literal"}
    assert not is_wr_conflict(na, nb, content)
    nb2 = {"find_text": "alpha", "replace_text": "X", "match_mode": "literal"}
    assert is_wr_conflict(na, nb2, content)


def test_ww_mutex_no_cycle_edge():
    content = "import foo\nimport bar\n"
    na = {"find_text": "import foo", "replace_text": "import foo_x", "match_mode": "literal"}
    nb = {"find_text": "import foo", "replace_text": "import foo_y", "match_mode": "literal"}
    assert is_ww_conflict(na, nb, content)
    graph = build_conflict_graph(["SPEC-A", "SPEC-B"], [])
    assert graph == {"SPEC-A": [], "SPEC-B": []}
    assert not detect_cycles(graph)


def test_graph_safe_order_and_risk():
    graph = build_conflict_graph(["SPEC-B", "SPEC-A"], [("SPEC-A", "SPEC-B")])
    assert detect_cycles(graph) == []
    order = topological_safe_order(["SPEC-A", "SPEC-B"], graph)
    assert order.index("SPEC-A") < order.index("SPEC-B")
    risk = compute_risk_score([{"type": "write_read"}], False)
    assert risk == 0.5
    assert compute_risk_score([], True) == 1.0


def test_mcp_spec_interference_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    if tm is None:
        pytest.skip("FastMCP tool manager unavailable")
    assert "apatch_spec_interference" in tm._tools


def test_cli_spec_interference(tmp_path):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    for sid in ("SPEC-A", "SPEC-B"):
        (spec_dir / f"{sid}.md").write_text(
            f"# {sid} — t\n> **apatch artifact:** `spec:{sid}`\n## R1 x\n(verify: true)\n",
            encoding="utf-8",
        )
    _ledger(tmp_path, _overlap_entries("src/x.py"))
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "apatch.cli",
            "spec",
            "interference",
            "--spec",
            "SPEC-A",
            "--spec",
            "SPEC-B",
            "--target-dir",
            str(tmp_path),
            "--json",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("ok") is True
    assert data.get("summary", {}).get("total_conflicts", 0) >= 1


def test_dogfood_ledger_coverage_overlap():
    repo = Path(__file__).resolve().parents[1]
    shared = "apatch/spec_coverage.py"
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-la",
            "timestamp": "2026-06-10T10:00:01Z",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "governed_session_id": "sess-la",
                "files": {shared: {"sha256": "la1"}},
                "artifacts": [{"kind": "spec", "id": "SPEC-LEDGER-ACTOR-1#R2"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-la-a",
            "timestamp": "2026-06-10T10:00:02Z",
            "payload": {
                "session_id": "sess-la",
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-LEDGER-ACTOR-1#R2", "content_hash": "sha256:la"}
                ],
            },
        },
        {
            "tool_id": "apatch",
            "id": "op-cv",
            "timestamp": "2026-06-10T10:00:03Z",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "governed_session_id": "sess-cv",
                "files": {shared: {"sha256": "cv1"}},
                "artifacts": [{"kind": "spec", "id": "SPEC-COVERAGE-1#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-cv-a",
            "timestamp": "2026-06-10T10:00:04Z",
            "payload": {
                "session_id": "sess-cv",
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-COVERAGE-1#R1", "content_hash": "sha256:cv"}
                ],
            },
        },
    ]
    out = spec_interference_from_data(
        str(repo),
        ["SPEC-LEDGER-ACTOR-1", "SPEC-COVERAGE-1"],
        entries,
        level=1,
    )
    assert out["ok"] is True
    assert out["has_cycle"] is False
    assert len(out["safe_order"]) == 2
    overlap = [c for c in out["conflicts"] if c["file"] == shared]
    assert overlap


def test_spec_interference_workspace_requires_two_specs(tmp_path):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC-ONE.md").write_text(
        "# SPEC-ONE — t\n> **apatch artifact:** `spec:SPEC-ONE`\n## R1 x\n(verify: true)\n",
        encoding="utf-8",
    )
    out = spec_interference_workspace(str(tmp_path), specs=["SPEC-ONE"])
    assert out.get("ok") is False
