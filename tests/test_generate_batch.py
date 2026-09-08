"""apatch_generate_batch — multi-needle JSONL without custom scripts."""

from __future__ import annotations

import json
import os
import stat

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.generate import generate_patches_batch, read_jsonl_patches
from apatch.workflows import generate_patch_jsonl, generate_patch_jsonl_batch


def test_generate_batch_multi_needle_same_file(tmp_path):
    f = tmp_path / "hook.ts"
    f.write_text("const a = 1;\nconst b = 2;\nconst c = 3;\n", encoding="utf-8")
    out = tmp_path / "patches.jsonl"
    needles = [
        {
            "find_text": "const a = 1;",
            "replace_text": "const a = 10;",
            "target_file": "hook.ts",
            "label": "a",
        },
        {
            "find_text": "const b = 2;",
            "replace_text": "const b = 20;",
            "target_file": "hook.ts",
            "label": "b",
        },
    ]
    result = generate_patch_jsonl_batch(
        needles=needles,
        target_dir=str(tmp_path),
        out_path=str(out),
    )
    assert result["ok"] is True
    assert result["count"] == 2
    resolved = result.get("out_path") or str(out)
    rows = read_jsonl_patches(resolved)
    assert [r["step_index"] for r in rows] == [1, 2]
    assert rows[0]["tool_calls"][0]["arguments"]["TargetFile"] == "hook.ts"


def test_generate_batch_overlap_returns_safe_retry_without_mutation(tmp_path):
    source = tmp_path / "module.py"
    original = "alpha = 1\nbeta = 2\ngamma = 3\n"
    source.write_text(original, encoding="utf-8")
    out = tmp_path / "patches.jsonl"

    result = generate_patch_jsonl_batch(
        needles=[
            {
                "target_file": "module.py",
                "find_text": "alpha = 1\nbeta = 2",
                "replace_text": "alpha = 10\nbeta = 20",
            },
            {
                "target_file": "module.py",
                "find_text": "beta = 2\ngamma = 3",
                "replace_text": "beta = 200\ngamma = 300",
            },
        ],
        target_dir=str(tmp_path),
        out_path=str(out),
    )

    assert result["ok"] is False
    assert result["error_type"] == "NEEDLE_OVERLAP"
    assert result["recommended_action"] == "retry_split"
    assert result["mutation_performed"] is False
    assert result["retry_plan"]["safe_batches"] == [[1], [2]]
    assert source.read_text(encoding="utf-8") == original
    assert not out.exists()


def test_generate_batch_fails_when_needle_missing(tmp_path):
    f = tmp_path / "only.ts"
    f.write_text("x = 1\n", encoding="utf-8")
    result = generate_patch_jsonl_batch(
        needles=[{"find_text": "MISSING", "replace_text": "y", "target_file": "only.ts"}],
        target_dir=str(tmp_path),
        out_path=str(tmp_path / "p.jsonl"),
    )
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_generate_batch_accepts_empty_replace_text(tmp_path):
    f = tmp_path / "only.ts"
    f.write_text("delete me\nkeep me\n", encoding="utf-8")
    out = tmp_path / "p.jsonl"
    result = generate_patch_jsonl_batch(
        needles=[{"find_text": "delete me\n", "replace_text": "", "target_file": "only.ts"}],
        target_dir=str(tmp_path),
        out_path=str(out),
    )

    assert result["ok"] is True
    rows = read_jsonl_patches(str(out))
    args = rows[0]["tool_calls"][0]["arguments"]
    assert args["TargetContent"] == "delete me\n"
    assert args["ReplacementContent"] == ""


