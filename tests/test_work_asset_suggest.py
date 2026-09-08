"""SPEC-WORK-ASSET-SUGGEST-1 (RFP-031 Phase 3 / AUC-1 recall edge) — tests."""
import hashlib
import json
import os

import pytest

SPEC_A = "SPEC-DEMO-ALPHA-1"
SPEC_B = "SPEC-DEMO-BETA-1"


def _write_spec(tmp_path, spec_id, title):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / f"{spec_id}.md").write_text(
        "\n".join([
            f"# {spec_id} — {title}",
            "",
            f"> **apatch artifact:** `spec:{spec_id}`",
            "",
            "## R1 Requirement",
            "",
            "(verify: `python3 -m pytest tests/test_demo.py -q`)",
        ]),
        encoding="utf-8",
    )


def _write_method(tmp_path, spec_id, body="Apply the guarded steps."):
    method_dir = tmp_path / "docs" / "work_assets"
    method_dir.mkdir(parents=True, exist_ok=True)
    path = method_dir / f"{spec_id}.method.md"
    path.write_text("\n".join([
        f"# Method — {spec_id}",
        "## Procedure", body,
        "## Checks", "Run the verify commands.",
        "## Contraindications", "Boundaries: governed sessions only.",
    ]), encoding="utf-8")
    return path


def _coverage_entries(spec_id, prefix, minute=0):
    """Complete-coverage triple; `minute` keeps multi-spec streams strictly
    ordered so one spec's intent never preempts another's apply/attest."""
    def ts(offset):
        return f"2026-06-01T00:{minute + offset:02d}:00"
    return [
        {"id": f"{prefix}i", "tool_id": "apatch", "timestamp": ts(0),
         "payload": {"action": "engineering_pipeline", "intent": f"{spec_id}#R1",
                     "artifacts": [{"kind": "spec", "id": f"{spec_id}#R1"}]}},
        {"id": f"{prefix}m", "tool_id": "apatch", "timestamp": ts(1),
         "payload": {"action": "apply", "applied_patches": 1}},
        {"id": f"{prefix}a", "tool_id": "apatch_attest", "timestamp": ts(2),
         "payload": {"action": "attest"}},
    ]


def _promote_event(spec_id, sha256, op_id, ts="2026-06-02T00:00:00"):
    return {"id": op_id, "tool_id": "apatch_work_asset_promote", "timestamp": ts,
            "payload": {"action": "work_asset_promote", "spec_id": spec_id,
                        "from_state": "candidate", "to_state": "accepted",
                        "reason": "vetted",
                        "method": {"path": f"docs/work_assets/{spec_id}.method.md",
                                   "sha256": sha256}}}


def _use_event(spec_id, op_id, ts):
    return {"id": op_id, "tool_id": "apatch_work_asset_use", "timestamp": ts,
            "payload": {"action": "work_asset_use", "spec_id": spec_id,
                        "adaptation": "unchanged",
                        "verification": {"command": "pytest -q", "ok": True}}}


def _patch(monkeypatch, entries):
    from apatch.trustchain_helper import TrustChainHelper

    monkeypatch.setattr(TrustChainHelper, "iter_ledger_entries", lambda self: list(entries))
    monkeypatch.setattr("apatch.contribution.resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 8, "cert_fingerprint": "sha256:dead", "agent_id": "tester",
        "ca": "platform", "trust_level": "attested",
    })


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _two_recallable(tmp_path, monkeypatch):
    """Two accepted+method assets: BETA has 2 signed use events, ALPHA none."""
    _write_spec(tmp_path, SPEC_A, "Guarded remote repair workflow")
    _write_spec(tmp_path, SPEC_B, "Guarded remote repair playbook")
    sha_a = _sha(_write_method(tmp_path, SPEC_A))
    sha_b = _sha(_write_method(tmp_path, SPEC_B))
    entries = (
        _coverage_entries(SPEC_A, "a") + _coverage_entries(SPEC_B, "b", minute=10) +
        [_promote_event(SPEC_A, sha_a, "p1"), _promote_event(SPEC_B, sha_b, "p2"),
         _use_event(SPEC_B, "u1", "2026-06-03T00:00:00"),
         _use_event(SPEC_B, "u2", "2026-06-04T00:00:00")]
    )
    _patch(monkeypatch, entries)
    return entries


