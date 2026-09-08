"""Tests for SPEC-PLAN-ARTIFACT-1 (RFP-011) schema v1 + v2."""

from __future__ import annotations

from pathlib import Path

import pytest

from apatch.spec import parse_spec
from apatch.spec_plan import (
    PLAN_SCHEMA_V2,
    lint_plan_dict,
    plan_diff_workspace,
    register_plan_workspace,
    resolve_plan_version,
    show_plan_workspace,
    spec_plan_lint_workspace,
)


SPEC_PLAN = """# SPEC-PLAN-TEST - plan test

> **apatch artifact:** `spec:SPEC-PLAN-TEST`

## R1 One

(verify: true)

## R2 Two

(verify: true)
"""


def _write_spec(tmp_path):
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SPEC-PLAN-TEST.md"
    p.write_text(SPEC_PLAN, encoding="utf-8")
    return p


def _sample_plan_v1():
    return {
        "schema_version": 1,
        "spec": "SPEC-PLAN-TEST",
        "rationale": "test plan",
        "requirements": {
            "R1": {
                "needles": [
                    {
                        "action": "create",
                        "target_file": "marker.txt",
                        "content": "x\n",
                    }
                ],
                "files": ["marker.txt"],
                "rationale": "create marker",
            },
            "R2": {
                "needles": [
                    {
                        "action": "replace",
                        "target_file": "marker.txt",
                        "find_text": "x",
                        "replace_text": "y",
                    }
                ],
            },
        },
    }


def _sample_plan_v2():
    return {
        "schema_version": PLAN_SCHEMA_V2,
        "spec": "SPEC-PLAN-TEST",
        "decision_plan": {
            "chosen_strategy": "Inline marker files for plan tests",
            "rejected_alternatives": ["Skip needles and rely on verify-only Rk"],
            "assumptions": ["tmp_path is writable"],
            "risks": ["None for unit scope"],
        },
        "execution_plan": {
            "R1": {
                "target_files": ["marker.txt"],
                "needles": [
                    {
                        "action": "create",
                        "target_file": "marker.txt",
                        "content": "x\n",
                    }
                ],
            },
            "R2": {
                "needles": [
                    {
                        "action": "replace",
                        "target_file": "marker.txt",
                        "find_text": "x",
                        "replace_text": "y",
                    }
                ],
            },
        },
    }


@pytest.fixture
def fake_tc(monkeypatch):
    class FakeTC:
        def has_trustchain(self):
            return True

        def commit_action(self, tool_id, payload):
            return True

        def iter_ledger_entries(self):
            return iter([])

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC()
    )


def test_plan_schema_v2_lint(tmp_path):  # R1
    _write_spec(tmp_path)
    parsed = parse_spec(SPEC_PLAN, spec_id="SPEC-PLAN-TEST")
    req_ids = [r.id for r in parsed.requirements]
    good = lint_plan_dict(
        _sample_plan_v2(), spec_id="SPEC-PLAN-TEST", requirement_ids=req_ids
    )
    assert good["ok"] is True
    assert good["schema_version"] == 2
    assert good["decision_plan_present"] is True
    assert good["execution_rk_count"] == 2

    bad = dict(_sample_plan_v2())
    bad["decision_plan"]["rejected_alternatives"] = []
    bad_lint = lint_plan_dict(bad, spec_id="SPEC-PLAN-TEST", requirement_ids=req_ids)
    assert bad_lint["ok"] is False

    out = spec_plan_lint_workspace(
        str(tmp_path), spec="SPEC-PLAN-TEST", plan=_sample_plan_v2()
    )
    assert out["ok"] is True
    assert out["decision_plan_present"] is True


def test_plan_schema_v1_backward_compat(tmp_path):  # R2
    _write_spec(tmp_path)
    parsed = parse_spec(SPEC_PLAN, spec_id="SPEC-PLAN-TEST")
    req_ids = [r.id for r in parsed.requirements]
    lint = lint_plan_dict(
        _sample_plan_v1(), spec_id="SPEC-PLAN-TEST", requirement_ids=req_ids
    )
    assert lint["ok"] is True
    assert any("schema_version 2" in w for w in lint["warnings"])


