from __future__ import annotations

import json

import pytest

from apatch.runtime.request_journal import request_journal_path, run_idempotent_request
from apatch.runtime.runtime import MutationRuntime


def test_request_journal_replays_once_without_persisting_secret(tmp_path):
    calls = []
    request_id = "request-1234567890abcdef"

    def execute():
        calls.append(True)
        return {"ok": True, "value": 7, "session_token": "top-secret"}

    first = run_idempotent_request(
        str(tmp_path), operation="unit", request_id=request_id, payload={"value": 7}, execute=execute
    )
    second = run_idempotent_request(
        str(tmp_path), operation="unit", request_id=request_id, payload={"value": 7}, execute=execute
    )

    assert len(calls) == 1
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    persisted = open(request_journal_path(str(tmp_path)), encoding="utf-8").read()
    assert request_id not in persisted
    assert "top-secret" not in persisted


def test_session_start_retry_rotates_capability_without_second_session(tmp_path):
    root = str(tmp_path)
    request_id = "session-start-1234567890"
    first = MutationRuntime(root).open_session("retry me", request_id=request_id)
    second = MutationRuntime(root).open_session("retry me", request_id=request_id)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["idempotent_replay"] is True
    assert second["session_capability"]["session_id"] == first["session"]["session_id"]
    assert second["session_token"] != first["session_token"]
    journal = json.load(open(request_journal_path(root), encoding="utf-8"))
    assert len(journal["requests"]) == 1


def test_request_id_cannot_be_reused_for_different_payload(tmp_path):
    request_id = "request-conflict-123456"
    run_idempotent_request(
        str(tmp_path), operation="unit", request_id=request_id, payload={"value": 1}, execute=lambda: {"ok": True}
    )
    conflict = run_idempotent_request(
        str(tmp_path), operation="unit", request_id=request_id, payload={"value": 2}, execute=lambda: {"ok": True}
    )
    assert conflict["ok"] is False
    assert conflict["error_type"] == "REQUEST_ID_CONFLICT"


def test_crash_after_session_creation_recovers_exact_session(tmp_path):
    root = str(tmp_path)
    request_id = "request-crash-session-123456"
    session_ids = []
    retries = []

    def crash_after_start():
        opened = MutationRuntime(root).open_session("crash recovery")
        session_ids.append(opened["session"]["session_id"])
        raise RuntimeError("simulated hard failure after session creation")

    with pytest.raises(RuntimeError):
        run_idempotent_request(
            root,
            operation="crash-test",
            request_id=request_id,
            payload={"case": "session-created"},
            execute=crash_after_start,
        )

    recovered = run_idempotent_request(
        root,
        operation="crash-test",
        request_id=request_id,
        payload={"case": "session-created"},
        execute=lambda: retries.append(True) or {"ok": True},
    )

    assert retries == []
    assert recovered["ok"] is True
    assert recovered["interrupted_request_recovered"] is True
    assert recovered["original_outcome_known"] is False
    assert recovered["session_capability"]["session_id"] == session_ids[0]


def test_dead_owner_without_session_reexecutes_once(monkeypatch, tmp_path):
    root = str(tmp_path)
    request_id = "request-dead-before-session-123"
    calls = []

    def fail_before_session():
        calls.append("failed")
        raise RuntimeError("simulated process death before session creation")

    with pytest.raises(RuntimeError):
        run_idempotent_request(
            root,
            operation="crash-test",
            request_id=request_id,
            payload={"case": "no-session"},
            execute=fail_before_session,
        )

    monkeypatch.setattr("apatch.runtime.request_journal._pid_alive", lambda _pid: False)
    replayed = run_idempotent_request(
        root,
        operation="crash-test",
        request_id=request_id,
        payload={"case": "no-session"},
        execute=lambda: calls.append("retried") or {"ok": True, "value": 9},
    )

    assert calls == ["failed", "retried"]
    assert replayed["ok"] is True
    assert replayed["idempotent_replay"] is False
