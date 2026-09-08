"""Tests for R31–R35 recommendation implementations."""

import json

import pytest

from apatch.boundary_ranker import rank_until_candidates
from apatch.consumer_profiles import get_profile, list_profiles, manifest_content_for_profile
from apatch.doctor import init_consumer
from apatch.json_mapping import apply_json_semantic_patch, looks_like_mapping_document
from apatch.matcher import ASTMatcher
from apatch.generate import generate_patches
from tests.fixtures.planning_tsx import PLANNING_TSX


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

MAPPING_JSON_REFORMATTED = """{
  "index_patterns": ["logs-*"],
  "template": {
    "mappings": {
      "dynamic": true,
      "properties": {
        "level": { "type": "keyword" },
        "message": { "type": "text" }
      }
    }
  }
}
"""


def test_json_mapping_detection():
    assert looks_like_mapping_document(MAPPING_JSON)


def test_json_semantic_patch_key_order(tmp_path):
    path = tmp_path / "template.json"
    path.write_text(MAPPING_JSON, encoding="utf-8")
    old = '{\n        "message": { "type": "text" },\n        "level": { "type": "keyword" }\n      }'
    new = '{\n        "message": { "type": "text" },\n        "level": { "type": "keyword" },\n        "ts": { "type": "date" }\n      }'
    ok, out = apply_json_semantic_patch(path.read_text(encoding="utf-8"), old, new)
    assert ok is True
    assert '"ts"' in out


def test_ast_matcher_json_semantic_strategy(tmp_path):
    path = tmp_path / "index-template.json"
    path.write_text(MAPPING_JSON, encoding="utf-8")
    matcher = ASTMatcher(str(path))
    old = '{"type": "text"}'
    new = '{"type": "keyword"}'
    result = matcher.evaluate(old, new)
    assert result.success is True
    assert result.strategy in ("json-semantic", "whitespace-fuzzy")
    assert '"type": "keyword"' in result.content


def test_generate_json_match_mode(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(MAPPING_JSON, encoding="utf-8")
    patches = generate_patches(
        find='{ "type": "text" }',
        replace='{ "type": "keyword" }',
        target_dir=str(tmp_path),
        glob_pattern="*.json",
        match_mode="json",
    )
    assert len(patches) == 1


def test_apply_verify_rollback_ok_false(tmp_path):
    pytest.importorskip("mcp")
    from apatch.mcp.server import apatch_apply

    src = tmp_path / "value.py"
    src.write_text("KEEP = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    log.write_text(
        json.dumps({
            "step_index": 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "value.py",
                    "TargetContent": "KEEP = 1",
                    "ReplacementContent": "KEEP = 2",
                },
            }],
        }) + "\n",
        encoding="utf-8",
    )
    result = apatch_apply(
        logs_path=str(log),
        target_dir=str(tmp_path),
        verify="exit 1",
        no_trustchain=True,
    )
    assert result["ok"] is False
    assert result["verify_rollback"] is True
    assert result["rolled_back"] >= 1
    assert src.read_text(encoding="utf-8") == "KEEP = 1\n"


def test_phase_bundle_shared_trustchain_checkpoint(tmp_path):
    from apatch.workflows import phase_run_bundle
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    src = tmp_path / "src"
    hooks = tmp_path / "src" / "hooks"
    extracted = tmp_path / "extracted"
    src.mkdir()
    hooks.mkdir(parents=True)
    extracted.mkdir()
    (src / "A.tsx").write_text(PLANNING_TSX, encoding="utf-8")
    (src / "B.tsx").write_text(PLANNING_TSX, encoding="utf-8")
    manifest = {
        "verify_command": "echo ok",
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
    assert result["ok"] is True, result
    tc = result.get("trustchain") or {}
    assert tc.get("bundle") is True
    assert tc.get("checkpoint")


def test_init_consumer_prisma_django_profiles(tmp_path):
    for profile in ("prisma", "django"):
        created = init_consumer(str(tmp_path / profile), profile=profile)
        pack = get_profile(profile)
        manifest = tmp_path / profile / "manifests" / pack["manifest_name"]
        assert manifest.is_file()
        assert profile in manifest.read_text(encoding="utf-8").lower() or "verify" in manifest.read_text(encoding="utf-8")


def test_suggest_until_ranked(tmp_path):
    page = tmp_path / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    lines = page.read_text(encoding="utf-8").splitlines(keepends=True)
    start_idx = next(i for i, ln in enumerate(lines) if "HANDLERS START" in ln)
    ranked = rank_until_candidates(str(page), lines, start_idx)
    assert ranked
    assert all("score" in r and "confidence" in r for r in ranked)
    assert ranked[0]["score"] >= ranked[-1]["score"]


def test_profiles_include_prisma_django():
    names = list_profiles()
    assert "prisma" in names
    assert "django" in names
    assert "schema.prisma" in manifest_content_for_profile("prisma") or "prisma" in manifest_content_for_profile("prisma")
