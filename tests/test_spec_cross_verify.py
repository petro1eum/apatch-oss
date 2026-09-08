"""Tests for SPEC-INTERFERENCE-2 (RFP-014 Phase 2 cross-verify)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from apatch.backup import BackupManager
from apatch.spec_registry import update_spec_registry


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "cross_verify"
MODULE_REL = "shared/module.py"


def _write_registry(tmp_path: Path, spec_id: str, needles: list) -> None:
    manifest = {"requirements": {"R1": {"needles": needles}}}
    update_spec_registry(str(tmp_path), spec_id, manifest)


def _copy_fixture_tree(tmp_path: Path) -> Path:
    import shutil

    dest = tmp_path / "cv"
    shutil.copytree(FIXTURE_ROOT, dest, ignore=shutil.ignore_patterns(".apatch"))
    reg_src = FIXTURE_ROOT / "registry"
    if reg_src.is_dir():
        reg_dest = dest / ".apatch" / "specs"
        reg_dest.mkdir(parents=True, exist_ok=True)
        for f in reg_src.glob("*.json"):
            shutil.copy2(f, reg_dest / f.name)
    return dest


def test_apply_needles_sandboxed(tmp_path):
    from apatch.spec_cross_verify import apply_needles_sandboxed

    mod = tmp_path / "src" / "mod.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("alpha\nbeta\n", encoding="utf-8")
    needles = [
        {
            "target_file": "src/mod.py",
            "find_text": "alpha",
            "replace_text": "ALPHA",
            "match_mode": "literal",
        }
    ]
    out = apply_needles_sandboxed(str(tmp_path), needles)
    assert out["applied"] == ["src/mod.py"]
    assert mod.read_text(encoding="utf-8") == "ALPHA\nbeta\n"
    BackupManager.rollback_session(str(tmp_path), out["session_id"])
    assert mod.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_run_spec_verifies(tmp_path):
    from apatch.spec_cross_verify import run_spec_verifies

    specs_dir = tmp_path / "docs" / "specs"
    specs_dir.mkdir(parents=True)
    (specs_dir / "SPEC-T.md").write_text(
        "# SPEC-T\n\n> **apatch artifact:** `spec:SPEC-T`\n\n"
        "## R1 check\n\n(verify: python3 -c \"import os; assert os.path.isdir('.')\")\n",
        encoding="utf-8",
    )
    results = run_spec_verifies(str(tmp_path), "SPEC-T")
    assert len(results) == 1
    assert results[0]["ok"] is True


def test_cross_verify_rollback(tmp_path):
    from apatch.spec_cross_verify import cross_verify_pair

    root = _copy_fixture_tree(tmp_path)
    _write_registry(
        root,
        "SPEC-CV-SOURCE",
        [
            {
                "target_file": MODULE_REL,
                "find_text": "ORIGINAL",
                "replace_text": "RENAMED",
                "match_mode": "literal",
            }
        ],
    )
    mod = root / "shared" / "module.py"
    before = mod.read_text(encoding="utf-8")
    pair = cross_verify_pair(
        str(root),
        "SPEC-CV-SOURCE",
        "SPEC-CV-VICTIM",
        [
            {
                "target_file": MODULE_REL,
                "find_text": "ORIGINAL",
                "replace_text": "RENAMED",
                "match_mode": "literal",
            }
        ],
        victim_spec_path=str(root / "docs" / "specs" / "SPEC-CV-VICTIM.md"),
    )
    assert pair["rolled_back"] is True
    assert mod.read_text(encoding="utf-8") == before


def test_semantic_conflict_detected(tmp_path):
    from apatch.spec_cross_verify import spec_cross_verify_workspace

    root = _copy_fixture_tree(tmp_path)
    _write_registry(
        root,
        "SPEC-CV-SOURCE",
        [
            {
                "target_file": MODULE_REL,
                "find_text": "ORIGINAL",
                "replace_text": "RENAMED",
                "match_mode": "literal",
            }
        ],
    )
    out = spec_cross_verify_workspace(
        str(root),
        specs=["SPEC-CV-SOURCE", "SPEC-CV-VICTIM"],
        include_attested=False,
    )
    assert out["ok"] is True
    assert len(out["semantic_conflicts"]) >= 1
    assert out["semantic_conflicts"][0]["type"] == "semantic_cross_verify"
    assert out["error_type"] == "SPEC_CROSS_VERIFY_FAILED"
    assert out["recommended_action"] == "refactor_needles"


def test_mcp_spec_cross_verify_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    if tm is None:
        pytest.skip("FastMCP tool manager unavailable")
    assert "apatch_spec_cross_verify" in tm._tools


def test_cli_spec_cross_verify(tmp_path):
    root = _copy_fixture_tree(tmp_path)
    _write_registry(
        root,
        "SPEC-CV-SOURCE",
        [
            {
                "target_file": MODULE_REL,
                "find_text": "ORIGINAL",
                "replace_text": "RENAMED",
                "match_mode": "literal",
            }
        ],
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "apatch.cli",
            "spec",
            "cross-verify",
            "--spec",
            "SPEC-CV-SOURCE",
            "--spec",
            "SPEC-CV-VICTIM",
            "--target-dir",
            str(root),
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("ok") is True
    assert data.get("semantic_conflicts")


def test_fixture_integration(tmp_path):
    from apatch.spec_cross_verify import spec_cross_verify_workspace

    root = _copy_fixture_tree(tmp_path)
    mod = root / "shared" / "module.py"
    before = mod.read_text(encoding="utf-8")
    _write_registry(
        root,
        "SPEC-CV-SOURCE",
        [
            {
                "target_file": MODULE_REL,
                "find_text": "ORIGINAL",
                "replace_text": "RENAMED",
                "match_mode": "literal",
            }
        ],
    )
    out = spec_cross_verify_workspace(
        str(root),
        specs=["SPEC-CV-SOURCE", "SPEC-CV-VICTIM"],
        include_attested=False,
    )
    assert len(out["semantic_conflicts"]) == 1
    assert mod.read_text(encoding="utf-8") == before
