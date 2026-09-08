"""governed_mode: off / auto_session / strict session gate."""

import json
import os

import pytest

from apatch.enforcement import (
    GOVERNED_MODE_AUTO_SESSION,
    GOVERNED_MODE_OFF,
    GOVERNED_MODE_STRICT,
    resolve_governed_mode,
    write_enforcement_config,
)
from apatch.runtime.runtime import MutationRuntime


def _write_enforcement(tmp_path, governed_mode: str, *, trustchain_enforce: bool = False):
    path = tmp_path / ".apatch"
    path.mkdir(parents=True, exist_ok=True)
    (path / "enforcement.json").write_text(
        json.dumps({
            "mode": "strict" if trustchain_enforce else "off",
            "governed_mode": governed_mode,
        }),
        encoding="utf-8",
    )


def _write_probe_patch(tmp_path, logs_path):
    probe = tmp_path / ".apatch" / "probe.txt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("PROBE\n", encoding="utf-8")
    entry = {
        "step_index": 1,
        "tool_calls": [
            {
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": ".apatch/probe.txt",
                    "TargetContent": "PROBE",
                    "ReplacementContent": "PROBE_OK",
                    "AllowMultiple": True,
                },
            }
        ],
    }
    logs_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


def test_resolve_governed_mode_default_off(tmp_path):
    assert resolve_governed_mode(str(tmp_path)) == GOVERNED_MODE_OFF


def test_resolve_governed_mode_from_config(tmp_path):
    _write_enforcement(tmp_path, GOVERNED_MODE_AUTO_SESSION)
    assert resolve_governed_mode(str(tmp_path)) == GOVERNED_MODE_AUTO_SESSION


def test_resolve_governed_mode_env_overrides_config(tmp_path, monkeypatch):
    _write_enforcement(tmp_path, GOVERNED_MODE_AUTO_SESSION)
    monkeypatch.setenv("APATCH_GOVERNED_MODE", GOVERNED_MODE_STRICT)
    assert resolve_governed_mode(str(tmp_path)) == GOVERNED_MODE_STRICT


def test_resolve_governed_mode_env_cannot_weaken_committed_floor(tmp_path, monkeypatch):
    """RFP-005 §audit #2: env may only ratchet governance up, never down."""
    _write_enforcement(tmp_path, GOVERNED_MODE_STRICT)
    monkeypatch.setenv("APATCH_GOVERNED_MODE", GOVERNED_MODE_OFF)
    assert resolve_governed_mode(str(tmp_path)) == GOVERNED_MODE_STRICT
    monkeypatch.setenv("APATCH_GOVERNED_MODE", GOVERNED_MODE_AUTO_SESSION)
    assert resolve_governed_mode(str(tmp_path)) == GOVERNED_MODE_STRICT


def test_resolve_governed_mode_env_can_ratchet_up_from_off(tmp_path, monkeypatch):
    # No committed floor (off) → env strict is a legitimate upgrade.
    monkeypatch.setenv("APATCH_GOVERNED_MODE", GOVERNED_MODE_STRICT)
    assert resolve_governed_mode(str(tmp_path)) == GOVERNED_MODE_STRICT


def test_resolve_governed_mode_explicit_off_is_honored(tmp_path):
    """An explicit opt-out is a deliberate operator choice."""
    _write_enforcement(tmp_path, GOVERNED_MODE_OFF)
    assert resolve_governed_mode(str(tmp_path)) == GOVERNED_MODE_OFF


def test_init_consumer_enforcement_writes_auto_session(tmp_path):
    from apatch.doctor import init_consumer

    init_consumer(str(tmp_path), with_enforcement=True, governed_mode=GOVERNED_MODE_AUTO_SESSION)
    cfg_path = tmp_path / ".apatch" / "enforcement.json"
    assert cfg_path.is_file()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg.get("governed_mode") == GOVERNED_MODE_AUTO_SESSION


