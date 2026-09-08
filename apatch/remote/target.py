"""Remote workspace target parsing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from typing import Any, Callable, Dict, Optional, Tuple

from apatch.remote.errors import RemoteTaskError


_SSH_URI_RE = re.compile(r"^ssh://(?P<host>[^/]+)/(?P<path>.+)$")
_SCP_STYLE_RE = re.compile(r"^(?P<host>[^:]+):(?P<path>/.*)$")


@dataclass(frozen=True)
class RemoteTarget:
    """Canonical remote workspace location."""

    host: str
    path: str
    uri: str
    workspace_id: str
    alias: Optional[str] = None
    display: Optional[str] = None
    redact: bool = False
    python: Optional[str] = None
    runtime_path: Optional[str] = None
    ssh_args: Optional[Tuple[str, ...]] = None
    timeout_sec: Optional[int] = None
    allow_transport_overrides: bool = True
    allowed_operations: Optional[Tuple[str, ...]] = None

    def as_dict(self) -> Dict[str, Any]:
        if self.redact:
            label = self.display or self.alias or "remote"
            return {
                "alias": self.alias or label,
                "display": label,
                "workspace_id": self.workspace_id,
                "redacted": True,
            }
        result = {
            "host": self.host,
            "path": self.path,
            "uri": self.uri,
            "workspace_id": self.workspace_id,
            "redacted": False,
        }
        if self.alias:
            result["alias"] = self.alias
        if self.display:
            result["display"] = self.display
        if self.runtime_path:
            result["runtime_path"] = self.runtime_path
        return result


def parse_remote_target(value: str) -> RemoteTarget:
    """Parse ssh://host/path or host:/path into a canonical remote target."""

    raw = (value or "").strip()
    match = _SSH_URI_RE.match(raw)
    if match:
        host = match.group("host").strip()
        path = "/" + match.group("path").strip()
        return _build_target(host, path)

    match = _SCP_STYLE_RE.match(raw)
    if match:
        host = match.group("host").strip()
        path = match.group("path").strip()
        return _build_target(host, path)

    raise RemoteTaskError(
        "REMOTE_TARGET_INVALID",
        "Remote target must use ssh://host/absolute/path or host:/absolute/path.",
        recoverable=True,
        recommended_action="Pass an explicit SSH host and absolute workspace path.",
    )


def is_remote_target(value: str) -> bool:
    try:
        parse_remote_target(value)
    except RemoteTaskError:
        return False
    return True


def guard_local_workspace(
    value: str,
    *,
    local_exists: Optional[Callable[[str], bool]] = None,
) -> None:
    """Reject a remote-looking absolute path passed as a local mutating target.

    A POSIX absolute path that does not exist on the local controller and was not
    supplied as an explicit remote target (``ssh://host/path`` or ``host:/path``)
    almost always means the agent meant a remote workspace. Fail closed with
    remediation instead of silently creating a local directory tree.
    """

    raw = (value or "").strip()
    if not raw or raw in (".", "./"):
        return
    if is_remote_target(raw):
        return
    if not raw.startswith("/"):
        return
    exists = local_exists or os.path.exists
    if exists(raw):
        return
    raise RemoteTaskError(
        "WORKSPACE_NOT_LOCAL",
        "Absolute path is not a local workspace and no remote target was supplied.",
        recoverable=True,
        recommended_action=(
            "Use ssh://host/path (or host:/path), mount/sync the repo locally, "
            "or run apatch MCP on the remote host."
        ),
    )


def build_remote_target(
    host: str,
    path: str,
    *,
    alias: Optional[str] = None,
    display: Optional[str] = None,
    redact: bool = False,
    python: Optional[str] = None,
    runtime_path: Optional[str] = None,
    ssh_args: Optional[Tuple[str, ...]] = None,
    timeout_sec: Optional[int] = None,
    allow_transport_overrides: bool = True,
    allowed_operations: Optional[Tuple[str, ...]] = None,
) -> RemoteTarget:
    return _build_target(
        host,
        path,
        alias=alias,
        display=display,
        redact=redact,
        python=python,
        runtime_path=runtime_path,
        ssh_args=ssh_args,
        timeout_sec=timeout_sec,
        allow_transport_overrides=allow_transport_overrides,
        allowed_operations=allowed_operations,
    )


def _build_target(
    host: str,
    path: str,
    *,
    alias: Optional[str] = None,
    display: Optional[str] = None,
    redact: bool = False,
    python: Optional[str] = None,
    runtime_path: Optional[str] = None,
    ssh_args: Optional[Tuple[str, ...]] = None,
    timeout_sec: Optional[int] = None,
    allow_transport_overrides: bool = True,
    allowed_operations: Optional[Tuple[str, ...]] = None,
) -> RemoteTarget:
    if not host:
        raise RemoteTaskError(
            "REMOTE_TARGET_INVALID",
            "Remote target host is empty.",
            recoverable=True,
            recommended_action="Use ssh://host/absolute/path or host:/absolute/path.",
        )
    if not path.startswith("/"):
        raise RemoteTaskError(
            "REMOTE_TARGET_INVALID",
            "Remote workspace path must be absolute.",
            recoverable=True,
            recommended_action="Use an absolute path on the remote host.",
        )

    normalized_path = "/" if path == "/" else path.rstrip("/")
    uri = "ssh://{}{}".format(host, normalized_path)
    workspace_id = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
    return RemoteTarget(
        host=host,
        path=normalized_path,
        uri=uri,
        workspace_id=workspace_id,
        alias=alias,
        display=display,
        redact=redact,
        python=python,
        runtime_path=runtime_path,
        ssh_args=ssh_args,
        timeout_sec=timeout_sec,
        allow_transport_overrides=allow_transport_overrides,
        allowed_operations=allowed_operations,
    )
