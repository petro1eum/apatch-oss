"""Test for MCP apatch_timesheet tool (SPEC-CONTRIB-TIMESHEET-1 R9)."""
import json

import pytest

pytest.importorskip("mcp")


def _write_event(store_dir, key_id="k1"):
    d = store_dir / key_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "e1.json").write_text(json.dumps({
        "schema_version": 1, "kind": "contribution", "event_id": "e1",
        "source": "apatch", "trust_level": "attested", "idempotency_key": "e1",
        "identity": {"key_id": key_id, "ca": "platform", "trust_level": "attested", "agent_id": "a"},
        "project": {"id": "p1", "name": "p1", "remote": None},
        "session": {"session_id": "s1", "intent": "x", "artifacts": ["spec:SPEC-X#R1"],
                    "started_at": "2026-06-18T10:00:00+00:00",
                    "ended_at": "2026-06-18T11:00:00+00:00",
                    "duration_sec": 3600.0, "active_sec": None},
        "volume": {"ops": 2, "files_touched": 1, "insertions": 3, "deletions": 1},
        "signature": "sig", "attestation": {"op_ids": ["op_1"], "head": "op_1"},
    }), encoding="utf-8")


def test_r9_mcp_timesheet_readonly(tmp_path, monkeypatch):
    from apatch.mcp import server as S

    fn = getattr(S, "apatch_timesheet", None)
    assert fn is not None, "apatch_timesheet MCP tool not registered"
    call = getattr(fn, "fn", fn)  # unwrap FastMCP tool wrapper if present
    _write_event(tmp_path)
    monkeypatch.setenv("APATCH_CONTRIB_STORE", str(tmp_path))
    out = call(by="identity")
    assert isinstance(out, dict)
    assert out["by"] == ["identity"]
    assert out["groups"][0]["key"] == ["k1"]
    assert out["groups"][0]["hours"] == 1.0