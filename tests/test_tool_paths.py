"""Central tool path resolution (tool_paths.py)."""

import json
import os
from pathlib import Path

from apatch.session_state import PHASE_VERIFY, enrich_tool_response
from apatch.runtime.session import start_session
from apatch.runtime.state_machine import OP_VERIFY, assert_operation
from apatch.tool_paths import (
    build_subprocess_env,
    materialize_verify_command,
    resolve_executable,
)
from apatch.toolchain import detect_toolchain


def test_resolve_executable_finds_npm():
    npm = resolve_executable("npm")
    if os.path.isfile("/opt/homebrew/bin/npm"):
        assert npm == os.path.realpath("/opt/homebrew/bin/npm")
    elif npm:
        assert os.path.basename(npm) == "npm"


def test_detect_toolchain_finds_npm_with_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "18.0.0"}}),
        encoding="utf-8",
    )
    data = detect_toolchain(str(tmp_path))
    npm = data["tools"]["npm"]
    if resolve_executable("npm", workspace=str(tmp_path)):
        assert npm["found"] is True
        assert npm["path"]
    assert "recommended_verify_resolved" in data
    if npm["found"]:
        assert "npm" in data["recommended_verify"]
        assert data["recommended_verify_resolved"].startswith("/")


def test_materialize_verify_command_rewrites_npm(tmp_path):
    npm = resolve_executable("npm", workspace=str(tmp_path))
    if not npm:
        return
    out = materialize_verify_command("npm run build", str(tmp_path))
    assert out.startswith(npm)
    assert "run build" in out


def test_build_subprocess_env_prepends_tool_dirs(tmp_path):
    env = build_subprocess_env(str(tmp_path), base_env={"PATH": "/usr/bin"})
    assert "PATH" in env
    assert "/usr/bin" in env["PATH"]




def test_build_subprocess_env_drops_mcp_server_only_policy(tmp_path):
    env = build_subprocess_env(
        str(tmp_path),
        base_env={
            "PATH": "/usr/bin",
            "APATCH_MCP_TARGET_POLICY": "alias_only",
            "KEEP_ME": "yes",
        },
    )
    assert "APATCH_MCP_TARGET_POLICY" not in env
    assert env["KEEP_ME"] == "yes"


def test_strip_success_moves_session_to_verify_phase(tmp_path):
    start_session(str(tmp_path), "strip test")
    out = enrich_tool_response(
        "apatch_strip",
        {"ok": True, "checkpoint": "ck_strip"},
        target_dir=str(tmp_path),
    )
    assert out["state_update"]["phase"] == PHASE_VERIFY


def test_verify_allowed_after_successful_strip(tmp_path):
    start_session(str(tmp_path), "verify gate")
    enrich_tool_response(
        "apatch_strip",
        {"ok": True, "checkpoint": "ck_strip"},
        target_dir=str(tmp_path),
    )
    assert_operation(str(tmp_path), OP_VERIFY)


def test_start_session_replaces_failed_session(tmp_path):
    from apatch.session_state import save_session_state, load_session_state, PHASE_BLOCKED

    save_session_state(
        str(tmp_path),
        {
            "session_id": "apatch_sess_old",
            "intent": "old",
            "phase": PHASE_BLOCKED,
            "failure": {"error_type": "APPLY_FAILED", "message": "x"},
        },
    )
    second = start_session(str(tmp_path), "fresh intent")
    assert second["ok"] is True
    assert second["session"]["intent"] == "fresh intent"
    raw = load_session_state(str(tmp_path))
    assert raw.get("failure") is None
