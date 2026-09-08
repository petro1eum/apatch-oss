"""SPEC-CONCEPT-CRUD-1 — create/update/delete over concept fences (RFP-034 §6): pure
markdown transforms, and a CLI that turns each into a governed needle of the canon."""
import json
import os

from click.testing import CliRunner

from apatch.cli import cli
from apatch.concept_compile import compile_concept_graph
from apatch.concept_crud import add_concept, has_concept, remove_concept, update_concept

_DOC = """# seed

<!-- @cid:cpt_a -->
```concept
name: Alpha
c4: component
```
The alpha concept.
"""


def test_r1_create_roundtrips_and_rejects_dup():
    """R1: a created concept round-trips through compile; a duplicate id is rejected."""
    t = add_concept(_DOC, "cpt_b", name="Beta", c4="container",
                    relations=[{"rel": "depends_on", "to": "cpt_a"}],
                    definition="The beta concept.")
    g = compile_concept_graph(t)
    assert g["errors"] == []
    assert g["concepts"]["cpt_b"]["c4"] == "container"
    assert any(r["to"] == "cpt_a" for r in g["concepts"]["cpt_b"]["relations"])
    try:
        add_concept(t, "cpt_a", name="dup")
        assert False, "expected duplicate to raise"
    except ValueError:
        pass


def test_r2_update_and_delete():
    """R2: update edits fields/relations in place; delete removes the fence, rest intact."""
    t = add_concept(_DOC, "cpt_b", name="Beta", c4="container", definition="b")
    t = update_concept(t, "cpt_b", c4="component", definition="edited",
                       add_relations=[{"rel": "consumes", "to": "cpt_a"}])
    g = compile_concept_graph(t)
    assert g["errors"] == []
    n = g["concepts"]["cpt_b"]
    assert n["c4"] == "component" and "edited" in n["definition"]
    assert any(r["to"] == "cpt_a" for r in n["relations"])
    t = remove_concept(t, "cpt_b")
    g2 = compile_concept_graph(t)
    assert "cpt_b" not in g2["concepts"] and "cpt_a" in g2["concepts"]
    assert g2["errors"] == []
    try:
        remove_concept(t, "cpt_nope")
        assert False, "expected unknown to raise"
    except ValueError:
        pass


def test_r3_cli_new_edit_rm_emit_governed_needles(tmp_path):
    """R3: `apatch concept new/edit/rm` emit a governed needle (replace of the canon) whose
    result compiles clean; invalid c4 and unknown id are rejected."""
    doc = tmp_path / "seed.md"
    doc.write_text(_DOC, encoding="utf-8")
    runner = CliRunner()

    def _apply(needle_path):
        spec = json.load(open(needle_path, encoding="utf-8"))[0]
        cur = doc.read_text(encoding="utf-8")
        assert spec["find_text"] == cur
        doc.write_text(spec["replace_text"], encoding="utf-8")

    r = runner.invoke(cli, ["concept", "new", "cpt_b", "--name", "Beta", "--c4", "component",
                            "--rel", "depends_on:cpt_a", "--def", "Beta.",
                            "--doc", str(doc), "--target-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    _apply(tmp_path / ".apatch" / "tmp" / "concept_new_cpt_b.json")
    g = compile_concept_graph(doc.read_text(encoding="utf-8"))
    assert "cpt_b" in g["concepts"] and g["errors"] == []

    r = runner.invoke(cli, ["concept", "edit", "cpt_b", "--c4", "container",
                            "--doc", str(doc), "--target-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    _apply(tmp_path / ".apatch" / "tmp" / "concept_edit_cpt_b.json")
    assert compile_concept_graph(doc.read_text(encoding="utf-8"))["concepts"]["cpt_b"]["c4"] == "container"

    r = runner.invoke(cli, ["concept", "rm", "cpt_b", "--doc", str(doc), "--target-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    _apply(tmp_path / ".apatch" / "tmp" / "concept_rm_cpt_b.json")
    assert "cpt_b" not in compile_concept_graph(doc.read_text(encoding="utf-8"))["concepts"]

    # invalid c4 and unknown id rejected
    assert runner.invoke(cli, ["concept", "new", "cpt_x", "--c4", "bogus",
                               "--doc", str(doc), "--target-dir", str(tmp_path)]).exit_code != 0
    assert runner.invoke(cli, ["concept", "edit", "cpt_nope", "--name", "x",
                               "--doc", str(doc), "--target-dir", str(tmp_path)]).exit_code != 0