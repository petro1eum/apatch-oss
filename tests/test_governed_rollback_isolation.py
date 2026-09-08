from __future__ import annotations

from pathlib import Path

import pytest

from apatch.backup import BackupError, BackupManager
from apatch.lane_context import bind_lane_from_kwargs
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import start_session
from apatch.sandbox import acquire_lease, load_active_leases, release_lease
from apatch.session_state import load_session_state, save_session_state


def _start(monkeypatch, root: str, lane: str, intent: str) -> dict:
    bind_lane_from_kwargs({})
    monkeypatch.setenv("APATCH_LANE", lane)
    opened = start_session(root, intent)
    assert opened["ok"] is True, opened
    return opened


def _checkpoint(
    root: str,
    checkpoint: str,
    owner: str,
    path: Path,
    replacement: str,
) -> None:
    manager = BackupManager(
        root,
        session_id=checkpoint,
        governed_session_id=owner,
    )
    assert manager.create_backup(str(path))
    path.write_text(replacement, encoding="utf-8")


def test_failed_disjoint_session_rollback_restores_only_its_checkpoint(
    tmp_path, monkeypatch
):
    root = str(tmp_path)
    apple = tmp_path / "apple" / "runtime.py"
    olang = tmp_path / "o_lang" / "runtime.cpp"
    apple.parent.mkdir(parents=True)
    olang.parent.mkdir(parents=True)
    apple.write_text("apple-old\n", encoding="utf-8")
    olang.write_text("olang-old\n", encoding="utf-8")

    foreign = _start(monkeypatch, root, "apple-lane", "foreign Apple apply")
    own = _start(monkeypatch, root, "olang-lane", "failed disjoint OLang apply")
    foreign_id = foreign["session"]["session_id"]
    own_id = own["session"]["session_id"]
    _checkpoint(root, "foreign-checkpoint", foreign_id, apple, "apple-new\n")
    _checkpoint(root, "own-checkpoint", own_id, olang, "olang-new\n")

    acquire_lease(
        root,
        ["apple/runtime.py"],
        tool="foreign_apply",
        governed_session_id=foreign_id,
    )
    acquire_lease(
        root,
        ["o_lang/runtime.cpp"],
        tool="failed_disjoint_apply",
        governed_session_id=own_id,
    )
    assert len(load_active_leases(root)) == 2

    rolled = MutationRuntime(
        root,
        session_id=own_id,
        session_token=own["session_token"],
        enforce_binding=True,
    ).rollback()

    assert rolled["ok"] is True, rolled
    assert rolled["checkpoint"] == "own-checkpoint"
    assert rolled["governed_session_id"] == own_id
    assert olang.read_text(encoding="utf-8") == "olang-old\n"
    assert apple.read_text(encoding="utf-8") == "apple-new\n"
    assert (tmp_path / ".apatch" / "backups" / "foreign-checkpoint").is_dir()
    assert not (tmp_path / ".apatch" / "backups" / "own-checkpoint").exists()

    release_lease(root, governed_session_id=own_id)
    release_lease(root, governed_session_id=foreign_id)


def test_governed_rollback_refuses_foreign_checkpoint(tmp_path, monkeypatch):
    root = str(tmp_path)
    apple = tmp_path / "apple.py"
    olang = tmp_path / "olang.cpp"
    apple.write_text("apple-old\n", encoding="utf-8")
    olang.write_text("olang-old\n", encoding="utf-8")

    foreign = _start(monkeypatch, root, "apple-lane", "foreign")
    own = _start(monkeypatch, root, "olang-lane", "own")
    foreign_id = foreign["session"]["session_id"]
    own_id = own["session"]["session_id"]
    _checkpoint(root, "foreign-checkpoint", foreign_id, apple, "apple-new\n")
    _checkpoint(root, "own-checkpoint", own_id, olang, "olang-new\n")

    rejected = MutationRuntime(
        root,
        session_id=own_id,
        session_token=own["session_token"],
        enforce_binding=True,
    ).rollback("foreign-checkpoint")

    assert rejected["ok"] is False
    assert rejected["error_type"] == "ROLLBACK_CHECKPOINT_MISMATCH"
    assert rejected["expected"] == own_id
    assert rejected["actual"] == "foreign-checkpoint"
    assert apple.read_text(encoding="utf-8") == "apple-new\n"
    assert olang.read_text(encoding="utf-8") == "olang-new\n"


def test_governed_rollback_without_owned_checkpoint_never_uses_latest(
    tmp_path, monkeypatch
):
    root = str(tmp_path)
    apple = tmp_path / "apple.py"
    apple.write_text("apple-old\n", encoding="utf-8")
    foreign = _start(monkeypatch, root, "apple-lane", "foreign")
    own = _start(monkeypatch, root, "olang-lane", "denied before mutation")
    foreign_id = foreign["session"]["session_id"]
    own_id = own["session"]["session_id"]
    _checkpoint(root, "foreign-checkpoint", foreign_id, apple, "apple-new\n")

    rejected = MutationRuntime(
        root,
        session_id=own_id,
        session_token=own["session_token"],
        enforce_binding=True,
    ).rollback()

    assert rejected["ok"] is False
    assert rejected["error_type"] == "ROLLBACK_CHECKPOINT_MISSING"
    assert rejected["expected"] == own_id
    assert apple.read_text(encoding="utf-8") == "apple-new\n"
    assert (tmp_path / ".apatch" / "backups" / "foreign-checkpoint").is_dir()


def test_new_governed_session_does_not_inherit_previous_checkpoint(
    tmp_path, monkeypatch
):
    root = str(tmp_path)
    bind_lane_from_kwargs({})
    monkeypatch.setenv("APATCH_LANE", "reused-lane")
    save_session_state(
        root,
        {
            "session_id": "ended-session",
            "intent": "old",
            "ended_at": "2026-08-26T00:00:00+00:00",
            "phase": "idle",
            "checkpoint": "foreign-old-checkpoint",
        },
        force=True,
    )

    opened = start_session(root, "fresh")

    assert opened["ok"] is True, opened
    assert opened["session"]["checkpoint"] is None
    assert load_session_state(root)["checkpoint"] is None


def test_backup_checkpoint_owner_is_immutable(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    assert BackupManager(
        str(tmp_path),
        "checkpoint",
        governed_session_id="session-a",
    ).create_backup(str(first))

    with pytest.raises(BackupError, match="cannot be reassigned"):
        BackupManager(
            str(tmp_path),
            "checkpoint",
            governed_session_id="session-b",
        ).create_backup(str(second))
