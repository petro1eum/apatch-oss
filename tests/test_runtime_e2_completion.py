"""RFP-004 E2 completion — strip/pipeline/runtime facades and failure contracts."""

import json

import pytest

from apatch.failure_taxonomy import (
    ERROR_APPLY_FAILED,
    ERROR_VERIFY_FAILED,
    RECOMMENDED_ACTION,
    RECOVERABLE_DEFAULTS,
    classify_failure,
)
from apatch.runtime.attestation import export_attestation_bundle
from apatch.runtime.errors import RuntimeTransitionError
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import continue_apply_session, start_session
from apatch.runtime.state_machine import OP_PIPELINE, OP_STRIP, assert_operation


def _enable_enforce(tmp_path):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "enforcement.json").write_text(
        json.dumps({"mode": "strict"}),
        encoding="utf-8",
    )


def test_strip_routes_through_runtime(monkeypatch, tmp_path):
    seen = []

    def fake_strip(file_path, **kwargs):
        seen.append(file_path)
        return {"ok": True, "strip_results": []}

    monkeypatch.setattr("apatch.runtime.runtime.run_strip", fake_strip)
    out = MutationRuntime(str(tmp_path)).strip("sample.cpp", dry_run=True)
    assert out.get("ok") is True
    assert seen == ["sample.cpp"]


def test_pipeline_routes_through_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "apatch.runtime.runtime.pipeline_run_manifest",
        lambda m, t, dry_run=False: {"ok": True, "manifest": m},
    )
    manifest = tmp_path / "pipe.json"
    manifest.write_text("{}", encoding="utf-8")
    out = MutationRuntime(str(tmp_path)).pipeline_run(str(manifest))
    assert out.get("ok") is True


def test_enforce_strip_requires_session(tmp_path):
    _enable_enforce(tmp_path)
    with pytest.raises(RuntimeTransitionError):
        assert_operation(str(tmp_path), OP_STRIP)


def test_enforce_pipeline_requires_session(tmp_path):
    _enable_enforce(tmp_path)
    with pytest.raises(RuntimeTransitionError):
        assert_operation(str(tmp_path), OP_PIPELINE)


def test_continue_apply_session_delegates(monkeypatch, tmp_path):
    calls = []

    class FakeRT:
        def apply_session(self, logs, **kwargs):
            calls.append((logs, kwargs.get("reset")))
            return {"ok": True, "continue": False}

    monkeypatch.setattr(
        "apatch.runtime.runtime.MutationRuntime",
        lambda _td: FakeRT(),
    )
    logs = tmp_path / "p.jsonl"
    logs.write_text("", encoding="utf-8")
    out = continue_apply_session(str(tmp_path), str(logs))
    assert out["ok"] is True
    assert calls == [(str(logs), False)]


def test_apply_session_reset_binds_lane_before_reopening_phase(monkeypatch, tmp_path):
    order = []
    runtime = MutationRuntime(str(tmp_path))
    monkeypatch.setattr(
        runtime,
        "_capture_binding",
        lambda *args, **kwargs: order.append("bind"),
    )
    monkeypatch.setattr(runtime, "_reset_phase_for_reapply", lambda: order.append("reset"))
    monkeypatch.setattr(runtime, "_ensure_mutation", lambda *args, **kwargs: order.append("ensure"))
    monkeypatch.setattr(runtime, "_finish", lambda _tool, result: result)
    monkeypatch.setattr(
        "apatch.runtime.runtime.assert_operation",
        lambda *args, **kwargs: order.append("assert"),
    )
    monkeypatch.setattr(
        "apatch.runtime.runtime.run_apply_session",
        lambda *args, **kwargs: {"ok": True},
    )

    out = runtime.apply_session("patches.jsonl", reset=True)

    assert out["ok"] is True
    assert order[:3] == ["bind", "reset", "ensure"]


def test_attestation_export_bundle(tmp_path):
    start_session(str(tmp_path), "export test")
    out_path = tmp_path / "audit.json"
    result = export_attestation_bundle(str(tmp_path), str(out_path))
    assert result["ok"] is True
    assert out_path.is_file()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["session"]["session"]["intent"] == "export test"
    assert "attestation" in data
    assert "events" in data


@pytest.mark.parametrize(
    "error_type",
    [
        ERROR_VERIFY_FAILED,
        ERROR_APPLY_FAILED,
    ],
)
def test_failure_taxonomy_has_lifecycle_actions(error_type):
    assert error_type in RECOVERABLE_DEFAULTS
    assert error_type in RECOMMENDED_ACTION
    failure = classify_failure(
        {"ok": False, "error": "test", "verify_rollback": error_type == ERROR_VERIFY_FAILED},
        "apatch_apply",
    )
    assert failure is not None
    assert failure.error_type in (error_type, ERROR_APPLY_FAILED, ERROR_VERIFY_FAILED)
