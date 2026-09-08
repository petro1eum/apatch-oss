import json
import subprocess
import sys

from apatch.remote.ssh import SshRemoteTransport
from apatch.remote.target import parse_remote_target


def test_ssh_transport_sends_json_envelope_and_parses_result(monkeypatch):
    calls = []

    def fake_run(argv, input, text, capture_output, timeout, check):
        calls.append(
            {
                "argv": argv,
                "payload": json.loads(input),
                "text": text,
                "capture_output": capture_output,
                "timeout": timeout,
                "check": check,
            }
        )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"ok": True, "workspace": "/home/ubuntu/project"}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    transport = SshRemoteTransport(
        python="/opt/py/bin/python3",
        ssh_args=["-o", "BatchMode=yes"],
        timeout_sec=12,
    )

    result = transport.call(
        parse_remote_target("example-search-host:/home/ubuntu/project"),
        "apatch_doctor",
        {},
    )

    assert result["ok"] is True
    assert result["operation"] == "apatch_doctor"
    assert result["ssh_returncode"] == 0
    assert calls[0]["argv"][:4] == ["ssh", "-o", "BatchMode=yes", "example-search-host"]
    assert "cd /home/ubuntu/project" in calls[0]["argv"][-1]
    assert "/opt/py/bin/python3" in calls[0]["argv"][-1]
    assert "-m apatch.remote.worker" in calls[0]["argv"][-1]
    assert "-c" not in calls[0]["argv"][-1]
    assert calls[0]["payload"] == {"operation": "apatch_doctor", "arguments": {}}
    assert calls[0]["timeout"] == 12
    assert calls[0]["check"] is False


def test_ssh_transport_returns_typed_error_when_remote_fails(monkeypatch):
    def fake_run(argv, input, text, capture_output, timeout, check):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="python: apatch not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SshRemoteTransport().call(
        parse_remote_target("example-search-host:/home/ubuntu/project"),
        "apatch_doctor",
        {},
    )

    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_COMMAND_FAILED"
    assert result["ssh_returncode"] == 2
    assert "apatch not found" in result["stderr"]


def test_ssh_transport_classifies_missing_remote_apatch(monkeypatch):
    def fake_run(argv, input, text, capture_output, timeout, check):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="No module named apatch.remote.worker")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SshRemoteTransport().call(parse_remote_target("example-search-host:/home/ubuntu/project"), "apatch_doctor", {})

    assert result["error_type"] == "REMOTE_APATCH_MISSING"
    assert "raw scp" in result["recommended_action"]


def test_ssh_transport_returns_typed_error_for_invalid_json(monkeypatch):
    def fake_run(argv, input, text, capture_output, timeout, check):
        return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SshRemoteTransport().call(
        parse_remote_target("example-search-host:/home/ubuntu/project"),
        "apatch_doctor",
        {},
    )

    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_JSON_INVALID"
    assert result["recoverable"] is True


def test_ssh_transport_recovers_trailing_worker_json(monkeypatch):
    payload = {
        "ok": False,
        "error_type": "VERIFY_FAILED",
        "diagnostics": [{"kind": "pytest", "message": "assertion failed"}],
    }

    def fake_run(argv, input, text, capture_output, timeout, check):
        stdout = "TrustChain commit accepted\nRich progress output\n" + json.dumps(payload)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SshRemoteTransport().call(
        parse_remote_target("example-search-host:/home/ubuntu/project"),
        "apatch_spec_run_multi",
        {},
    )

    assert result["ok"] is False
    assert result["error_type"] == "VERIFY_FAILED"
    assert result["diagnostics"][0]["kind"] == "pytest"


