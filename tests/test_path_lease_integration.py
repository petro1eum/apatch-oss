from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from apatch.backup import BackupManager
from apatch.sandbox import SandboxError, acquire_lease, assert_lease_for_paths, release_lease
from apatch.trustchain_helper import TrustChainHelper


def _hold_apple(root: str, ready, release) -> None:
    cap = acquire_lease(
        root,
        ["apple/ed/runtime.py"],
        tool="independent_apply",
        governed_session_id="apple-session",
        max_seconds=60,
    )
    Path(root, "apple/ed/runtime.py").write_text("apple-new\n", encoding="utf-8")
    ready.put(cap["lease_id"])
    release.wait(30)
    release_lease(root, lease_id=cap["lease_id"], governed_session_id="apple-session")


def test_shared_maintenance_coexists_with_independent_apply(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    (tmp_path / "apple" / "ed").mkdir(parents=True)
    (tmp_path / "o_lang" / "cpp").mkdir(parents=True)
    (tmp_path / "apple" / "ed" / "runtime.py").write_text("apple-old\n", encoding="utf-8")
    (tmp_path / "o_lang" / "cpp" / "a.cpp").write_text("old-a\n", encoding="utf-8")
    (tmp_path / "o_lang" / "cpp" / "b.cpp").write_text("old-b\n", encoding="utf-8")
    for spec_id, file_name, expected in (
        ("SPEC-A", "a.cpp", "new-a"),
        ("SPEC-B", "b.cpp", "new-b"),
    ):
        (docs / (spec_id + ".md")).write_text(
            "# {}\n> **apatch artifact:** `spec:{}`\n"
            "## R1 maintenance\n(verify: grep -q {} o_lang/cpp/{})\n".format(
                spec_id, spec_id, expected, file_name
            ),
            encoding="utf-8",
        )
    requirements = {
        "SPEC-A": {"R1": {"needles": [{
            "action": "replace", "target_file": "o_lang/cpp/a.cpp",
            "find_text": "old-a", "replace_text": "new-a",
        }]}},
        "SPEC-B": {"R1": {"needles": [{
            "action": "replace", "target_file": "o_lang/cpp/b.cpp",
            "find_text": "old-b", "replace_text": "new-b",
        }]}},
    }

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Event()
    holder = ctx.Process(target=_hold_apple, args=(str(tmp_path), ready, release))
    holder.start()
    ready.get(timeout=20)
    monkeypatch.setenv("APATCH_LANE", "shared-maintenance")
    try:
        from apatch.shared_maintenance import shared_maintenance_workspace

        result = shared_maintenance_workspace(
            str(tmp_path),
            specs=["SPEC-A", "SPEC-B"],
            requirements=requirements,
            maintenance_verify="grep -q new-a o_lang/cpp/a.cpp && grep -q new-b o_lang/cpp/b.cpp",
        )
        assert result["ok"] is True, result
    finally:
        release.set()
        holder.join(timeout=30)
    assert holder.exitcode == 0
    assert (tmp_path / "apple/ed/runtime.py").read_text() == "apple-new\n"
    assert "new-a" in (tmp_path / "o_lang/cpp/a.cpp").read_text()
    assert "new-b" in (tmp_path / "o_lang/cpp/b.cpp").read_text()


def test_apply_rejects_write_set_expansion(tmp_path):
    acquire_lease(
        str(tmp_path), ["planned.py"], tool="outer", governed_session_id="session-a"
    )
    assert_lease_for_paths(
        str(tmp_path), ["planned.py"], tool="inner", governed_session_id="session-a"
    )
    with pytest.raises(SandboxError) as raised:
        assert_lease_for_paths(
            str(tmp_path), ["unplanned.py"], tool="inner", governed_session_id="session-a"
        )
    assert raised.value.error_type == "LEASE_SCOPE_VIOLATION"


def test_parallel_rollback_does_not_change_foreign_result(tmp_path):
    first = tmp_path / "apple.py"
    second = tmp_path / "olang.cpp"
    first.write_text("apple-old\n", encoding="utf-8")
    second.write_text("olang-old\n", encoding="utf-8")

    backup = BackupManager(str(tmp_path), session_id="apple-session")
    assert backup.create_backup(str(first))
    first.write_text("apple-new\n", encoding="utf-8")
    foreign = acquire_lease(
        str(tmp_path), ["olang.cpp"], tool="olang", governed_session_id="olang-session"
    )
    second.write_text("olang-new\n", encoding="utf-8")

    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    helper.bind_physical_backup("apple-session", backup)
    assert helper.rollback_checkpoint("apple-session") is True
    assert first.read_text() == "apple-old\n"
    assert second.read_text() == "olang-new\n"
    release_lease(
        str(tmp_path), lease_id=foreign["lease_id"], governed_session_id="olang-session"
    )


def test_read_only_workflows_do_not_acquire_writer_lease(tmp_path):
    source = tmp_path / "a.py"
    source.write_text("x = 1\n", encoding="utf-8")
    from apatch.workflows import plan_from_logs

    logs = tmp_path / "patches.jsonl"
    logs.write_text(
        '{"target_file":"a.py","find_text":"x = 1","replace_text":"x = 2"}\n',
        encoding="utf-8",
    )
    plan_from_logs(str(logs), str(tmp_path))
    from apatch.sandbox import load_active_leases

    assert load_active_leases(str(tmp_path)) == []
