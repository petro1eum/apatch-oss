"""SPEC-AVATAR-FOUNDATION-1 (RFP-025) — Avatar Compiler / asset_summary tests."""
import inspect
import json
import os

import pytest


def _fake_entries():
    return [
        {"id": "i1", "tool_id": "apatch", "timestamp": "2026-06-01T00:00:00",
         "payload": {"action": "engineering_pipeline", "intent": "SPEC-FOO-1#R1",
                     "artifacts": [{"kind": "spec", "id": "SPEC-FOO-1#R1"}]}},
        {"id": "m1", "tool_id": "apatch", "timestamp": "2026-06-01T00:01:00",
         "payload": {"action": "apply", "applied_patches": 1}},
        {"id": "a1", "tool_id": "apatch_attest", "timestamp": "2026-06-02T00:00:00",
         "payload": {"action": "attest"}},
    ]


def _patch(monkeypatch):
    from apatch.trustchain_helper import TrustChainHelper
    monkeypatch.setattr(TrustChainHelper, "iter_ledger_entries",
                        lambda self: _fake_entries())
    monkeypatch.setattr("apatch.contribution.resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 8, "cert_fingerprint": "sha256:dead",
        "agent_id": "tester", "ca": "platform", "trust_level": "attested"})


def test_r0_self_coverage_rfp_025():
    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-025-avatar-foundation.md"), encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", "SPEC-AVATAR-FOUNDATION-1.md"), encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-025", spec_id="SPEC-AVATAR-FOUNDATION-1")
    assert out["passed"] is True
    assert not out.get("gaps")


def test_r1_asset_summary_schema(monkeypatch):
    _patch(monkeypatch)
    from apatch import avatar_compiler as A
    s = A.build_asset_summary(".")
    assert s["schema_version"] == 1 and s["kind"] == "asset_summary"
    assert s["artifact_count"] == 1
    assert s["spec_ids"] == ["SPEC-FOO-1"]
    assert s["spec_count"] == 1
    assert s["requirement_states"] == {"SPEC-FOO-1": ["R1"]}
    assert s["attested_requirement_count"] == 1
    assert "spec" in s["methodology_tags"]
    assert "SPEC-FOO" in s["methodology_tags"]  # family tag
    assert s["identity"]["key_id"] == "k" * 8
    assert s["attested_at_range"]["last"] is not None
    # deterministic
    assert A.build_asset_summary(".") == s


def test_r2_discovery_no_upload():
    from apatch.avatar_compiler import build_asset_summary
    params = list(inspect.signature(build_asset_summary).parameters)
    assert params == ["target_dir"]  # computed from ledger, no upload/profile input


def test_r3_economic_boundary(monkeypatch):
    _patch(monkeypatch)
    from apatch.avatar_compiler import build_asset_summary
    s = build_asset_summary(".")
    blob = json.dumps(s).lower()
    for forbidden in ("gpi", "creator_bonus", "creator bonus", "clearing", "\"pi\"", "marketplace"):
        assert forbidden not in blob
    assert not (set(s) & {"pi", "gpi", "creator_bonus", "clearing", "economics", "price"})


def test_r4_project_status_embeds_asset_summary(tmp_path):
    from apatch.project_status import project_status_workspace
    (tmp_path / ".apatch").mkdir()
    dto = project_status_workspace(str(tmp_path))
    assert "asset_summary" in dto
    assert dto["asset_summary"].get("kind") == "asset_summary"


def test_r5_cli_and_mcp_surfaces():
    from apatch.cli import cli
    assert "asset" in cli.commands  # CLI group registered
    pytest.importorskip("mcp")
    from apatch.mcp import server as S
    assert "apatch_asset_summary" in S.mcp._tool_manager._tools