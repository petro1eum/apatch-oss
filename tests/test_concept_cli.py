"""SPEC-CONCEPT-CLI-1 — `apatch concept ...` CLI (RFP-034 Level 2). No-mock CliRunner."""
import json

import pytest
from click.testing import CliRunner

from apatch.cli import cli


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def test_r1_compile():
    """R1: `apatch concept compile <doc> --json` compiles fences to a clean graph."""
    r = _run("concept", "compile", "docs/concepts/avatar-layer.md", "--json")
    assert r.exit_code == 0, r.output
    g = json.loads(r.output)
    assert len(g["concepts"]) >= 5 and g["errors"] == []


def test_r2_coverage():
    """R2: `apatch concept coverage --json` reports guarded/grey over docs/concepts/."""
    r = _run("concept", "coverage", "--json")
    assert r.exit_code == 0, r.output
    cov = json.loads(r.output)
    assert cov["total"] >= 5 and "cpt_contribution_event" in cov["guarded"]


def test_r3_verify():
    """R3: `apatch concept verify --json` runs the invariants; ContributionEvent is green."""
    pytest.importorskip("avatar_contract")  # cross-repo anchor, absent in CI
    r = _run("concept", "verify", "--json")
    assert r.exit_code == 0, r.output
    res = json.loads(r.output)
    assert res["cpt_contribution_event"]["status"] == "green"


def test_r4_graph_and_dedup():
    """R4: `apatch concept graph` emits a Mermaid page; `dedup --json` returns a list."""
    rg = _run("concept", "graph")
    assert rg.exit_code == 0 and "```mermaid" in rg.output
    rd = _run("concept", "dedup", "--json")
    assert rd.exit_code == 0
    assert isinstance(json.loads(rd.output), list)