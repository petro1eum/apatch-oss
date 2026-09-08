"""Tests for SPEC-COVERAGE-1 (RFP-010)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from apatch.spec_coverage import (
    compute_drift,
    coverage_rows,
    requirement_file_sets,
    spec_coverage_workspace,
)
from apatch.spec import parse_spec


def _ledger(tmp_path: Path, entries):
    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    for i, data in enumerate(entries):
        payload = {
            "value": {
                "tool_id": data["tool_id"],
                "data": data["payload"],
                "signature": data.get("signature", f"sig{i}"),
                "id": data.get("id", f"op-{i}"),
                "timestamp": data.get("timestamp", f"2026-06-10T10:00:{i:02d}Z"),
            }
        }
        (tc_dir / f"entry{i}.json").write_text(json.dumps(payload), encoding="utf-8")


def _spec_covtest():
    return parse_spec(
        """# SPEC-COVTEST - coverage test spec

> **apatch artifact:** `spec:SPEC-COVTEST`

## R1 First

(verify: true)

## R2 Second

(verify: true)
""",
        spec_id="SPEC-COVTEST",
    )


def test_file_sets_from_ledger():
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m1",
            "timestamp": "2026-06-10T10:00:01Z",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "governed_session_id": "apatch_sess_r1",
                "files": {
                    "src/auth/service.py": {"sha256": "aaa111"},
                    "src/auth/models.py": {"sha256": "bbb222"},
                },
                "artifacts": [{"kind": "spec", "id": "SPEC-COVTEST#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a1",
            "timestamp": "2026-06-10T10:00:02Z",
            "payload": {
                "intent": "R1 done",
                "session_id": "apatch_sess_r1",
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-COVTEST#R1", "content_hash": "sha256:abc"}
                ],
            },
        },
    ]
    sets = requirement_file_sets(entries, "SPEC-COVTEST")
    assert "R1" in sets
    assert sets["R1"]["files"] == {
        "src/auth/service.py": "aaa111",
        "src/auth/models.py": "bbb222",
    }
    assert sets["R1"]["session_id"] == "apatch_sess_r1"
    assert "op-m1" in sets["R1"]["op_ids"]


def test_partitioned_mutation_keeps_requirement_file_sets_disjoint():
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-partitioned",
            "timestamp": "2026-08-01T10:00:01Z",
            "payload": {
                "action": "chunk",
                "applied_patches": 2,
                "governed_session_id": "apatch_sess_shared",
                "files": {
                    "categories/a/query.py": {"sha256": "aaa"},
                    "categories/b/query.py": {"sha256": "bbb"},
                },
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-COVTEST#R1"},
                    {"kind": "spec", "id": "SPEC-COVTEST#R2"},
                ],
                "artifact_files": {
                    "spec:SPEC-COVTEST#R1": ["categories/a/query.py"],
                    "spec:SPEC-COVTEST#R2": ["categories/b/query.py"],
                },
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-partitioned-attest",
            "timestamp": "2026-08-01T10:00:02Z",
            "payload": {
                "session_id": "apatch_sess_shared",
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-COVTEST#R1"},
                    {"kind": "spec", "id": "SPEC-COVTEST#R2"},
                ],
            },
        },
    ]
    sets = requirement_file_sets(entries, "SPEC-COVTEST")
    assert sets["R1"]["files"] == {"categories/a/query.py": "aaa"}
    assert sets["R2"]["files"] == {"categories/b/query.py": "bbb"}


def test_file_sets_latest_session_only():
    """Only mutations from the latest attestation session define file-drift refs."""
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m-old",
            "timestamp": "2026-06-10T10:00:01Z",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "governed_session_id": "apatch_sess_old",
                "files": {"old/file.py": {"sha256": "deadbeef"}},
                "artifacts": [{"kind": "spec", "id": "SPEC-COVTEST#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a-old",
            "timestamp": "2026-06-10T10:00:02Z",
            "payload": {
                "session_id": "apatch_sess_old",
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-COVTEST#R1", "content_hash": "sha256:old"}
                ],
            },
        },
        {
            "tool_id": "apatch",
            "id": "op-m-new",
            "timestamp": "2026-06-10T10:00:10Z",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "governed_session_id": "apatch_sess_new",
                "files": {"src/new.py": {"sha256": "abc123"}},
                "artifacts": [{"kind": "spec", "id": "SPEC-COVTEST#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a-new",
            "timestamp": "2026-06-10T10:00:11Z",
            "payload": {
                "session_id": "apatch_sess_new",
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-COVTEST#R1", "content_hash": "sha256:new"}
                ],
            },
        },
    ]
    sets = requirement_file_sets(entries, "SPEC-COVTEST")
    assert sets["R1"]["files"] == {"src/new.py": "abc123"}
    assert sets["R1"]["session_id"] == "apatch_sess_new"
    assert "op-m-new" in sets["R1"]["op_ids"]
    assert "op-m-old" not in sets["R1"]["op_ids"]


def test_stale_on_file_drift(tmp_path):
    f = tmp_path / "src" / "auth" / "service.py"
    f.parent.mkdir(parents=True)
    f.write_text("unchanged\n", encoding="utf-8")
    import hashlib

    good_hash = hashlib.sha256(f.read_bytes()).hexdigest()
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m1",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "files": {"src/auth/service.py": {"sha256": good_hash}},
                "artifacts": [{"kind": "spec", "id": "SPEC-COVTEST#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a1",
            "payload": {
                "artifacts": [{"kind": "spec", "id": "SPEC-COVTEST#R1"}],
            },
        },
    ]
    spec = _spec_covtest()
    rows = coverage_rows(spec, entries, str(tmp_path))
    r1 = next(r for r in rows if r["id"] == "R1")
    assert r1["state"] == "attested"

    f.write_text("drifted\n", encoding="utf-8")
    rows2 = coverage_rows(spec, entries, str(tmp_path))
    r1b = next(r for r in rows2 if r["id"] == "R1")
    assert r1b["state"] == "stale"
    assert r1b["stale_reason"] == "file_drift"
    assert "src/auth/service.py" in r1b["drifted"]

    drifted = compute_drift({"src/auth/service.py": good_hash}, str(tmp_path))
    assert "src/auth/service.py" in drifted


def test_dot_prefixed_paths_not_falsely_stale(tmp_path):
    """Regression: '.apatch/conformance.json' must keep its leading dot.

    lstrip('./') mangled it to 'apatch/conformance.json', so drift hashed a
    non-existent path and flipped a green requirement to stale (SPEC-UGOL-1
    R15 on prod) while the live conformance gate stayed green.
    """
    f = tmp_path / ".apatch" / "conformance.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}\n", encoding="utf-8")

    good_hash = hashlib.sha256(f.read_bytes()).hexdigest()
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m1",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "files": {".apatch/conformance.json": {"sha256": good_hash}},
                "artifacts": [{"kind": "spec", "id": "SPEC-COVTEST#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a1",
            "payload": {
                "artifacts": [{"kind": "spec", "id": "SPEC-COVTEST#R1"}],
            },
        },
    ]
    sets = requirement_file_sets(entries, "SPEC-COVTEST")
    assert set(sets["R1"]["files"]) == {".apatch/conformance.json"}

    rows = coverage_rows(_spec_covtest(), entries, str(tmp_path))
    r1 = next(r for r in rows if r["id"] == "R1")
    assert r1["state"] == "attested"
    assert not r1.get("drifted")


def test_mcp_spec_coverage_registered():
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None
    assert "apatch_spec_coverage" in tm._tools


def test_spec_status_reports_stale(tmp_path, monkeypatch):
    from apatch.spec import spec_status_workspace

    spec_text = """# SPEC-STALE - stale integration

