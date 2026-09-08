"""Tests for RFP-024 needles scaffold bridge (spec_lint → plan/spec_run)."""

from __future__ import annotations

import os

import pytest

from apatch.spec_needles_scaffold import (
    infer_target_files_from_verify,
    scaffold_execution_plan_entry,
    spec_needles_scaffold_workspace,
)


def _write_fixture_spec(root: str) -> None:
    """A spec with a UNIQUE id (never attested anywhere), so `pending` is deterministic and
    independent of the local TrustChain ledger state — fixes the ledger-coupled flakiness
    where a fully-attested dogfood spec made `pending` empty on a developer machine."""
    specs = os.path.join(root, "docs", "specs")
    os.makedirs(specs, exist_ok=True)
    with open(os.path.join(specs, "SPEC-NEEDLES-FIXTURE-1.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# SPEC-NEEDLES-FIXTURE-1 — needles scaffold fixture\n\n"
            "> **apatch artifact:** `spec:SPEC-NEEDLES-FIXTURE-1`\n\n"
            "## R1 parse the thing (verify: python3 -m pytest tests/test_fixture.py::test_r1 -q)\n\n"
            "Update `apatch/fixture_mod.py` to parse the thing.\n")


def test_infer_target_files_from_verify_pytest_node():
    verify = "python3 -m pytest tests/test_rfp_coverage.py::test_r1_parse_rfp_acceptance -q"
    assert infer_target_files_from_verify(verify) == ["tests/test_rfp_coverage.py"]


def test_infer_target_files_o_lang_cpp():
    from apatch.spec_needles_scaffold import infer_target_files_from_text

    body = "Update `o_lang/cpp/omega.hpp` and register in CMakeLists."
    paths = infer_target_files_from_text(body)
    assert paths == ["o_lang/cpp/omega.hpp"]


def test_infer_target_files_from_verify_path_in_cmd():
    verify = "python3 -m pytest tests/test_foo.py -q && apatch rfp lint --rfp docs/RFP-023-rfp-spec-coverage.md"
    paths = infer_target_files_from_verify(verify)
    assert "tests/test_foo.py" in paths
    assert "docs/RFP-023-rfp-spec-coverage.md" in paths


def test_scaffold_execution_plan_entry_pending_has_templates():
    entry = scaffold_execution_plan_entry(
        rk="R1",
        title="Parse table",
        verify="python3 -m pytest tests/test_rfp_coverage.py::test_r1 -q",
        body_text="`parse_rfp_acceptance` in apatch/rfp_coverage.py",
        state="pending",
    )
    assert entry["target_files"] == [
        "tests/test_rfp_coverage.py",
        "apatch/rfp_coverage.py",
    ]
    assert entry["needles"] == []
    assert entry["needle_templates"]
    assert "apatch_spec_plan_register" in entry["agent_next"]


def test_scaffold_reuses_attested_needles():
    entry = scaffold_execution_plan_entry(
        rk="R2",
        title="Reuse",
        verify="pytest tests/test_x.py -q",
        body_text="",
        state="attested",
        attested_needles=[{"action": "replace", "target_file": "apatch/x.py", "find_text": "a", "replace_text": "b"}],
    )
    assert entry["needles_source"] == "ledger_attested"
    assert len(entry["needles"]) == 1


def test_declared_ownership_is_authoritative_for_plan_targets(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-STRICT-PLAN.md").write_text(
        "# SPEC-STRICT-PLAN Strict plan\n\n"
        "> **apatch artifact:** `spec:SPEC-STRICT-PLAN`\n"
        "> **ownership mode:** strict\n\n"
        "## R1 exact targets\n\n"
        "owns: `apatch/owned.py`, `tests/owned/**`\n\n"
        "Narrative mentions `apatch/not_owned.py` and must not enlarge the plan.\n\n"
        "(verify: python3 -m pytest tests/test_not_owned.py -q)\n",
        encoding="utf-8",
    )

    out = spec_needles_scaffold_workspace(
        str(tmp_path), spec="SPEC-STRICT-PLAN", include_attested=False
    )

    assert out["ok"] is True
    entry = out["execution_plan"]["R1"]
    assert entry["target_files"] == ["apatch/owned.py", "tests/owned/**"]
    assert entry["target_files_sources"] == {
        "declared_ownership": ["apatch/owned.py", "tests/owned/**"]
    }
    assert out["plan_scaffold"]["execution_plan"]["R1"]["target_files"] == entry["target_files"]
    assert out["manifest_scaffold"]["requirements"]["R1"]["target_files"] == entry["target_files"]


def test_spec_needles_scaffold_workspace_pending(tmp_path):
    _write_fixture_spec(str(tmp_path))
    out = spec_needles_scaffold_workspace(str(tmp_path), spec="SPEC-NEEDLES-FIXTURE-1")
    assert out["ok"] is True
    assert out["spec"] == "SPEC-NEEDLES-FIXTURE-1"
    assert out["pending"]  # unique spec id is never attested -> deterministically pending
    plan = out["plan_scaffold"]
    assert plan["schema_version"] == 2
    assert plan["spec"] == "SPEC-NEEDLES-FIXTURE-1"
    r1 = plan["execution_plan"]["R1"]
    assert r1["target_files"]
    workflow = " ".join(out["workflow"])
    assert "apatch_spec_lint" in workflow
    assert "plan_scaffold" in workflow
    assert "apatch_spec_needles_scaffold" not in workflow


def test_spec_lint_passed_includes_rfp_coverage_on_traceability_spec():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    from apatch.spec import spec_lint_workspace

    out = spec_lint_workspace(root, spec="SPEC-RFP-COVERAGE-1")
    assert out.get("passed") is True
    assert out.get("rfp_coverage", {}).get("passed") is True
    assert out.get("rfp_lint", {}).get("passed") is True


def test_spec_lint_passed_includes_needles_scaffold():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    from apatch.spec import spec_lint_workspace

    out = spec_lint_workspace(root, spec="SPEC-RFP-COVERAGE-1")
    assert out.get("passed") is True
    assert out.get("plan_scaffold")
    assert out.get("needles_scaffold", {}).get("spec") == "SPEC-RFP-COVERAGE-1"


def test_agent_onboarding_playbook_in_doctor():
    from apatch.agent_guidance import agent_onboarding_playbook, autonomy_boundary

    ob = agent_onboarding_playbook()
    assert ob["doc"] == "docs/agent-onboarding.md"
    assert "apatch_spec_lint" in ob["primary_spec_gate"]
    assert ob["pipeline"]
    assert ob.get("autonomy_boundary")
    assert autonomy_boundary()["manifest_gap_means"]


def test_spec_run_dry_run_includes_needles_scaffold(tmp_path):
    from apatch import spec_run

    _write_fixture_spec(str(tmp_path))
    out = spec_run.spec_run_enriched(str(tmp_path), spec="SPEC-NEEDLES-FIXTURE-1", dry_run=True)
    assert out.get("ok") is True
    assert out.get("needles_scaffold")
    assert out.get("plan_scaffold")
    assert out.get("autonomy_boundary") or "execute_next" in (out.get("agent_next") or "")
