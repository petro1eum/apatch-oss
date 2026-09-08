from __future__ import annotations

import json
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apatch.path_leases import (
    COMPAT_OWNER,
    canonicalize_paths,
    lease_registry_path,
    sweep_stale_leases,
)
from apatch.sandbox import (
    SandboxError,
    acquire_lease,
    lease_path,
    load_active_leases,
    release_lease,
)


def _hold_lease(root: str, path: str, session_id: str, ready, release) -> None:
    try:
        cap = acquire_lease(
            root,
            [path],
            tool="concurrency_test",
            governed_session_id=session_id,
            max_seconds=30,
        )
        Path(root, path).parent.mkdir(parents=True, exist_ok=True)
        Path(root, path).write_text(session_id, encoding="utf-8")
        ready.put({"ok": True, "lease_id": cap["lease_id"]})
        release.wait(20)
        release_lease(root, lease_id=cap["lease_id"], governed_session_id=session_id)
    except Exception as exc:  # pragma: no cover - child diagnostic
        ready.put({"ok": False, "error": repr(exc)})


def test_disjoint_sessions_acquire_concurrently(tmp_path):
    first = acquire_lease(
        str(tmp_path), ["apple/ed/runtime.py"], tool="apple", governed_session_id="apple"
    )
    second = acquire_lease(
        str(tmp_path), ["o_lang/cpp/CMakeLists.txt"], tool="olang", governed_session_id="olang"
    )
    assert first["lease_id"] != second["lease_id"]
    assert {cap["governed_session_id"] for cap in load_active_leases(str(tmp_path))} == {
        "apple",
        "olang",
    }


def test_same_file_conflict_is_exact_and_non_mutating(tmp_path):
    acquire_lease(str(tmp_path), ["src/a.py"], tool="first", governed_session_id="s1")
    before = Path(lease_registry_path(str(tmp_path))).read_bytes()
    with pytest.raises(SandboxError) as raised:
        acquire_lease(str(tmp_path), ["src/a.py"], tool="second", governed_session_id="s2")
    assert raised.value.error_type == "LEASE_CONFLICT"
    conflict = raised.value.details["conflicts"][0]
    assert conflict["requested_path"] == "src/a.py"
    assert conflict["held_path"] == "src/a.py"
    assert conflict["governed_session_id"] == "s1"
    assert Path(lease_registry_path(str(tmp_path))).read_bytes() == before


def test_file_directory_symlink_case_rename_delete_canonicalization(tmp_path, monkeypatch):
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "real.py").write_text("x", encoding="utf-8")
    (tmp_path / "alias.py").symlink_to(tmp_path / "tree" / "real.py")
    alias_root = tmp_path.parent / (tmp_path.name + "-alias")
    alias_root.symlink_to(tmp_path, target_is_directory=True)

    acquire_lease(str(tmp_path), ["tree"], tool="dir", governed_session_id="dir")
    with pytest.raises(SandboxError):
        acquire_lease(str(tmp_path), ["tree/real.py"], tool="file", governed_session_id="file")
    with pytest.raises(SandboxError):
        acquire_lease(str(alias_root), ["alias.py"], tool="alias", governed_session_id="alias")
    release_lease(str(tmp_path), governed_session_id="dir")

    acquire_lease(
        str(tmp_path),
        ["rename/source.py", "rename/target.py"],
        tool="rename",
        governed_session_id="rename",
    )
    with pytest.raises(SandboxError):
        acquire_lease(
            str(tmp_path), ["rename/target.py"], tool="target", governed_session_id="target"
        )
    with pytest.raises(SandboxError):
        acquire_lease(
            str(tmp_path), ["rename/source.py"], tool="delete", governed_session_id="delete"
        )
    release_lease(str(tmp_path), governed_session_id="rename")

    import apatch.path_leases as path_leases

    monkeypatch.setattr(path_leases, "_case_sensitive", lambda _root: False)
    first = canonicalize_paths(str(tmp_path), ["Case/File.py"])[0]
    second = canonicalize_paths(str(tmp_path), ["case/file.py"])[0]
    assert first["canonical_key"] == second["canonical_key"]


