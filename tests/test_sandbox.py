import json
import os

import pytest

from apatch.sandbox import (
    SandboxError,
    acquire_lease,
    detect_shell_mutation,
    evaluate_pre_mcp,
    evaluate_pre_tool_use,
    evaluate_shell_hook,
    evaluate_write_policy,
    is_path_protected,
    is_sandbox_enforce,
    load_active_lease,
    release_lease,
    sandbox_apply_scope,
    write_sandbox_config,
)


def test_protected_globs_default():
    assert is_path_protected("app/models/user.py")
    assert is_path_protected("src/foo.py")
    assert is_path_protected("e2e/smoke.spec.ts")
    assert is_path_protected("server/db/crm.js")
    assert is_path_protected("server.js")
    assert is_path_protected("scripts/dev.mjs")
    assert is_path_protected("docs/specs/SPEC-FOO.md")
    assert is_path_protected("playwright.config.ts")
    assert not is_path_protected("tests/test_x.py")
    assert not is_path_protected("manifests/x.json")
    assert not is_path_protected("docs/guide.md")
    assert not is_path_protected("AGENTS.md")


def test_docs_star_md_does_not_allow_specs():
    assert is_path_protected("docs/specs/SPEC-FOO.md")
    assert not is_path_protected("docs/guide.md")


def test_allow_overrides_protected(tmp_path):
    from apatch.sandbox import load_sandbox_config

    write_sandbox_config(str(tmp_path))
    cfg = load_sandbox_config(str(tmp_path))
    assert not is_path_protected("tests/harness/app.py", cfg)


def test_control_files_identified_despite_allow_globs():
    from apatch.sandbox import is_control_path

    # These live under allow_globs (.apatch/**, .trustchain/**, .cursor/**)
    # but the agent write channel must still refuse them.
    for p in (
        ".apatch/sandbox.json",
        ".apatch/enforcement.json",
        ".trustchain/ledger/000.json",
        ".cursor/hooks.json",
        ".cursor/hooks/apatch-deny-direct-edit.sh",
    ):
        assert is_control_path(p), p
    # Ordinary .apatch working files are not control plane.
    assert not is_control_path(".apatch/probe.txt")


def test_control_plane_locked_in_write_channel(tmp_path):
    write_sandbox_config(str(tmp_path))
    decision = evaluate_write_policy(str(tmp_path), ".apatch/enforcement.json")
    assert decision["allowed"] is False
    assert decision["reason"] == "control_plane_locked"


def test_control_plane_locked_even_with_lease(tmp_path):
    from apatch.sandbox import acquire_lease

    write_sandbox_config(str(tmp_path))
    acquire_lease(str(tmp_path), [".apatch/enforcement.json"], tool="manual")
    decision = evaluate_write_policy(str(tmp_path), ".apatch/enforcement.json")
    assert decision["allowed"] is False
    assert decision["reason"] == "control_plane_locked"


