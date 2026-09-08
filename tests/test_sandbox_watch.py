import json
import subprocess

from apatch.enforcement import file_sha256, record_notarized_files, record_session_attested
from apatch.sandbox import write_sandbox_config
from apatch.sandbox_watch import (
    append_violations,
    run_sandbox_watch_once,
    scan_unleased_violations,
)


def _git_init(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def test_scan_detects_unleased_protected_file(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    cfg_path = tmp_path / ".apatch" / "sandbox.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["watcher"] = "audit"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    f = app / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/x.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    f.write_text("x = 2\n", encoding="utf-8")
    violations = scan_unleased_violations(str(tmp_path))
    assert any(v["path"] == "app/x.py" for v in violations)


def test_audit_logs_violations(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    cfg_path = tmp_path / ".apatch" / "sandbox.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["watcher"] = "audit"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    n = append_violations(
        str(tmp_path),
        [{"path": "app/a.py", "sha256": "abc", "reason": "direct_write_blocked", "detected_at": "t"}],
    )
    assert n == 1
    result = run_sandbox_watch_once(str(tmp_path), force=True)
    assert result.get("skipped") is not True
    assert (tmp_path / ".apatch" / "sandbox_violations.jsonl").is_file()


def test_watch_skipped_when_off(tmp_path):
    write_sandbox_config(str(tmp_path))
    cfg_path = tmp_path / ".apatch" / "sandbox.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["watcher"] = "off"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    result = run_sandbox_watch_once(str(tmp_path))
    assert result.get("skipped") is True


def test_auto_revert_tracked_modification(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    cfg_path = tmp_path / ".apatch" / "sandbox.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["watcher"] = "revert"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    f = app / "m.py"
    f.write_text("v = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/m.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    f.write_text("v = 2\n", encoding="utf-8")
    result = run_sandbox_watch_once(str(tmp_path), force=True)
    assert result.get("auto_revert") is True
    assert "app/m.py" in (result.get("reverted_tracked") or [])
    assert f.read_text(encoding="utf-8") == "v = 1\n"
    assert result.get("ok") is True


def test_auto_revert_removes_untracked_protected(tmp_path):
    write_sandbox_config(str(tmp_path))
    cfg_path = tmp_path / ".apatch" / "sandbox.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["watcher"] = "revert"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    rogue = app / "rogue.py"
    rogue.write_text("bad = 1\n", encoding="utf-8")
    result = run_sandbox_watch_once(str(tmp_path), force=True, auto_revert=True)
    assert "app/rogue.py" in (result.get("removed_untracked") or [])
    assert not rogue.is_file()
    assert result.get("ok") is True


def test_explicit_audit_does_not_revert_when_watcher_is_revert(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    cfg_path = tmp_path / ".apatch" / "sandbox.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["watcher"] = "revert"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    f = app / "audit.py"
    f.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/audit.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    f.write_text("value = 2\n", encoding="utf-8")

    result = run_sandbox_watch_once(
        str(tmp_path),
        force=True,
        auto_revert=False,
        respect_watcher_revert=False,
    )

    assert result.get("auto_revert") is False
    assert result.get("ok") is False
    assert result.get("reverted_tracked") == []
    assert f.read_text(encoding="utf-8") == "value = 2\n"


def test_audit_flag_revert_without_watcher_revert(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    f = app / "z.py"
    f.write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/z.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp_path, check=True)
    f.write_text("a = 9\n", encoding="utf-8")
    result = run_sandbox_watch_once(str(tmp_path), force=True, auto_revert=True)
    assert result.get("mode") == "enforce"
    assert "app/z.py" in (result.get("reverted_tracked") or [])


def test_watcher_keeps_attested_dirty_bytes_after_session_end(tmp_path):
    """Releasing the live lease must not invalidate the exact attested bytes."""
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    source = app / "committed.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/committed.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    source.write_text("value = 2\n", encoding="utf-8")
    record_notarized_files(
        str(tmp_path),
        {"app/committed.py": {"sha256": file_sha256(str(source))}},
        governed_session_id="sess-complete",
    )
    record_session_attested(str(tmp_path), "sess-complete")

    result = run_sandbox_watch_once(str(tmp_path), force=True)

    assert result["ok"] is True
    assert result["scanned_violations"] == 0
    assert result["reverted_count"] == 0
    assert source.read_text(encoding="utf-8") == "value = 2\n"
