"""Tests for SPEC-DIAGNOSTIC-GRAPH-1 (RFP-022 Phase 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "build_diagnose"

CLANG_LOG = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")
PYTEST_LOG = (
    "============================= test session starts ==============================\n"
    "FAILED tests/test_widget.py::test_answer - AssertionError: expected 42\n"
    "========================= 1 failed in 0.12s =========================\n"
)


def test_r1_schema_validate_required_fields() -> None:
    from apatch.diagnostics.schema import normalize_diagnostic, validate_diagnostic

    with pytest.raises(ValueError, match="missing required field"):
        validate_diagnostic({"source": "clang"})

    d = normalize_diagnostic(
        {
            "source": "clang",
            "type": "missing_member",
            "severity": "error",
            "message": "no member named 'x'",
            "recommended_action": "fix_forward",
        }
    )
    assert d["schema_version"] == 1
    assert d["id"].startswith("diag_")


def test_r2_clang_adapter_missing_member() -> None:
    from apatch.diagnostics.adapters.clang import adapt_clang_log

    diags = adapt_clang_log(CLANG_LOG, str(FIXTURES))
    assert len(diags) == 1
    d = diags[0]
    assert d["source"] == "clang"
    assert d["type"] == "missing_member"
    assert d["location"]["symbol"] == "omega_core::SparseOmega::get_concept_name"
    assert d["suggestions"]


def test_r3_pytest_adapter_failed_node() -> None:
    from apatch.diagnostics.adapters.pytest import adapt_pytest_log

    diags = adapt_pytest_log(PYTEST_LOG)
    assert len(diags) == 1
    d = diags[0]
    assert d["source"] == "pytest"
    assert d["type"] == "test_failure"
    assert d["location"]["file"] == "tests/test_widget.py"
    assert d["location"]["symbol"] == "test_answer"
    assert d["recommended_action"] == "fix_forward"


def test_r4_spec_adapter_violation() -> None:
    from apatch.diagnostics.adapters.spec import adapt_spec_lint_errors

    diags = adapt_spec_lint_errors(
        [
            {
                "code": "missing_verify",
                "message": "R2 has no verify command",
                "requirement": "R2",
            }
        ]
    )
    assert len(diags) == 1
    assert diags[0]["source"] == "spec"
    assert diags[0]["type"] == "spec_violation"
    assert diags[0]["edges"]["requirements"] == ["R2"]


def test_r4_trust_adapter_notarization() -> None:
    from apatch.diagnostics.adapters.trust import adapt_failure_info
    from apatch.failure_taxonomy import ERROR_NOTARIZATION_FAILED, FailureInfo

    fi = FailureInfo(
        error_type=ERROR_NOTARIZATION_FAILED,
        recoverable=True,
        recommended_action="rollback",
        message="notarization failed",
        tool="apatch_attest",
    )
    diags = adapt_failure_info(fi)
    assert len(diags) == 1
    assert diags[0]["source"] == "trust"
    assert diags[0]["type"] == "notarization_failed"
    assert diags[0]["recommended_action"] == "rollback"


def test_r5_collect_from_clang_log() -> None:
    from apatch.diagnostics.collect import collect_diagnostics

    result = {"ok": False, "error_type": "VERIFY_FAILED", "verify_output": CLANG_LOG}
    diags = collect_diagnostics(
        result,
        str(FIXTURES),
        log_text=CLANG_LOG,
        verify="make",
        session_id="apatch_sess_test",
        write_artifacts=False,
    )
    assert len(diags) >= 1
    assert diags[0]["source"] == "clang"
    assert result.get("diagnostics") == diags


def test_r5_verify_run_response_has_diagnostics() -> None:
    from apatch.build_diagnose import enrich_verify_failure

    result = {"ok": False, "error": "verify failed"}
    out = enrich_verify_failure(
        result,
        str(FIXTURES),
        log_text=CLANG_LOG,
        verify="make",
        write_artifacts=False,
    )
    assert out["diagnostic_count"] >= 1
    assert out["diagnostics"][0]["schema_version"] == 1
    assert out.get("build_diagnose")


def test_r6_session_artifact_written(tmp_path: Path) -> None:
    from apatch.diagnostics.artifacts import write_session_diagnostics
    from apatch.diagnostics.schema import normalize_diagnostic

    d = normalize_diagnostic(
        {
            "source": "pytest",
            "type": "test_failure",
            "severity": "error",
            "message": "failed",
            "recommended_action": "fix_forward",
        }
    )
    path = write_session_diagnostics(
        str(tmp_path),
        "apatch_sess_art",
        [d],
        write_artifacts=True,
    )
    assert path is not None
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["session_id"] == "apatch_sess_art"
    assert len(doc["diagnostics"]) == 1


def test_r7_diagnose_playbook_resource() -> None:
    from apatch.agent_playbooks import diagnose_playbook

    pb = diagnose_playbook()
    assert pb["resource"] == "apatch://playbook/diagnose"
    assert "diagnostics[0].recommended_action" in " ".join(pb["workflow"])


def test_collect_binds_governed_session_id(tmp_path: Path) -> None:
    from apatch.diagnostics.collect import collect_diagnostics

    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    sid = "apatch_sess_partner_smoke"
    (apatch_dir / "session_state.json").write_text(
        json.dumps(
            {
                "session_id": sid,
                "checkpoint": sid,
                "phase": "verify",
                "intent": "partner smoke",
            }
        ),
        encoding="utf-8",
    )
    result: dict = {"ok": False}
    collect_diagnostics(
        result,
        str(tmp_path),
        log_text=PYTEST_LOG,
        write_artifacts=True,
    )
    artifact = apatch_dir / "diagnostics" / f"{sid}.json"
    assert artifact.is_file()
    assert sid in str(result.get("diagnostics_artifact", ""))
    assert result["diagnostics"][0]["session_id"] == sid
