"""SPEC-WORK-ASSET-LIFECYCLE-1 (RFP-031 Phase 2 / AUC-1) — lifecycle tests."""
import json
import os

import pytest

SPEC = "SPEC-WORK-ASSET-LIFECYCLE-1"


def _write_spec(tmp_path, spec_id=SPEC):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / f"{spec_id}.md").write_text(
        "\n".join([
            f"# {spec_id} — Governed promotion workflow",
            "",
            f"> **apatch artifact:** `spec:{spec_id}`",
            "",
            "## R1 Ledger-derived promotion states",
            "",
            "(verify: `python3 -m pytest tests/test_work_asset_lifecycle.py::test_r1 -q`)",
        ]),
        encoding="utf-8",
    )


def _write_method(tmp_path, spec_id=SPEC, text=None):
    method_dir = tmp_path / "docs" / "work_assets"
    method_dir.mkdir(parents=True, exist_ok=True)
    path = method_dir / f"{spec_id}.method.md"
    path.write_text(text if text is not None else "\n".join([
        f"# Method — {spec_id}",
        "",
        "## Procedure",
        "1. Plan the governed change. 2. Apply. 3. Attest.",
        "",
        "## Checks",
        "Run the requirement verify commands.",
        "",
        "## Contraindications",
        "Do not use outside a governed session; boundaries apply.",
    ]), encoding="utf-8")
    return path


def _base_entries(spec_id=SPEC):
    """Complete-coverage triple so Phase-1 extraction yields one asset."""
    return [
        {"id": "i1", "tool_id": "apatch", "timestamp": "2026-06-01T00:00:00",
         "payload": {"action": "engineering_pipeline", "intent": f"{spec_id}#R1",
                     "artifacts": [{"kind": "spec", "id": f"{spec_id}#R1"}]}},
        {"id": "m1", "tool_id": "apatch", "timestamp": "2026-06-01T00:01:00",
         "payload": {"action": "apply", "applied_patches": 1}},
        {"id": "a1", "tool_id": "apatch_attest", "timestamp": "2026-06-01T00:02:00",
         "payload": {"action": "attest"}},
    ]


def _promote_event(op_id="p1", ts="2026-06-02T00:00:00", spec_id=SPEC,
                   from_state="candidate", to_state="accepted", method=True, **extra):
    payload = {"action": "work_asset_promote", "spec_id": spec_id,
               "from_state": from_state, "to_state": to_state, "reason": "vetted"}
    if method:
        payload["method"] = {"path": f"docs/work_assets/{spec_id}.method.md",
                             "sha256": "f" * 64}
    payload.update(extra)
    return {"id": op_id, "tool_id": "apatch_work_asset_promote",
            "timestamp": ts, "payload": payload}


def _use_event(op_id="u1", ts="2026-06-03T00:00:00", spec_id=SPEC, ok=True):
    return {"id": op_id, "tool_id": "apatch_work_asset_use", "timestamp": ts,
            "payload": {"action": "work_asset_use", "spec_id": spec_id,
                        "adaptation": "unchanged",
                        "verification": {"command": "pytest -q", "ok": ok}}}


def _patch(monkeypatch, entries):
    from apatch.trustchain_helper import TrustChainHelper

    monkeypatch.setattr(TrustChainHelper, "iter_ledger_entries", lambda self: list(entries))
    monkeypatch.setattr("apatch.contribution.resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 8, "cert_fingerprint": "sha256:dead", "agent_id": "tester",
        "ca": "platform", "trust_level": "attested",
    })


def _capture_commits(monkeypatch):
    from apatch.trustchain_helper import TrustChainHelper

    calls = []

    def fake_commit(self, tool_id, payload):
        calls.append((tool_id, payload))
        return True

    monkeypatch.setattr(TrustChainHelper, "commit_action", fake_commit)
    return calls


def test_r0_self_coverage_rfp_031_lifecycle():
    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-031-work-assets.md"), encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", f"{SPEC}.md"), encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-031", spec_id=SPEC)
    assert out["passed"] is True
    assert not out.get("gaps")


