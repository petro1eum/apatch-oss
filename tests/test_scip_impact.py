"""SPEC-SCIP-IMPACT-1 — SCIP cross-file reference impact (RFP-033).

SAMPLE_SCIP_B64 is a real serialized scip.Index (scip-python symbol scheme):
a.py defines foo; b.py defines bar and references foo (bar calls foo); c.py
defines baz. Decoded by apatch.scip_ingest.decode_scip.
"""
import base64

from apatch.scip_ingest import (
    decode_scip,
    ScipModel,
    symbol_local_name,
    resolve_symbol,
    load_scip_model,
    scip_impacted_requirements,
)

NL = chr(10)
SAMPLE_SCIP_B64 = (
    "Cg4aDGZpbGU6Ly8vcmVwbxI/CgRhLnB5Ei8KAwAEBxImc2NpcC1weXRob24gcHl0aG9uIHJlcG8g"
    "MS4wIGBhYC9mb28oKS4YASIGUHl0aG9uEm4KBGIucHkSLwoDAAQHEiZzY2lwLXB5dGhvbiBweXRo"
    "b24gcmVwbyAxLjAgYGJgL2JhcigpLhgBEi0KAwELDhImc2NpcC1weXRob24gcHl0aG9uIHJlcG8g"
    "MS4wIGBhYC9mb28oKS4iBlB5dGhvbhI/CgRjLnB5Ei8KAwAEBxImc2NpcC1weXRob24gcHl0aG9u"
    "IHJlcG8gMS4wIGBjYC9iYXooKS4YASIGUHl0aG9u"
)
SYM_FOO = "scip-python python repo 1.0 `a`/foo()."
SYM_BAR = "scip-python python repo 1.0 `b`/bar()."
SYM_BAZ = "scip-python python repo 1.0 `c`/baz()."


def _model():
    return ScipModel(decode_scip(base64.b64decode(SAMPLE_SCIP_B64)))


def _write_sources(tmp_path):
    (tmp_path / "a.py").write_text("def foo():" + NL + "    return 1" + NL, encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():" + NL + "    return foo()" + NL, encoding="utf-8")
    (tmp_path / "c.py").write_text("def baz():" + NL + "    return 2" + NL, encoding="utf-8")


def test_r1_decode_scip():
    docs = decode_scip(base64.b64decode(SAMPLE_SCIP_B64))
    assert set(docs) == {"a.py", "b.py", "c.py"}
    a = docs["a.py"]
    assert len(a) == 1 and a[0].symbol == SYM_FOO
    assert a[0].is_definition and a[0].range == [0, 4, 7]
    syms = {(o.symbol, o.is_definition) for o in docs["b.py"]}
    assert (SYM_BAR, True) in syms
    assert (SYM_FOO, False) in syms
    assert decode_scip(bytes()) == {}
    assert decode_scip(bytes([255, 255, 255])) == {}


def test_r2_reference_graph():
    m = _model()
    assert m.definitions(SYM_FOO) == ["a.py"]
    assert m.references(SYM_FOO) == ["b.py"]
    assert (SYM_FOO, 1) in m.references_in_file("b.py")
    assert m.definitions(SYM_BAZ) == ["c.py"]
    assert m.references(SYM_BAZ) == []


def test_r3_bridge():
    assert symbol_local_name(SYM_FOO) == "foo"
    assert symbol_local_name("scip x y 1 `a`/Beta#") == "Beta"
    assert symbol_local_name("scip x y 1 `a`/Beta#m().") == "m"
    assert symbol_local_name("local 0") == "local 0"
    m = _model()
    assert resolve_symbol(m, "a.py", "foo") == SYM_FOO
    assert resolve_symbol(m, "b.py", "bar") == SYM_BAR
    assert resolve_symbol(m, "a.py", "nope") is None


def test_r4_refactor_impact_go_no_go(tmp_path):
    _write_sources(tmp_path)
    m = _model()
    anchors = {
        "R-bar": {"b.py": {"bar": "h1"}},
        "R-baz": {"c.py": {"baz": "h2"}},
    }
    out = scip_impacted_requirements([("a.py", "foo")], anchors, m, str(tmp_path))
    impacted = {w["requirement"] for w in out}
    # editing foo flags bar (which calls foo) and never the unrelated baz
    assert impacted == {"R-bar"}
    w = out[0]
    assert w["via_symbol"] == "b.py::bar"
    assert w["impacted_by"] == "a.py::foo"
    assert w["reference_file"] == "b.py"
    # advisory only: a warning, never a staleness flag
    assert all("stale" not in entry for entry in out)


def test_r5_safe_fallback(tmp_path):
    # no index present -> None, never raises
    assert load_scip_model(str(tmp_path)) is None
    # unparsable index -> None
    (tmp_path / "index.scip").write_bytes(bytes([110, 111, 116, 32, 115, 99, 105, 112]))
    assert load_scip_model(str(tmp_path)) is None
    # impact with no model -> empty, verify path unaffected
    out = scip_impacted_requirements([("a.py", "foo")], {"R": {"b.py": {"bar": "h"}}}, None, str(tmp_path))
    assert out == []


def test_r0_scip_impact_rfp_coverage():
    import os
    import pytest

    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(
        os.path.join(root, "docs", "RFP-033-scip-reference-impact.md"),
        encoding="utf-8",
    ) as f:
        rfp = f.read()
    with open(
        os.path.join(root, "docs", "specs", "SPEC-SCIP-IMPACT-1.md"),
        encoding="utf-8",
    ) as f:
        spec = f.read()
    out = rfp_spec_coverage(
        rfp, spec, rfp_id="RFP-033", spec_id="SPEC-SCIP-IMPACT-1"
    )
    assert out["passed"] is True
    assert not out.get("gaps")


# spec-scip-impact-tests