> **apatch artifact:** `spec:SPEC-STALE`

## R1 One

(verify: true)
"""
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC-STALE.md").write_text(spec_text, encoding="utf-8")

    f = tmp_path / "marker.txt"
    f.write_text("v1\n", encoding="utf-8")
    import hashlib

    h1 = hashlib.sha256(f.read_bytes()).hexdigest()

    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m",
            "payload": {
                "action": "apply",
                "applied_patches": 1,
                "files": {"marker.txt": {"sha256": h1}},
                "artifacts": [{"kind": "spec", "id": "SPEC-STALE#R1"}],
            },
        },
        {
            "tool_id": "apatch_attest",
            "id": "op-a",
            "payload": {
                "artifacts": [
                    {"kind": "spec", "id": "SPEC-STALE#R1", "content_hash": "sha256:old"}
                ],
            },
        },
    ]

    def fake_ledger(_td):
        return entries, True

    monkeypatch.setattr("apatch.spec._ledger_entries", fake_ledger)
    monkeypatch.setattr("apatch.spec_coverage._ledger_entries", fake_ledger)

    from apatch.spec_coverage import spec_status_with_coverage

    out = spec_status_with_coverage(str(tmp_path), spec="SPEC-STALE")
    assert out["ok"] is True
    assert out["done"] is False
    r1 = out["requirements"][0]
    assert r1["state"] == "stale"
    assert r1.get("stale_reason") in ("spec_text_changed", "file_drift")

    f.write_text("v2\n", encoding="utf-8")
    out2 = spec_status_with_coverage(str(tmp_path), spec="SPEC-STALE")
    r1b = out2["requirements"][0]
    assert r1b["state"] == "stale"
    assert r1b.get("stale_reason") == "file_drift"


def test_doctor_and_template_surface():
    from apatch.agent_guidance import spec_execution_playbook

    pb = spec_execution_playbook()
    states = pb.get("states", "")
    assert "file_drift" in states or "file drift" in states.lower()

    template = Path(__file__).resolve().parent.parent / "docs" / "AGENTS.template.md"
    text = template.read_text(encoding="utf-8")
    assert "apatch_spec_coverage" in text


def test_spec_coverage_workspace_empty(tmp_path):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC-COVTEST.md").write_text(
        """# SPEC-COVTEST - x

> **apatch artifact:** `spec:SPEC-COVTEST`

## R1 A

(verify: true)
""",
        encoding="utf-8",
    )
    out = spec_coverage_workspace(str(tmp_path), spec="SPEC-COVTEST")
    assert out["ok"] is True
    assert out["summary"]["pending"] >= 1