def test_plan_schema_lint(tmp_path):
    """Legacy name: v1 plan still lints."""
    test_plan_schema_v1_backward_compat(tmp_path)


def test_plan_register_signed_ledger_op(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    captured = {}

    class CapturingTC:
        def has_trustchain(self):
            return True

        def commit_action(self, tool_id, payload):
            captured["tool_id"] = tool_id
            captured["payload"] = payload
            return True

        def iter_ledger_entries(self):
            return iter([])

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: CapturingTC()
    )
    out = register_plan_workspace(
        str(tmp_path), spec="SPEC-PLAN-TEST", plan=_sample_plan_v2()
    )
    assert out["ok"] is True
    assert out["version"] == 1
    assert out["plan_id"] == "plan:SPEC-PLAN-TEST@v1"
    assert captured["tool_id"] == "apatch_plan"
    assert captured["payload"]["plan_sha256"] == out["plan_sha256"]
    assert out["decision_plan_summary"]["rejected_count"] == 1
    assert Path(out["local_path"]).is_file()


def test_plan_supersession_and_diff(tmp_path, fake_tc):  # R4
    _write_spec(tmp_path)
    register_plan_workspace(str(tmp_path), spec="SPEC-PLAN-TEST", plan=_sample_plan_v2())
    p2 = _sample_plan_v2()
    p2["decision_plan"]["chosen_strategy"] = "Updated strategy"
    p2["execution_plan"]["R2"]["rationale"] = "updated"
    r2 = register_plan_workspace(str(tmp_path), spec="SPEC-PLAN-TEST", plan=p2)
    assert r2["version"] == 2
    assert r2["supersedes"] == 1

    diff = plan_diff_workspace(
        str(tmp_path), spec="SPEC-PLAN-TEST", from_version=1, to_version=2
    )
    assert diff["ok"] is True
    assert "chosen_strategy" in diff["decision_plan_delta"]
    assert "R2" in diff["execution_plan_delta"]


def test_spec_run_with_registered_plan(tmp_path, fake_tc):
    from apatch.spec_run import spec_run_workspace

    _write_spec(tmp_path)
    plan = _sample_plan_v2()
    plan["execution_plan"]["R1"]["needles"] = [
        {"action": "create", "target_file": "plan-marker.txt", "content": "PLAN\n"}
    ]
    reg = register_plan_workspace(str(tmp_path), spec="SPEC-PLAN-TEST", plan=plan)
    assert reg["ok"]

    loaded, ver, err = resolve_plan_version(str(tmp_path), "SPEC-PLAN-TEST", "latest")
    assert err is None
    assert ver == 1

    out = spec_run_workspace(
        str(tmp_path),
        spec="SPEC-PLAN-TEST",
        plan="v1",
        reset=True,
        dry_run=True,
    )
    assert out["ok"] is True


def test_plan_coverage_and_show(tmp_path, fake_tc):
    _write_spec(tmp_path)
    out = register_plan_workspace(
        str(tmp_path), spec="SPEC-PLAN-TEST", plan=_sample_plan_v2()
    )
    assert out["ok"]
    show = show_plan_workspace(str(tmp_path), spec="SPEC-PLAN-TEST", version="latest")
    assert show["ok"] is True
    assert show["decision_plan"]["chosen_strategy"]
    assert "R1" in show["execution_plan"]
    assert show["plan_id"] == out["plan_id"]


def test_plan_optional_paths_unchanged(tmp_path, monkeypatch):
    from apatch.spec_run import spec_run_workspace

    _write_spec(tmp_path)

    class FakeTC:
        def has_trustchain(self):
            return False

        def iter_ledger_entries(self):
            return iter([])

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC()
    )
    out = spec_run_workspace(str(tmp_path), spec="SPEC-PLAN-TEST", dry_run=True)
    assert out["ok"] is True


def test_mcp_spec_plan_tools_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None
    for name in (
        "apatch_spec_plan_lint",
        "apatch_spec_plan_register",
        "apatch_spec_plan_diff",
        "apatch_spec_plan_show",
    ):
        assert name in tm._tools
