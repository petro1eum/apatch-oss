"""apatch_generate_batch via MCP: needles_path file input (feedback fix)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

from apatch.mcp import server as mcp_server


def _tool(name):
    return mcp_server.mcp._tool_manager._tools[name].fn


def test_generate_batch_accepts_needles_path(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    needles_file = tmp_path / "needles.json"
    needles_file.write_text(
        json.dumps(
            [{"find_text": "x = 1", "replace_text": "x = 2", "target_file": "m.py"}]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "p.jsonl"
    res = _tool("apatch_generate_batch")(
        needles_path="needles.json", target_dir=str(tmp_path), out_path=str(out)
    )
    assert res["ok"] is True
    assert res["count"] == 1


def test_generate_batch_rejects_both_needles_and_path(tmp_path):
    res = _tool("apatch_generate_batch")(
        needles=[{"find_text": "a", "replace_text": "b", "target_file": "x"}],
        needles_path="needles.json",
        target_dir=str(tmp_path),
    )
    assert res["ok"] is False
    assert "not both" in res["error"]


def test_generate_batch_requires_some_needles(tmp_path):
    res = _tool("apatch_generate_batch")(target_dir=str(tmp_path))
    assert res["ok"] is False
    assert "needles" in res["error"]