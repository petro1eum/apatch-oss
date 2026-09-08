"""SPEC-WORK-ASSET-INDEX-1 (RFP-031) — WorkAsset / Наработка tests."""
import json
import os

import pytest
from click.testing import CliRunner


def _write_spec(tmp_path, spec_id="SPEC-WORK-ASSET-INDEX-1"):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    path = spec_dir / f"{spec_id}.md"
    path.write_text(
        "\n".join([
            f"# {spec_id} — Remote SSH governed repair workflow",
            "",
            "> **apatch artifact:** `spec:SPEC-WORK-ASSET-INDEX-1`",
            "",
            "## RFP traceability",
            "",
            "| RFP acceptance | SPEC coverage | Status |",
            "|----------------|---------------|--------|",
            "| RFP-031 A31-A WorkAsset definition | R1 | covered |",
            "| RFP-031 A31-B Candidate extraction | R2 | covered |",
            "",
            "## R1 WorkAsset schema v1",
            "",
            "(verify: `python3 -m pytest tests/test_work_assets.py::test_r1_work_asset_schema -q`)",
            "",
            "## R2 Deterministic candidate extraction",
            "",
            "(verify: `python3 -m pytest tests/test_work_assets.py::test_r2_candidate_extraction_deterministic -q`)",
        ]),
        encoding="utf-8",
    )
    return path


def _fake_entries():
    artifacts_r1 = [{"kind": "spec", "id": "SPEC-WORK-ASSET-INDEX-1#R1"}]
    artifacts_r2 = [{"kind": "spec", "id": "SPEC-WORK-ASSET-INDEX-1#R2"}]
    return [
        {"id": "i1", "tool_id": "apatch", "timestamp": "2026-06-01T00:00:00",
         "payload": {"action": "engineering_pipeline", "intent": "SPEC-WORK-ASSET-INDEX-1#R1",
                     "artifacts": artifacts_r1}},
        {"id": "m1", "tool_id": "apatch", "timestamp": "2026-06-01T00:01:00",
         "payload": {"action": "apply", "applied_patches": 1}},
        {"id": "a1", "tool_id": "apatch_attest", "timestamp": "2026-06-01T00:02:00",
         "payload": {"action": "attest"}},
        {"id": "i2", "tool_id": "apatch", "timestamp": "2026-06-01T00:03:00",
         "payload": {"action": "engineering_pipeline", "intent": "SPEC-WORK-ASSET-INDEX-1#R2",
                     "artifacts": artifacts_r2}},
        {"id": "m2", "tool_id": "apatch", "timestamp": "2026-06-01T00:04:00",
         "payload": {"action": "apply", "applied_patches": 1}},
        {"id": "a2", "tool_id": "apatch_attest", "timestamp": "2026-06-01T00:05:00",
         "payload": {"action": "attest"}},
    ]


def _patch(monkeypatch):
    from apatch.trustchain_helper import TrustChainHelper

    monkeypatch.setattr(TrustChainHelper, "iter_ledger_entries", lambda self: _fake_entries())
    monkeypatch.setattr("apatch.contribution.resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 8,
        "cert_fingerprint": "sha256:dead",
        "agent_id": "tester",
        "ca": "platform",
        "trust_level": "attested",
    })


def test_r0_self_coverage_rfp_031():
    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-031-work-assets.md"), encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", "SPEC-WORK-ASSET-INDEX-1.md"), encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-031", spec_id="SPEC-WORK-ASSET-INDEX-1")
    assert out["passed"] is True
    assert not out.get("gaps")


