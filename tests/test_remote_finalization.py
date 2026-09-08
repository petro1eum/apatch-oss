"""Remote repair must preserve the session's mandatory verification boundary."""

import pytest

from apatch.remote import worker
from apatch.remote.orchestrator import remote_task_run
from apatch.remote.transport import FakeRemoteTransport


TARGET = "ssh://example-search-host/srv/repo"
NEEDLE = {"action": "replace", "target_file": "a.py", "find_text": "a", "replace_text": "b"}


def test_fix_forward_deferred_does_not_verify_attest_or_close():
    transport = FakeRemoteTransport()
    result = remote_task_run(
        TARGET, "deferred repair",
        plan={"fix_forward_current": True, "defer_finalize": True,
              "verify_deferred": False, "needles": [NEEDLE]},
        transport=transport,
    )
    assert result["ok"]
    assert result["finalize_deferred"] is True
    assert result["finalized"] is False
    operations = [call["operation"] for call in transport.calls]
    assert operations == [
        "apatch_doctor", "apatch_resume_session", "apatch_generate_batch",
        "apatch_simulate", "apatch_apply_session",
    ]
    assert transport.calls[-1]["arguments"]["plan"]["verify_deferred"] is True


@pytest.mark.parametrize("plan", [
    {"fix_forward_current": True, "defer_finalize": "true", "needles": [NEEDLE]},
    {"defer_finalize": True, "needles": [NEEDLE]},
    {"finalize_current": True, "defer_finalize": True},
])
def test_invalid_defer_is_rejected_before_transport(plan):
    transport = FakeRemoteTransport()
    result = remote_task_run(TARGET, "bad plan", plan=plan, verify="true", transport=transport)
    assert result["error_type"] == "REMOTE_DEFER_FINALIZE_INVALID"
    assert transport.calls == []


@pytest.mark.parametrize("plan", [
    {"finalize_current": True},
    {"fix_forward_current": True, "needles": [NEEDLE]},
])
def test_old_worker_cannot_silently_attest_shortened_verify(plan):
    transport = FakeRemoteTransport({"apatch_verify_run": {"ok": True}})
    result = remote_task_run(TARGET, "short check", plan=plan, verify="true", transport=transport)
    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_SPEC_VERIFY_UNCONFIRMED"
    assert transport.calls[-1]["arguments"]["enforce_requirement_verify"] is True
    assert "apatch_attest" not in [call["operation"] for call in transport.calls]
    assert "apatch_session_end" not in [call["operation"] for call in transport.calls]


@pytest.fixture
def bound_requirement(tmp_path, monkeypatch):
    from apatch.spec import resolve_requirement

    folder = tmp_path / "docs" / "specs"
    folder.mkdir(parents=True)
    spec = folder / "SPEC-FINALIZE-1.md"
    spec.write_text(
        "# SPEC-FINALIZE-1\n"
        "> **apatch artifact:** `spec:SPEC-FINALIZE-1`\n"
        "## R1 Mandatory live check\n"
        "(verify: python -m pytest tests/live.py -q)\n"
    )
    resolved = resolve_requirement(str(tmp_path), "SPEC-FINALIZE-1#R1")
    assert resolved["ok"]
    state = {
        "session_id": "session-1",
        "artifacts": [{"kind": "spec", "id": "SPEC-FINALIZE-1#R1",
                       "content_hash": resolved["content_hash"]}],
    }
    monkeypatch.setattr("apatch.session_state.load_session_state", lambda _: state)
    return str(tmp_path), state, spec, resolved["verify"]


class RecordingRuntime:
    def __init__(self, results=()):
        self.commands = []
        self.results = list(results)

    def verify_run(self, *, verify):
        self.commands.append(verify)
        return self.results.pop(0) if self.results else {"ok": True, "verify_command": verify}


def test_short_verify_is_supplementary_not_a_replacement(bound_requirement):
    root, _, _, mandatory = bound_requirement
    runtime = RecordingRuntime()
    result = worker._verify_for_finalization(runtime, root, {"verify": "python -m pytest tests/unit.py"})
    assert runtime.commands == [mandatory, "python -m pytest tests/unit.py"]
    assert result["requirement_verification"]["complete"] is True


def test_identical_verify_is_executed_once(bound_requirement):
    root, _, _, mandatory = bound_requirement
    runtime = RecordingRuntime()
    result = worker._verify_for_finalization(runtime, root, {"verify": mandatory})
    assert result["ok"]
    assert runtime.commands == [mandatory]


def test_failed_mandatory_check_cannot_be_masked_by_green_short_check(bound_requirement):
    root, _, _, mandatory = bound_requirement
    runtime = RecordingRuntime([{"ok": False, "error_type": "VERIFY_FAILED"}])
    result = worker._verify_for_finalization(runtime, root, {"verify": "true"})
    assert result["ok"] is False
    assert result["requirement_verification"]["complete"] is False
    assert runtime.commands == [mandatory]


