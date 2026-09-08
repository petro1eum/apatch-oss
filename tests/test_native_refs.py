"""SPEC-SCIP-IMPACT-3 — native in-process cross-file reference resolver (no scip-python).

a.py defines foo; b.py `from a import foo` + calls it; e.py `import a; a.foo()`; c.py's
baz is unrelated. Editing foo must flag the requirements guarding b.py and e.py, never c.py.
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


def _repo(tmp_path):
    (tmp_path / "a.py").write_text("def foo():" + NL + "    return 1" + NL, encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "from a import foo" + NL + "def bar():" + NL + "    return foo()" + NL, encoding="utf-8")
    (tmp_path / "c.py").write_text("def baz():" + NL + "    return 2" + NL, encoding="utf-8")
    (tmp_path / "e.py").write_text(
        "import a" + NL + "def qux():" + NL + "    return a.foo()" + NL, encoding="utf-8")
    return str(tmp_path)


def _git_native(tmp_path):
    d = _repo(tmp_path)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    (tmp_path / "a.py").write_text("def foo():" + NL + "    return 99" + NL, encoding="utf-8")
    return d


def test_native_resolves_import_and_attribute(tmp_path):
    from apatch.native_refs import native_reference_model

    m = native_reference_model(_repo(tmp_path))
    assert m.references("a.py::foo") == ["b.py", "e.py"]  # from-import + attribute access
    assert m.definition_occurrence("a.py", "foo").symbol == "a.py::foo"
    assert m.definition_occurrence("a.py", "nope") is None
    assert m.references("a.py::baz_does_not_exist") == []


def test_native_impact_flags_only_referencing(tmp_path):
    from apatch.scip_producer import scip_impact_workspace

    d = _git_native(tmp_path)
    anchors = {"R-bar": {"b.py": {"bar": ""}}, "R-baz": {"c.py": {"baz": ""}},
               "R-qux": {"e.py": {"qux": ""}}}
    out = scip_impact_workspace(d, since="HEAD", anchors=anchors)
    assert out["model_present"] is True
    assert out["model_source"] == "native" and out["index_present"] is False
    impacted = {w["requirement"] for w in out["impact"]}
    assert impacted == {"R-bar", "R-qux"}  # both reference foo; baz unrelated
    assert all("stale" not in w for w in out["impact"])  # advisory only


def test_scip_index_wins_over_native(tmp_path):
    from apatch.scip_producer import scip_impact_workspace

    (tmp_path / "index.scip").write_bytes(base64.b64decode(SAMPLE_SCIP_B64))
    out = scip_impact_workspace(str(tmp_path), since="HEAD")
    assert out["model_present"] is True
    assert out["model_source"] == "scip" and out["index_present"] is True
