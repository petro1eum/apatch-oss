from __future__ import annotations

import json

from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import start_session
from apatch.session_state import PHASE_COMPLETE, load_session_state, save_session_state


class _CommittedTrustChain:
    def has_trustchain(self):
        return True

    def iter_ledger_entries(self):
        return iter([])

    def commit_action(self, tool_id, payload):
        assert tool_id == "apatch_attest"
        return True


class _NoTrustChain:
    def has_trustchain(self):
        return False


def _ready_attestation(monkeypatch, tmp_path, trustchain=None) -> None:
    start_session(str(tmp_path), "Avatar receipt status")
    state = load_session_state(str(tmp_path))
    state["phase"] = PHASE_COMPLETE
    save_session_state(str(tmp_path), state)
    chain = trustchain if trustchain is not None else _CommittedTrustChain()
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper",
        lambda *_a, **_k: chain,
    )


def test_attest_reports_emitted_contribution(monkeypatch, tmp_path):
    _ready_attestation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "apatch.contribution.emit_contribution",
        lambda *_a, **_k: {"event_id": "event-avatar-1"},
    )

    result = MutationRuntime(str(tmp_path)).attest()

    assert result["ok"] is True
    assert result["contribution_receipt"] == {
        "status": "emitted",
        "event_id": "event-avatar-1",
    }


def test_attest_passes_only_explicit_completion_summary(monkeypatch, tmp_path):
    _ready_attestation(monkeypatch, tmp_path)
    captured = {}

    def emit(_target_dir, _session, *, completion_summary=None, **_kwargs):
        captured["completion_summary"] = completion_summary
        return {"event_id": "event-avatar-summary"}

    monkeypatch.setattr("apatch.contribution.emit_contribution", emit)

    result = MutationRuntime(str(tmp_path)).attest(
        message="Delivered the declared public result."
    )

    assert result["ok"] is True
    assert captured == {
        "completion_summary": "Delivered the declared public result."
    }


def test_attest_automatically_runs_outcome_first_avatar_sync(monkeypatch, tmp_path):
    _ready_attestation(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        "apatch.contribution.emit_contribution",
        lambda *_a, **_k: {"event_id": "event-avatar-1"},
    )
    monkeypatch.setattr(
        "apatch.avatar_delivery.sync_avatar_state",
        lambda target_dir: calls.append(target_dir) or {
            "ok": True,
            "complete": True,
            "status": "synchronized",
            "outcomes": {"status": "pulled", "stored": 1},
            "evidence": {"delivery": {"delivered": 1, "pending": 0}},
        },
    )

    result = MutationRuntime(str(tmp_path)).attest()

    assert calls == [str(tmp_path)]
    assert result["avatar_sync"]["complete"] is True
    assert result["avatar_sync"]["outcomes"]["stored"] == 1


def test_attest_sanitizes_avatar_sync_failure(monkeypatch, tmp_path):
    _ready_attestation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "apatch.contribution.emit_contribution",
        lambda *_a, **_k: {"event_id": "event-avatar-1"},
    )

    def failed_sync(_target_dir):
        raise RuntimeError("secret-token-and-private-path")

    monkeypatch.setattr(
        "apatch.avatar_delivery.sync_avatar_state",
        failed_sync,
    )

    result = MutationRuntime(str(tmp_path)).attest()

    assert result["avatar_sync"] == {
        "ok": False,
        "status": "evidence_sync_failed",
        "retryable": True,
        "outbox_preserved": True,
    }
    assert "secret-token-and-private-path" not in json.dumps(result)


def test_attest_reports_missing_avatar_contract_without_failing_commit(
    monkeypatch, tmp_path
):
    _ready_attestation(monkeypatch, tmp_path)

    def unavailable(*_a, **_k):
        raise ImportError("private-module-path-must-not-leak")

    monkeypatch.setattr("apatch.contribution.emit_contribution", unavailable)
    result = MutationRuntime(str(tmp_path)).attest()

    assert result["ok"] is True
    assert result["committed"] is True
    assert result["contribution_receipt"] == {
        "status": "unavailable",
        "code": "AVATAR_CONTRACT_UNAVAILABLE",
    }
    assert "private-module-path-must-not-leak" not in json.dumps(result)


def test_attest_reports_emission_failure_without_leaking_exception(
    monkeypatch, tmp_path
):
    _ready_attestation(monkeypatch, tmp_path)

    def failed(*_a, **_k):
        raise RuntimeError("sensitive-avatar-emission-detail")

    monkeypatch.setattr("apatch.contribution.emit_contribution", failed)
    result = MutationRuntime(str(tmp_path)).attest()

    assert result["ok"] is True
    assert result["committed"] is True
    assert result["contribution_receipt"] == {
        "status": "failed",
        "code": "CONTRIBUTION_EMISSION_FAILED",
    }
    assert "sensitive-avatar-emission-detail" not in json.dumps(result)


def test_attest_without_trustchain_has_no_unbound_receipt_status(
    monkeypatch, tmp_path
):
    _ready_attestation(monkeypatch, tmp_path, _NoTrustChain())

    result = MutationRuntime(str(tmp_path)).attest()

    assert result["ok"] is True
    assert result["committed"] is False
    assert "contribution_receipt" not in result
