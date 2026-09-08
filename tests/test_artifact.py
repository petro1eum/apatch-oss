"""RFP-006: Artifact-anchored intent."""

import json

import pytest

from apatch.artifact import (
    Artifact,
    coerce_artifacts,
    entry_matches_artifact_filter,
    normalize_manifest_artifacts,
    parse_artifact_token,
)
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import build_session_view, set_session_intent, start_session
from apatch.session_state import load_session_state
from apatch.trustchain_helper import TrustChainHelper
from apatch.workflows import trustchain_intent_history_workspace


def test_parse_artifact_token():
    a = parse_artifact_token("spec:SPEC-42@sha256:abc")
    assert a.kind == "spec"
    assert a.id == "SPEC-42"
    assert a.content_hash == "sha256:abc"
    assert a.token() == "spec:SPEC-42@sha256:abc"

    b = parse_artifact_token("adr:ADR-001")
    assert b.kind == "adr"
    assert b.id == "ADR-001"
    assert b.content_hash is None


def test_normalize_manifest_adr_legacy():
    manifest = {"intent": "refactor", "adr": "ADR-000", "artifacts": []}
    arts = normalize_manifest_artifacts(manifest)
    assert len(arts) == 1
    assert arts[0] == {"kind": "adr", "id": "ADR-000"}

    manifest2 = {
        "adr": "ADR-000",
        "artifacts": [{"kind": "spec", "id": "SPEC-1", "ref": "docs/spec.md"}],
    }
    arts2 = normalize_manifest_artifacts(manifest2)
    assert len(arts2) == 2
    assert {"kind": "spec", "id": "SPEC-1", "ref": "docs/spec.md"} in arts2
    assert {"kind": "adr", "id": "ADR-000"} in arts2


def test_session_start_binds_artifacts(tmp_path):
    arts = [{"kind": "spec", "id": "SPEC-42", "content_hash": "sha256:dead"}]
    out = start_session(str(tmp_path), "implement billing v2", artifacts=arts)
    assert out["ok"] is True
    state = load_session_state(str(tmp_path))
    assert state["artifacts"] == arts
    view = build_session_view(str(tmp_path))
    assert view["session"]["artifacts"] == arts


def test_set_session_intent_updates_artifacts(tmp_path):
    start_session(str(tmp_path), "first intent")
    updated = set_session_intent(
        str(tmp_path),
        "updated intent",
        artifacts=["ticket:JIRA-99"],
    )
    assert updated["ok"] is True
    state = load_session_state(str(tmp_path))
    assert state["intent"] == "updated intent"
    assert state["artifacts"] == [{"kind": "ticket", "id": "JIRA-99"}]


def test_attest_includes_artifacts_in_metadata(monkeypatch, tmp_path):
    from apatch.session_state import PHASE_COMPLETE, save_session_state

    arts = [{"kind": "spec", "id": "SPEC-42"}]
    start_session(str(tmp_path), "attest with artifact", artifacts=arts)
    state = load_session_state(str(tmp_path))
    state["phase"] = PHASE_COMPLETE
    save_session_state(str(tmp_path), state)

    captured = {}

    class FakeTC:
        def has_trustchain(self):
            return True

        def iter_ledger_entries(self):
            return iter([])

        def commit_action(self, tool_id, payload):
            captured["tool_id"] = tool_id
            captured["payload"] = payload
            return True

    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC())
    out = MutationRuntime(str(tmp_path)).attest()
    assert out["ok"] is True
    assert captured["tool_id"] == "apatch_attest"
    assert captured["payload"]["artifacts"] == arts


def test_spec_requirement_attest_warns_without_mutation(monkeypatch, tmp_path):
    from apatch.session_state import PHASE_COMPLETE, save_session_state

    arts = [{"kind": "spec", "id": "SPEC-42#R1", "content_hash": "sha256:abc"}]
    start_session(str(tmp_path), "SPEC-42#R1: only verify", artifacts=arts)
    state = load_session_state(str(tmp_path))
    state["phase"] = PHASE_COMPLETE
    save_session_state(str(tmp_path), state)

    class FakeTC:
        def has_trustchain(self):
            return True

        def iter_ledger_entries(self):
            return iter([])

        def commit_action(self, tool_id, payload):
            assert tool_id == "apatch_attest"
            return True

    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC())
    out = MutationRuntime(str(tmp_path)).attest()
    assert out["ok"] is True
    warning = out.get("spec_requirement_warning")
    assert warning["code"] == "SPEC_ATTEST_WITHOUT_MUTATION"
    assert warning["will_close_spec_requirement"] is False
    assert warning["artifacts"] == ["spec:SPEC-42#R1"]


def test_trustchain_enrich_uses_target_session_before_parent(tmp_path):
    parent = tmp_path / "repo"
    child = parent / "opensearch" / "search"
    child.mkdir(parents=True)
    (parent / ".trustchain").mkdir()
    arts = [{"kind": "spec", "id": "SPEC-CHILD#R1", "content_hash": "sha256:child"}]
    partition = {"spec:SPEC-CHILD#R1": ["x"]}
    start_session(
        str(child),
        "child spec session",
        artifacts=arts,
        artifact_files=partition,
    )

    helper = TrustChainHelper(str(child), auto_init=False)
    enriched = helper._enrich_payload_from_governed_session({"files": {"x": {"sha256": "1"}}})

    assert enriched["governed_session_id"]
    assert enriched["intent"] == "child spec session"
    assert enriched["artifacts"] == arts
    assert enriched["artifact_files"] == partition