def test_init_consumer_enforcement_default_is_safe(tmp_path):
    """RFP-005 §audit #8: --with-enforcement (no explicit mode) defaults to a
    real governed gate (auto_session), never a silent off."""
    from apatch.doctor import init_consumer

    init_consumer(str(tmp_path), with_enforcement=True)
    cfg = json.loads((tmp_path / ".apatch" / "enforcement.json").read_text(encoding="utf-8"))
    assert cfg.get("mode") == "strict"
    assert cfg.get("governed_mode") == GOVERNED_MODE_AUTO_SESSION


def test_init_consumer_sandbox_default_enforces(tmp_path):
    """RFP-005 §audit #8: --with-sandbox writes mode=enforce, not a soft off."""
    from apatch.doctor import init_consumer

    init_consumer(str(tmp_path), with_sandbox=True)
    cfg = json.loads((tmp_path / ".apatch" / "sandbox.json").read_text(encoding="utf-8"))
    assert cfg.get("mode") == "enforce"


def test_write_enforcement_config_governed_mode(tmp_path):
    path = write_enforcement_config(str(tmp_path), governed_mode=GOVERNED_MODE_STRICT)
    cfg = json.loads(open(path, encoding="utf-8").read())
    assert cfg["governed_mode"] == GOVERNED_MODE_STRICT


def test_auto_session_creates_session_on_apply(tmp_path):
    _write_enforcement(tmp_path, GOVERNED_MODE_AUTO_SESSION)
    logs = tmp_path / "patch.jsonl"
    _write_probe_patch(tmp_path, logs)
    rt = MutationRuntime(str(tmp_path))

    result = rt.apply(str(logs), no_trustchain=True)
    assert result.get("ok") is True

    tail = rt.events_tail(limit=20)
    started = [e for e in tail["events"] if e.get("type") == "SessionStarted"]
    assert started
    assert started[0].get("payload", {}).get("auto_started") is True


def test_auto_session_creates_session_on_phase_run(tmp_path):
    import json

    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    _write_enforcement(tmp_path, GOVERNED_MODE_AUTO_SESSION)
    src = tmp_path / "src" / "pages"
    src.mkdir(parents=True)
    page = src / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")

    rt = MutationRuntime(str(tmp_path))
    result = rt.phase_run(
        str(page),
        str(manifest),
        profile="frontend",
        out_dir=str(tmp_path / "extracted"),
        module_out_dir=str(tmp_path / "src" / "hooks"),
        to_module="hook",
        verify_cmd="echo ok",
        no_trustchain=True,
    )
    assert result.get("ok") is True, result

    tail = rt.events_tail(limit=20)
    started = [e for e in tail["events"] if e.get("type") == "SessionStarted"]
    assert started
    assert started[0].get("payload", {}).get("auto_started") is True


def test_strict_blocks_apply_without_session(tmp_path):
    _write_enforcement(tmp_path, GOVERNED_MODE_STRICT)
    logs = tmp_path / "patch.jsonl"
    _write_probe_patch(tmp_path, logs)
    rt = MutationRuntime(str(tmp_path))

    blocked = rt.apply(str(logs), no_trustchain=True)
    assert blocked.get("ok") is False
    assert "apatch_session_start" in (blocked.get("hint") or "")


def test_mcp_strip_uses_governed_mode(tmp_path):
    import json

    pytest.importorskip("pydantic")  # apatch.mcp.server needs the mcp extra
    from apatch.mcp.server import apatch_strip
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    _write_enforcement(tmp_path, GOVERNED_MODE_AUTO_SESSION)
    src = tmp_path / "src"
    src.mkdir()
    page = src / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")

    result = apatch_strip(
        file_path="src/Planning.tsx",
        manifest_path=str(manifest),
        to_module="hook",
        module_out_dir="src/hooks",
        verify="echo ok",
        no_trustchain=True,
        target_dir=str(tmp_path),
    )
    assert result.get("ok") is True, result
    assert result.get("state_update") is not None
    tail = MutationRuntime(str(tmp_path)).events_tail(limit=20)
    started = [e for e in tail["events"] if e.get("type") == "SessionStarted"]
    assert started
    assert started[0].get("payload", {}).get("auto_started") is True


def test_mutation_replay_cli_alias(tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["mutation", "replay", "--help"])
    assert result.exit_code == 0
    assert "Replay apply_session" in result.output or "replay" in result.output.lower()