def test_r1_work_asset_schema(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W

    index = W.build_work_asset_index(str(tmp_path))
    assert index["schema_version"] == 1
    assert index["kind"] == "work_asset_index"
    assert index["asset_count"] == 1
    asset = index["assets"][0]
    assert asset["schema_version"] == 1
    assert asset["kind"] == "work_asset"
    assert asset["asset_id"].startswith("wa_spec_work_asset_index_1_")
    assert asset["asset_kind"] == "spec_bundle"
    assert asset["title"] == "Remote SSH governed repair workflow"
    assert asset["owner"]["key_id"] == "k" * 8
    assert asset["spec_refs"] == ["spec:SPEC-WORK-ASSET-INDEX-1#R1", "spec:SPEC-WORK-ASSET-INDEX-1#R2"]
    assert "rfp:RFP-031" in asset["rfp_refs"]
    assert asset["proof_refs"] == [{"ledger": "trustchain", "op_ids": ["a1", "a2"]}]
    assert len(asset["verification_refs"]) == 2
    assert asset["source_evidence"]["metadata_only"] is True
    assert asset["confidentiality_class"] == "metadata_only"
    assert asset["portability_policy"] == {
        "content_safe": True,
        "exports_code": False,
        "exports_private_prompts": False,
        "exports_credentials": False,
        "exports_ssh_topology": False,
        "raw_logs": False,
        "portability_class": "portable_method_review_required",
        "rights_claim": "evidence_record_not_ownership_transfer",
        "employer_review": "required_when_private_context_or_contract_applies",
        "redaction": "strict",
    }
    assert asset["dependency_lens"]["human_method"] == 1.0
    assert asset["reuse"] == {"count": 0, "last_used_at": None}
    assert asset["lifecycle"] == "candidate"


def test_verify_refs_parse_without_backticks(tmp_path, monkeypatch):
    """Regression: verify lines without backticks (SPEC-TENANT-1 style) must
    still land in verification_refs — bundle evidence should not be lost."""
    _patch(monkeypatch)
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC-WORK-ASSET-INDEX-1.md").write_text(
        "\n".join([
            "# SPEC-WORK-ASSET-INDEX-1 — Bare verify style",
            "## R1 One",
            "(verify: pytest backend/tests/test_one.py -q)",
            "## R2 Two",
            "(verify: `python3 -m pytest tests/test_two.py -q`)",
        ]),
        encoding="utf-8",
    )
    from apatch import work_assets as W

    asset = W.build_work_asset_index(str(tmp_path))["assets"][0]
    assert asset["verification_refs"] == [
        "pytest backend/tests/test_one.py -q",
        "python3 -m pytest tests/test_two.py -q",
    ]


def test_r2_candidate_extraction_deterministic(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W

    first = W.build_work_asset_index(str(tmp_path))
    second = W.build_work_asset_index(str(tmp_path))
    assert first == second
    asset_id = first["assets"][0]["asset_id"]
    found = W.search_work_assets(str(tmp_path), query="remote ssh")
    assert found["count"] == 1
    assert found["results"][0]["asset"]["asset_id"] == asset_id
    limited = W.list_work_assets(str(tmp_path), limit=0)
    assert limited["asset_count"] == 0
    assert limited["lifecycle_counts"] == {}
    assert W.show_work_asset(str(tmp_path), asset_id)["ok"] is True
    missing = W.show_work_asset(str(tmp_path), "missing")
    assert missing["ok"] is False
    assert missing["error_type"] == "WORK_ASSET_NOT_FOUND"


def test_r3_cli_and_mcp_surfaces(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch.cli import cli

    assert "work-assets" in cli.commands
    list_res = CliRunner().invoke(cli, ["work-assets", "list", "--target-dir", str(tmp_path), "--json"])
    assert list_res.exit_code == 0, list_res.output
    listed = json.loads(list_res.output)
    asset_id = listed["assets"][0]["asset_id"]
    show_res = CliRunner().invoke(cli, ["work-assets", "show", asset_id, "--target-dir", str(tmp_path), "--json"])
    assert show_res.exit_code == 0, show_res.output
    search_res = CliRunner().invoke(cli, ["work-assets", "search", "remote ssh", "--target-dir", str(tmp_path), "--json"])
    assert search_res.exit_code == 0, search_res.output
    export_res = CliRunner().invoke(cli, ["work-assets", "export", "--target-dir", str(tmp_path), "--json"])
    assert export_res.exit_code == 0, export_res.output

    pytest.importorskip("mcp")
    from apatch.mcp import server as S

    for name in (
        "apatch_work_assets",
        "apatch_work_asset_show",
        "apatch_work_asset_search",
        "apatch_work_asset_export",
    ):
        assert name in S.mcp._tool_manager._tools
    call = S.mcp._tool_manager._tools["apatch_work_assets"].fn
    out = call(target_dir=str(tmp_path))
    assert out["asset_count"] == 1


def test_r4_export_bundle_redaction_boundary(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W

    bundle = W.export_work_assets(str(tmp_path))
    assert bundle["kind"] == "work_asset_export"
    assert bundle["content_safe"] is True
    assert bundle["redaction"] == {
        "exports_code": False,
        "exports_private_prompts": False,
        "exports_credentials": False,
        "exports_ssh_topology": False,
        "raw_logs": False,
    }
    blob = json.dumps(bundle, ensure_ascii=False).lower()
    for forbidden in ("private_key", "password", "ssh://"):
        assert forbidden not in blob
    assert bundle["assets"][0]["source_evidence"]["metadata_only"] is True


def test_r5_economic_boundary(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W

    index = W.build_work_asset_index(str(tmp_path))
    bundle = W.export_work_assets(str(tmp_path))
    for obj in (index, bundle):
        blob = json.dumps(obj, ensure_ascii=False).lower()
        for forbidden in ("gpi", "creator_bonus", "creator bonus", "clearing", "escrow", "marketplace", "price", '"pi"'):
            assert forbidden not in blob
