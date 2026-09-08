"""Tests for SPEC-CONTRACT-EDGES-1 (RFP-022 Phase 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "build_diagnose"
CLANG_LOG = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")


def test_r1_resolve_empty_when_no_symbol() -> None:
    from apatch.diagnostics.edges import resolve_symbol_edges
    from apatch.diagnostics.schema import normalize_diagnostic

    d = normalize_diagnostic(
        {
            "source": "pytest",
            "type": "test_failure",
            "severity": "error",
            "message": "failed",
            "recommended_action": "fix_forward",
        }
    )
    edges = resolve_symbol_edges(d, str(FIXTURES))
    assert edges["symbols"] == []
    assert edges["files"] == []
    assert edges["requirements"] == []
    assert edges["artifacts"] == []


def test_r2_sparse_omega_missing_member_edges() -> None:
    from apatch.diagnostics.adapters.clang import adapt_clang_log
    from apatch.diagnostics.edges import resolve_symbol_edges

    diags = adapt_clang_log(CLANG_LOG, str(FIXTURES))
    assert len(diags) == 1
    edges = resolve_symbol_edges(diags[0], str(FIXTURES))
    assert "omega_core::SparseOmega::get_concept_name" in edges["symbols"]
    assert "sparse_omega.h" in edges["files"]
    assert any("omega_sensor.cpp" in path for path in edges["files"])


def test_r3_impact_ref_on_python_symbol(tmp_path: Path) -> None:
    from apatch.diagnostics.edges import resolve_symbol_edges
    from apatch.diagnostics.schema import normalize_diagnostic

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "target.py").write_text(
        "def edge_target():\n    return 1\n",
        encoding="utf-8",
    )
    (lib / "caller.py").write_text(
        "from target import edge_target\n\ndef run():\n    return edge_target()\n",
        encoding="utf-8",
    )

    d = normalize_diagnostic(
        {
            "source": "pytest",
            "type": "test_failure",
            "severity": "error",
            "message": "failed",
            "recommended_action": "fix_forward",
            "location": {"symbol": "edge_target"},
        }
    )
    edges = resolve_symbol_edges(d, str(tmp_path))
    impact = edges.get("impact_ref")
    assert impact is not None
    assert impact["target"] == "edge_target"
    assert impact["kind"] == "symbol"
    assert impact["affected_files_count"] >= 1
    assert "edge_target" in edges["symbols"]
    assert "lib/target.py" in edges["files"]


def test_pytest_file_edge_does_not_build_repository_index(tmp_path: Path, monkeypatch) -> None:
    """A parametrized failure must not trigger a repository-wide symbol scan."""
    from apatch.diagnostics import edges as edge_module
    from apatch.diagnostics.schema import normalize_diagnostic

    def fail_index(*_args, **_kwargs):
        raise AssertionError("pytest file edge must not scan the repository")

    monkeypatch.setattr(edge_module, "build_symbol_index", fail_index)
    diagnostic = normalize_diagnostic(
        {
            "source": "pytest",
            "type": "test_failure",
            "severity": "error",
            "message": "failed",
            "recommended_action": "fix_forward",
            "location": {
                "file": "tests/test_widget.py",
                "symbol": "test_answer[param]",
            },
        }
    )

    resolved = edge_module.resolve_symbol_edges(diagnostic, str(tmp_path))

    assert resolved["files"] == ["tests/test_widget.py"]
    assert resolved["symbols"] == ["test_answer[param]"]
    assert "impact_ref" not in resolved


def test_r4_collect_attaches_edges() -> None:
    from apatch.diagnostics.collect import collect_diagnostics

    result = {"ok": False, "error_type": "VERIFY_FAILED", "verify_output": CLANG_LOG}
    diags = collect_diagnostics(
        result,
        str(FIXTURES),
        log_text=CLANG_LOG,
        verify="make",
        session_id="apatch_sess_edges",
        write_artifacts=False,
    )
    assert len(diags) >= 1
    edges = diags[0].get("edges") or {}
    assert edges.get("symbols")
    assert edges.get("files")


def test_r5_sid_block_edge_when_map_present(tmp_path: Path) -> None:
    from apatch.diagnostics.edges import resolve_symbol_edges
    from apatch.diagnostics.schema import normalize_diagnostic

    kmap = {"claim_1": {"type": "paragraph", "hash": "sha256:abc123"}}
    (tmp_path / "knowledge_map.json").write_text(json.dumps(kmap), encoding="utf-8")

    d = normalize_diagnostic(
        {
            "source": "spec",
            "type": "spec_violation",
            "severity": "error",
            "message": "block drift",
            "recommended_action": "fix_forward",
            "location": {"symbol": "claim_1"},
        }
    )
    edges = resolve_symbol_edges(d, str(tmp_path))
    assert "claim_1@sha256:abc123" in edges["artifacts"]
