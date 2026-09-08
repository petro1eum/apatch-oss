"""MCP process lifecycle — stale lease sweep and shutdown release."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
import pytest

from apatch.mcp import lifecycle as mcp_lifecycle
from apatch.sandbox import LEASE_REL, load_active_lease, release_lease


@pytest.fixture(autouse=True)
def _reset_lifecycle_state():
    mcp_lifecycle._TOUCHED_WORKSPACES.clear()
    mcp_lifecycle._SWEPT_WORKSPACES.clear()
    mcp_lifecycle._HOOKS_REGISTERED = False
    yield
    mcp_lifecycle._TOUCHED_WORKSPACES.clear()
    mcp_lifecycle._SWEPT_WORKSPACES.clear()
    mcp_lifecycle._HOOKS_REGISTERED = False


def _write_lease(root: str, *, pid: int, lease_id: str = "lease_test") -> None:
    path = os.path.join(root, LEASE_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "version": 1,
        "capability": {
            "lease_id": lease_id,
            "holder": "apatch",
            "pid": pid,
            "tool": "apatch_apply_session",
            "paths": ["src/foo.py"],
            "issued_at": "2026-06-10T20:00:00+00:00",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=2)
            ).isoformat(),
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_sweep_stale_lease_removes_dead_pid(tmp_path):
    dead_pid = 999_999_999
    _write_lease(str(tmp_path), pid=dead_pid)
    assert load_active_lease(str(tmp_path)) is not None

    out = mcp_lifecycle.sweep_stale_lease(str(tmp_path))

    assert out["swept"] is True
    assert out["reason"] == "stale_lease_removed"
    assert load_active_lease(str(tmp_path)) is None


def test_sweep_stale_lease_keeps_live_pid(tmp_path):
    _write_lease(str(tmp_path), pid=os.getpid())
    out = mcp_lifecycle.sweep_stale_lease(str(tmp_path))
    assert out["swept"] is False
    assert out["reason"] == "lease_valid"
    assert load_active_lease(str(tmp_path)) is not None
    release_lease(str(tmp_path))


def test_touch_workspace_sweeps_once(tmp_path):
    dead_pid = 999_999_998
    _write_lease(str(tmp_path), pid=dead_pid)
    mcp_lifecycle.touch_workspace(str(tmp_path))
    assert load_active_lease(str(tmp_path)) is None
    _write_lease(str(tmp_path), pid=dead_pid, lease_id="lease_second")
    mcp_lifecycle.touch_workspace(str(tmp_path))
    assert load_active_lease(str(tmp_path)) is not None


def test_release_leases_for_current_pid(tmp_path):
    my_pid = os.getpid()
    _write_lease(str(tmp_path), pid=my_pid, lease_id="lease_mine")
    mcp_lifecycle._TOUCHED_WORKSPACES.add(os.path.abspath(str(tmp_path)))

    released = mcp_lifecycle.release_leases_for_current_pid()

    assert len(released) == 1
    assert released[0]["released"] is True
    assert load_active_lease(str(tmp_path)) is None


def test_register_mcp_lifecycle_hooks_idempotent():
    mcp_lifecycle.register_mcp_lifecycle_hooks()
    first = mcp_lifecycle._HOOKS_REGISTERED
    mcp_lifecycle.register_mcp_lifecycle_hooks()
    assert first is True
    assert mcp_lifecycle._HOOKS_REGISTERED is True


def test_on_mcp_startup_sweeps_cwd_with_apatch(tmp_path, monkeypatch):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    dead_pid = 999_999_997
    _write_lease(str(tmp_path), pid=dead_pid)
    monkeypatch.chdir(tmp_path)
    # Hermetic: on_mcp_startup resolves the swept root via bind_mcp_workspace(), which
    # caches in a module global (_BOUND) and reads APATCH_WORKSPACE/APATCH_MCP_BOUND.
    # Clear both so the sweep targets cwd (tmp_path) regardless of suite order / env.
    monkeypatch.setattr("apatch.mcp.bound_workspace._BOUND", None)
    monkeypatch.delenv("APATCH_WORKSPACE", raising=False)
    monkeypatch.delenv("APATCH_MCP_BOUND", raising=False)

    mcp_lifecycle.on_mcp_startup()

    assert load_active_lease(str(tmp_path)) is None
    assert mcp_lifecycle._HOOKS_REGISTERED is True


def test_startup_sweeps_registered_aliases(tmp_path, monkeypatch):
    bound = tmp_path / "bound"
    alias = tmp_path / "alias"
    for root in (bound, alias):
        (root / ".apatch").mkdir(parents=True)
    _write_lease(str(alias), pid=999_999_996)
    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps({
            "version": 1,
            "workspaces": {"probstates": {"path": str(alias)}},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("APATCH_WORKSPACE_REGISTRY", str(registry))
    monkeypatch.setenv("APATCH_WORKSPACE", str(bound))
    monkeypatch.setattr("apatch.mcp.bound_workspace._BOUND", None)

    mcp_lifecycle.on_mcp_startup()

    assert load_active_lease(str(alias)) is None
