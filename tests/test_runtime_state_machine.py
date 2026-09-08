import json
import os

import pytest

from apatch.runtime.errors import RuntimeTransitionError
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import start_session
from apatch.runtime.state_machine import OP_APPLY, OP_PLAN, assert_operation


def _enable_enforce(tmp_path):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "enforcement.json").write_text(
        json.dumps({"mode": "strict"}),
        encoding="utf-8",
    )


def test_enforce_requires_session_for_plan(tmp_path):
    _enable_enforce(tmp_path)
    with pytest.raises(RuntimeTransitionError) as exc:
        assert_operation(str(tmp_path), OP_PLAN)
    assert "Governed session required" in str(exc.value)


def test_enforce_plan_after_session_start(tmp_path):
    _enable_enforce(tmp_path)
    start_session(str(tmp_path), "test intent")
    assert_operation(str(tmp_path), OP_PLAN)


def test_enforce_apply_allowed_in_draft_with_session(tmp_path):
    """Draft + active session allows apply (auto_session / zero-friction path)."""
    _enable_enforce(tmp_path)
    start_session(str(tmp_path), "test intent")
    assert_operation(str(tmp_path), OP_APPLY)


def test_runtime_plan_blocked_without_session(tmp_path):
    _enable_enforce(tmp_path)
    logs = tmp_path / "patches.jsonl"
    logs.write_text("", encoding="utf-8")
    rt = MutationRuntime(str(tmp_path))
    out = rt.plan(str(logs))
    assert out["ok"] is False
    assert out["error_type"] == "RUNTIME_TRANSITION"


def test_audit_allows_plan_without_session(tmp_path):
    assert_operation(str(tmp_path), OP_PLAN, strict=False)


def test_apply_session_blocked_without_governed_session(tmp_path):
    from apatch.runtime.state_machine import OP_APPLY_SESSION

    _enable_enforce(tmp_path)
    logs = tmp_path / "patches.jsonl"
    logs.write_text("", encoding="utf-8")
    out = MutationRuntime(str(tmp_path)).apply_session(str(logs), abort=True)
    assert out.get("ok") is True or out.get("aborted") is True

    out2 = MutationRuntime(str(tmp_path)).apply_session(str(logs))
    assert out2.get("ok") is False
    assert out2.get("error_type") == "RUNTIME_TRANSITION"

    start_session(str(tmp_path), "chunked apply")
    assert_operation(str(tmp_path), OP_APPLY_SESSION)
