"""RFP-004 completion — typed Session, attest, verify status, apply interactive, CLI/MCP."""

import json

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.runtime.errors import RuntimeTransitionError
from apatch.runtime.models import Session
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import load_typed_session, start_session
from apatch.runtime.state_machine import OP_ATTEST, assert_operation
from apatch.runtime.verification import build_verification_status
from apatch.session_state import PHASE_COMPLETE, save_session_state, load_session_state


def _enable_enforce(tmp_path):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir(exist_ok=True)
    (apatch_dir / "enforcement.json").write_text(
        json.dumps({"mode": "strict"}),
        encoding="utf-8",
    )


def test_session_dataclass_from_view(tmp_path):
    start_session(str(tmp_path), "typed session")
    sess = load_typed_session(str(tmp_path))
    assert isinstance(sess, Session)
    assert sess.intent == "typed session"
    assert sess.lifecycle == "draft"
    assert sess.active is True
    d = sess.to_dict()
    assert d["intent"] == "typed session"


def test_verification_status_schema(tmp_path):
    start_session(str(tmp_path), "verify status")
    status = build_verification_status(str(tmp_path))
    assert status["ok"] is True
    assert status["session"]["intent"] == "verify status"
    assert "verification" in status
    assert "recommended" in status["verification"]


def test_attest_commits_via_runtime(monkeypatch, tmp_path):
    start_session(str(tmp_path), "attest me")
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
            assert payload["intent"] == "attest me"
            return True

    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", lambda *_a, **_k: FakeTC())
    out = MutationRuntime(str(tmp_path)).attest()
    assert out["ok"] is True
    assert out["committed"] is True


def test_attest_blocked_in_draft_when_enforce(tmp_path):
    _enable_enforce(tmp_path)
    start_session(str(tmp_path), "no attest yet")
    with pytest.raises(RuntimeTransitionError):
        assert_operation(str(tmp_path), OP_ATTEST)


def test_apply_interactive_routes_through_runtime(monkeypatch, tmp_path):
    seen = []

    def fake_apply(*_a, **kwargs):
        seen.append(kwargs.get("interactive"))
        return {"ok": True, "total": 1, "applied": 0, "skipped": 1, "failed": 0}

    monkeypatch.setattr("apatch.runtime.runtime.apply_from_logs", fake_apply)
    logs = tmp_path / "p.jsonl"
    logs.write_text('{"type":"test"}\n', encoding="utf-8")
    out = MutationRuntime(str(tmp_path)).apply_interactive(str(logs))
    assert seen == [True]
    assert out.get("ok") is True


def test_attest_emits_domain_event(monkeypatch, tmp_path):
    from apatch.runtime.events import tail_events

    start_session(str(tmp_path), "event attest")
    state = load_session_state(str(tmp_path))
    state["phase"] = PHASE_COMPLETE
    save_session_state(str(tmp_path), state)

    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper",
        lambda *_a, **_k: type(
            "TC",
            (),
            {
                "has_trustchain": lambda self: True,
                "iter_ledger_entries": lambda self: iter([]),
                "commit_action": lambda *a, **k: True,
            },
        )(),
    )
    MutationRuntime(str(tmp_path)).attest()
    types = [e["type"] for e in tail_events(str(tmp_path))]
    assert "AttestationCommitted" in types


def test_cli_verify_status_json(tmp_path):
    start_session(str(tmp_path), "cli status")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["verify", "status", "--json", "--target-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["session"]["intent"] == "cli status"


def test_cli_verify_run_semantic(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "apatch.runtime.runtime.MutationRuntime.verify_run",
        lambda self, **kwargs: {"ok": True, "violations": []},
    )
    runner = CliRunner()
    result = runner.invoke(
        cli, ["verify", "run", "--semantic", "--json", "--target-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True


def test_cli_session_continue_json(monkeypatch, tmp_path):
    class FakeRT:
        def apply_session(self, logs, **kwargs):
            return {"ok": True, "continue": False, "progress": {"chunks_done": 1, "chunks_total": 1}}

    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", lambda _td: FakeRT())
    runner = CliRunner()
    runner.invoke(cli, ["session", "start", "--intent", "cont", "--target-dir", str(tmp_path)])
    logs = tmp_path / "empty.jsonl"
    logs.write_text("", encoding="utf-8")
    result = runner.invoke(
        cli,
        [
            "session",
            "continue",
            "--logs",
            str(logs),
            "--json",
            "--target-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True


def test_cli_attestation_export(tmp_path):
    runner = CliRunner()
    runner.invoke(cli, ["session", "start", "--intent", "exp", "--target-dir", str(tmp_path)])
    out_file = tmp_path / "bundle.json"
    result = runner.invoke(
        cli,
        [
            "attestation",
            "export",
            "--out",
            str(out_file),
            "--target-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert out_file.is_file()


def test_cli_mutation_plan_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "apatch.runtime.runtime.MutationRuntime.plan",
        lambda self, logs, **kwargs: {"ok": True, "total": 0, "would_apply": 0, "entries": []},
    )
    logs = tmp_path / "p.jsonl"
    logs.write_text("", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "mutation",
            "plan",
            "--logs",
            str(logs),
            "--json",
            "--target-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0


def test_console_mutations_panel_empty():
    from apatch.console.mvp import _mutations_panel

    panel = _mutations_panel({})
    assert "Mutations" in panel.title


def test_console_mutations_panel_with_entries():
    from apatch.console.mvp import _mutations_panel

    ui = {
        "plan_entries": [
            {
                "step_index": 1,
                "target_file": "foo.py",
                "strategy": "exact",
                "confidence": 0.99,
                "would_apply": True,
            }
        ],
        "plan_summary": {"would_apply": 1, "total": 1},
    }
    panel = _mutations_panel(ui)
    assert "1/1" in panel.title
