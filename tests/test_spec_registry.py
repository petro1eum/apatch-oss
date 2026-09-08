"""Tests for spec registry (RFP-014 Phase 1.5)."""

from __future__ import annotations

import json
from pathlib import Path

from apatch.spec_registry import load_spec_registry, needles_sha256, update_spec_registry


def test_registry_roundtrip(tmp_path):
    manifest = {
        "spec": "SPEC-REGTEST",
        "requirements": {
            "R1": {"needles": [{"action": "replace", "target_file": "a.py", "find_text": "x", "replace_text": "y"}]}
        },
    }
    out = update_spec_registry(str(tmp_path), "SPEC-REGTEST", manifest, source_path="docs/specs/SPEC-REGTEST.md")
    assert out["ok"] is True
    loaded = load_spec_registry(str(tmp_path), "SPEC-REGTEST")
    assert loaded is not None
    assert loaded["id"] == "SPEC-REGTEST"
    assert loaded["requirements"]["R1"]["needles"][0]["find_text"] == "x"
    assert loaded["last_needles_sha256"] == needles_sha256(manifest)
    reg_path = Path(tmp_path) / ".apatch" / "specs" / "SPEC-REGTEST.json"
    assert reg_path.is_file()
    data = json.loads(reg_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_remember_spec_preserves_needles(tmp_path):
    from apatch.spec import _load_spec, parse_spec_file

    specs_dir = tmp_path / "docs" / "specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "SPEC-MERGE.md"
    spec_path.write_text(
        "# SPEC-MERGE\n\n> **apatch artifact:** `spec:SPEC-MERGE`\n\n## R1 x\n\n(verify: python3 -c \"pass\")\n",
        encoding="utf-8",
    )
    manifest = {
        "requirements": {
            "R1": {"needles": [{"target_file": "a.py", "find_text": "x", "replace_text": "y", "match_mode": "literal"}]}
        }
    }
    update_spec_registry(str(tmp_path), "SPEC-MERGE", manifest)
    _load_spec(str(tmp_path), spec="SPEC-MERGE")
    loaded = load_spec_registry(str(tmp_path), "SPEC-MERGE")
    assert loaded is not None
    assert loaded["requirements"]["R1"]["needles"][0]["find_text"] == "x"
    assert loaded.get("source_path")
