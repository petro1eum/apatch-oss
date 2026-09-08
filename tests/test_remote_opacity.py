"""The agent never learns where the server is (RFP-030 / SPEC-REMOTE-OPACITY-1).

Remote work is brokered: the agent addresses a workspace by an opaque alias and apatch
resolves host, root, jump host and credentials from local policy. These gates freeze the
property that none of that reaches the agent -- not in a response, a timeline, a nested
result, or an error quoting its own host.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from apatch.remote.errors import RemoteTaskError
from apatch.remote.policy import resolve_remote_target
from apatch.remote.transport import FakeRemoteTransport
from apatch.remote.target import parse_remote_target

ROOT = Path(__file__).resolve().parents[1]
SECRET_HOST = "opacity-probe-host.internal.invalid"
SECRET_PATH = "/srv/opacity/probe-root"


def _write_policy(tmp_path: Path, body: str) -> None:
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir(exist_ok=True)
    (apatch_dir / "remote.json").write_text(body, encoding="utf-8")


def _alias_policy(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        json.dumps({"targets": {"probe": {"host": SECRET_HOST, "path": SECRET_PATH}}}),
    )


def test_malformed_policy_fails_closed(tmp_path):
    """A30-C: a broken policy refuses; it never degrades to an unbrokered path."""
    _write_policy(tmp_path, "{not json")
    with pytest.raises(RemoteTaskError) as broken:
        resolve_remote_target("probe", policy_root=str(tmp_path))
    assert broken.value.error_type == "REMOTE_POLICY_INVALID"

    _write_policy(tmp_path, json.dumps(["not", "an", "object"]))
    with pytest.raises(RemoteTaskError) as shaped:
        resolve_remote_target("probe", policy_root=str(tmp_path))
    assert shaped.value.error_type == "REMOTE_POLICY_INVALID"


def test_a_host_leaked_inside_a_nested_failure_is_rewritten(tmp_path):
    """A30-F: a remote failure quoting its own host must not carry it back."""
    from apatch.remote.orchestrator import remote_task_run

    _alias_policy(tmp_path)
    transport = FakeRemoteTransport(
        {
            "apatch_doctor": {
                "ok": False,
                "error_type": "REMOTE_WORKER_FAILED",
                "message": "ssh %s: cannot open %s" % (SECRET_HOST, SECRET_PATH),
                "host": SECRET_HOST,
            }
        }
    )
    result = remote_task_run(
        "probe", intent="opacity probe", transport=transport, policy_root=str(tmp_path)
    )
    blob = json.dumps(result, ensure_ascii=False)
    assert SECRET_HOST not in blob
    assert SECRET_PATH not in blob


def test_an_explicit_target_stays_readable_for_debugging():
    """A30-G: only aliases are opaque; a hand-typed target is not silently redacted."""
    explicit = parse_remote_target("ssh://example-host/srv/example/x")
    assert explicit.redact is False
    exposed = explicit.as_dict()
    assert exposed["host"] == "example-host"
    assert exposed["redacted"] is False


def test_a_locked_alias_keeps_its_own_transport(monkeypatch, tmp_path):
    """A30-I: policy transport wins; the caller may shorten a timeout, never widen it."""
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp import server as mcp_server

    _write_policy(
        tmp_path,
        json.dumps(
            {
                "targets": {
                    "probe": {
                        "host": SECRET_HOST,
                        "path": SECRET_PATH,
                        "python": "/opt/policy/bin/python3",
                        "ssh_args": ["-J", "policy-bastion"],
                        "timeout_sec": 30,
                    }
                }
            }
        ),
    )
    built = {}

    def _record(python, ssh_args, timeout_sec):
        built.update(python=python, ssh_args=ssh_args, timeout_sec=timeout_sec)
        return FakeRemoteTransport({"apatch_doctor": {"ok": True}})

    import apatch.remote.ssh as ssh_module

    monkeypatch.setattr(ssh_module, "SshRemoteTransport", _record)
    tool = mcp_server.mcp._tool_manager._tools["apatch_remote_task_run"].fn
    tool(
        remote_target="probe",
        intent="opacity probe",
        dry_run=False,
        python="/tmp/attacker/python3",
        ssh_args=["-J", "attacker-bastion"],
        timeout_sec=9000,
        target_dir=str(tmp_path),
    )

    assert built["python"] == "/opt/policy/bin/python3"
    assert built["ssh_args"] == ["-J", "policy-bastion"]
    assert built["timeout_sec"] == 30


def test_remote_gates_never_open_a_real_connection():
    """A30-K: the suite proves this without SSH, network or secrets."""
    transport_test = (ROOT / "tests" / "test_remote_ssh_transport.py").read_text(encoding="utf-8")
    for block in transport_test.split("\ndef ")[1:]:
        if "SshRemoteTransport" in block:
            assert "monkeypatch" in block.split("\n", 1)[0], block.split("(", 1)[0]

    for name in ("test_remote_target.py", "test_remote_mcp_routing.py", "test_remote_onboarding.py"):
        text = (ROOT / "tests" / name).read_text(encoding="utf-8")
        assert "SshRemoteTransport" not in text, name


def test_the_broker_model_is_written_down():
    """A30-L: an operator can read why SSH details are not agent context."""
    doc = (ROOT / "docs" / "remote-onboarding.md").read_text(encoding="utf-8")
    assert "broker" in doc.lower()
    assert "alias" in doc.lower()
