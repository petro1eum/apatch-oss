"""Regression coverage for top-level generate-batch defaults."""

from apatch.generate import read_jsonl_patches
from apatch.workflows import generate_patch_jsonl_batch


def test_generate_batch_honors_top_level_match_defaults(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "catalog.md").write_text("96 tools and 196 records; 96 tools\n", encoding="utf-8")
    (tmp_path / "outside.md").write_text("96 tools\n", encoding="utf-8")
    out_path = tmp_path / "patches.jsonl"

    result = generate_patch_jsonl_batch(
        needles=[{"action": "replace", "find_text": r"\b96\b", "replace_text": "98"}],
        target_dir=str(tmp_path),
        out_path=str(out_path),
        default_glob_pattern="docs/*.md",
        default_match_mode="regex",
        default_replace_all=True,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    row = read_jsonl_patches(result["out_path"])[0]
    args = row["tool_calls"][0]["arguments"]
    assert args["TargetFile"] == "docs/catalog.md"
    assert args["TargetContent"] == "96"
    assert args["ReplacementContent"] == "98"
    assert args["AllowMultiple"] is True