def test_r1_promotion_states_ledger_derived(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    from apatch import work_assets as W
    from apatch import work_asset_lifecycle as L

    # no events -> candidate; fold is deterministic
    _patch(monkeypatch, _base_entries())
    first = W.build_work_asset_index(str(tmp_path))
    assert first["assets"][0]["lifecycle"] == "candidate"
    assert first == W.build_work_asset_index(str(tmp_path))

    # valid signed transition -> accepted, lifecycle_counts follow
    _patch(monkeypatch, _base_entries() + [_promote_event()])
    accepted = W.build_work_asset_index(str(tmp_path))
    assert accepted["assets"][0]["lifecycle"] == "accepted"
    assert accepted["lifecycle_counts"] == {"accepted": 1}

    # forged invalid transition (candidate -> deprecated) is ignored at fold time
    _patch(monkeypatch, _base_entries() + [_promote_event(to_state="deprecated")])
    forged = W.build_work_asset_index(str(tmp_path))
    assert forged["assets"][0]["lifecycle"] == "candidate"

    # write path refuses invalid transitions and commits nothing
    calls = _capture_commits(monkeypatch)
    _patch(monkeypatch, _base_entries())
    out = L.promote_work_asset(str(tmp_path), SPEC, "deprecated")
    assert out["ok"] is False
    assert "invalid transition" in " ".join(out["errors"])
    assert calls == []

    # accepted -> superseded requires superseded_by, then commits with snapshot
    _write_method(tmp_path)
    _patch(monkeypatch, _base_entries() + [_promote_event()])
    missing = L.promote_work_asset(str(tmp_path), SPEC, "superseded")
    assert missing["ok"] is False
    ok = L.promote_work_asset(str(tmp_path), SPEC, "superseded", superseded_by="wa_next_123")
    assert ok["ok"] is True
    tool_id, payload = calls[-1]
    assert tool_id == "apatch_work_asset_promote"
    assert payload["action"] == "work_asset_promote"
    assert payload["from_state"] == "accepted"
    assert payload["superseded_by"] == "wa_next_123"


def test_r2_method_required_for_acceptance(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    from apatch import work_asset_lifecycle as L

    calls = _capture_commits(monkeypatch)
    _patch(monkeypatch, _base_entries())

    # no method file -> refused, nothing committed (R-AUC-1)
    out = L.promote_work_asset(str(tmp_path), SPEC, "accepted")
    assert out["ok"] is False
    assert any("method file not found" in e for e in out["errors"])
    assert calls == []

    # method missing a required section -> refused
    _write_method(tmp_path, text="# Method\n\n## Procedure\nonly a procedure here, verify nothing")
    out = L.promote_work_asset(str(tmp_path), SPEC, "accepted")
    assert out["ok"] is False
    assert any("missing required section: contraindications" in e for e in out["errors"])

    # method violating the content-safety boundary -> refused
    _write_method(tmp_path, text="\n".join([
        "## Procedure", "use the password from vault",
        "## Checks", "verify output", "## Contraindications", "boundaries apply",
    ]))
    out = L.promote_work_asset(str(tmp_path), SPEC, "accepted")
    assert out["ok"] is False
    assert any("content-safety" in e for e in out["errors"])
    assert calls == []

    # proper method -> committed with pinned sha256
    import hashlib
    path = _write_method(tmp_path)
    out = L.promote_work_asset(str(tmp_path), SPEC, "accepted")
    assert out["ok"] is True
    tool_id, payload = calls[-1]
    assert tool_id == "apatch_work_asset_promote"
    assert payload["to_state"] == "accepted"
    assert payload["method"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert payload["method"]["path"] == os.path.join("docs", "work_assets", f"{SPEC}.method.md")


def test_r3_use_record_shape_and_boundary(tmp_path, monkeypatch):
    from apatch import work_asset_lifecycle as L

    calls = _capture_commits(monkeypatch)

    # invalid inputs refused
    assert L.record_work_asset_use(str(tmp_path), "", verification={"ok": True})["ok"] is False
    assert L.record_work_asset_use(str(tmp_path), SPEC, verification=None)["ok"] is False
    assert L.record_work_asset_use(str(tmp_path), SPEC, verification={"ok": True},
                                   adaptation="rewritten")["ok"] is False
    assert calls == []

    # full A31-F shape
    out = L.record_work_asset_use(
        str(tmp_path), SPEC,
        verification={"command": "python3 -m pytest -q", "ok": True},
        adaptation="adapted", spec_refs=[f"spec:{SPEC}#R1"],
        proof_ref="a1", asset_id="wa_snapshot_1", session_id="sess-9",
    )
    assert out["ok"] is True
    tool_id, payload = calls[-1]
    assert tool_id == "apatch_work_asset_use"
    assert payload["action"] == "work_asset_use"
    assert payload["spec_id"] == SPEC
    assert payload["session_id"] == "sess-9"
    assert payload["spec_refs"] == [f"spec:{SPEC}#R1"]
    assert payload["proof_ref"] == "a1"
    assert payload["verification"] == {"command": "python3 -m pytest -q", "ok": True}
    assert payload["adaptation"] == "adapted"

    # economic boundary over the signed payload
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("gpi", "creator_bonus", "clearing", "escrow",
                      "marketplace", "price", '"pi"'):
        assert forbidden not in blob


def test_cli_promote_and_use_surfaces(tmp_path, monkeypatch):
    """CLI wiring: work-assets promote/use commit signed events; refusals exit 1."""
    import json as _json
    from click.testing import CliRunner

    _write_spec(tmp_path)
    _write_method(tmp_path)
    calls = _capture_commits(monkeypatch)
    _patch(monkeypatch, _base_entries())
    from apatch.cli import cli

    # invalid transition -> refused, exit 1, nothing committed
    res = CliRunner().invoke(cli, ["work-assets", "promote", SPEC, "deprecated",
                                   "--target-dir", str(tmp_path), "--json"])
    assert res.exit_code == 1
    assert calls == []

    # governed acceptance -> signed event with pinned method
    res = CliRunner().invoke(cli, ["work-assets", "promote", SPEC, "accepted",
                                   "--reason", "vetted", "--target-dir", str(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    out = _json.loads(res.output)
    assert out["ok"] is True and out["event"]["method"]["sha256"]
    assert calls[-1][0] == "apatch_work_asset_promote"

    # use record with verification outcome
    res = CliRunner().invoke(cli, ["work-assets", "use", SPEC,
                                   "--verify-command", "pytest -q", "--verify-ok",
                                   "--adaptation", "adapted",
                                   "--target-dir", str(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    tool_id, payload = calls[-1]
    assert tool_id == "apatch_work_asset_use"
    assert payload["verification"] == {"command": "pytest -q", "ok": True}
    assert payload["adaptation"] == "adapted"


def test_r4_reuse_from_ledger(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    from apatch import work_assets as W

    # zero use events -> exactly the INDEX-1 baseline shape
    _patch(monkeypatch, _base_entries())
    baseline = W.build_work_asset_index(str(tmp_path))["assets"][0]
    assert baseline["reuse"] == {"count": 0, "last_used_at": None}

    # two signed use events -> count 2, last_used_at = newest timestamp
    _patch(monkeypatch, _base_entries() + [
        _use_event("u1", "2026-06-03T00:00:00"),
        _use_event("u2", "2026-06-04T12:00:00", ok=False),
    ])
    used = W.build_work_asset_index(str(tmp_path))["assets"][0]
    assert used["reuse"] == {"count": 2, "last_used_at": "2026-06-04T12:00:00"}


def test_r5_recallable_flag(tmp_path, monkeypatch):
    _write_spec(tmp_path)
    from apatch import work_assets as W

    # candidate -> visible-only
    _patch(monkeypatch, _base_entries())
    asset = W.build_work_asset_index(str(tmp_path))["assets"][0]
    assert asset["recallable"] is False
    assert asset["method_ref"] is None

    # accepted with pinned method -> participates in recall (AUC-1 §5)
    _patch(monkeypatch, _base_entries() + [_promote_event()])
    asset = W.build_work_asset_index(str(tmp_path))["assets"][0]
    assert asset["recallable"] is True
    assert asset["method_ref"]["sha256"] == "f" * 64

    # hand-forged accepted WITHOUT method -> accepted but never recallable
    _patch(monkeypatch, _base_entries() + [_promote_event(method=False)])
    asset = W.build_work_asset_index(str(tmp_path))["assets"][0]
    assert asset["lifecycle"] == "accepted"
    assert asset["recallable"] is False