def _write_ledger_entry(tmp_path, data: dict, name: str = "entry1.json") -> None:
    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    payload = {"value": {"tool_id": "apatch", "data": data, "signature": "sig1", "id": "op-1"}}
    (tc_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def test_trustchain_history_artifact_filter(tmp_path):
    _write_ledger_entry(
        tmp_path,
        {
            "action": "engineering_pipeline",
            "intent": "billing",
            "artifacts": [{"kind": "spec", "id": "SPEC-42"}],
            "manifest": "pipe.json",
        },
    )
    _write_ledger_entry(
        tmp_path,
        {
            "action": "engineering_pipeline",
            "intent": "other",
            "adr": "ADR-001",
            "manifest": "other.json",
        },
        name="entry2.json",
    )

    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    spec_hits = helper.list_intent_history(artifact="spec:SPEC-42")
    assert spec_hits["ok"] is True
    assert len(spec_hits["entries"]) == 1
    assert spec_hits["entries"][0]["artifacts"][0]["id"] == "SPEC-42"

    adr_hits = trustchain_intent_history_workspace(str(tmp_path), artifact="adr:ADR-001")
    assert len(adr_hits["entries"]) == 1
    assert adr_hits["entries"][0]["adr"] == "ADR-001"

    miss = helper.list_intent_history(artifact="spec:NONEXISTENT")
    assert miss["entries"] == []


def test_coerce_artifacts_dedupes():
    raw = ["spec:SPEC-1", {"kind": "spec", "id": "SPEC-1"}, "adr:ADR-2"]
    out = coerce_artifacts(raw)
    assert len(out) == 2
    assert {"kind": "spec", "id": "SPEC-1"} in out
    assert {"kind": "adr", "id": "ADR-2"} in out


def test_invalid_artifact_token():
    with pytest.raises(ValueError):
        parse_artifact_token("not-valid")


def test_doctor_embeds_artifact_playbook_for_agents(tmp_path):
    from apatch.doctor import run_doctor

    out = run_doctor(str(tmp_path))
    playbook = out.get("artifact_anchored_intent") or {}
    assert playbook.get("session_start", {}).get("tool") == "apatch_session_start"
    assert "apatch_trustchain_coverage" in playbook.get("after_attest", "")
    assert any("artifacts" in step for step in out.get("recommended_workflow") or [])
    sandbox = out.get("sandbox_agent_protocol") or {}
    assert "pip install" in str(sandbox.get("never_via_shell", [])).lower()
    assert sandbox.get("if_mcp_stale_or_missing_tools")


def _ledger(tmp_path, entries):
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
                "timestamp": data.get("timestamp", f"2026-06-07T10:00:{i:02d}Z"),
            }
        }
        (tc_dir / f"entry{i}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_trustchain_coverage_artifact_chain(tmp_path):
    _ledger(
        tmp_path,
        [
            {
                "tool_id": "apatch",
                "id": "op-intent",
                "payload": {
                    "action": "engineering_pipeline",
                    "intent": "billing v2",
                    "artifacts": [{"kind": "spec", "id": "SPEC-42"}],
                },
            },
            {
                "tool_id": "apatch",
                "id": "op-mut",
                "payload": {
                    "action": "apply",
                    "applied_patches": 3,
                    "files": {"src/billing.py": {"sha256": "abc"}},
                },
            },
            {
                "tool_id": "apatch_attest",
                "id": "op-attest",
                "payload": {
                    "intent": "billing v2",
                    "artifacts": [{"kind": "spec", "id": "SPEC-42"}],
                    "session_id": "apatch_sess_1",
                },
            },
        ],
    )
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    report = helper.artifact_coverage(artifact="spec:SPEC-42")
    assert report["ok"] is True
    assert report["coverage"]["complete"] is True
    assert report["coverage"]["mutation_count"] == 1
    assert report["coverage"]["attestation_count"] == 1
    assert "op-mut" in report["op_id_index"]
    assert "op-attest" in report["op_id_index"]


def test_resolve_op_id_reverse_mapping(tmp_path):
    _ledger(
        tmp_path,
        [
            {
                "tool_id": "apatch_attest",
                "id": "op-99",
                "payload": {
                    "artifacts": [{"kind": "ticket", "id": "JIRA-99"}],
                    "intent": "hotfix",
                },
            }
        ],
    )
    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    resolved = helper.resolve_op_id("op-99")
    assert resolved["ok"] is True
    assert resolved["role"] == "attestation"
    assert resolved["artifacts"][0]["id"] == "JIRA-99"


def test_commit_enriches_governed_session_artifacts(tmp_path, monkeypatch):
    # Patches trustchain.TrustChain directly, so the module must be importable.
    pytest.importorskip("trustchain")
    from apatch.session_state import save_session_state

    save_session_state(
        str(tmp_path),
        {
            "session_id": "apatch_sess_test",
            "intent": "enriched intent",
            "artifacts": [{"kind": "spec", "id": "SPEC-1"}],
            "phase": "idle",
        },
    )
    captured = {}

    class FakeTC:
        def sign(self, tool_id, data):
            captured["payload"] = data

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper.has_trustchain",
        lambda self: True,
    )
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper._policy_allows",
        lambda self, *_a, **_k: True,
    )
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper._tc_config",
        lambda self: object(),
    )
    monkeypatch.setattr("trustchain.TrustChain", lambda *_a, **_k: FakeTC())

    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    helper.trustchain_dir = str(tmp_path / ".trustchain")
    helper.commit_action("apatch", {"action": "apply", "files": {"a.py": {"sha256": "x"}}})
    assert captured["payload"]["artifacts"] == [{"kind": "spec", "id": "SPEC-1"}]
    assert captured["payload"]["governed_session_id"] == "apatch_sess_test"
