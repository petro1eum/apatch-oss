"""Remote workspace support for apatch."""

from apatch.remote.orchestrator import remote_task_run
from apatch.remote.handoff import execute_source_handoff, plan_source_handoff
from apatch.remote.policy import load_remote_policy, resolve_remote_target
from apatch.remote.ssh import SshRemoteTransport
from apatch.remote.target import RemoteTarget, parse_remote_target

__all__ = [
    "RemoteTarget",
    "SshRemoteTransport",
    "execute_source_handoff",
    "load_remote_policy",
    "parse_remote_target",
    "plan_source_handoff",
    "remote_task_run",
    "resolve_remote_target",
]
