"""Tests for SPEC-BUILD-DIAGNOSE-1 (RFP-018 compiler feedback loop MVP)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "build_diagnose"


def test_r1_parse_missing_member() -> None:
    from apatch.build_diagnose import parse_compiler_output

    log = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")
    diags = parse_compiler_output(log)
    assert len(diags) == 1
    d = diags[0]
    assert d["error_type"] == "missing_member"
    assert d["member"] == "get_concept_name"
    assert d["type"] == "omega_core::SparseOmega"
    assert d["file"].endswith("omega_sensor.cpp")
    assert d["line"] == 134


def test_r2_extract_cpp_members() -> None:
    from apatch.build_diagnose import extract_cpp_class_members

    header = (FIXTURES / "sparse_omega.h").read_text(encoding="utf-8")
    members = extract_cpp_class_members(header, "SparseOmega")
    assert "lookup" in members
    assert "add_node" in members
    assert "get_concept_name" not in members


def test_r3_suggest_similar_member() -> None:
    from apatch.build_diagnose import parse_compiler_output, run_build_diagnose

    log = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")
    result = run_build_diagnose(str(FIXTURES), log_text=log, write_artifacts=False)
    assert result["ok"] is True
    assert result["build_ok"] is False
    assert result["diagnostic_count"] == 1
    diag = result["diagnostics"][0]
    assert diag["type_definition"]["file"] == "sparse_omega.h"
    strategies = {s["strategy"] for s in diag["suggestions"]}
    assert "restore_api" in strategies
    assert result["agent_next"]


def test_r4_artifacts_written(tmp_path: Path) -> None:
    from apatch.build_diagnose import run_build_diagnose

    log = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")
    apatch_dir = tmp_path / ".apatch"
    result = run_build_diagnose(str(tmp_path), log_text=log, write_artifacts=True)
    assert (apatch_dir / "build_log.json").is_file()
    assert (apatch_dir / "diagnostics.json").is_file()
    doc = json.loads((apatch_dir / "diagnostics.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert doc["diagnostic_count"] == 1
    assert result["artifacts"]["diagnostics"] == str(apatch_dir / "diagnostics.json")


def test_r5_workflow_wrapper() -> None:
    from apatch.workflows import build_diagnose_workspace

    log = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")
    out = build_diagnose_workspace(str(FIXTURES), log_text=log, write_artifacts=False)
    assert out["diagnostic_count"] == 1


@pytest.mark.skipif(
    not (ROOT / "apatch" / "mcp" / "server.py").read_text(encoding="utf-8").find(
        "apatch_build_diagnose"
    )
    >= 0,
    reason="MCP tool not wired yet",
)
def test_r6_mcp_tool_registered() -> None:
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tools = getattr(mcp_server.mcp, "_tool_manager", None)
    if tools is None:
        pytest.skip("FastMCP tool manager unavailable")
    assert "apatch_build_diagnose" in tools._tools


def test_r6_enrich_verify_failure_attaches_diagnose() -> None:
    from apatch.build_diagnose import enrich_verify_failure, looks_like_compiler_output

    log = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")
    assert looks_like_compiler_output(log)
    result: dict = {"ok": False, "verify_output": log}
    enriched = enrich_verify_failure(
        result, str(FIXTURES), log_text=log, write_artifacts=False
    )
    assert enriched["build_diagnose"]["diagnostic_count"] == 1
    assert enriched.get("agent_next")


def test_r7_apply_verify_rollback_includes_build_diagnose(tmp_path: Path) -> None:
    import json

    from apatch.workflows import apply_from_logs

    log = (FIXTURES / "clang_missing_member.log").read_text(encoding="utf-8")
    src = tmp_path / "value.py"
    src.write_text("KEEP = 1\n", encoding="utf-8")
    fail = tmp_path / "fail_with_clang.sh"
    fail.write_text(
        "#!/bin/bash\n"
        f"cat <<'EOLOG' >&2\n{log}\nEOLOG\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fail.chmod(0o755)
    patch_log = tmp_path / "p.jsonl"
    patch_log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [
                    {
                        "name": "replace_file_content",
                        "arguments": {
                            "TargetFile": "value.py",
                            "TargetContent": "KEEP = 1",
                            "ReplacementContent": "KEEP = 2",
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = apply_from_logs(
        str(patch_log),
        str(tmp_path),
        verify=str(fail),
        no_trustchain=True,
    )
    assert out["verify_rollback"] is True
    assert out.get("build_diagnose", {}).get("diagnostic_count") == 1
