import subprocess

from apatch.enforcement import (
    file_sha256,
    record_notarized_files,
    record_session_attested,
    write_enforcement_config,
)
from apatch.sandbox import write_sandbox_config
from apatch.sandbox_watch import run_sandbox_ci_gate


def _git_init(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def test_ci_gate_skipped_without_config(tmp_path):
    result = run_sandbox_ci_gate(str(tmp_path))
    assert result.get("skipped") is True
    assert result.get("ok") is True


def test_ci_gate_fails_on_unleased_protected(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    f = app / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/x.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    f.write_text("x = 2\n", encoding="utf-8")
    result = run_sandbox_ci_gate(str(tmp_path))
    assert result.get("ok") is False
    assert result.get("gate") == "failed"


def test_ci_gate_enforcement_without_violations(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))
    result = run_sandbox_ci_gate(str(tmp_path))
    assert result.get("ok") is True
    assert result.get("gate") == "passed"


def _commit(path, msg):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=path, check=True)


def _head(path):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_ci_gate_base_catches_committed_unleased(tmp_path):
    """RFP-005 §audit #6: a committed (e.g. --no-verify) protected change is
    caught when the gate diffs against the merge base, even with a clean tree."""
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    (app / "x.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "base")
    base = _head(tmp_path)

    # Simulate a bypass: protected file changed and committed (clean worktree).
    (app / "x.py").write_text("x = 2\n", encoding="utf-8")
    _commit(tmp_path, "sneaky")

    # Base mode (PR diff vs merge base) catches the committed protected change.
    result = run_sandbox_ci_gate(str(tmp_path), base=base)
    assert result.get("ok") is False
    assert result.get("gate") == "failed"
    assert result.get("base") == base
    paths = [v.get("path") for v in result["audit"].get("violations", [])]
    assert "app/x.py" in paths


def test_ci_gate_ignores_control_plane_changes(tmp_path):
    """Ring-2 does not flag operator commits to sandbox/enforcement JSON."""
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))
    # Simulate first-time policy commit (tracked config files in diff).
    subprocess.run(["git", "add", ".apatch/sandbox.json", ".apatch/enforcement.json"], cwd=tmp_path, check=True)
    result = run_sandbox_ci_gate(str(tmp_path))
    assert result.get("ok") is True
    assert result.get("gate") == "passed"


def test_ci_gate_base_ignores_unprotected_committed(tmp_path):
    """Base mode only flags protected paths changed vs base."""
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    _commit(tmp_path, "base")
    base = _head(tmp_path)
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(tmp_path, "docs only")

    result = run_sandbox_ci_gate(str(tmp_path), base=base)
    assert result.get("ok") is True
    assert result.get("gate") == "passed"


def _record_attested_file(tmp_path, rel_path, session_id="sess-committed"):
    source = tmp_path / rel_path
    record_notarized_files(
        str(tmp_path),
        {rel_path: {"sha256": file_sha256(str(source))}},
        governed_session_id=session_id,
    )
    assert record_session_attested(str(tmp_path), session_id) == 1


def test_ci_gate_passes_attested_staged_change_after_lease_release(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    source = app / "x.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _commit(tmp_path, "base")

    source.write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/x.py"], cwd=tmp_path, check=True)
    _record_attested_file(tmp_path, "app/x.py")

    result = run_sandbox_ci_gate(str(tmp_path), base="HEAD")

    assert result["ok"] is True
    assert result["gate"] == "passed"
    assert result["candidate_paths"] == ["app/x.py"]
    assert result["audit"]["violations"] == []
    assert result["notarization"]["checked"] == 1


def test_ci_gate_rejects_notarized_but_unattested_change(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    source = app / "x.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _commit(tmp_path, "base")

    source.write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/x.py"], cwd=tmp_path, check=True)
    record_notarized_files(
        str(tmp_path),
        {"app/x.py": {"sha256": file_sha256(str(source))}},
        governed_session_id="sess-unverified",
    )

    result = run_sandbox_ci_gate(str(tmp_path), base="HEAD")

    assert result["ok"] is False
    assert result["gate"] == "failed"
    assert result["audit"]["violations"][0]["path"] == "app/x.py"


def test_ci_gate_preserves_previous_proof_across_failed_attempt_and_rollback(tmp_path):
    """Rollback restores the prior committed state without reviving pending proof."""
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    source = app / "x.py"
    source.write_text("value = 0\n", encoding="utf-8")
    _commit(tmp_path, "base")

    source.write_text("value = 1\n", encoding="utf-8")
    _record_attested_file(tmp_path, "app/x.py", session_id="sess-proven")

    source.write_text("value = 2\n", encoding="utf-8")
    record_notarized_files(
        str(tmp_path),
        {"app/x.py": {"sha256": file_sha256(str(source))}},
        governed_session_id="sess-failed",
    )
    assert run_sandbox_ci_gate(str(tmp_path))["ok"] is False

    source.write_text("value = 1\n", encoding="utf-8")
    record_notarized_files(
        str(tmp_path),
        {"app/x.py": {"sha256": file_sha256(str(source))}},
        governed_session_id="sess-failed",
    )

    result = run_sandbox_ci_gate(str(tmp_path))
    assert result["ok"] is True
    assert result["gate"] == "passed"


def test_ci_gate_base_excludes_unstaged_and_untracked_owner_work(tmp_path):
    _git_init(tmp_path)
    write_sandbox_config(str(tmp_path))
    write_enforcement_config(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    source = app / "included.py"
    owner = app / "owner.py"
    source.write_text("value = 1\n", encoding="utf-8")
    owner.write_text("owner = 1\n", encoding="utf-8")
    _commit(tmp_path, "base")

    source.write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/included.py"], cwd=tmp_path, check=True)
    owner.write_text("owner = 2\n", encoding="utf-8")
    (app / "scratch.py").write_text("scratch = True\n", encoding="utf-8")
    _record_attested_file(tmp_path, "app/included.py")

    result = run_sandbox_ci_gate(str(tmp_path), base="HEAD")

    assert result["ok"] is True
    assert result["candidate_paths"] == ["app/included.py"]
