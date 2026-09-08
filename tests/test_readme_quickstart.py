"""Tests for SPEC-ONBOARDING-1 R1 — README governed-spec quickstart."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def test_readme_governed_quickstart_present():
    text = README.read_text(encoding="utf-8")
    for phrase in (
        "init-consumer",
        "apatch doctor",
        "spec lint",
        "spec run",
        "apatch status",
        "apatch report",
        "RFP-020",
        "project_status",
    ):
        assert phrase in text, f"missing: {phrase}"


def test_readme_transcript_replay_secondary():
    text = README.read_text(encoding="utf-8")
    governed = text.lower().index("executable specs")
    replay = text.index("## Replay IDE transcripts (secondary workflow)")
    assert governed < replay


def test_readme_primary_description_is_english():
    text = README.read_text(encoding="utf-8")
    assert not re.search(r"[\u0400-\u04ff]", text), "Primary product description must be English"
    assert text.startswith("# APatch — executable contracts for AI agents")
    assert "python -m pip install apatch" in text
    assert "APatch OSS is available under the MIT license" in text
    assert "private/unreleased" not in text


def test_readme_contract_story_keeps_enforcement_boundaries():
    text = README.read_text(encoding="utf-8")
    for phrase in (
        "specification-driven development",
        "test-driven",
        "Frozen contract + task envelope",
        "fixed-purpose SDD verifier",
        "mediated-only",
        "Sandbox/enforcement setup does not automatically freeze a test contract.",
        "An evidence upload is not business acceptance.",
    ):
        assert phrase in text, f"missing product boundary: {phrase}"
