"""Tests for R36–R38 recommendation implementations."""

import json

import pytest

from apatch.generate import generate_patches
from apatch.json_mapping import (
    apply_mapping_semantic_patch,
    apply_yaml_semantic_patch,
    looks_like_mapping_document,
)
from apatch.matcher import ASTMatcher
from apatch.workflows import phase_run_bundle
from click.testing import CliRunner

from apatch.cli import cli
from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

MAPPING_YAML = """index_patterns:
  - logs-*
template:
  mappings:
    dynamic: true
    properties:
      message:
        type: text
      level:
        type: keyword
"""

MAPPING_JSON = """{
  "index_patterns": ["logs-*"],
  "template": {
    "mappings": {
      "dynamic": true,
      "properties": {
        "message": { "type": "text" },
        "level": { "type": "keyword" }
      }
    }
  }
}
"""


@pytest.fixture
def pyyaml():
    return pytest.importorskip("yaml")


def test_yaml_mapping_detection(pyyaml):
    assert looks_like_mapping_document(MAPPING_YAML)


def test_yaml_semantic_patch_property_block(pyyaml, tmp_path):
    path = tmp_path / "template.yaml"
    path.write_text(MAPPING_YAML, encoding="utf-8")
    old = "type: text"
    new = "type: keyword"
    ok, out = apply_yaml_semantic_patch(path.read_text(encoding="utf-8"), old, new)
    assert ok is True
    assert "keyword" in out
    assert "text" not in out or "context" in out


def test_mapping_semantic_patch_yaml_via_matcher(pyyaml, tmp_path):
    path = tmp_path / "index-template.yml"
    path.write_text(MAPPING_YAML, encoding="utf-8")
    matcher = ASTMatcher(str(path))
    # JSON-shaped fragment — not a literal substring in the YAML file
    result = matcher.evaluate('{ "type": "text" }', '{ "type": "keyword" }')
    assert result.success is True
    assert result.strategy == "json-semantic"
    assert "keyword" in result.content


def test_generate_json_match_mode_on_yaml(pyyaml, tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text(MAPPING_YAML, encoding="utf-8")
    patches = generate_patches(
        find="type: text",
        replace="type: keyword",
        target_dir=str(tmp_path),
        glob_pattern="*.yaml",
        match_mode="json",
    )
    assert len(patches) == 1


def test_generate_yaml_match_mode_on_general_yaml(pyyaml, tmp_path):
    path = tmp_path / "slug-contract.yaml"
    path.write_text(
        "schema_version: 1\n"
        "identify:\n"
        "  enabled: false\n"
        "  reason: pending review\n",
        encoding="utf-8",
    )

    patches = generate_patches(
        find='{ "enabled": false, "reason": "pending review" }',
        replace='{ "enabled": true, "reason": "reviewed" }',
        target_dir=str(tmp_path),
        glob_pattern="slug-contract.yaml",
        match_mode="yaml",
    )

    assert len(patches) == 1
    arguments = patches[0]["tool_calls"][0]["arguments"]
    assert arguments["TargetContent"] == path.read_text(encoding="utf-8")
    assert pyyaml.safe_load(arguments["ReplacementContent"])["identify"] == {
        "enabled": True,
        "reason": "reviewed",
    }


def test_matcher_applies_semantic_patch_to_general_yaml(pyyaml, tmp_path):
    path = tmp_path / "slug-contract.yaml"
    path.write_text(
        "schema_version: 1\n"
        "identify:\n"
        "  enabled: false\n"
        "  reason: pending review\n",
        encoding="utf-8",
    )

    result = ASTMatcher(str(path)).evaluate(
        '{ "enabled": false, "reason": "pending review" }',
        '{ "enabled": true, "reason": "reviewed" }',
    )

    assert result.success is True
    assert pyyaml.safe_load(result.content)["identify"] == {
        "enabled": True,
        "reason": "reviewed",
    }


def test_generate_yaml_replaces_general_yaml_root(pyyaml, tmp_path):
    path = tmp_path / "slug-contract.yaml"
    old = {"schema_version": 1, "slug": "valve", "status": "draft"}
    new = {"schema_version": 2, "slug": "valve", "status": "used_for_spec"}
    path.write_text(pyyaml.safe_dump(old, sort_keys=False), encoding="utf-8")

    patches = generate_patches(
        find=json.dumps(old),
        replace=json.dumps(new),
        target_dir=str(tmp_path),
        glob_pattern="slug-contract.yaml",
        match_mode="yaml",
    )

    assert len(patches) == 1
    replacement = patches[0]["tool_calls"][0]["arguments"]["ReplacementContent"]
    assert pyyaml.safe_load(replacement) == new


def test_plan_json_shows_json_semantic_strategy(tmp_path):
    path = tmp_path / "index-template.json"
    path.write_text(MAPPING_JSON, encoding="utf-8")
    # Key order differs from file — exact match fails, json-semantic succeeds
    old = '{\n        "level": { "type": "keyword" },\n        "message": { "type": "text" }\n      }'
    new = '{\n        "level": { "type": "keyword" },\n        "message": { "type": "keyword" }\n      }'
    log = tmp_path / "p.jsonl"
    log.write_text(
        json.dumps({
            "step_index": 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "index-template.json",
                    "TargetContent": old,
                    "ReplacementContent": new,
                },
            }],
        }) + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["plan", "--logs", str(log), "--target-dir", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["strategy"] == "json-semantic"
    assert data[0]["would_apply"] is True


def test_bundle_verify_fail_removes_artifacts(tmp_path):
    src = tmp_path / "src"
    hooks = tmp_path / "src" / "hooks"
    src.mkdir()
    hooks.mkdir(parents=True)
    original = PLANNING_TSX
    (src / "A.tsx").write_text(original, encoding="utf-8")
    (src / "B.tsx").write_text(original, encoding="utf-8")
    manifest = {
        "verify_command": "exit 1",
        "files": [
            {"path": "src/A.tsx", "strips": PLANNING_MANIFEST["strips"]},
            {"path": "src/B.tsx", "strips": PLANNING_MANIFEST["strips"]},
        ],
    }
    mpath = tmp_path / "multi.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    result = phase_run_bundle(
        str(mpath),
        profile="frontend",
        out_dir="extracted",
        module_out_dir="src/hooks",
        to_module="hook",
        target_dir=str(tmp_path),
        no_trustchain=False,
    )
    assert result["ok"] is False
    rollback = result.get("rollback") or {}
    assert rollback.get("removed_artifacts")
    assert rollback.get("restored")

    hook_path = hooks / "usePlanningHandlers.ts"
    assert not hook_path.exists()
    assert (src / "A.tsx").read_text(encoding="utf-8") == original
    assert (src / "B.tsx").read_text(encoding="utf-8") == original

    extracted = tmp_path / "extracted"
    if extracted.exists():
        leftover = list(extracted.rglob("*"))
        files_only = [p for p in leftover if p.is_file()]
        assert not files_only, f"expected no extracted artifacts, got {files_only}"


def test_apply_mapping_semantic_patch_json_still_works(tmp_path):
    ok, out = apply_mapping_semantic_patch(
        MAPPING_JSON,
        '{"type": "text"}',
        '{"type": "keyword"}',
        file_ext=".json",
    )
    assert ok is True
    assert '"keyword"' in out