def test_r0_self_coverage_rfp_031_suggest():
    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-031-work-assets.md"), encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", "SPEC-WORK-ASSET-SUGGEST-1.md"),
              encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-031", spec_id="SPEC-WORK-ASSET-SUGGEST-1")
    assert out["passed"] is True
    assert not out.get("gaps")


def test_r1_intent_suggestion_explainable(tmp_path, monkeypatch):
    _two_recallable(tmp_path, monkeypatch)
    from apatch.work_asset_suggest import suggest_work_assets

    out = suggest_work_assets(str(tmp_path), intent="guarded remote repair")
    assert out["kind"] == "work_asset_suggestions"
    assert out["count"] == 2
    top = out["results"][0]
    # equal term match -> the reused asset (BETA, 2 signed uses) ranks first
    assert top["spec_id"] == SPEC_B
    assert top["reuse"]["count"] == 2
    assert 0 < top["score"] <= 1.0
    assert set(top["reasons"]) <= {"guarded", "remote", "repair"}
    assert top["reasons"]  # explainable: at least one matched term
    # deterministic: same ledger + same intent -> same output
    assert out == suggest_work_assets(str(tmp_path), intent="guarded remote repair")
    # limit honored
    assert suggest_work_assets(str(tmp_path), intent="guarded", limit=1)["count"] == 1


def test_r2_only_recallable_participate(tmp_path, monkeypatch):
    # candidate only (no promotion) -> excluded from recall
    _write_spec(tmp_path, SPEC_A, "Guarded remote repair workflow")
    _patch(monkeypatch, _coverage_entries(SPEC_A, "a"))
    from apatch import work_assets as W
    from apatch.work_asset_suggest import build_recall_bundle, suggest_work_assets

    out = suggest_work_assets(str(tmp_path), intent="guarded remote repair")
    assert out["count"] == 0
    asset_id = W.build_work_asset_index(str(tmp_path))["assets"][0]["asset_id"]
    bundle = build_recall_bundle(str(tmp_path), asset_id)
    assert bundle["ok"] is False
    assert bundle["error_type"] == "NOT_RECALLABLE"

    # forged accepted WITHOUT method -> still excluded
    _patch(monkeypatch, _coverage_entries(SPEC_A, "a") + [{
        "id": "p1", "tool_id": "apatch_work_asset_promote",
        "timestamp": "2026-06-02T00:00:00",
        "payload": {"action": "work_asset_promote", "spec_id": SPEC_A,
                    "from_state": "candidate", "to_state": "accepted"},
    }])
    assert suggest_work_assets(str(tmp_path), intent="guarded")["count"] == 0


def test_r3_guarded_next_readonly(tmp_path, monkeypatch):
    _two_recallable(tmp_path, monkeypatch)
    from apatch.trustchain_helper import TrustChainHelper
    from apatch import work_assets as W
    from apatch.work_asset_suggest import build_recall_bundle, suggest_work_assets

    calls = []
    monkeypatch.setattr(TrustChainHelper, "commit_action",
                        lambda self, tool_id, payload: calls.append(tool_id) or True)

    out = suggest_work_assets(str(tmp_path), intent="guarded remote repair")
    for row in out["results"]:
        assert row["next"]["tool"] == "apatch_session_start"
        assert f"work_asset:{row['asset_id']}" in row["next"]["artifacts"]
        assert any(a.startswith("spec:") for a in row["next"]["artifacts"])
    asset_id = out["results"][0]["asset_id"]
    bundle = build_recall_bundle(str(tmp_path), asset_id, intent="guarded")
    assert bundle["next_action"]["tool"] == "apatch_session_start"
    # read-only surface: no ledger writes from suggest/bundle
    assert calls == []


