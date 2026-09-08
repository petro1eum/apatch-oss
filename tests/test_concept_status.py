"""SPEC-CONCEPT-STATUS-1 — live verify-status colours on the graph (RFP-034 §B.7)."""
import os

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.concept_compile import compile_concept_graph, render_concept_graph_md

CONCEPTS = os.path.join(os.path.dirname(__file__), "..", "docs", "concepts", "avatar-layer.md")


def _graph():
    with open(CONCEPTS, encoding="utf-8") as fh:
        return compile_concept_graph(fh.read())


def test_r1_status_colours():
    """R1: given live statuses, the graph colours nodes green/red/broken and shows a
    Verify summary."""
    md = render_concept_graph_md(_graph(), statuses={
        "cpt_contribution_event": "red", "cpt_avatar": "green"})
    assert "classDef red" in md and "classDef green" in md
    assert any(line.strip().startswith("class ") and "red" in line
               and "cpt_contribution_event" in line for line in md.splitlines())
    assert "Verify:" in md


def test_r2_default_coverage_unchanged():
    """R2: without statuses the render is unchanged — coverage line + guarded colouring."""
    md = render_concept_graph_md(_graph())
    assert "Покрытие" in md
    assert any(line.strip().startswith("class ") and "guarded" in line
               for line in md.splitlines())


def test_r3_cli_graph_verify():
    """R3: `apatch concept graph --verify` colours by the live verify run — the
    ContributionEvent node is green because its invariants pass."""
    pytest.importorskip("avatar_contract")  # cross-repo anchor, absent in CI
    r = CliRunner().invoke(cli, ["concept", "graph", "--verify"])
    assert r.exit_code == 0, r.output
    assert "Verify:" in r.output and "classDef green" in r.output
    assert any(line.strip().startswith("class ") and "green" in line
               and "cpt_contribution_event" in line for line in r.output.splitlines())