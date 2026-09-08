"""Transactional multi-agent session safety (RFP-036)."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from apatch.artifact_governance import register_ephemeral_logs
from apatch.lane import lane_state_path_for, resolve_lane
from apatch.lane_context import (
    bind_governed_session_id,
    bind_lane_from_kwargs,
    current_tool_governed_session_id,
    resolve_active_lane,
)
from apatch.runtime.errors import SessionBindingError
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import end_session, start_session
from apatch.runtime.session_binding import validate_patch_log_contract
from apatch.sandbox import SandboxError, acquire_lease, lease_path, release_lease
from apatch.session_state import (
    enrich_tool_response,
    load_session_state,
    save_session_state,
    session_state_path,
)


def _start_session_worker(root, ready, go, results):
    os.environ["APATCH_LANE"] = "race-lane"
    ready.put(True)
    go.wait(10)
    try:
        results.put(start_session(root, "concurrent start"))
    except Exception as exc:  # pragma: no cover - diagnostic transport
        results.put({"ok": False, "error": repr(exc)})


def _lease_holder(root, session_id, ready, release):
    os.environ["APATCH_LANE"] = "lane-a"
    try:
        cap = acquire_lease(
            root,
            ["src/a.py"],
            tool="holder",
            governed_session_id=session_id,
            max_seconds=30,
        )
        ready.put({"ok": True, "lease_id": cap["lease_id"]})
        release.wait(10)
        release_lease(
            root,
            lease_id=cap["lease_id"],
            governed_session_id=session_id,
        )
    except Exception as exc:  # pragma: no cover - diagnostic transport
        ready.put({"ok": False, "error": repr(exc)})


def _start_in_lane(monkeypatch, root, lane, intent):
    bind_lane_from_kwargs({})
    monkeypatch.setenv("APATCH_LANE", lane)
    result = start_session(root, intent)
    assert result["ok"] is True, result
    return result


def test_session_capability_is_unique_and_token_is_not_persisted(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start_in_lane(monkeypatch, root, "lane-a", "first")
    first_id = first["session"]["session_id"]
    first_token = first["session_token"]

    persisted = json.loads(Path(session_state_path(root)).read_text())
    assert persisted["session_token_hash"]
    assert first_token not in Path(session_state_path(root)).read_text()

    ended = end_session(
        root,
        expected_session_id=first_id,
        session_token=first_token,
        require_binding=True,
    )
    assert ended["ok"] is True

    second = start_session(root, "second")
    assert second["ok"] is True
    assert second["session"]["session_id"] != first_id
    assert second["session_token"] != first_token


def test_repeated_spec_lane_start_rebinds_request_context(monkeypatch, tmp_path):
    root = str(tmp_path)
    monkeypatch.delenv("APATCH_LANE", raising=False)
    bind_lane_from_kwargs({"target_dir": root, "spec": "SPEC-X"})

    first = start_session(root, "first")
    first_id = first["session"]["session_id"]
    assert end_session(
        root,
        expected_session_id=first_id,
        session_token=first["session_token"],
        require_binding=True,
    )["ok"]

    second = start_session(root, "second")
    assert second["ok"] is True
    second_id = second["session"]["session_id"]
    assert second_id != first_id
    assert current_tool_governed_session_id(root) == second_id
    assert load_session_state(root)["session_id"] == second_id


def test_existing_active_session_is_classified_as_session_conflict(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start_in_lane(monkeypatch, root, "race-lane", "first")
    second = start_session(root, "second")

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error_type"] == "SESSION_CONFLICT"
    assert second["session_id"] == first["session"]["session_id"]


def test_concurrent_session_start_has_single_winner(tmp_path):
    root = str(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    results = ctx.Queue()
    go = ctx.Event()
    workers = [
        ctx.Process(target=_start_session_worker, args=(root, ready, go, results))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    assert ready.get(timeout=15)
    assert ready.get(timeout=15)
    go.set()
    rows = [results.get(timeout=20), results.get(timeout=20)]
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0
    assert sum(bool(row.get("ok")) for row in rows) == 1
    assert any(row.get("error_type") == "SESSION_CONFLICT" for row in rows)


def test_session_binding_routes_exact_lane(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start_in_lane(monkeypatch, root, "lane-a", "first")
    second = _start_in_lane(monkeypatch, root, "lane-b", "second")
    first_id = first["session"]["session_id"]
    second_id = second["session"]["session_id"]
    first_path = Path(lane_state_path_for(root, "lane-a", "session_state.json"))
    second_path = Path(lane_state_path_for(root, "lane-b", "session_state.json"))

    monkeypatch.delenv("APATCH_LANE")
    bind_lane_from_kwargs({"governed_session_id": first_id})
    assert resolve_lane(root).lane_id == "lane-a"
    bind_lane_from_kwargs({"governed_session_id": second_id})
    assert resolve_lane(root).lane_id == "lane-b"

    bind_lane_from_kwargs({})
    before_first = first_path.read_bytes()
    before_second = second_path.read_bytes()
    ambiguous = MutationRuntime(root).resume_session()
    assert ambiguous["ok"] is False
    assert ambiguous["error_type"] == "SESSION_AMBIGUOUS"
    assert first_path.read_bytes() == before_first
    assert second_path.read_bytes() == before_second

    missing = MutationRuntime(
        root, session_id="apatch_sess_missing"
    ).resume_session()
    assert missing["ok"] is False
    assert missing["error_type"] == "SESSION_MISMATCH"
    assert first_path.read_bytes() == before_first
    assert second_path.read_bytes() == before_second

    resumed = MutationRuntime(root, session_id=first_id).resume_session()
    assert resumed["ok"] is True
    assert resumed["session_id"] == first_id
    assert resumed["session_capability"]["session_id"] == first_id
    assert resumed["session_capability"]["session_token"]
    persisted_first = json.loads(first_path.read_text())
    assert resumed["session_token"] not in first_path.read_text()
    assert persisted_first["session_token_hash"]
    assert first_path.read_bytes() != before_first
    assert second_path.read_bytes() == before_second

def test_session_binding_context_is_workspace_scoped(monkeypatch, tmp_path):
    first_root = str(tmp_path / "first")
    second_root = str(tmp_path / "second")
    Path(first_root).mkdir()
    Path(second_root).mkdir()
    opened = _start_in_lane(monkeypatch, first_root, "lane-a", "scoped")

    session_id = opened["session"]["session_id"]
    bind_governed_session_id(session_id, root=first_root)
    assert current_tool_governed_session_id(first_root) == session_id
    assert current_tool_governed_session_id(second_root) is None

    monkeypatch.delenv("APATCH_LANE")
    assert resolve_lane(second_root).lane_id != "ambiguous"


def test_ambiguous_lane_has_no_newest_fallback(monkeypatch, tmp_path):
    root = str(tmp_path)
    _start_in_lane(monkeypatch, root, "lane-a", "first")
    _start_in_lane(monkeypatch, root, "lane-b", "second")

    monkeypatch.delenv("APATCH_LANE")
    bind_lane_from_kwargs({})
    lane, error = resolve_active_lane(root)
    assert lane is None
    assert error and "ambiguous" in error.lower()


def test_foreign_capability_cannot_finalize_session(monkeypatch, tmp_path):
    from apatch.mcp import server as mcp_server

    if mcp_server.mcp is None:
        pytest.skip("MCP SDK unavailable")

    root = str(tmp_path)
    opened = _start_in_lane(monkeypatch, root, "lane-a", "owned")
    session_id = opened["session"]["session_id"]
    token = opened["session_token"]
    tool = mcp_server.mcp._tool_manager._tools["apatch_session_end"].fn

    rejected = tool(
        target_dir=root,
        governed_session_id=session_id,
        session_token="foreign-token",
    )
    assert rejected["ok"] is False
    assert rejected["error_type"] == "SESSION_TOKEN_MISMATCH"
    assert load_session_state(root).get("ended_at") is None

    closed = tool(
        target_dir=root,
        governed_session_id=session_id,
        session_token=token,
    )
    assert closed["ok"] is True


def test_enrich_cas_does_not_touch_replacement_session(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start_in_lane(monkeypatch, root, "lane-a", "first")
    first_id = first["session"]["session_id"]
    first_token = first["session_token"]
    first_revision = load_session_state(root)["revision"]
    assert end_session(
        root,
        expected_session_id=first_id,
        session_token=first_token,
        require_binding=True,
    )["ok"]

    bind_lane_from_kwargs({})
    second = start_session(root, "replacement")
    before = Path(session_state_path(root)).read_bytes()
    out = enrich_tool_response(
        "apatch_verify_run",
        {"ok": True},
        target_dir=root,
        expected_session_id=first_id,
        expected_revision=first_revision,
    )
    assert out["ok"] is False
    assert out["error_type"] == "SESSION_MISMATCH"
    assert Path(session_state_path(root)).read_bytes() == before
    assert load_session_state(root)["session_id"] == second["session"]["session_id"]


def test_session_state_revision_and_compare_swap(monkeypatch, tmp_path):
    root = str(tmp_path)
    opened = _start_in_lane(monkeypatch, root, "lane-a", "revision")
    raw = load_session_state(root)
    stale = dict(raw)
    updated = {**raw, "phase": "plan"}

    save_session_state(
        root,
        updated,
        expected_session_id=opened["session"]["session_id"],
        expected_revision=raw["revision"],
    )
    assert load_session_state(root)["revision"] == raw["revision"] + 1

    with pytest.raises(SessionBindingError) as exc:
        save_session_state(
            root,
            stale,
            expected_session_id=opened["session"]["session_id"],
            expected_revision=raw["revision"],
        )
    assert exc.value.error_type == "SESSION_REVISION_MISMATCH"


def test_writer_lease_is_path_scoped_across_lanes(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start_in_lane(monkeypatch, root, "lane-a", "writer a")
    second = _start_in_lane(monkeypatch, root, "lane-b", "writer b")
    first_id = first["session"]["session_id"]
    second_id = second["session"]["session_id"]

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Event()
    holder = ctx.Process(target=_lease_holder, args=(root, first_id, ready, release))
    holder.start()
    status = ready.get(timeout=15)
    assert status["ok"], status

    monkeypatch.setenv("APATCH_LANE", "lane-b")
    try:
        disjoint = acquire_lease(
            root,
            ["src/b.py"],
            tool="contender",
            governed_session_id=second_id,
        )
        assert disjoint["governed_session_id"] == second_id
        with pytest.raises(SandboxError) as exc:
            acquire_lease(
                root,
                ["src/a.py"],
                tool="overlap",
                governed_session_id=second_id,
            )
        assert exc.value.error_type == "LEASE_CONFLICT"
        assert lease_path(root) == str(tmp_path / ".apatch" / "write_lease.json")
        release_lease(root, governed_session_id=second_id)
    finally:
        release.set()
        holder.join(timeout=20)
    assert holder.exitcode == 0


def test_patch_log_owner_and_digest_are_enforced(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start_in_lane(monkeypatch, root, "lane-a", "patch owner")
    second = _start_in_lane(monkeypatch, root, "lane-b", "foreign")
    first_id = first["session"]["session_id"]
    second_id = second["session"]["session_id"]

    rel = ".apatch/tmp/{}/patches.jsonl".format(first_id)
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text('{"step_index": 1}\n')
    register_ephemeral_logs(root, rel, created_by_tool="test", governed_session_id=first_id)

    contract = validate_patch_log_contract(root, str(path), expected_session_id=first_id)
    assert contract["ok"] is True
    assert contract["content_sha256"]

    with pytest.raises(SessionBindingError) as foreign:
        validate_patch_log_contract(root, str(path), expected_session_id=second_id)
    assert foreign.value.error_type == "PATCH_LOG_OWNER_MISMATCH"

    path.write_text('{"step_index": 2}\n')
    with pytest.raises(SessionBindingError) as drift:
        validate_patch_log_contract(root, str(path), expected_session_id=first_id)
    assert drift.value.error_type == "PATCH_LOG_DIGEST_MISMATCH"


def test_session_end_cannot_close_replacement(monkeypatch, tmp_path):
    root = str(tmp_path)
    first = _start_in_lane(monkeypatch, root, "lane-a", "first")
    first_id = first["session"]["session_id"]
    first_token = first["session_token"]
    assert end_session(
        root,
        expected_session_id=first_id,
        session_token=first_token,
        require_binding=True,
    )["ok"]

    bind_lane_from_kwargs({})
    replacement = start_session(root, "replacement")
    rejected = end_session(
        root,
        expected_session_id=first_id,
        session_token=first_token,
        require_binding=True,
    )
    assert rejected["ok"] is False
    assert rejected["error_type"] == "SESSION_MISMATCH"
    assert load_session_state(root)["session_id"] == replacement["session"]["session_id"]
    assert load_session_state(root).get("ended_at") is None


def test_read_only_enrichment_does_not_persist_phase(monkeypatch, tmp_path):
    root = str(tmp_path)
    opened = _start_in_lane(monkeypatch, root, "lane-a", "read only")
    path = Path(session_state_path(root))
    cases = (
        ("apatch_doctor", {"ok": True, "hygiene": {"status": "ok"}}),
        ("apatch_workspace_inspect", {"ok": True, "workspace": root}),
        (
            "apatch_index_query",
            {"ok": False, "error": "index not built", "error_type": "INDEX_NOT_BUILT"},
        ),
    )

    for tool_name, result in cases:
        before = path.read_bytes()
        out = enrich_tool_response(
            tool_name,
            result,
            target_dir=root,
            expected_session_id=opened["session"]["session_id"],
        )
        assert out["session_state_write_behind"] is True
        assert out["state_update"]["phase"] == "idle"
        assert out["state_update"]["risk_level"] == "low"
        assert out["state_update"]["next_action"] != "apatch_rollback()"
        assert "failure" not in out
        assert out.get("recommended_action") is None
        assert path.read_bytes() == before


def test_mcp_hot_path_exposes_session_capability():
    from apatch.mcp import server as mcp_server

    if mcp_server.mcp is None:
        pytest.skip("MCP SDK unavailable")
    names = (
        "apatch_generate",
        "apatch_generate_batch",
        "apatch_apply",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    )
    tools = mcp_server.mcp._tool_manager._tools
    for name in names:
        properties = (tools[name].parameters or {}).get("properties") or {}
        assert "governed_session_id" in properties, name
        assert "session_token" in properties, name


def test_pre_session_orchestrator_failure_does_not_create_lane_state(tmp_path):
    root = str(tmp_path)
    path = Path(session_state_path(root))
    out = enrich_tool_response(
        "apatch_spec_run",
        {
            "ok": False,
            "error": "manifest does not cover R1",
            "error_type": "MANIFEST_GAP",
            "steps_completed": ["lint"],
        },
        target_dir=root,
    )
    assert out["pre_session_failure"] is True
    assert out["state_update"]["phase"] == "idle"
    assert out["session_state_write_behind"] is True
    assert not path.exists()