def test_pending_async_check_is_not_completion(bound_requirement):
    root, _, _, mandatory = bound_requirement
    runtime = RecordingRuntime([{"ok": True, "verify_job_id": "job-1", "verify_job_state": "running"}])
    result = worker._verify_for_finalization(runtime, root, {"verify": "true"})
    assert result["error_type"] == "REMOTE_SPEC_VERIFY_PENDING"
    assert runtime.commands == [mandatory]


@pytest.mark.parametrize("options", [
    {"command": "true", "dry_run": True},
    {"command": "true", "baseline": "compare"},
    {"command": "true", "allowed_failures": ["*"]},
    {"command": "true", "semantic": True},
    {"command": "true", "async_mode": True},
])
def test_finalization_rejects_verify_weakening_options(bound_requirement, options):
    root, _, _, _ = bound_requirement
    runtime = RecordingRuntime()
    result = worker._verify_for_finalization(runtime, root, {"verify": options})
    assert result["error_type"] == "REMOTE_SPEC_VERIFY_OPTIONS_INVALID"
    assert runtime.commands == []


@pytest.mark.parametrize("mode,error", [
    ("drift", "REMOTE_SPEC_VERIFY_DRIFT"),
    ("missing", "REMOTE_SPEC_VERIFY_UNRESOLVED"),
    ("unbound", "REMOTE_SPEC_VERIFY_BINDING_MISSING"),
    ("ended", "REMOTE_SPEC_VERIFY_SESSION_MISSING"),
    ("no_verify", "REMOTE_SPEC_VERIFY_MISSING"),
])
def test_missing_or_changed_requirement_fails_closed(bound_requirement, mode, error):
    from apatch.spec import resolve_requirement

    root, state, spec, _ = bound_requirement
    if mode == "drift":
        spec.write_text(spec.read_text().replace("tests/live.py", "tests/unit.py"))
    elif mode == "missing":
        spec.unlink()
    elif mode == "unbound":
        state["artifacts"][0].pop("content_hash")
    elif mode == "ended":
        state["ended_at"] = "now"
    else:
        spec.write_text("# SPEC-FINALIZE-1\n## R1 No verify\n")
        state["artifacts"][0]["content_hash"] = resolve_requirement(root, "SPEC-FINALIZE-1#R1")["content_hash"]
    runtime = RecordingRuntime()
    result = worker._verify_for_finalization(runtime, root, {"verify": "true"})
    assert result["error_type"] == error
    assert runtime.commands == []


def test_legacy_non_spec_session_keeps_explicit_verify(bound_requirement):
    root, state, _, _ = bound_requirement
    state["artifacts"] = []
    runtime = RecordingRuntime()
    result = worker._verify_for_finalization(runtime, root, {"verify": "pytest"})
    assert result["ok"]
    assert result["requirement_verification"]["requirements"] == []
    assert runtime.commands == ["pytest"]


def test_worker_dispatch_uses_guard_and_does_not_drop_binding(monkeypatch):
    runtime = RecordingRuntime()
    monkeypatch.setattr(worker, "_mutation_runtime", lambda *_: runtime)
    captured = {}

    def guarded(rt, root, args):
        captured.update(args)
        assert rt is runtime
        return {"ok": False, "error_type": "SESSION_TOKEN_MISMATCH"}

    monkeypatch.setattr(worker, "_verify_for_finalization", guarded)
    result = worker.dispatch({"operation": "apatch_verify_run", "arguments": {
        "verify": "true", "enforce_requirement_verify": True,
        "governed_session_id": "exact-session", "session_token": "test-only",
    }})
    assert result["error_type"] == "SESSION_TOKEN_MISMATCH"
    assert captured["governed_session_id"] == "exact-session"
    assert captured["session_token"] == "test-only"
    assert runtime.commands == []


def test_mcp_reports_deferred_fix_forward_without_claiming_finalization(monkeypatch):
    pytest.importorskip("mcp")
    from apatch.mcp.server import apatch_remote_task_run

    monkeypatch.setattr("apatch.remote.orchestrator.remote_task_run", lambda *a, **k: {
        "ok": True, "finalize_deferred": True,
        "timeline": [{"operation": "apatch_apply_session", "ok": True,
                      "result": {"ok": True, "applied": 1}}],
    })
    result = apatch_remote_task_run(
        remote_target=TARGET, intent="repair",
        plan={"fix_forward_current": True, "defer_finalize": True, "needles": [NEEDLE]},
        dry_run=False,
    )
    assert result["applied"] is True
    assert result["finalize_deferred"] is True
    assert result["finalized"] is False
    assert "DEFERRED" in result["message"]
    assert "attested, and closed" not in result["message"]
