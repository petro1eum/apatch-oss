"""exec and policy-defined command actions (apatch_remote_service_action)."""
import pytest

from apatch.remote.services import plan_service_action
from apatch.remote.errors import RemoteTaskError

POLICY = {
    "targets": {
        "prod": {
            "host": "example-search-host",
            "path": "/srv",
            "services": {
                "search": {"kind": "command", "allowed": ["logs", "exec"], "commands": {"logs": "tail x"}},
                "noexec": {"kind": "command", "allowed": ["logs"], "commands": {"logs": "tail x"}},
                "maintenance": {
                    "kind": "command",
                    "allowed": ["cleanup_macos_metadata"],
                    "commands": {"cleanup_macos_metadata": "find . -name '._*' -delete"},
                },
                "broken_maintenance": {
                    "kind": "command",
                    "allowed": ["cleanup_macos_metadata"],
                    "commands": {},
                },
            },
        }
    }
}


def _etype(exc):
    return exc.value.to_result().get("error_type")


def test_exec_builds_shell_argv():
    plan = plan_service_action(
        alias="prod", service="search", action="exec",
        command="curl -s localhost:8004/health | head", policy=POLICY,
    )
    assert plan["operation"] == "command"
    assert plan["argv"][:2] == ["bash", "-lc"]
    assert "curl" in plan["argv"][2]


def test_exec_denied_without_allowed():
    with pytest.raises(RemoteTaskError) as exc:
        plan_service_action(alias="prod", service="noexec", action="exec",
                            command="cat x", policy=POLICY)
    assert _etype(exc) == "REMOTE_SERVICE_ACTION_DENIED"


def test_exec_rejects_destructive():
    with pytest.raises(RemoteTaskError) as exc:
        plan_service_action(alias="prod", service="search", action="exec",
                            command="rm -rf /tmp/x", policy=POLICY)
    assert _etype(exc) == "REMOTE_EXEC_NOT_READ_ONLY"


def test_exec_requires_command():
    with pytest.raises(RemoteTaskError) as exc:
        plan_service_action(alias="prod", service="search", action="exec",
                            command="", policy=POLICY)
    assert _etype(exc) == "REMOTE_EXEC_COMMAND_MISSING"


def test_policy_defined_command_action_builds_shell_argv():
    plan = plan_service_action(
        alias="prod",
        service="maintenance",
        action="cleanup_macos_metadata",
        policy=POLICY,
    )
    assert plan["operation"] == "command"
    assert plan["argv"] == ["bash", "-lc", "find . -name '._*' -delete"]


def test_policy_defined_command_action_requires_command_entry():
    with pytest.raises(RemoteTaskError) as exc:
        plan_service_action(
            alias="prod",
            service="broken_maintenance",
            action="cleanup_macos_metadata",
            policy=POLICY,
        )
    assert _etype(exc) == "REMOTE_SERVICE_ACTION_INVALID"
