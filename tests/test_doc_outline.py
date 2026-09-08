"""Tests for markdown outline mutations (doc_outline + generate_batch actions)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apatch.doc_outline import (
    insert_before_in_text,
    insert_section_in_text,
    shift_outline_in_text,
)
from apatch.generate import generate_patches_batch, read_jsonl_patches
from apatch.workflows import generate_patch_jsonl_batch


FIXTURE = Path(__file__).parent / "fixtures" / "doc_outline" / "sample.md"


def _read_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_shift_outline_from_section_three():
    text = _read_fixture()
    out = shift_outline_in_text(
        text,
        after="## 3.",
        levels=[2, 3],
        delta=1,
    )
    assert "## 4. Agent" in out
    assert "### 4.1 Продукт" in out
    assert "## 5. Platform" in out
    assert "### 5.1 OSS" in out
    assert "## 3. Agent" not in out


def test_insert_section_shifts_following():
    text = _read_fixture()
    new_section = "## 3. Entity Model\n\nНовый блок с кириллицей и `code`."
    out = insert_section_in_text(
        text,
        before="## 3.",
        section_content=new_section,
        shift_following={"levels": [2, 3], "delta": 1},
    )
    assert "## 3. Entity Model" in out
    assert "## 4. Agent" in out
    assert "### 4.1 Продукт" in out
    assert "## 5. Platform" in out
    assert out.index("## 3. Entity Model") < out.index("## 4. Agent")


def test_insert_before_without_shift():
    text = _read_fixture()
    out = insert_before_in_text(
        text,
        before="## 5.",
        insert="## 4.5 Bridge\n",
    )
    assert "## 4.5 Bridge" in out
    assert "## 5. Заключение" in out


def test_generate_batch_shift_outline_needle(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(_read_fixture(), encoding="utf-8")
    out = tmp_path / "p.jsonl"
    result = generate_patch_jsonl_batch(
        needles=[
            {
                "action": "shift_outline",
                "target_file": "doc.md",
                "after": "## 3.",
                "levels": [2, 3],
                "delta": 1,
            }
        ],
        target_dir=str(tmp_path),
        out_path=str(out),
    )
    assert result["ok"] is True
    rows = read_jsonl_patches(str(out))
    assert len(rows) == 1
    assert rows[0]["tool_calls"][0]["name"] == "replace_file_content"


def test_generate_batch_insert_section_needle(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(_read_fixture(), encoding="utf-8")
    patches = generate_patches_batch(
        [
            {
                "action": "insert_section",
                "target_file": "doc.md",
                "before": "## 3.",
                "content": "## 3. Entity Model\n\nBody.",
                "shift_following": {"levels": [2, 3], "delta": 1},
            }
        ],
        target_dir=str(tmp_path),
    )
    assert len(patches) == 1


def test_shift_outline_anchor_missing():
    with pytest.raises(ValueError, match="anchor not found"):
        shift_outline_in_text("## 1. Only\n", after="## 99.", levels=[2], delta=1)
