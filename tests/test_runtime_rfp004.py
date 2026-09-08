import json

from apatch.runtime.attestation import build_attestation_view
from apatch.runtime.events import emit_domain_event, events_path, tail_events
from apatch.runtime.session import build_session_view, end_session, start_session
from apatch.session_state import enrich_tool_response


def test_session_start_requires_intent(tmp_path):
    r = start_session(str(tmp_path), "")
    assert r["ok"] is False


def test_session_start_end_lifecycle(tmp_path):
    r = start_session(str(tmp_path), "R44: arch fix")
    assert r["ok"] is True
    assert r["session"]["intent"] == "R44: arch fix"
    assert r["session"]["lifecycle"] == "draft"
    assert r["invariant"]["satisfied"] is True

    view = build_session_view(str(tmp_path))
    assert view["schema_version"] == 1
    assert view["session"]["session_id"]

    ended = end_session(str(tmp_path))
    assert ended["ok"] is True
    assert ended["session"]["lifecycle"] == "ended"


def test_session_start_rejects_duplicate_active(tmp_path):
    assert start_session(str(tmp_path), "first")["ok"] is True
    second = start_session(str(tmp_path), "second")
    assert second["ok"] is False
    assert "active session" in second["error"]


def test_session_start_rejects_duplicate_after_successful_mutation(tmp_path):
    from apatch.session_state import enrich_tool_response

    assert start_session(str(tmp_path), "first")["ok"] is True
    enrich_tool_response(
        "apatch_strip",
        {"ok": True, "checkpoint": "ck"},
        target_dir=str(tmp_path),
    )
    second = start_session(str(tmp_path), "second")
    assert second["ok"] is False


def test_domain_events_append(tmp_path):
    p = emit_domain_event(str(tmp_path), "SessionStarted", {"intent": "x"}, session_id="s1")
    assert p == events_path(str(tmp_path))
    rows = tail_events(str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["type"] == "SessionStarted"
    assert rows[0]["session_id"] == "s1"


def test_enrich_emits_mutation_applied(tmp_path):
    start_session(str(tmp_path), "apply test")
    enrich_tool_response(
        "apatch_apply_session",
        {"ok": True, "checkpoint": "ck1", "trustchain_committed": True},
        target_dir=str(tmp_path),
    )
    types = [e["type"] for e in tail_events(str(tmp_path))]
    assert "MutationApplied" in types
    assert "AttestationCommitted" in types


def test_attestation_view_schema(tmp_path):
    view = build_attestation_view(str(tmp_path))
    assert view["ok"] is True
    assert view["attestation"]["mode"] in ("audit_pending", "audit", "enforce")
    assert "verification_note" in view


def test_verify_semantic_uses_op_verify(monkeypatch, tmp_path):
    from apatch.runtime.runtime import MutationRuntime
    from apatch.runtime.state_machine import OP_VERIFY

    seen: list[str] = []
    monkeypatch.setattr(
        "apatch.runtime.runtime.assert_operation",
        lambda _td, op: seen.append(op),
    )
    monkeypatch.setattr(
        "apatch.workflows.semantic_verify_workspace",
        lambda *_a, **_k: {"ok": True, "violations": []},
    )
    out = MutationRuntime(str(tmp_path)).verify_semantic()
    assert OP_VERIFY in seen
    assert isinstance(out, dict)


def test_verify_allowed_after_session_end(tmp_path):
    from apatch.runtime.runtime import MutationRuntime
    from apatch.runtime.state_machine import assert_operation, OP_VERIFY

    start_session(str(tmp_path), "verify after end")
    end_session(str(tmp_path))
    # Must not raise — verify is read-only post-session
    assert_operation(str(tmp_path), OP_VERIFY)


def test_set_session_intent_updates_active(tmp_path):
    from apatch.runtime.session import set_session_intent, start_session

    start_session(str(tmp_path), "first")
    out = set_session_intent(str(tmp_path), "second")
    assert out["ok"] is True
    assert out.get("intent_updated") is True
    assert out["session"]["intent"] == "second"


def test_cli_session_status_json(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    runner = CliRunner()
    runner.invoke(cli, ["session", "start", "--intent", "demo", "--target-dir", str(tmp_path)])
    result = runner.invoke(
        cli, ["session", "status", "--json", "--target-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["session"]["intent"] == "demo"