def test_control_plane_blocked_via_hook(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_tool_use(
        {"tool_name": "StrReplace", "tool_input": {"file_path": ".apatch/sandbox.json"}},
        root=str(tmp_path),
    )
    assert out["permission"] == "deny"


def test_write_policy_deny_without_lease(tmp_path):
    write_sandbox_config(str(tmp_path))
    decision = evaluate_write_policy(str(tmp_path), "app/foo.py")
    assert decision["allowed"] is False
    assert decision["reason"] == "direct_write_blocked"


def test_lease_allows_protected_path(tmp_path):
    write_sandbox_config(str(tmp_path))
    acquire_lease(str(tmp_path), ["app/foo.py"], tool="test")
    decision = evaluate_write_policy(str(tmp_path), "app/foo.py")
    assert decision["allowed"] is True
    assert decision["reason"] == "lease"
    release_lease(str(tmp_path))


def test_disjoint_lease_allows_other_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    write_sandbox_config(str(tmp_path))
    acquire_lease(str(tmp_path), ["app/a.py"], tool="test")
    cap = load_active_lease(str(tmp_path))
    cap["pid"] = 999999
    from apatch.sandbox import _save_lease

    _save_lease(str(tmp_path), cap)
    second = acquire_lease(str(tmp_path), ["app/b.py"], tool="test2")
    assert second["lease_id"] != cap["lease_id"]


def test_lease_apply_session_rejects_overlap_without_stealing(tmp_path, monkeypatch):
    # Regression: a concurrent apply_session must NOT silently steal another live
    # agent's lease — stealing reverted the victim's in-flight work ("wiping edits").
    # apatch must refuse with LEASE_CONFLICT instead.
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)  # any pid looks alive
    write_sandbox_config(str(tmp_path))
    acquire_lease(str(tmp_path), ["app/a.py"], tool="apatch_apply_session")
    from apatch.sandbox import _save_lease

    cap = load_active_lease(str(tmp_path))
    cap["pid"] = 999999  # another (mock-alive) holder
    _save_lease(str(tmp_path), cap)
    with pytest.raises(SandboxError) as exc:
        acquire_lease(str(tmp_path), ["app/a.py"], tool="apatch_apply_session")
    assert exc.value.error_type == "LEASE_CONFLICT"


def test_sandbox_apply_scope_releases(tmp_path):
    write_sandbox_config(str(tmp_path))
    assert is_sandbox_enforce(str(tmp_path))
    with sandbox_apply_scope(str(tmp_path), ["app/x.py"], tool="test_apply", owns_lease=True):
        assert load_active_lease(str(tmp_path)) is not None
    assert load_active_lease(str(tmp_path)) is None


