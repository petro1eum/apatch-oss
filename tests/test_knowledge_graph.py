"""Tests for SPEC-KNOWLEDGE-GRAPH-1 (RFP-022 Phase 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apatch.diagnostics.schema import normalize_diagnostic


def _write_diag_fixture(
    tmp_path: Path,
    session_id: str,
    *,
    intent: str = "fix verify failure",
    artifacts: list | None = None,
    attested: bool = False,
    rolled_back: bool = False,
    diagnostics: list | None = None,
    mutated_files: list | None = None,
) -> None:
    apatch = tmp_path / ".apatch"
    apatch.mkdir(parents=True, exist_ok=True)
    state = {
        "session_id": session_id,
        "checkpoint": session_id,
        "intent": intent,
        "artifacts": artifacts or [],
        "phase": "complete" if attested else "blocked",
        "attested": attested,
    }
    if rolled_back:
        state["failure"] = {
            "error_type": "VERIFY_FAILED",
            "recommended_action": "rollback",
            "recoverable": True,
            "message": "verify failed",
        }
    (apatch / "session_state.json").write_text(json.dumps(state), encoding="utf-8")

    diags = diagnostics or [
        normalize_diagnostic(
            {
                "source": "pytest",
                "type": "test_failure",
                "severity": "error",
                "message": "assertion failed",
                "recommended_action": "fix_forward",
                "location": {"file": "tests/test_x.py", "symbol": "test_x"},
            }
        )
    ]
    diag_dir = apatch / "diagnostics"
    diag_dir.mkdir(exist_ok=True)
    (diag_dir / f"{session_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": session_id,
                "diagnostic_count": len(diags),
                "diagnostics": diags,
            }
        ),
        encoding="utf-8",
    )

    if mutated_files:
        report = {
            "checkpoint": session_id,
            "verify_rollback": rolled_back,
            "applied": len(mutated_files),
            "entries": [
                {"outcome": "applied", "target_file": path} for path in mutated_files
            ],
        }
        (apatch / "apply_session_chunk_1.json").write_text(json.dumps(report), encoding="utf-8")
        (apatch / "apply_session.json").write_text(
            json.dumps(
                {
                    "checkpoints": [session_id],
                    "last_checkpoint": session_id,
                    "chunk_reports": [str(apatch / "apply_session_chunk_1.json")],
                    "chunks": [[1]],
                }
            ),
            encoding="utf-8",
        )


def test_r1_session_graph_intent_to_diagnostic(tmp_path: Path) -> None:
    sid = "apatch_sess_graph_r1"
    _write_diag_fixture(tmp_path, sid, attested=True)
    from apatch.knowledge_graph import knowledge_graph_for_session

    graph = knowledge_graph_for_session(sid, str(tmp_path))
    assert graph["ok"] is True
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "Intent" in kinds
    assert "Diagnostic" in kinds
    assert "Attestation" in kinds
    edge_kinds = {e["kind"] for e in graph["edges"]}
    assert "DIAGNOSED_AS" in edge_kinds
    assert any(e["from"].startswith("intent:") and e["to"].startswith("diag_") for e in graph["edges"])


def test_r2_requirement_and_file_nodes(tmp_path: Path) -> None:
    sid = "apatch_sess_graph_r2"
    diag = normalize_diagnostic(
        {
            "source": "clang",
            "type": "missing_member",
            "severity": "error",
            "message": "no member",
            "recommended_action": "fix_forward",
            "location": {"symbol": "Foo::bar"},
            "edges": {"files": ["src/foo.h", "src/caller.cpp"]},
        }
    )
    _write_diag_fixture(
        tmp_path,
        sid,
        artifacts=[{"kind": "spec", "id": "SPEC-KNOWLEDGE-GRAPH-1", "ref": "R2"}],
        diagnostics=[diag],
        mutated_files=["apatch/knowledge_graph.py"],
    )
    from apatch.knowledge_graph import knowledge_graph_for_session

    graph = knowledge_graph_for_session(sid, str(tmp_path))
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "Requirement" in kinds
    assert "File" in kinds
    edge_kinds = {e["kind"] for e in graph["edges"]}
    assert {"COVERS", "MUTATED", "DIAGNOSED_AS"}.issubset(edge_kinds)


def test_r3_project_status_diagnostics_summary(tmp_path: Path) -> None:
    sid = "apatch_sess_graph_r3"
    _write_diag_fixture(tmp_path, sid)
    from apatch.project_status import project_status_workspace

    dto = project_status_workspace(str(tmp_path))
    summary = dto.get("diagnostics_summary")
    assert summary is not None
    assert summary["count"] >= 1
    assert summary["top_type"] == "test_failure"
    assert summary["last_session_id"] == sid
    assert summary["artifact_path"].endswith(f"{sid}.json")


def test_r4_status_json_diagnostics_summary(tmp_path: Path) -> None:
    sid = "apatch_sess_graph_r4"
    _write_diag_fixture(tmp_path, sid)
    from apatch.cli_status import build_status_view

    dto = build_status_view(str(tmp_path))
    assert dto.get("diagnostics_summary", {}).get("count") >= 1


def test_r5_mcp_knowledge_graph_tool(tmp_path: Path) -> None:
    sid = "apatch_sess_graph_r5"
    _write_diag_fixture(tmp_path, sid)
    from apatch.knowledge_graph import knowledge_graph_enriched

    out = knowledge_graph_enriched(str(tmp_path), session_id=sid)
    assert out["ok"] is True
    assert out["session_id"] == sid
    assert out.get("state_update")


def test_r6_report_md_mentions_diagnostic_summary(tmp_path: Path) -> None:
    sid = "apatch_sess_graph_r6"
    _write_diag_fixture(tmp_path, sid)
    from apatch.report_render import build_report_bundle, render_md

    bundle = build_report_bundle(str(tmp_path))
    md = render_md(bundle, locale="en")
    assert "Verification & diagnostics" in md
    assert "test_failure" in md
    assert "diagnostic" in md.lower()
