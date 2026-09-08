"""Design Partner Program playbook (SPEC-DESIGN-PARTNER-1 / RFP-021 AR-7)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "docs" / "design-partner-playbook.md"
README = ROOT / "docs" / "README.md"


@pytest.fixture
def playbook_text() -> str:
    assert PLAYBOOK.is_file(), "docs/design-partner-playbook.md missing"
    return PLAYBOOK.read_text(encoding="utf-8")


def test_playbook_has_prerequisites(playbook_text):
    assert "## 2. Prerequisites" in playbook_text
    assert "Python 3.11" in playbook_text
    assert "TrustChain" in playbook_text


def test_playbook_has_setup(playbook_text):
    assert "init-consumer" in playbook_text
    assert "--with-sandbox" in playbook_text
    assert "--with-enforcement" in playbook_text
    assert "--with-mcp" in playbook_text
    assert "mcp sync" in playbook_text


def test_playbook_has_agent_contract(playbook_text):
    assert "apatch_doctor" in playbook_text
    assert "apatch_session_start" in playbook_text
    assert "apatch_spec_run" in playbook_text
    assert "protocol_contract" in playbook_text


def test_playbook_has_l1_gates(playbook_text):
    for gate in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"):
        assert gate in playbook_text
    assert "L1 readiness checklist" in playbook_text


def test_playbook_has_escalation(playbook_text):
    assert "fix_forward" in playbook_text
    assert "rollback" in playbook_text
    assert "resume_session" in playbook_text


def test_playbook_has_parallel_agents(playbook_text):
    assert "worktree" in playbook_text.lower()
    assert "lane" in playbook_text.lower()
    assert "RFP-019" in playbook_text


def test_init_consumer_copies_playbook(tmp_path):
    from apatch.doctor import init_consumer

    init_consumer(str(tmp_path), with_mcp=True)
    dst = tmp_path / "docs" / "design-partner-playbook.md"
    assert dst.is_file()
    assert "Design Partner Program" in dst.read_text(encoding="utf-8")


def test_docs_readme_links_playbook():
    text = README.read_text(encoding="utf-8")
    assert "design-partner-playbook.md" in text