def test_hook_denies_protected_write(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_tool_use(
        {"tool_name": "Write", "tool_input": {"path": "app/models/user.py"}},
        root=str(tmp_path),
    )
    assert out["permission"] == "deny"


def test_mcp_hook_allows_apatch_tool(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_mcp(
        {"server": "apatch", "tool_name": "apatch_apply_session"},
        root=str(tmp_path),
    )
    assert out["permission"] == "allow"


def test_mcp_hook_denies_foreign_tool(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_mcp(
        {"server": "other-mcp", "tool_name": "write_file"},
        root=str(tmp_path),
    )
    assert out["permission"] == "deny"


def test_mcp_hook_allows_when_sandbox_off(tmp_path):
    write_sandbox_config(str(tmp_path), mode="off")
    out = evaluate_pre_mcp(
        {"server": "other-mcp", "tool_name": "write_file"},
        root=str(tmp_path),
    )
    assert out["permission"] == "allow"


def test_mcp_hook_allows_cursor_browser_by_default(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_mcp(
        {"server": "cursor-ide-browser", "tool_name": "browser_navigate"},
        root=str(tmp_path),
    )
    assert out["permission"] == "allow"


def test_mcp_hook_allows_browser_tool_without_server(tmp_path):
    """Cursor beforeMCPExecution often sends tool_name only (no server field)."""
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_mcp({"tool_name": "browser_tabs"}, root=str(tmp_path))
    assert out["permission"] == "allow"


def test_mcp_hook_blocks_unknown_mcp_server(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_mcp(
        {"server": "rogue-mcp", "tool_name": "write_file"},
        root=str(tmp_path),
    )
    assert out["permission"] == "deny"




def test_hook_denies_absolute_path(tmp_path):
    write_sandbox_config(str(tmp_path))
    root = str(tmp_path)
    abs_path = str(tmp_path / "app" / "foo.py")
    out = evaluate_pre_tool_use(
        {"tool_name": "StrReplace", "tool_input": {"path": abs_path}},
        root=root,
    )
    assert out["permission"] == "deny"


def test_hook_denies_apply_patch(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_tool_use(
        {
            "tool_name": "ApplyPatch",
            "tool_input": {"patch": "*** Update File: app/models/user.py\n"},
        },
        root=str(tmp_path),
    )
    assert out["permission"] == "deny"


def test_hook_fail_closed_empty_write(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_tool_use(
        {"tool_name": "Write", "tool_input": {}},
        root=str(tmp_path),
    )
    assert out["permission"] == "deny"


def test_hook_ignores_lease_for_agent_channel(tmp_path, monkeypatch):
    write_sandbox_config(str(tmp_path))
    acquire_lease(str(tmp_path), ["app/foo.py"], tool="test")
    out = evaluate_pre_tool_use(
        {"tool_name": "Write", "tool_input": {"path": "app/foo.py"}},
        root=str(tmp_path),
    )
    assert out["permission"] == "deny"
    release_lease(str(tmp_path))


def test_hook_allows_tests_path(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_pre_tool_use(
        {"tool_name": "Write", "tool_input": {"path": "tests/test_new.py"}},
        root=str(tmp_path),
    )
    assert out["permission"] == "allow"


def test_strip_acquires_lease_in_enforce_mode(tmp_path):
    from apatch.workflows import run_strip

    write_sandbox_config(str(tmp_path))
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    target = src_dir / "page.py"
    target.write_text("def foo():\n    pass\n# START\n    x = 1\n# END\n", encoding="utf-8")
    manifest = tmp_path / "manifests" / "strip.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "strips": [
                    {
                        "start": "# START",
                        "until": "# END",
                        "replace": "    pass\n",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    run_strip(
        str(target),
        manifest_path=str(manifest),
        dry_run=False,
        no_trustchain=True,
    )
    assert load_active_lease(str(tmp_path)) is None


def test_init_consumer_with_sandbox(tmp_path):
    from apatch.doctor import init_consumer

    created = init_consumer(str(tmp_path), with_sandbox=True)
    assert any(p.endswith("sandbox.json") for p in created)
    assert os.path.isfile(tmp_path / ".cursor" / "hooks.json")
    assert os.path.isfile(tmp_path / ".cursor" / "hooks" / "apatch-deny-direct-edit.sh")
    assert os.path.isfile(tmp_path / ".cursor" / "hooks" / "apatch-deny-mcp-mutate.sh")


def _write_sandbox_mode(tmp_path, mode: str) -> None:
    path = tmp_path / ".apatch" / "sandbox.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "mode": mode}), encoding="utf-8")


def test_env_killswitch_cannot_disable_enforce_sandbox(tmp_path, monkeypatch):
    """RFP-005 §audit #2: APATCH_SANDBOX=0 must NOT disable a committed enforce."""
    from apatch.sandbox import is_sandbox_enabled

    write_sandbox_config(str(tmp_path))  # mode=enforce
    for val in ("0", "off", "false"):
        monkeypatch.setenv("APATCH_SANDBOX", val)
        assert is_sandbox_enabled(str(tmp_path)) is True


def test_shell_hook_blocks_pip_install_under_enforce(tmp_path):
    write_sandbox_config(str(tmp_path))
    cmd = (
        '/opt/homebrew/bin/python3.14 -m pip install --break-system-packages '
        '-e "/Users/edcher/Documents/GitHub/apatch[mcp]" 2>&1 | tail -25'
    )
    out = evaluate_shell_hook({"command": cmd}, root=str(tmp_path))
    assert out["permission"] == "deny"
    assert "human" in out["agent_message"].lower() or "Human" in out["agent_message"]
    assert "pip" in out["agent_message"].lower() or "package" in out["user_message"].lower()


def test_shell_hook_allows_pytest_under_enforce(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_shell_hook({"command": "pytest tests/ -q"}, root=str(tmp_path))
    assert out["permission"] == "allow"


# --- detect_shell_mutation: quote-aware, fd-redirect-aware (regression) ---

@pytest.mark.parametrize(
    "command",
    [
        'python3 -c "assert abs(a - b) < 0.01"',          # comparison inside quotes
        "python3 -c \"print('->', a > b)\"",                 # '>' inside quotes
        "pytest tests/ -q 2>&1 | tail -20",                  # fd redirect + pipe
        "cmd 2>&1",                                          # fd dup only
        "echo done >&2",                                     # fd dup to stderr
        "pytest tests/ -q 2>/dev/null",                      # discard stderr
        "git status 2>/dev/null",                            # discard stderr
        "cmd >/dev/null 2>&1",                               # discard stdout + fd dup
        "noisy &>/dev/null",                                 # discard both (bash)
        "head -n 10 *.txt",                                  # plain read with glob
        "python3 -c \"x = 'sed -i'\"",                       # 'sed' only inside quotes
        "git diff | grep foo",                               # pipe, no redirect
        "ls -la",                                            # plain
    ],
)
def test_detect_shell_mutation_allows_readonly(command):
    assert detect_shell_mutation(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > out.txt",                                 # file redirect
        "echo hi >> log.txt",                                # append redirect
        "cmd 2> err.log",                                    # stderr to file
        "sed -i 's/a/b/' file.py",                           # in-place edit
        "awk '{print}' f > g",                               # redirect to file
        "cat a | tee b.txt",                                 # tee writes file
        "perl -pi -e 's/x/y/' f.py",                         # perl in-place
    ],
)
def test_detect_shell_mutation_flags_real_mutations(command):
    assert detect_shell_mutation(command) is not None


def test_shell_hook_allows_quoted_comparison_under_enforce(tmp_path):
    """Regression: python -c with '<'/'>' comparisons must not be blocked."""
    write_sandbox_config(str(tmp_path))
    cmd = 'python3 -c "from m import f; assert f(1) < 2 and f(3) > 0; print(1>0)"'
    out = evaluate_shell_hook({"command": cmd}, root=str(tmp_path))
    assert out["permission"] == "allow"


def test_shell_hook_allows_fd_redirect_pipe_under_enforce(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_shell_hook(
        {"command": "python -m pytest tests/test_sandbox.py -q 2>&1 | tail -20"},
        root=str(tmp_path),
    )
    assert out["permission"] == "allow"


def test_shell_hook_allows_dev_null_discard_under_enforce(tmp_path):
    """Feedback fix: 2>/dev/null in diagnostics must not be blocked."""
    write_sandbox_config(str(tmp_path))
    for cmd in ("pytest tests/ -q 2>/dev/null", "ls missing 2>/dev/null || true"):
        out = evaluate_shell_hook({"command": cmd}, root=str(tmp_path))
        assert out["permission"] == "allow", cmd


def test_shell_hook_blocks_real_redirect_under_enforce(tmp_path):
    write_sandbox_config(str(tmp_path))
    out = evaluate_shell_hook({"command": "echo pwned > app/config.py"}, root=str(tmp_path))
    assert out["permission"] == "deny"
    assert "redirect" in out["user_message"].lower()


def test_hooks_allow_when_no_sandbox_committed(tmp_path):
    """No .apatch/sandbox.json → sandbox not enabled → hooks must not enforce.

    Consistency with apatch_doctor.sandbox.enabled=false; prevents a user-level hook
    from silently enforcing on projects that never ran init-consumer --with-sandbox.
    """
    # real file redirect would be blocked under enforce, but no config here
    assert evaluate_shell_hook(
        {"command": "echo x > app/y.py"}, root=str(tmp_path)
    )["permission"] == "allow"
    assert evaluate_pre_tool_use(
        {"tool_name": "Write", "tool_input": {"path": "src/x.py"}}, root=str(tmp_path)
    )["permission"] == "allow"
    assert evaluate_pre_mcp(
        {"server": "other-mcp", "tool_name": "do_thing"}, root=str(tmp_path)
    )["permission"] == "allow"


def test_env_killswitch_honored_for_non_enforce_mode(tmp_path, monkeypatch):
    """Dev convenience: kill-switch still disables non-enforcing (warn) sandbox."""
    from apatch.sandbox import is_sandbox_enabled

    _write_sandbox_mode(tmp_path, "warn")
    assert is_sandbox_enabled(str(tmp_path)) is True
    monkeypatch.setenv("APATCH_SANDBOX", "0")
    assert is_sandbox_enabled(str(tmp_path)) is False