def test_r4_recall_bundle_shape_and_budget(tmp_path, monkeypatch):
    _two_recallable(tmp_path, monkeypatch)
    from apatch import work_assets as W
    from apatch.work_asset_suggest import BUNDLE_MAX_BYTES, build_recall_bundle

    index = W.build_work_asset_index(str(tmp_path))
    beta = next(a for a in index["assets"] if SPEC_B in (a.get("spec_refs") or [""])[0])
    bundle = build_recall_bundle(str(tmp_path), beta["asset_id"], intent="guarded repair")
    assert bundle["ok"] is True
    assert bundle["kind"] == "recall_bundle"
    for key in ("why_fit", "method", "context", "evidence", "rights", "next_action"):
        assert key in bundle, key
    assert bundle["method"]["mode"] == "inline"
    assert "Procedure" in bundle["method"]["content"]
    assert bundle["why_fit"]["matched"]
    assert bundle["evidence"]["reuse"]["count"] == 2
    assert bundle["rights"]["portability_policy"]["content_safe"] is True
    # deterministic
    assert bundle == build_recall_bundle(str(tmp_path), beta["asset_id"], intent="guarded repair")
    size = len(json.dumps(bundle, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8"))
    assert size <= BUNDLE_MAX_BYTES

    # oversized method -> pointer fallback, never truncated prose
    big = _write_method(tmp_path, SPEC_A, body="guarded step " * 3000)  # >16KB
    entries = (_coverage_entries(SPEC_A, "a") +
               [_promote_event(SPEC_A, _sha(big), "p1")])
    _patch(monkeypatch, entries)
    alpha_id = W.build_work_asset_index(str(tmp_path))["assets"][0]["asset_id"]
    fallback = build_recall_bundle(str(tmp_path), alpha_id)
    assert fallback["ok"] is True
    assert fallback["method"]["mode"] == "pointer"
    assert "content" not in fallback["method"]
    assert fallback["method"]["sha256"] == _sha(big)


def test_cli_suggest_and_recall_surfaces(tmp_path, monkeypatch):
    """CLI wiring: work-assets suggest/recall (read-only) + MCP recall family."""
    import json as _json
    from click.testing import CliRunner

    _two_recallable(tmp_path, monkeypatch)
    from apatch.cli import cli

    res = CliRunner().invoke(cli, ["work-assets", "suggest", "--intent",
                                   "guarded remote repair", "--target-dir", str(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    out = _json.loads(res.output)
    assert out["count"] == 2
    asset_id = out["results"][0]["asset_id"]

    res = CliRunner().invoke(cli, ["work-assets", "recall", asset_id,
                                   "--target-dir", str(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    bundle = _json.loads(res.output)
    assert bundle["ok"] is True and bundle["kind"] == "recall_bundle"

    # non-recallable id -> exit 1 (honest failure)
    res = CliRunner().invoke(cli, ["work-assets", "recall", "missing",
                                   "--target-dir", str(tmp_path), "--json"])
    assert res.exit_code == 1

    pytest.importorskip("mcp")
    from apatch.mcp import server as S

    for name in ("apatch_work_asset_suggest", "apatch_work_asset_recall",
                 "apatch_work_asset_use"):
        assert name in S.mcp._tool_manager._tools


def test_r5_method_integrity_and_boundary(tmp_path, monkeypatch):
    from apatch import work_assets as W
    from apatch.work_asset_suggest import build_recall_bundle

    # (a) drifted method -> METHOD_INTEGRITY refusal
    _write_spec(tmp_path, SPEC_A, "Guarded remote repair workflow")
    path = _write_method(tmp_path, SPEC_A)
    entries = _coverage_entries(SPEC_A, "a") + [_promote_event(SPEC_A, _sha(path), "p1")]
    _patch(monkeypatch, entries)
    path.write_text(path.read_text(encoding="utf-8") + "\ntampered", encoding="utf-8")
    asset_id = W.build_work_asset_index(str(tmp_path))["assets"][0]["asset_id"]
    out = build_recall_bundle(str(tmp_path), asset_id)
    assert out["ok"] is False
    assert out["error_type"] == "METHOD_INTEGRITY"
    assert "re-promote" in out["error"]

    # (b) forbidden content pinned by a forged event -> BOUNDARY_VIOLATION
    bad = _write_method(tmp_path, SPEC_A, body="use the password from vault")
    _patch(monkeypatch, _coverage_entries(SPEC_A, "a") +
           [_promote_event(SPEC_A, _sha(bad), "p1")])
    asset_id = W.build_work_asset_index(str(tmp_path))["assets"][0]["asset_id"]
    out = build_recall_bundle(str(tmp_path), asset_id)
    assert out["ok"] is False
    assert out["error_type"] == "BOUNDARY_VIOLATION"
