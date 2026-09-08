"""SPEC-SYMBOL-ANCHOR-1 — symbol-granular attestation drift."""

from __future__ import annotations

from apatch.symbol_anchor import (
    extract_file_symbols,
    requirement_stale_with_symbols,
    symbol_drift,
    symbols_for_line_range,
)

_SRC = (
    "import os\n"
    "\n"
    "def alpha():\n"
    "    return 1\n"
    "\n"
    "class Beta:\n"
    "    def m(self):\n"
    "        return 2\n"
    "\n"
    "def gamma(x):\n"
    "    return x\n"
)


def test_r1_extract_symbols(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(_SRC, encoding="utf-8")

    syms = extract_file_symbols(str(src))
    assert set(syms) == {"alpha", "Beta", "gamma"}
    assert syms["alpha"]["start_line"] == 3
    assert syms["alpha"]["end_line"] == 4
    assert len(syms["alpha"]["hash"]) == 64
    # deterministic for identical input
    assert extract_file_symbols(str(src)) == syms
    # unsupported language -> file-level signal (empty)
    other = tmp_path / "x.unknownext"
    other.write_text("whatever", encoding="utf-8")
    assert extract_file_symbols(str(other)) == {}


def test_r2_symbols_for_range(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(_SRC, encoding="utf-8")
    # alpha spans lines 3-4
    assert symbols_for_line_range(str(src), 3, 3) == ["alpha"]
    assert symbols_for_line_range(str(src), 4, 4) == ["alpha"]
    # gamma spans 10-11
    assert symbols_for_line_range(str(src), 10, 11) == ["gamma"]
    # a range covering the whole file hits all three top-level symbols
    assert symbols_for_line_range(str(src), 3, 11) == ["Beta", "alpha", "gamma"]
    # a blank-line gap (line 5) belongs to no symbol
    assert symbols_for_line_range(str(src), 5, 5) == []


def test_r3_symbol_drift(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(_SRC, encoding="utf-8")
    syms = extract_file_symbols(str(src))
    anchors = {"m.py": {n: syms[n]["hash"] for n in ("alpha", "gamma")}}

    # unchanged -> no drift
    assert symbol_drift(anchors, str(tmp_path)) == []

    # change gamma's body only -> only gamma drifts
    src.write_text(_SRC.replace("return x\n", "return x * 999\n"), encoding="utf-8")
    assert symbol_drift(anchors, str(tmp_path)) == ["m.py::gamma"]

    # a removed symbol is reported as drifted (alpha still matches)
    src.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    assert symbol_drift(anchors, str(tmp_path)) == ["m.py::gamma"]


def test_r4_shared_file_symbol_isolation(tmp_path):
    # Two requirements share one file but anchor to different symbols.
    shared = tmp_path / "shared.py"
    shared.write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    syms = extract_file_symbols(str(shared))
    anchor_foo = {"shared.py": {"foo": syms["foo"]["hash"]}}
    anchor_bar = {"shared.py": {"bar": syms["bar"]["hash"]}}

    # Edit only foo's body — the classic shared-file cascade trigger.
    shared.write_text(
        "def foo():\n    return 1000\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )

    # The requirement anchored to bar is NOT drifted: the file_drift cascade is gone.
    assert symbol_drift(anchor_bar, str(tmp_path)) == []
    # The requirement anchored to foo IS drifted (its own symbol changed).
    assert symbol_drift(anchor_foo, str(tmp_path)) == ["shared.py::foo"]


def test_r5_file_hash_fallback_compat(tmp_path):
    import hashlib

    shared = tmp_path / "shared.py"
    shared.write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    file_hashes = {"shared.py": hashlib.sha256(shared.read_bytes()).hexdigest()}
    syms = extract_file_symbols(str(shared))

    # No symbol anchors + unchanged file -> not stale (file-hash behaviour).
    assert requirement_stale_with_symbols(file_hashes, None, str(tmp_path)) is False

    # Edit foo -> the file changed on disk.
    shared.write_text(
        "def foo():\n    return 1000\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )

    # file-hash-only requirement is stale -> exact current behaviour preserved.
    assert requirement_stale_with_symbols(file_hashes, None, str(tmp_path)) is True
    # symbol-anchored to bar -> NOT stale despite the file changing.
    assert (
        requirement_stale_with_symbols(
            file_hashes, {"shared.py": {"bar": syms["bar"]["hash"]}}, str(tmp_path)
        )
        is False
    )
    # symbol-anchored to foo -> stale.
    assert (
        requirement_stale_with_symbols(
            file_hashes, {"shared.py": {"foo": syms["foo"]["hash"]}}, str(tmp_path)
        )
        is True
    )
    # a drifted file with no symbol anchor falls back to file-level (stale).
    assert (
        requirement_stale_with_symbols(
            file_hashes, {"other.py": {"x": "deadbeef"}}, str(tmp_path)
        )
        is True
    )


def test_r0_symbol_anchor_rfp_coverage():
    import os
    import pytest

    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(
        os.path.join(root, "docs", "RFP-032-symbol-granular-anchoring.md"),
        encoding="utf-8",
    ) as f:
        rfp = f.read()
    with open(
        os.path.join(root, "docs", "specs", "SPEC-SYMBOL-ANCHOR-1.md"),
        encoding="utf-8",
    ) as f:
        spec = f.read()
    out = rfp_spec_coverage(
        rfp, spec, rfp_id="RFP-032", spec_id="SPEC-SYMBOL-ANCHOR-1"
    )
    assert out["passed"] is True
    assert not out.get("gaps")