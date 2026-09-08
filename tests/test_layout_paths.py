"""Tests for SPEC-LAYOUT-1 (RFP-016 Phase 5 — structured .apatch layout)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_paths_flat_and_structured(tmp_path: Path) -> None:
    from apatch.apatch_paths import detect_layout, workspace_paths

    flat_root = tmp_path / "flat"
    (flat_root / ".apatch").mkdir(parents=True)
    assert detect_layout(str(flat_root)) == "flat"
    flat = workspace_paths(str(flat_root))
    assert flat.layout == "flat"
    assert flat.session_state.endswith(".apatch/session_state.json")
    assert flat.artifacts_registry.endswith(".apatch/artifacts.jsonl")
    assert flat.provenance_log.endswith(".apatch/provenance.jsonl")
    assert flat.events.endswith(".apatch/events.jsonl")
    assert flat.backups.endswith(".apatch/backups")

    struct_root = tmp_path / "structured"
    (struct_root / ".apatch" / "state").mkdir(parents=True)
    assert detect_layout(str(struct_root)) == "structured"
    structured = workspace_paths(str(struct_root))
    assert structured.layout == "structured"
    assert structured.session_state.endswith(".apatch/state/session_state.json")
    assert structured.artifacts_registry.endswith(".apatch/registry/artifacts.jsonl")
    assert structured.provenance_log.endswith(".apatch/registry/provenance.jsonl")
    assert structured.events.endswith(".apatch/debug/events.jsonl")
    assert structured.backups.endswith(".apatch/history/backups")


def test_layout_migrate_dry_run(tmp_path: Path) -> None:
    from apatch.layout_migrate import layout_migrate, layout_migrate_plan

    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    (apatch / "session_state.json").write_text("{}", encoding="utf-8")
    (apatch / "events.jsonl").write_text("{}\n", encoding="utf-8")

    plan = layout_migrate_plan(str(tmp_path))
    assert plan["layout_before"] == "flat"
    assert plan["layout_after"] == "structured"
    assert plan["moves"]
    assert any(m["from"] == ".apatch/session_state.json" for m in plan["moves"])
    assert (apatch / "session_state.json").is_file()
    assert (apatch / "events.jsonl").is_file()

    result = layout_migrate(str(tmp_path), dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert (apatch / "session_state.json").is_file()
    assert (apatch / "events.jsonl").is_file()


def test_layout_migrate_apply(tmp_path: Path) -> None:
    from apatch.apatch_paths import detect_layout, workspace_paths
    from apatch.artifact_governance import resolve_registry_paths
    from apatch.layout_migrate import layout_migrate

    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    (apatch / "session_state.json").write_text('{"phase":"idle"}\n', encoding="utf-8")
    (apatch / "artifacts.jsonl").write_text('{"artifact_id":"a1"}\n', encoding="utf-8")
    (apatch / "provenance.jsonl").write_text('{"artifact_id":"a1"}\n', encoding="utf-8")
    backups = apatch / "backups"
    backups.mkdir()
    (backups / "chunk_1.json").write_text("{}", encoding="utf-8")

    result = layout_migrate(str(tmp_path), dry_run=False)
    assert result["ok"] is True
    assert result["layout_after"] == "structured"
    assert result["moved_count"] >= 3

    assert detect_layout(str(tmp_path)) == "structured"
    paths = workspace_paths(str(tmp_path))
    assert Path(paths.session_state).is_file()
    assert not (apatch / "session_state.json").exists()

    art, prov, layout = resolve_registry_paths(str(tmp_path))
    assert layout == "structured"
    assert art.endswith("registry/artifacts.jsonl")
    assert prov.endswith("registry/provenance.jsonl")
    assert Path(art).is_file()
    assert Path(paths.backups, "chunk_1.json").is_file()

    manifest = apatch / "registry" / "layout_migrate.json"
    assert manifest.is_file()
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    assert doc["layout_before"] == "flat"
    assert doc["layout_after"] == "structured"
    assert doc["moves"]


def test_normalize_rel_preserves_leading_dots() -> None:
    """A prefix normalizer must preserve control-plane dot characters."""
    from apatch.apatch_paths import normalize_rel

    assert normalize_rel(".apatch/conformance.json") == ".apatch/conformance.json"
    assert normalize_rel("./.apatch/conformance.json") == ".apatch/conformance.json"
    assert normalize_rel(".github/workflows/ci.yml") == ".github/workflows/ci.yml"
    assert normalize_rel("./src/app.py") == "src/app.py"
    assert normalize_rel("././src/app.py") == "src/app.py"
    assert normalize_rel("src\\app.py") == "src/app.py"
    assert normalize_rel("/abs/leading.py") == "abs/leading.py"
    assert normalize_rel("plain.py") == "plain.py"