def test_multi_path_acquisition_is_atomic_and_order_independent(tmp_path):
    acquire_lease(str(tmp_path), ["a.py"], tool="a", governed_session_id="a")
    with pytest.raises(SandboxError):
        acquire_lease(
            str(tmp_path), ["b.py", "a.py"], tool="crossed", governed_session_id="crossed"
        )
    free = acquire_lease(str(tmp_path), ["b.py"], tool="b", governed_session_id="b")
    assert free["paths"] == ["b.py"]


def test_expiry_and_exact_recovery_are_per_lease(tmp_path):
    alive = acquire_lease(
        str(tmp_path), ["alive.py"], tool="alive", governed_session_id="alive", max_seconds=60
    )
    expired = acquire_lease(
        str(tmp_path), ["expired.py"], tool="expired", governed_session_id="expired", max_seconds=60
    )
    registry_path = Path(lease_registry_path(str(tmp_path)))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["leases"][expired["lease_id"]]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    removed = sweep_stale_leases(str(tmp_path))
    assert expired["lease_id"] in removed
    assert [cap["lease_id"] for cap in load_active_leases(str(tmp_path))] == [alive["lease_id"]]


def test_legacy_lease_migrates_without_replacing_old_owner(tmp_path):
    legacy = {
        "lease_id": "legacy-live",
        "holder": "apatch",
        "pid": os.getpid(),
        "tool": "apatch-0.8.30",
        "paths": ["apple/runtime.py"],
        "governed_session_id": "legacy-session",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    Path(lease_path(str(tmp_path))).parent.mkdir(parents=True)
    Path(lease_path(str(tmp_path))).write_text(
        json.dumps({"version": 1, "capability": legacy}), encoding="utf-8"
    )
    with pytest.raises(SandboxError):
        acquire_lease(
            str(tmp_path), ["o_lang/a.cpp"], tool="v2", governed_session_id="v2-session"
        )
    visible_to_v1 = json.loads(Path(lease_path(str(tmp_path))).read_text(encoding="utf-8"))
    assert visible_to_v1["capability"]["holder"] == "apatch"
    assert visible_to_v1["capability"]["governed_session_id"] == "legacy-session"
    assert visible_to_v1["capability"]["lease_id"] == "legacy-live"
    migrated = load_active_leases(str(tmp_path))[0]
    assert migrated["paths"] == ["."]
    assert migrated["legacy_migrated"] is True

    # v1 release is authoritative; the next v2 transaction removes the
    # compatibility row and admits a normal path-scoped writer.
    Path(lease_path(str(tmp_path))).unlink()
    current = acquire_lease(
        str(tmp_path), ["o_lang/a.cpp"], tool="v2", governed_session_id="v2-session"
    )
    assert current["paths"] == ["o_lang/a.cpp"]


def test_v2_guard_is_infrastructure_scoped_and_self_expiring(tmp_path):
    cap = acquire_lease(
        str(tmp_path),
        ["apple/runtime.py"],
        tool="v2",
        governed_session_id="v2-session",
        max_seconds=30,
    )
    guard = json.loads(Path(lease_path(str(tmp_path))).read_text(encoding="utf-8"))["capability"]
    assert guard["holder"] == COMPAT_OWNER
    assert guard["infrastructure_guard"] is True
    assert guard["minimum_writer_protocol"] == 2
    assert guard["governed_session_id"] == "APATCH_UPGRADE_REQUIRED"
    assert guard["tool"] == "apatch_protocol_v2_upgrade_required"
    assert guard["expires_at"] == cap["expires_at"]
    assert not guard["expires_at"].startswith("9999-")
    release_lease(
        str(tmp_path), lease_id=cap["lease_id"], governed_session_id="v2-session"
    )
    assert not Path(lease_path(str(tmp_path))).exists()


def test_apple_and_olang_real_processes_both_apply_and_finish(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Event()
    processes = [
        ctx.Process(
            target=_hold_lease,
            args=(str(tmp_path), "apple/ed/runtime.py", "apple", ready, release),
        ),
        ctx.Process(
            target=_hold_lease,
            args=(str(tmp_path), "o_lang/cpp/CMakeLists.txt", "olang", ready, release),
        ),
    ]
    for process in processes:
        process.start()
    results = [ready.get(timeout=20) for _ in processes]
    assert all(row["ok"] for row in results), results
    assert len(load_active_leases(str(tmp_path))) == 2
    release.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert Path(tmp_path, "apple/ed/runtime.py").read_text() == "apple"
    assert Path(tmp_path, "o_lang/cpp/CMakeLists.txt").read_text() == "olang"
