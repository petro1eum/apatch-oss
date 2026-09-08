"""Regression: kind=command remote service (nohup/custom-managed lifecycles).

A service whose start/stop is custom (kill + nohup) is not modeled by the
systemd or docker_compose kinds. `kind: command` lets the local policy define
the exact shell for each action while apatch keeps SSH host/path/key hidden from
the agent (verbs in, redacted plan out).
"""
import json

from apatch.remote import services as svc
from apatch.remote.errors import RemoteTaskError

POLICY = {
    "targets": {
        "prod": {
            "host": "ubuntu@198.51.100.7",
            "root": "/srv/app/example-search-service",
            "display": "prod",
            "redact": True,
            "ssh_args": ["-i", "~/.ssh/id_demo"],
            "services": {
                "search": {
                    "kind": "command",
                    "allowed": ["status", "restart", "logs"],
                    "commands": {
                        "status": "lsof -t -iTCP:8004 -sTCP:LISTEN || echo DOWN",
                        "restart": "kill $(lsof -t -iTCP:8004) 2>/dev/null; "
                        "nohup uvicorn app:app --port 8004 >> kb.log 2>&1 & echo OK",
                        "logs": "tail -n {lines} /srv/app/example-search-service/kb.log",
                    },
                }
            },
        }
    }
}


def _plan(action, lines=80):
    return svc.plan_service_action(
        alias="prod", service="search", action=action, policy=POLICY, lines=lines
    )


def test_command_kind_builds_shell_argv():
    plan = _plan("status")
    assert plan["kind"] == "command"
    assert plan["operation"] == "command"
    assert plan["argv"][:2] == ["bash", "-lc"]
    assert "lsof" in plan["argv"][2]
    assert plan["ok"] and plan["dry_run"]


def test_command_kind_lines_substitution():
    plan = _plan("logs", lines=25)
    assert "tail -n 25" in plan["argv"][2]
    assert "{lines}" not in plan["argv"][2]


def test_command_kind_redacts_host_and_path():
    blob = json.dumps(_plan("restart"))
    assert "198.51.100.7" not in blob
    assert "id_demo" not in blob
    assert "/srv/app/example-search-service" not in blob
    assert "ubuntu@" not in blob


def test_command_kind_denies_unlisted_action():
    try:
        _plan("healthcheck")
        raise AssertionError("expected denial")
    except RemoteTaskError as exc:
        assert getattr(exc, "error_type", "") == "REMOTE_SERVICE_ACTION_DENIED"


def test_command_kind_missing_template_is_unsupported():
    bad = json.loads(json.dumps(POLICY))
    del bad["targets"]["prod"]["services"]["search"]["commands"]["restart"]
    try:
        svc.plan_service_action(alias="prod", service="search", action="restart", policy=bad)
        raise AssertionError("expected unsupported")
    except RemoteTaskError as exc:
        assert getattr(exc, "error_type", "") == "REMOTE_SERVICE_ACTION_UNSUPPORTED"