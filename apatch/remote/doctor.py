"""Remote doctor / bootstrap handshake over the worker transport."""

from __future__ import annotations

from typing import Any, Dict

from apatch.remote.target import RemoteTarget
from apatch.remote.transport import RemoteTransport


def remote_doctor(target: RemoteTarget, *, transport: RemoteTransport) -> Dict[str, Any]:
    """Probe a remote workspace through the worker, never via ad-hoc shell.

    Runs ``apatch_doctor`` on the remote host and normalizes the result so that
    SSH unreachability, a missing remote apatch, version skew, or a non-git root
    surface as typed remediation. It never triggers package installation —
    bootstrap stays an explicit human/broker action.
    """

    response = transport.call(target, "apatch_doctor", {})
    remote_meta = {
        "host": target.host,
        "root": target.path,
        "workspace_id": target.workspace_id,
    }

    if not isinstance(response, dict):
        return {
            "ok": False,
            "error_type": "REMOTE_PROTOCOL_ERROR",
            "message": "Remote doctor returned a non-object response.",
            "recoverable": True,
            "recommended_action": "Ensure the remote apatch worker returns exactly one JSON object.",
            "remote": remote_meta,
        }

    if response.get("ok"):
        return {
            "ok": True,
            "remote": remote_meta,
            "remote_version": response.get("version"),
            "remote_fingerprint": response.get("fingerprint"),
            "doctor": response,
        }

    error_type = response.get("error_type") or "REMOTE_UNREACHABLE"
    return {
        "ok": False,
        "error_type": error_type,
        "message": response.get("message") or "Remote doctor handshake failed.",
        "recoverable": bool(response.get("recoverable", True)),
        "recommended_action": response.get("recommended_action")
        or (
            "Inspect remote reachability, Python, git root, and apatch install; "
            "do not auto-install remote dependencies."
        ),
        "remote": remote_meta,
    }


def remote_session_state(target: RemoteTarget, *, transport: RemoteTransport) -> Dict[str, Any]:
    """Re-fetch authoritative session state from the remote workspace.

    The local controller never trusts a stale local ``.apatch`` ghost for a
    remote repo: every state read is routed to the remote worker so lifecycle
    decisions use the remote's own ``session_state.json``.
    """

    response = transport.call(target, "apatch_session_state", {})
    remote_meta = {
        "host": target.host,
        "root": target.path,
        "workspace_id": target.workspace_id,
    }
    if not isinstance(response, dict):
        return {
            "ok": False,
            "error_type": "REMOTE_PROTOCOL_ERROR",
            "message": "Remote session_state returned a non-object response.",
            "recoverable": True,
            "remote": remote_meta,
        }
    out = dict(response)
    out.setdefault("ok", True)
    out["remote"] = remote_meta
    out["authoritative"] = "remote"
    return out