def test_service_transport_nonzero_command_has_diagnostic_hint(monkeypatch):
    from apatch.remote.services import SshServiceTransport

    def fake_run(argv, input, text, capture_output, timeout, check):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"ok": False, "returncode": 2, "stderr": "bad command"}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SshServiceTransport().call(
        parse_remote_target("example-search-host:/home/ubuntu/project"),
        {"operation": "command", "argv": ["bash", "-lc", "false"]},
    )

    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_SERVICE_COMMAND_FAILED"
    assert "rollback" not in result["recommended_action"].lower()


def test_mcp_remote_task_run_pins_policy_identity_and_bounds_timeout(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    import apatch.remote.ssh as ssh_mod
    from apatch.mcp.server import apatch_remote_task_run
    from apatch.remote.transport import FakeRemoteTransport

    captured = {}

    class CapturingTransport(FakeRemoteTransport):
        def __init__(self, *, python, ssh_args, timeout_sec):
            captured["python"] = python
            captured["ssh_args"] = ssh_args
            captured["timeout_sec"] = timeout_sec
            super().__init__(
                {
                    "apatch_generate_batch": {
                        "ok": True,
                        "logs_path": ".apatch/remote/patches.jsonl",
                    }
                }
            )

    monkeypatch.setattr(ssh_mod, "SshRemoteTransport", CapturingTransport)
    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "targets": {
                    "search-example": {
                        "host": "example-search-host",
                        "path": "/srv/example/search-workspace",
                        "python": "/opt/remote/bin/python",
                        "ssh_args": ["-J", "bastion"],
                        "apatch_runtime": {"path": "/home/ubuntu/.local/src/apatch_runtime"},
                        "timeout_sec": 77,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = apatch_remote_task_run(
        remote_target="search-example",
        intent="Fix filter",
        target_dir=str(tmp_path),
        dry_run=False,
        python="/agent/should/not/win",
        ssh_args=["-x"],
        timeout_sec=1,
    )

    assert result["ok"] is True
    assert result["transport"] == "ssh"
    assert captured == {
        "python": "/opt/remote/bin/python",
        "ssh_args": ["-J", "bastion"],
        "timeout_sec": 1,
    }

    result = apatch_remote_task_run(
        remote_target="search-example",
        intent="Fix filter",
        target_dir=str(tmp_path),
        dry_run=False,
        timeout_sec=999,
    )

    assert result["ok"] is True
    assert captured["timeout_sec"] == 77


def test_service_action_bounds_outer_ssh_timeout_to_operation(monkeypatch):
    import apatch.remote.services as services

    captured = {}

    class CapturingServiceTransport:
        def __init__(self, *, python, ssh_args, timeout_sec):
            captured.update(python=python, ssh_args=ssh_args, timeout_sec=timeout_sec)

        def call(self, target, plan):
            captured["plan"] = dict(plan)
            return {"ok": True}

    monkeypatch.setattr(services, "SshServiceTransport", CapturingServiceTransport)
    policy = {
        "targets": {
            "prod": {
                "host": "example-search-host",
                "path": "/srv/search",
                "timeout_sec": 600,
                "services": {
                    "search": {
                        "kind": "http",
                        "allowed": ["healthcheck"],
                        "healthcheck": {
                            "url": "http://127.0.0.1:8004/health",
                            "timeout_sec": 10,
                        },
                    }
                },
            }
        }
    }

    result = services.execute_service_action(
        alias="prod",
        service="search",
        action="healthcheck",
        policy=policy,
    )

    assert result["ok"] is True
    assert captured["timeout_sec"] == 25
    assert captured["plan"]["timeout_sec"] == 10


def test_remote_service_script_times_out_command():
    from apatch.remote.services import _REMOTE_SERVICE_SCRIPT

    proc = subprocess.run(
        [sys.executable, "-c", _REMOTE_SERVICE_SCRIPT],
        input=json.dumps(
            {
                "plan": {
                    "operation": "command",
                    "argv": [sys.executable, "-c", "import time; time.sleep(2)"],
                    "timeout_sec": 1,
                }
            }
        ),
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error_type"] == "REMOTE_SERVICE_COMMAND_TIMEOUT"
