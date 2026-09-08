"""Collision-proof runtime namespace for governed session artifacts."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, Optional

from apatch.artifact import artifact_from_dict, parse_artifact_token

_SPEC_REQUIREMENT_RE = re.compile(
    r"^spec:(?P<spec>[^#@]+)#(?P<requirement>R\d+)(?:@(?P<hash>.+))?$"
)


def _slug(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-")
    return value or fallback


def runtime_namespace(
    target_dir: str,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.lane import resolve_lane
    from apatch.session_state import load_session_state

    root = os.path.abspath(target_dir)
    state = load_session_state(root)
    sid = str(session_id or state.get("session_id") or "scratch")
    lane = resolve_lane(root).lane_id
    workspace_id = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
    spec = None
    requirement = None
    content_hash = None
    artifact = None
    for candidate in state.get("artifacts") or []:
        if isinstance(candidate, dict):
            parsed = artifact_from_dict(candidate)
        else:
            try:
                parsed = parse_artifact_token(str(candidate))
            except ValueError:
                parsed = None
        if parsed is None:
            continue
        token = parsed.token()
        match = _SPEC_REQUIREMENT_RE.match(token)
        if match:
            artifact = token
            spec = match.group("spec")
            requirement = match.group("requirement")
            content_hash = match.group("hash")
            break
    hash_slug = _slug(content_hash or "unhashed", "unhashed")[:48]
    namespace = {
        "workspace_id": workspace_id,
        "lane": _slug(lane, "default"),
        "spec": _slug(spec or "unbound", "unbound"),
        "spec_hash": hash_slug,
        "requirement": _slug(requirement or "unbound", "unbound"),
        "session_id": _slug(sid, "scratch"),
        "artifact": artifact,
        "bound_requirement": bool(spec and requirement),
    }
    if namespace["bound_requirement"]:
        namespace["relative_dir"] = os.path.join(
            ".apatch",
            "tmp",
            namespace["lane"],
            namespace["spec_hash"],
            namespace["requirement"],
            namespace["session_id"],
        ).replace("\\", "/")
    else:
        namespace["relative_dir"] = os.path.join(
            ".apatch", "tmp", namespace["session_id"]
        ).replace("\\", "/")
    return namespace
