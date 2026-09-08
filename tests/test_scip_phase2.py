"""SPEC-SCIP-IMPACT-2 — SCIP Phase 2: producer + advisory cross-file impact glue (RFP-033).

Reuses the Phase-1 sample index: a.py defines foo; b.py's bar() calls foo; c.py's baz()
is unrelated. Editing foo must flag the requirement guarding b.py, never the one for c.py.
"""
import base64
import subprocess

NL = chr(10)
SAMPLE_SCIP_B64 = (
    "Cg4aDGZpbGU6Ly8vcmVwbxI/CgRhLnB5Ei8KAwAEBxImc2NpcC1weXRob24gcHl0aG9uIHJlcG8g"
    "MS4wIGBhYC9mb28oKS4YASIGUHl0aG9uEm4KBGIucHkSLwoDAAQHEiZzY2lwLXB5dGhvbiBweXRo"
    "b24gcmVwbyAxLjAgYGJgL2JhcigpLhgBEi0KAwELDhImc2NpcC1weXRob24gcHl0aG9uIHJlcG8g"
    "MS4wIGBhYC9mb28oKS4iBlB5dGhvbhI/CgRjLnB5Ei8KAwAEBxImc2NpcC1weXRob24gcHl0aG9u"
    "IHJlcG8gMS4wIGBjYC9iYXooKS4YASIGUHl0aG9u")


def _git_repo_with_change(tmp_path):
    d = str(tmp_path)
    (tmp_path / "a.py").write_text("def foo():" + NL + "    return 1" + NL, encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():" + NL + "    return foo()" + NL, encoding="utf-8")
    (tmp_path / "c.py").write_text("def baz():" + NL + "    return 2" + NL, encoding="utf-8")
    (tmp_path / "index.scip").write_bytes(base64.b64decode(SAMPLE_SCIP_B64))
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    (tmp_path / "a.py").write_text("def foo():" + NL + "    return 99" + NL, encoding="utf-8")
    return d


def test_producer_graceful_without_scip_python(tmp_path, monkeypatch):
    import apatch.scip_producer as sp

    monkeypatch.setattr(sp, "scip_python_available", lambda: False)
    res = sp.produce_scip_index(str(tmp_path))
    assert res["ok"] is False and res["available"] is False and "scip-python" in res["note"]


def test_changed_symbols_since_from_diff(tmp_path):
    from apatch.scip_producer import changed_symbols_since

    d = _git_repo_with_change(tmp_path)
    assert ("a.py", "foo") in changed_symbols_since(d, "HEAD")


def test_impact_flags_referencing_requirement_only(tmp_path):
    from apatch.scip_producer import scip_impact_workspace

    d = _git_repo_with_change(tmp_path)
    anchors = {"SPEC-X#R1": {"b.py": {"bar": ""}}, "SPEC-X#R2": {"c.py": {"baz": ""}}}
    out = scip_impact_workspace(d, since="HEAD", anchors=anchors)
    assert out["index_present"] is True
    impacted = {w["requirement"] for w in out["impact"]}
    assert impacted == {"SPEC-X#R1"}  # bar() calls foo; baz() unrelated
    assert all("stale" not in w for w in out["impact"])  # advisory only


def test_impact_fallback_without_index(tmp_path):
    from apatch.scip_producer import scip_impact_workspace

    out = scip_impact_workspace(str(tmp_path))
    assert out["ok"] is True and out["index_present"] is False and out["impact"] == []


def test_attested_requirement_anchors_empty_without_attestations(tmp_path):
    # a fresh workspace has no attested requirements -> no anchors (deterministic)
    from apatch.scip_producer import attested_requirement_anchors

    assert attested_requirement_anchors(str(tmp_path)) == {}


def test_apatch_scip_tool_registered():
    import pytest

    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    assert "apatch_scip" in mcp_server.mcp._tool_manager._tools


def test_verify_run_no_index_no_scip_field(tmp_path):
    import pytest

    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    vr = mcp_server.mcp._tool_manager._tools["apatch_verify_run"].fn
    res = vr(target_dir=str(tmp_path), verify="true")
    assert isinstance(res, dict)
    assert res.get("scip_impact") is None  # no .scip index -> advisory not attached
