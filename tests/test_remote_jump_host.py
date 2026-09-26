"""Bastion / ProxyJump routing for remote aliases (RFP-030 A30-O)."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.remote.errors import REMOTE_ERROR_TYPES, RemoteTaskError
from apatch.remote.jump import jump_argv, normalize_jump_hosts, ssh_args_declare_jump
from apatch.remote.onboarding import build_remote_config
from apatch.remote.policy import resolve_remote_target
from apatch.remote.ssh import SshRemoteTransport
from apatch.remote.target import build_remote_target, parse_remote_target


def _policy(entry, **top):
    cfg = {
        "targets": {
            "search-example": {
                "host": "search-internal",
                "path": "/srv/example/search-workspace",
                **entry,
            }
        }
    }
    cfg.update(top)
    return cfg


def test_normalize_jump_hosts_accepts_string_list_and_comma_hops():
    assert normalize_jump_hosts(None) is None
    assert normalize_jump_hosts(False) is None
    assert normalize_jump_hosts("") is None
    assert normalize_jump_hosts("bastion") == ("bastion",)
    assert normalize_jump_hosts("edge, core") == ("edge", "core")
    assert normalize_jump_hosts(["ops@edge:2222", "[2001:db8::1]:22"]) == ("ops@edge:2222", "[2001:db8::1]:22")


@pytest.mark.parametrize("bad", ["-oProxyCommand=evil", "bastion evil", ["ok", 3], {"host": "x"}, True])
def test_normalize_jump_hosts_fails_closed_on_unsafe_values(bad):
    with pytest.raises(RemoteTaskError) as excinfo:
        normalize_jump_hosts(bad)
    assert excinfo.value.error_type == "REMOTE_POLICY_INVALID"


def test_ssh_args_declare_jump_detects_every_proxyjump_spelling():
    assert ssh_args_declare_jump(["-J", "bastion"]) is True
    assert ssh_args_declare_jump(["-Jbastion"]) is True
    assert ssh_args_declare_jump(["-o", "ProxyJump=bastion"]) is True
    assert ssh_args_declare_jump(["-oproxyjump=bastion"]) is True
    assert ssh_args_declare_jump(["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]) is False
    assert ssh_args_declare_jump(None) is False
    assert jump_argv(None) == []
    assert jump_argv(("edge", "core")) == ["-J", "edge,core"]


def test_policy_alias_jump_host_string_and_list():
    single = resolve_remote_target("search-example", policy=_policy({"jump_host": "bastion"}))
    assert single.jump_hosts == ("bastion",)

    multi = resolve_remote_target("search-example", policy=_policy({"jump_hosts": ["edge", "core"]}))
    assert multi.jump_hosts == ("edge", "core")


def test_policy_default_jump_host_applies_and_alias_can_opt_out():
    inherited = resolve_remote_target("search-example", policy=_policy({}, jump_host="bastion"))
    assert inherited.jump_hosts == ("bastion",)

    opted_out = resolve_remote_target(
        "search-example", policy=_policy({"jump_host": None}, jump_host="bastion")
    )
    assert opted_out.jump_hosts is None


def test_policy_rejects_jump_host_combined_with_raw_proxyjump_flag():
    with pytest.raises(RemoteTaskError) as excinfo:
        resolve_remote_target(
            "search-example",
            policy=_policy({"jump_host": "bastion", "ssh_args": ["-o", "ProxyJump=other"]}),
        )
    assert excinfo.value.error_type == "REMOTE_POLICY_INVALID"
    assert "ProxyJump" in excinfo.value.message

    # Raw -J without jump_host stays a supported escape hatch.
    legacy = resolve_remote_target("search-example", policy=_policy({"ssh_args": ["-J", "bastion"]}))
    assert legacy.jump_hosts is None
    assert legacy.ssh_args == ("-J", "bastion")


def test_policy_enforces_allowed_jump_hosts_and_require_jump_host():
    with pytest.raises(RemoteTaskError) as excinfo:
        resolve_remote_target(
            "search-example",
            policy=_policy({"jump_host": "rogue"}, allowed_jump_hosts=["bastion-*"]),
        )
    assert excinfo.value.error_type == "REMOTE_JUMP_HOST_DENIED"

    allowed = resolve_remote_target(
        "search-example",
        policy=_policy({"jump_host": "bastion-eu"}, allowed_jump_hosts=["bastion-*"]),
    )
    assert allowed.jump_hosts == ("bastion-eu",)

    with pytest.raises(RemoteTaskError) as excinfo:
        resolve_remote_target("search-example", policy=_policy({}, require_jump_host=True))
    assert excinfo.value.error_type == "REMOTE_JUMP_HOST_REQUIRED"

    assert {"REMOTE_JUMP_HOST_DENIED", "REMOTE_JUMP_HOST_REQUIRED"} <= set(REMOTE_ERROR_TYPES)


def test_redacted_target_hides_hops_but_admits_a_jump_is_used():
    target = resolve_remote_target("search-example", policy=_policy({"jump_host": "bastion"}))
    exposed = target.as_dict()
    assert exposed["redacted"] is True
    assert exposed["via_jump_host"] is True
    assert "jump_hosts" not in exposed
    assert "bastion" not in json.dumps(exposed)

    plain = resolve_remote_target("search-example", policy=_policy({}))
    assert "via_jump_host" not in plain.as_dict()

    direct = build_remote_target("search-internal", "/srv/repo", jump_hosts=("bastion",))
    assert direct.as_dict()["jump_hosts"] == ["bastion"]


def test_ssh_transport_dials_through_jump_hosts(monkeypatch):
    calls = []

    def fake_run(argv, input, text, capture_output, timeout, check):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"ok": True}), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = resolve_remote_target(
        "search-example",
        policy=_policy({"jump_host": ["edge", "core"], "ssh_args": ["-o", "BatchMode=yes"]}),
    )
    result = SshRemoteTransport(ssh_args=list(target.ssh_args)).call(target, "apatch_doctor", {})

    assert result["ok"] is True
    argv = calls[0]
    assert argv[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert argv[3:5] == ["-J", "edge,core"]
    assert argv[5] == "search-internal"
    assert argv.count("-J") == 1

    SshRemoteTransport().call(parse_remote_target("example-search-host:/srv/repo"), "apatch_doctor", {})
    assert "-J" not in calls[1]


def test_service_transport_dials_through_jump_host_and_redacts_stderr(monkeypatch):
    from apatch.remote.services import SshServiceTransport, _redact_plan

    calls = []

    def fake_run(argv, input, text, capture_output, timeout, check):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 255, stdout="", stderr="ssh: connect to host bastion port 22: Connection refused"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = resolve_remote_target(
        "search-example", policy=_policy({"jump_host": "bastion", "display": "Search main"})
    )
    raw = SshServiceTransport().call(target, {"operation": "command", "argv": ["true"]})

    argv = calls[0]
    assert argv[argv.index("-J") + 1] == "bastion"
    assert argv[argv.index("-J") + 2] == "search-internal"
    assert raw["ok"] is False
    assert "bastion" in raw["stderr"]

    redacted = _redact_plan(target, raw)
    assert "bastion" not in json.dumps(redacted)
    assert "Search main" in redacted["stderr"]


def test_archive_transport_dials_through_jump_hosts(monkeypatch, tmp_path):
    from apatch.remote.handoff import SshArchiveTransport

    calls = []

    class Pipe:
        def close(self):
            pass

        def read(self):
            return b""

    class Proc:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.stdout = Pipe()
            self.stderr = Pipe()
            self.returncode = 0
            calls.append(self)

        def communicate(self, timeout=None):
            return b"", b""

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("apatch.remote.handoff.subprocess.Popen", Proc)
    target = build_remote_target(
        "search-internal",
        "/srv/example/search-workspace",
        alias="search-example",
        jump_hosts=("bastion",),
    )
    result = SshArchiveTransport(ssh_args=["-o", "BatchMode=yes"], timeout_sec=10).push(
        target, {"source_dir": str(tmp_path)}
    )

    assert result["ok"] is True
    ssh_cmd = calls[1].cmd
    assert ssh_cmd[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert ssh_cmd[3:6] == ["-J", "bastion", "search-internal"]


def test_timeline_sanitizer_scrubs_jump_host_names():
    from apatch.remote.orchestrator import _sanitize_for_target

    target = resolve_remote_target(
        "search-example", policy=_policy({"jump_host": "bastion", "display": "Search main"})
    )
    leaked = {
        "stderr": "channel 0: open failed: connect failed via bastion to search-internal",
        "jump_host": "bastion",
        "nested": ["ssh -J bastion search-internal"],
    }

    safe = _sanitize_for_target(target, leaked)

    serialized = json.dumps(safe)
    assert "bastion" not in serialized
    assert "search-internal" not in serialized
    assert safe["jump_host"] == "Search main"


def test_build_remote_config_writes_jump_host_policy():
    config = build_remote_config(
        alias="search-example",
        host="search-internal",
        path="/srv/example/search-workspace",
        jump_host=["edge", "core"],
        require_jump_host=True,
        ssh_args=["-o", "ServerAliveInterval=30"],
    )
    assert config["targets"]["search-example"]["jump_host"] == ["edge", "core"]
    assert config["allowed_jump_hosts"] == ["edge", "core"]
    assert config["require_jump_host"] is True
    resolved = resolve_remote_target("search-example", policy=config)
    assert resolved.jump_hosts == ("edge", "core")
    assert resolved.as_dict()["via_jump_host"] is True

    single = build_remote_config(alias="a", host="h", path="/srv/x", jump_host="bastion")
    assert single["targets"]["a"]["jump_host"] == "bastion"
    assert single["allowed_jump_hosts"] == ["bastion"]

    plain = build_remote_config(alias="a", host="h", path="/srv/x")
    assert "allowed_jump_hosts" not in plain
    assert "require_jump_host" not in plain

    with pytest.raises(RemoteTaskError) as excinfo:
        build_remote_config(alias="a", host="h", path="/srv/x", jump_host="bastion", ssh_args=["-J", "other"])
    assert excinfo.value.error_type == "REMOTE_CONFIG_INVALID"

    with pytest.raises(RemoteTaskError) as excinfo:
        build_remote_config(alias="a", host="h", path="/srv/x", require_jump_host=True)
    assert excinfo.value.error_type == "REMOTE_CONFIG_INVALID"


def test_remote_init_cli_jump_host_round_trip_and_merge(tmp_path):
    runner = CliRunner()
    base = ["remote", "init", "--target-dir", str(tmp_path), "--json"]

    first = runner.invoke(
        cli,
        [
            *base,
            "--alias",
            "search-example",
            "--host",
            "search-internal",
            "--path",
            "/srv/example/search-workspace",
            "--jump-host",
            "bastion.example.net",
            "--require-jump-host",
        ],
    )
    assert first.exit_code == 0, first.output
    policy = json.loads((tmp_path / ".apatch" / "remote.json").read_text(encoding="utf-8"))
    assert policy["targets"]["search-example"]["jump_host"] == "bastion.example.net"
    assert policy["allowed_jump_hosts"] == ["bastion.example.net"]
    assert policy["require_jump_host"] is True

    validate = runner.invoke(
        cli,
        ["remote", "validate", "--target-dir", str(tmp_path), "--alias", "search-example", "--json"],
    )
    assert validate.exit_code == 0, validate.output
    validated = json.loads(validate.output)
    assert validated["ok"] is True
    assert validated["target"]["via_jump_host"] is True
    assert "bastion.example.net" not in validate.output
    assert "search-internal" not in validate.output

    second = runner.invoke(
        cli,
        [
            *base,
            "--alias",
            "reports",
            "--host",
            "reports-internal",
            "--path",
            "/srv/example/reports",
            "--jump-host",
            "bastion2.example.net",
        ],
    )
    assert second.exit_code == 0, second.output
    merged = json.loads((tmp_path / ".apatch" / "remote.json").read_text(encoding="utf-8"))
    assert merged["allowed_jump_hosts"] == ["bastion.example.net", "bastion2.example.net"]
    assert merged["require_jump_host"] is True
    assert set(merged["targets"]) == {"search-example", "reports"}

    # A direct alias added later is refused by the sticky require_jump_host guard.
    direct = runner.invoke(
        cli,
        [*base, "--alias", "leaky", "--host", "leaky-internal", "--path", "/srv/example/leaky"],
    )
    assert direct.exit_code == 0, direct.output
    denied = runner.invoke(
        cli,
        ["remote", "validate", "--target-dir", str(tmp_path), "--alias", "leaky", "--json"],
    )
    assert denied.exit_code != 0
    assert "REMOTE_JUMP_HOST_REQUIRED" in denied.output
