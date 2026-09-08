"""SPEC-CONCEPT-CLARIFY-1 — the human-in-loop clarify loop (RFP-034 §4a): a person marks
a concept needs_clarification, the machine surfaces it not-green, only a person resolves."""
import os

from click.testing import CliRunner

from apatch.cli import cli
from apatch.concept_clarify import (clarifications, needs_clarification,
                                    resolve_clarification, set_clarification)
from apatch.concept_compile import compile_concept_graph, render_concept_graph_md

CONCEPTS = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "avatar-layer.md")


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_store_roundtrip(tmp_path):
    """R1: set records {note}, list returns it, resolve clears — a human-owned store."""
    d = str(tmp_path)
    assert clarifications(d) == {}
    set_clarification("cpt_avatar", "owner unclear", d, by="ed")
    assert needs_clarification(d) == {"cpt_avatar": "owner unclear"}
    assert clarifications(d)["cpt_avatar"]["by"] == "ed"
    assert resolve_clarification("cpt_avatar", d) is True
    assert clarifications(d) == {}
    assert resolve_clarification("cpt_avatar", d) is False  # idempotent


def test_r2_render_overlays_not_green():
    """R2: a clarified concept renders amber `needs_clarification` (never green) with its
    note, regardless of coverage/verify colour."""
    md = render_concept_graph_md(_graph(), clarify={"cpt_avatar": "owner unclear"})
    assert "classDef needs_clarification" in md
    assert any(line.strip().startswith("class ") and "needs_clarification" in line
               and "cpt_avatar" in line for line in md.splitlines())
    assert "owner unclear" in md and "Требует уточнения" in md
    # overlay wins over a green verify status
    md2 = render_concept_graph_md(_graph(), statuses={"cpt_avatar": "green"},
                                  clarify={"cpt_avatar": "owner unclear"})
    assert "cpt_avatar` · **needs_clarification**" in md2


def test_r3_cli_clarify_set_list_resolve(tmp_path):
    """R3: `apatch concept clarify` raises/lists/resolves; an unknown id is rejected."""
    d = str(tmp_path)
    r = CliRunner().invoke(cli, ["concept", "clarify", "cpt_avatar", "--note", "owner unclear",
                                 "--concepts", CONCEPTS, "--target-dir", d])
    assert r.exit_code == 0, r.output
    assert "flagged cpt_avatar" in r.output
    r = CliRunner().invoke(cli, ["concept", "clarify", "--target-dir", d, "--json"])
    assert "cpt_avatar" in r.output and "owner unclear" in r.output
    # unknown id rejected
    r = CliRunner().invoke(cli, ["concept", "clarify", "cpt_nope", "--note", "x",
                                 "--concepts", CONCEPTS, "--target-dir", d])
    assert r.exit_code != 0
    # resolve
    r = CliRunner().invoke(cli, ["concept", "clarify", "cpt_avatar", "--resolve",
                                 "--concepts", CONCEPTS, "--target-dir", d])
    assert r.exit_code == 0 and "resolved cpt_avatar" in r.output
    assert clarifications(d) == {}