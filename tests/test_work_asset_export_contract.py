"""SPEC-WORK-ASSET-EXPORT-1 — stable WorkAsset export contract tests."""
import json
import os


def _write_spec(tmp_path):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC-WORK-ASSET-EXPORT-1.md").write_text(
        "\n".join([
            "# SPEC-WORK-ASSET-EXPORT-1 — WorkAsset export contract",
            "",
            "> **apatch artifact:** `spec:SPEC-WORK-ASSET-EXPORT-1`",
            "",
            "## R1 Export contract identifiers and schema",
            "",
            "(verify: `python3 -m pytest tests/test_work_asset_export_contract.py::test_r1_export_contract_schema -q`)",
        ]),
        encoding="utf-8",
    )


def _fake_entries():
    artifacts = [{"kind": "spec", "id": "SPEC-WORK-ASSET-EXPORT-1#R1"}]
    return [
        {"id": "i1", "tool_id": "apatch", "timestamp": "2026-06-01T00:00:00",
         "payload": {"action": "engineering_pipeline", "intent": "SPEC-WORK-ASSET-EXPORT-1#R1",
                     "artifacts": artifacts}},
        {"id": "m1", "tool_id": "apatch", "timestamp": "2026-06-01T00:01:00",
         "payload": {"action": "apply", "applied_patches": 1}},
        {"id": "a1", "tool_id": "apatch_attest", "timestamp": "2026-06-01T00:02:00",
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


def _assert_contract(bundle):
    from apatch import work_assets as W

    assert bundle["schema_version"] == 1
    assert bundle["contract_id"] == W.EXPORT_CONTRACT_ID == "apatch.work_asset_export.v1"
    assert bundle["contract_version"] == W.EXPORT_CONTRACT_VERSION == 1
    assert bundle["kind"] == "work_asset_export"
    assert bundle["content_safe"] is True
    assert bundle["privacy_boundary"]["raw_work_exported"] is False
    assert bundle["privacy_boundary"]["source_code_exported"] is False
    assert bundle["privacy_boundary"]["credentials_exported"] is False
    assert bundle["data_access"]["raw_workspace"] == "not_exported"
    assert bundle["data_access"]["export_bundle"] == "explicit_export_only"
    assert bundle["data_access"]["external_training"] == "not_allowed_without_separate_consent"
    assert bundle["consent_retention"]["export_requires_explicit_action"] is True
    assert bundle["consent_retention"]["external_training_allowed"] is False
    assert bundle["assets"][0]["portability_policy"]["portability_class"]
    assert bundle["assets"][0]["portability_policy"]["rights_claim"] == "evidence_record_not_ownership_transfer"
    assert W.validate_work_asset_export_bundle(bundle)["ok"] is True


def test_r1_export_contract_schema(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W
    from apatch.cli import cli
    from click.testing import CliRunner

    schema = W.work_asset_export_schema()
    assert schema["$id"] == W.EXPORT_CONTRACT_ID
    assert "contract_id" in schema["required"]
    assert "privacy_boundary" in schema["required"]
    assert "data_access" in schema["required"]
    assert "consent_retention" in schema["required"]
    assert schema["properties"]["contract_version"]["const"] == 1
    bundle = W.export_work_assets(str(tmp_path))
    _assert_contract(bundle)

    res = CliRunner().invoke(cli, ["work-assets", "schema", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["$id"] == W.EXPORT_CONTRACT_ID


def test_r2_export_fixture_is_content_safe():
    from apatch import work_assets as W

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    fixture_path = os.path.join(root, "docs", "examples", "work_asset_export_v1.example.json")
    with open(fixture_path, encoding="utf-8") as fh:
        fixture = json.load(fh)
    _assert_contract(fixture)
    blob = json.dumps(fixture, ensure_ascii=False).lower()
    for forbidden in ("private_key", "password", "ssh://"):
        assert forbidden not in blob


def test_r4_export_policy_rejects_raw_work_and_training(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W

    bundle = W.export_work_assets(str(tmp_path))
    bundle["privacy_boundary"]["raw_work_exported"] = True
    bundle["consent_retention"]["external_training_allowed"] = True
    bundle["assets"][0]["portability_policy"]["exports_code"] = True
    bundle["assets"][0]["portability_policy"]["rights_claim"] = "ownership_transfer"

    result = W.validate_work_asset_export_bundle(bundle)

    assert result["ok"] is False
    assert "privacy_boundary.raw_work_exported" in result["errors"]
    assert "consent_retention.external_training_allowed" in result["errors"]
    assert "assets.0.portability_policy.exports_code" in result["errors"]
    assert "assets.0.portability_policy.rights_claim" in result["errors"]


def test_r3_export_contract_keeps_economics_out(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W

    bundle = W.export_work_assets(str(tmp_path))
    blob = json.dumps(bundle, ensure_ascii=False).lower()
    for forbidden in ("gpi", "creator_bonus", "creator bonus", "clearing", "escrow", "marketplace", "price", '"pi"'):
        assert forbidden not in blob


def test_extension_payload_is_not_a_work_asset_export(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    _patch(monkeypatch)
    from apatch import work_assets as W

    bundle = W.export_work_assets(str(tmp_path))
    bundle["assets"][0]["extension_manifest"] = {
        "owner": "someone else",
        "license": "redeclared",
        "code": "print('embedded')",
    }
    result = W.validate_work_asset_export_bundle(bundle)
    assert result["ok"] is False
    assert "extension_payload.assets.0.extension_manifest" in result["errors"]

    top_level = W.export_work_assets(str(tmp_path))
    top_level["extensions"] = [{"artifact": "runner.py"}]
    result = W.validate_work_asset_export_bundle(top_level)
    assert result["ok"] is False
    assert "extension_payload.extensions" in result["errors"]


def test_r4_mcp_schema_surface():
    import pytest

    pytest.importorskip("mcp")
    from apatch import work_assets as W
    from apatch.mcp import server as S

    assert "apatch_work_asset_export_schema" in S.mcp._tool_manager._tools
    call = S.mcp._tool_manager._tools["apatch_work_asset_export_schema"].fn
    assert call()["$id"] == W.EXPORT_CONTRACT_ID
