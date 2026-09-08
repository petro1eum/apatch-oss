import os

import pytest

from apatch.ast_window import extract_parse_slice, find_parse_anchor
from apatch.matcher import ASTMatcher
from apatch.path_index import PathIndex
from apatch.scale_config import get_ast_window_config, resolve_path_backend


def test_find_parse_anchor_exact():
    content = "aaa\nbbb\ntarget()\nccc"
    old = "target()"
    assert find_parse_anchor(content, old) == content.index("target()")


def test_extract_parse_slice_bounds():
    content = "line\n" * 100
    anchor = 50
    slice_text, offset = extract_parse_slice(content, anchor, 40)
    assert offset <= anchor
    assert len(slice_text) >= 40
    assert content[offset : offset + len(slice_text)] == slice_text


def test_ast_windowed_parse_large_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_AST_WINDOW_BYTES", "8192")
    monkeypatch.setenv("APATCH_AST_FULL_PARSE_MAX", "2048")

    padding = "pass\n" * 500
    target_fn = "def target_fn():\n    return 1\n"
    path = tmp_path / "big.py"
    path.write_text(padding + target_fn, encoding="utf-8")
    assert len(path.read_text()) > 2048

    matcher = ASTMatcher(str(path))
    result = matcher.evaluate(
        "def target_fn():\n\n    return 1",
        "def target_fn():\n    return 42",
    )
    assert result.success, result.strategy
    assert result.strategy in ("ast-fuzzy", "whitespace-fuzzy")
    assert "return 42" in result.content


def test_ast_window_config_defaults(monkeypatch):
    monkeypatch.delenv("APATCH_AST_WINDOW_BYTES", raising=False)
    monkeypatch.delenv("APATCH_AST_FULL_PARSE_MAX", raising=False)
    window, full_max = get_ast_window_config()
    assert window == 0
    assert full_max == 524288


def test_path_index_walk_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_PATH_BACKEND", "walk")
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    f = root / "src" / "a.py"
    f.write_text("x")
    index = PathIndex.build(str(root))
    assert index.source == "walk"
    assert os.path.abspath(f) in index.resolve_basename("a.py")


def test_resolve_path_backend_invalid(monkeypatch):
    monkeypatch.setenv("APATCH_PATH_BACKEND", "not-a-backend")
    assert resolve_path_backend() == "auto"
