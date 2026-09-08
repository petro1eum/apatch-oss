"""Tests for R39 barrel rollback, R40 yaml match, R50 index, R52 pipeline."""

import json
import subprocess

import pytest

from apatch.barrel_export import emit_barrel_exports, revert_barrel_exports
from apatch.generate import generate_patches
from apatch.workflows import (
    index_build_workspace,
    index_query_workspace,
    pipeline_run_manifest,
    remove_strip_artifacts_from_results,
)


def test_barrel_revert_removes_added_lines(tmp_path):
    barrel = tmp_path / "index.ts"
    barrel.write_text("export { A } from './a';\n", encoding="utf-8")
    meta = [{
        "module_path": str(tmp_path / "hooks" / "useFoo.ts"),
        "wiring_hints": {"exports": ["useFoo"], "barrel_export": "export { useFoo } from './hooks/useFoo';"},
    }]
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "useFoo.ts").write_text("export function useFoo() {}\n", encoding="utf-8")
    result = emit_barrel_exports(str(barrel), meta)
    assert result["added"]
    assert "useFoo" in barrel.read_text(encoding="utf-8")
    rev = revert_barrel_exports(str(barrel), result["added"])
    assert rev["reverted"]
    assert "useFoo" not in barrel.read_text(encoding="utf-8")
    assert "export { A }" in barrel.read_text(encoding="utf-8")


def test_bundle_rollback_reverts_barrel(tmp_path):
    barrel = tmp_path / "src" / "index.ts"
    barrel.parent.mkdir(parents=True)
    barrel.write_text("", encoding="utf-8")
    added_line = "export { usePlanningHandlers } from './hooks/usePlanningHandlers';"
    barrel.write_text(added_line + "\n", encoding="utf-8")
    report_data = {
        "barrel_export": {
            "barrel_path": str(barrel),
            "added": [added_line],
        }
    }
    file_results = [{"report_data": report_data, "exported_meta": []}]
    removed = remove_strip_artifacts_from_results(file_results)
    assert str(barrel) in removed or barrel.read_text(encoding="utf-8").strip() == ""


def test_generate_yaml_match_alias(tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "m.yaml"
    path.write_text(
        "index_patterns:\n  - logs-*\ntemplate:\n  mappings:\n    properties:\n      foo:\n        type: text\n",
        encoding="utf-8",
    )
    patches = generate_patches(
        find="type: text",
        replace="type: keyword",
        target_dir=str(tmp_path),
        glob_pattern="*.yaml",
        match_mode="yaml",
    )
    assert len(patches) == 1


def test_index_build_and_query(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("class UserModel: pass\n", encoding="utf-8")
    (tmp_path / "app" / "api.py").write_text(
        "from app.models import UserModel\n", encoding="utf-8"
    )
    built = index_build_workspace(str(tmp_path))
    assert built["stats"]["symbols"] >= 1
    hit = index_query_workspace(str(tmp_path), "UserModel")
    assert hit["symbols"]
    assert "app/models.py" in [s["file"] for s in hit["symbols"]]


def test_pipeline_dry_run(git_project):
    patches = git_project / "patches"
    patches.mkdir()
    (git_project / "value.py").write_text("OLD = 1\n", encoding="utf-8")
    plog = patches / "p.jsonl"
    plog.write_text(
        json.dumps({
            "step_index": 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "value.py",
                    "TargetContent": "OLD = 1",
                    "ReplacementContent": "OLD = 2",
                },
            }],
        }) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "kind": "engineering-pipeline",
        "patches_jsonl": "patches/p.jsonl",
        "phases": [
            {"action": "plan"},
            {"action": "apply", "no_trustchain": True},
            {"action": "impact", "target": "value.py", "kind": "file"},
        ],
    }
    mpath = git_project / "pipe.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    result = pipeline_run_manifest(str(mpath), str(git_project), dry_run=True)
    assert result["ok"] is True
    assert len(result["phases"]) == 3


@pytest.fixture
def git_project(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    yield tmp_path
