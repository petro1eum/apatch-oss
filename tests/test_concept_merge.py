"""SPEC-CONCEPT-MERGE-1 — the human-confirmed merge (RFP-034 §B.2): dedup raises candidates,
a human confirms `same_as`, the machine collapses the graph and stops raising the pair."""
import os

from click.testing import CliRunner

from apatch.cli import cli
from apatch.concept_compile import compile_concept_graph, dedup_candidates
from apatch.concept_merge import collapse_graph, merge_map, merges, record_merge, unmerge

_DUP_DOC = """# dup fixture

<!-- @cid:cpt_av1 -->
```concept
name: Аватар
realized_by: mod.x
```
First definition of avatar.

<!-- @cid:cpt_av2 -->
```concept
name: Аватар
realized_by: mod.y
```
Second definition of avatar.
"""


def test_r1_collapse_is_pure_and_rewrites_refs():
    """R1: collapse drops the merged node, folds its name/aliases/realized_by into the
    survivor, rewrites every reference to the survivor, and drops self-loops."""
    g = {"schema_version": 1, "concepts": {
        "a": {"cid": "a", "name": "Alpha", "aliases": [], "realized_by": ["mod.A"],
              "relations": [{"rel": "depends_on", "to": "b"}], "invariants": []},
        "b": {"cid": "b", "name": "Beta", "aliases": [], "realized_by": [],
              "relations": [], "invariants": []},
        "c": {"cid": "c", "name": "Gamma", "aliases": [], "realized_by": [],
              "relations": [{"rel": "consumes", "to": "a"}], "invariants": []},
    }, "claims": {"s1": {"sid": "s1", "about": ["a"]}}, "errors": []}
    out = collapse_graph(g, {"a": "b"})
    assert "a" not in out["concepts"]               # dropped
    assert "a" in g["concepts"]                      # input untouched (pure)
    b = out["concepts"]["b"]
    assert "Alpha" in b["aliases"] and "mod.A" in b["realized_by"]
    assert "a" in b["absorbed"]
    assert b["relations"] == []                      # a's depends_on->b became a self-loop, dropped
    # c's reference to a is rewritten to the survivor b
    assert any(r["rel"] == "consumes" and r["to"] == "b" for r in out["concepts"]["c"]["relations"])
    assert out["claims"]["s1"]["about"] == ["b"]     # claim ref rewritten


def test_r2_recorded_merge_excludes_dedup_candidate(tmp_path):
    """R2: dedup raises the duplicate pair; once a human records the merge, the collapsed
    graph has one node and dedup no longer raises it — the loop closes."""
    d = str(tmp_path)
    g = compile_concept_graph(_DUP_DOC)
    cands = dedup_candidates(g)
    assert any({c["a"], c["b"]} == {"cpt_av1", "cpt_av2"} for c in cands)
    record_merge("cpt_av2", "cpt_av1", d, by="ed", note="same avatar")
    collapsed = collapse_graph(g, merge_map(d))
    assert "cpt_av2" not in collapsed["concepts"]
    assert dedup_candidates(collapsed) == []
    assert unmerge("cpt_av2", d) is True
    assert merge_map(d) == {}


def test_r3_cli_merge_list_unmerge(tmp_path):
    """R3: `apatch concept merge` records/lists/unmerges; unknown id and self-merge rejected."""
    d = str(tmp_path)
    doc = tmp_path / "dup.md"
    doc.write_text(_DUP_DOC, encoding="utf-8")
    runner = CliRunner()
    r = runner.invoke(cli, ["concept", "merge", "cpt_av2", "cpt_av1", "--note", "same",
                            "--concepts", str(doc), "--target-dir", d])
    assert r.exit_code == 0, r.output
    assert "merged cpt_av2 -> cpt_av1" in r.output
    assert merges(d)["cpt_av2"]["into"] == "cpt_av1"
    # dedup via CLI now silent on the merged pair
    r = runner.invoke(cli, ["concept", "dedup", "--concepts", str(doc), "--target-dir", d])
    assert "cpt_av2" not in r.output
    # unknown id rejected
    r = runner.invoke(cli, ["concept", "merge", "cpt_nope", "cpt_av1",
                            "--concepts", str(doc), "--target-dir", d])
    assert r.exit_code != 0
    # self-merge rejected
    r = runner.invoke(cli, ["concept", "merge", "cpt_av1", "cpt_av1",
                            "--concepts", str(doc), "--target-dir", d])
    assert r.exit_code != 0
    # unmerge
    r = runner.invoke(cli, ["concept", "merge", "cpt_av2", "--unmerge", "--target-dir", d])
    assert r.exit_code == 0 and "unmerged cpt_av2" in r.output
    assert merge_map(d) == {}