def test_generate_append_continues_step_index(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("aaa\nbbb\n", encoding="utf-8")
    out = tmp_path / "p.jsonl"
    generate_patch_jsonl(
        find_text="aaa",
        replace_text="AAA",
        target_dir=str(tmp_path),
        glob_pattern="a.py",
        out_path=str(out),
    )
    generate_patch_jsonl(
        find_text="bbb",
        replace_text="BBB",
        target_dir=str(tmp_path),
        glob_pattern="a.py",
        out_path=str(out),
        append=True,
    )
    rows = read_jsonl_patches(str(out))
    assert [r["step_index"] for r in rows] == [1, 2]


def test_generate_batch_create_file(tmp_path):
    out = tmp_path / "patches.jsonl"
    result = generate_patch_jsonl_batch(
        needles=[
            {
                "action": "create",
                "target_file": "src/new_module.ts",
                "content": "export const x = 1;\n",
                "label": "new_module",
            }
        ],
        target_dir=str(tmp_path),
        out_path=str(out),
    )
    assert result["ok"] is True
    resolved = result.get("out_path") or str(out)
    rows = read_jsonl_patches(resolved)
    assert len(rows) == 1
    inp = rows[0]["tool_calls"][0]["arguments"]["input"]
    assert "*** Add File: src/new_module.ts" in inp
    assert "+export const x = 1;" in inp

    from apatch.ingestor import LogIngestor

    candidates = LogIngestor(resolved).parse()
    assert len(candidates) == 1
    assert candidates[0].action_type == "CREATE"
    assert candidates[0].target_file == "src/new_module.ts"
    assert "export const x = 1;" in candidates[0].new_content


def test_generate_batch_create_fails_if_exists(tmp_path):
    existing = tmp_path / "src"
    existing.mkdir()
    (existing / "t.ts").write_text("old\n", encoding="utf-8")
    result = generate_patch_jsonl_batch(
        needles=[{"action": "create", "target_file": "src/t.ts", "content": "new\n"}],
        target_dir=str(tmp_path),
        out_path=str(tmp_path / "p.jsonl"),
    )
    assert result["ok"] is False
    assert "already exists" in result["error"]


def test_generate_batch_delete(tmp_path):
    f = tmp_path / "old.ts"
    f.write_text("gone\n", encoding="utf-8")
    out = tmp_path / "p.jsonl"
    result = generate_patch_jsonl_batch(
        needles=[{"action": "delete", "target_file": "old.ts"}],
        target_dir=str(tmp_path),
        out_path=str(out),
    )
    assert result["ok"] is True
    inp = read_jsonl_patches(str(out))[0]["tool_calls"][0]["arguments"]["input"]
    assert "*** Delete File: old.ts" in inp


def test_generate_batch_chmod_file(tmp_path):
    f = tmp_path / "run_live_feedback_contract.sh"
    f.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(f, 0o644)
    out = tmp_path / "p.jsonl"

    result = generate_patch_jsonl_batch(
        needles=[
            {
                "action": "chmod",
                "target_file": "run_live_feedback_contract.sh",
                "mode": "100755",
            }
        ],
        target_dir=str(tmp_path),
        out_path=str(out),
    )

    assert result["ok"] is True
    row = read_jsonl_patches(str(out))[0]
    call = row["tool_calls"][0]
    assert call["name"] == "chmod_file"
    assert call["arguments"]["TargetFile"] == "run_live_feedback_contract.sh"
    assert call["arguments"]["Mode"] == "755"

    from apatch.ingestor import LogIngestor

    candidates = LogIngestor(str(out)).parse()
    assert len(candidates) == 1
    assert candidates[0].action_type == "CHMOD"
    assert candidates[0].new_content == "755"
    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_generate_batch_rename(tmp_path):
    src = tmp_path / "a.ts"
    src.write_text("const v = 1;\n", encoding="utf-8")
    out = tmp_path / "p.jsonl"
    result = generate_patch_jsonl_batch(
        needles=[{"action": "rename", "source_file": "a.ts", "target_file": "b.ts"}],
        target_dir=str(tmp_path),
        out_path=str(out),
    )
    assert result["ok"] is True
    rows = read_jsonl_patches(str(out))
    assert len(rows) == 2
    assert "Add File: b.ts" in rows[0]["tool_calls"][0]["arguments"]["input"]
    assert "Delete File: a.ts" in rows[1]["tool_calls"][0]["arguments"]["input"]


def test_generate_batch_mixed_create_and_replace(tmp_path):
    f = tmp_path / "hook.ts"
    f.write_text("const a = 1;\n", encoding="utf-8")
    out = tmp_path / "p.jsonl"
    result = generate_patch_jsonl_batch(
        needles=[
            {"action": "create", "target_file": "new.ts", "content": "export {};\n"},
            {"find_text": "const a = 1;", "replace_text": "const a = 2;", "target_file": "hook.ts"},
        ],
        target_dir=str(tmp_path),
        out_path=str(out),
    )
    assert result["ok"] is True
    rows = read_jsonl_patches(str(out))
    assert len(rows) == 2
    assert rows[0]["step_index"] == 1
    assert rows[1]["step_index"] == 2


def test_generate_batch_kind_create_alias(tmp_path):
    out = tmp_path / "p.jsonl"
    result = generate_patch_jsonl_batch(
        needles=[{"kind": "create", "target_file": "x.ts", "content": "// stub\n"}],
        target_dir=str(tmp_path),
        out_path=str(out),
    )
    assert result["ok"] is True


def test_apatch_plan_create_dry_run(tmp_path):
    pytest.importorskip("mcp")
    from apatch.mcp.server import apatch_plan

    result = apatch_plan(
        target_file="src/not_yet.ts",
        old_content="",
        new_content="export const ok = true;\n",
        target_dir=str(tmp_path),
    )
    assert result["success"] is True
    assert result["strategy"] == "create"


def test_mcp_generate_batch_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None
    assert "apatch_generate_batch" in tm._tools


def test_generate_batch_cli_json_output(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    needles = tmp_path / "needles.json"
    needles.write_text(
        json.dumps([{"target_file": "a.py", "find_text": "x = 1", "replace_text": "x = 2"}]),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "generate-batch",
            "--needles",
            str(needles),
            "--target-dir",
            str(tmp_path),
            "--out",
            str(tmp_path / "patches.jsonl"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["count"] == 1
