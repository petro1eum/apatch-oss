"""Remote verify post-processing: argv routing, bounded logs, redaction, resume."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Union

from apatch.remote.target import RemoteTarget
from apatch.remote.transport import RemoteTransport


DEFAULT_MAX_LOG_BYTES = 64 * 1024
DEFAULT_MAX_LOG_LINES = 2000
REDACTION_MARKER = "***REDACTED***"


def bound_log(
    text: Optional[str],
    *,
    max_bytes: int = DEFAULT_MAX_LOG_BYTES,
    max_lines: int = DEFAULT_MAX_LOG_LINES,
) -> Dict[str, Any]:
    """Bound a remote log stream with explicit (never silent) truncation."""

    raw = text or ""
    original_bytes = len(raw.encode("utf-8"))
    truncated = False

    lines = raw.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        truncated = True
    bounded = "\n".join(lines)

    encoded = bounded.encode("utf-8")
    if len(encoded) > max_bytes:
        bounded = encoded[-max_bytes:].decode("utf-8", errors="replace")
        truncated = True

    return {
        "text": bounded,
        "truncated": truncated,
        "original_bytes": original_bytes,
        "max_bytes": max_bytes,
        "max_lines": max_lines,
    }


def redact_env(text: Optional[str], secrets: Optional[Iterable[str]]) -> str:
    """Replace secret environment values with a redaction marker."""

    out = text or ""
    for secret in secrets or []:
        if secret:
            out = out.replace(str(secret), REDACTION_MARKER)
    return out


def remote_verify(
    target: RemoteTarget,
    *,
    verify: Union[str, Mapping[str, Any]],
    transport: RemoteTransport,
    secrets: Optional[Iterable[str]] = None,
    max_bytes: int = DEFAULT_MAX_LOG_BYTES,
    max_lines: int = DEFAULT_MAX_LOG_LINES,
) -> Dict[str, Any]:
    """Run verify on the remote worker and return bounded, redacted output.

    The verify command (argv or shell string) travels as structured data to the
    remote worker; stdout/stderr returned by the remote are redacted of known
    secrets and bounded with explicit truncation before reaching the caller.
    """

    payload_verify = verify if isinstance(verify, Mapping) else {"command": verify}
    response = transport.call(target, "apatch_verify_run", {"verify": dict(payload_verify)})
    if not isinstance(response, dict):
        return {
            "ok": False,
            "error_type": "REMOTE_PROTOCOL_ERROR",
            "message": "Remote verify returned a non-object response.",
            "recoverable": True,
        }

    out = dict(response)
    for channel in ("stdout", "stderr"):
        value = out.get(channel)
        if isinstance(value, str):
            redacted = redact_env(value, secrets)
            out[channel] = bound_log(redacted, max_bytes=max_bytes, max_lines=max_lines)
    return out


def remote_resume_after_disconnect(
    target: RemoteTarget,
    *,
    transport: RemoteTransport,
) -> Dict[str, Any]:
    """After a transport failure, re-read authoritative remote state for a next action.

    Local ghost state never forces a rollback: the resume decision is derived
    from the remote workspace's own session lifecycle.
    """

    from apatch.remote.doctor import remote_session_state

    state = remote_session_state(target, transport=transport)
    lifecycle = state.get("lifecycle") if isinstance(state, dict) else None
    next_action = {
        "verifying": "poll verify status or resume_session on the remote",
        "failed": "resume_session then re-verify on the remote",
        "applying": "resume_session or rollback the remote checkpoint",
        "idle": "session_end or start a new remote session",
    }.get(lifecycle, "re-run remote doctor then remote session_state")

    return {
        "ok": bool(state.get("ok", True)) if isinstance(state, dict) else False,
        "authoritative": "remote",
        "lifecycle": lifecycle,
        "next_action": next_action,
        "remote": state.get("remote") if isinstance(state, dict) else None,
        "local_state_forced_rollback": False,
    }