"""Per-request lane binding — multiple chats/specs on one catalog without window tracking."""

from __future__ import annotations

import json
import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_lane_tool_ctx: ContextVar[Dict[str, str]] = ContextVar("apatch_lane_tool_ctx", default={})

REGISTRY_REL = os.path.join(".apatch", "lane_registry.json")


def _sanitize(raw: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.strip()).strip("-")
    return slug or "default"


def parse_spec_id(*, spec: str = "", requirement: str = "") -> Optional[str]:
    spec = (spec or "").strip()
    requirement = (requirement or "").strip()
    if spec:
        return _sanitize(spec.upper())
    if requirement:
        head = requirement.split("#", 1)[0].strip().upper()
        if head.startswith("SPEC-"):
            return _sanitize(head)
        if re.match(r"^R\d+$", head, re.I) and "#" not in requirement:
            return None
    return None


def _normalized_workspace(raw: str) -> str:
    return os.path.abspath(os.path.expanduser(raw))


def _lane_from_artifacts(artifacts: Any) -> Optional[str]:
    """Compatibility route for MCP servers registered before lane existed."""
    if not isinstance(artifacts, (list, tuple)):
        return None
    for artifact in artifacts:
        if isinstance(artifact, str) and artifact.lower().startswith("lane:"):
            return _sanitize(artifact.split(":", 1)[1])
        if isinstance(artifact, dict) and str(artifact.get("kind") or "").lower() == "lane":
            lane_id = str(artifact.get("id") or "").strip()
            if lane_id:
                return _sanitize(lane_id)
    return None


def bind_lane_from_kwargs(kwargs: Dict[str, Any]) -> Optional[str]:
    """Set lane context from explicit session capability, lane, or SPEC arguments."""
    session_id = str(kwargs.get("governed_session_id") or "").strip()
    explicit_lane = str(kwargs.get("lane") or "").strip()
    lane_id = _sanitize(explicit_lane) if explicit_lane else _lane_from_artifacts(
        kwargs.get("artifacts")
    )
    spec_id = parse_spec_id(
        spec=str(kwargs.get("spec") or ""),
        requirement=str(kwargs.get("requirement") or ""),
    )
    target_dir = str(kwargs.get("target_dir") or "").strip()
    context: Dict[str, str] = {}
    if session_id:
        context["governed_session_id"] = session_id
    if lane_id:
        context["lane_id"] = lane_id
    if spec_id:
        context["spec_id"] = spec_id
    if target_dir:
        context["workspace"] = _normalized_workspace(target_dir)
    _lane_tool_ctx.set(context)
    return session_id or lane_id or spec_id


def bind_governed_session_id(
    session_id: str,
    *,
    root: Optional[str] = None,
) -> None:
    context = dict(_lane_tool_ctx.get())
    if session_id:
        context["governed_session_id"] = str(session_id)
    else:
        context.pop("governed_session_id", None)
    if root:
        context["workspace"] = _normalized_workspace(root)
    _lane_tool_ctx.set(context)


def current_tool_spec_id() -> Optional[str]:
    return _lane_tool_ctx.get().get("spec_id")


def current_tool_lane_id(root: Optional[str] = None) -> Optional[str]:
    context = _lane_tool_ctx.get()
    workspace = context.get("workspace")
    if root and workspace and workspace != _normalized_workspace(root):
        return None
    return context.get("lane_id")


def current_tool_governed_session_id(root: Optional[str] = None) -> Optional[str]:
    context = _lane_tool_ctx.get()
    workspace = context.get("workspace")
    if root and workspace and workspace != _normalized_workspace(root):
        return None
    return context.get("governed_session_id")


def registry_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), REGISTRY_REL)


def _load_registry_unlocked(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {"lanes": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"lanes": {}}
    if not isinstance(data, dict):
        return {"lanes": {}}
    if not isinstance(data.get("lanes"), dict):
        data["lanes"] = {}
    return data


def load_registry(root: str) -> Dict[str, Any]:
    return _load_registry_unlocked(registry_path(root))


def save_registry(root: str, data: Dict[str, Any]) -> None:
    from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock

    path = registry_path(root)
    with exclusive_file_lock(path):
        atomic_write_json(path, data)


def register_active_lane(root: str, lane_id: str, *, session_id: str) -> None:
    from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock

    path = registry_path(root)
    with exclusive_file_lock(path):
        data = _load_registry_unlocked(path)
        lanes = data.setdefault("lanes", {})
        lanes[_sanitize(lane_id)] = {
            "session_id": session_id,
            "active": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json(path, data)


def unregister_lane(
    root: str,
    lane_id: str,
    *,
    expected_session_id: Optional[str] = None,
) -> bool:
    from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock

    path = registry_path(root)
    with exclusive_file_lock(path):
        data = _load_registry_unlocked(path)
        lanes = data.get("lanes") or {}
        key = _sanitize(lane_id)
        row = lanes.get(key)
        if not isinstance(row, dict):
            return False
        if expected_session_id and str(row.get("session_id") or "") != expected_session_id:
            return False
        row["active"] = False
        row["ended_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(path, data)
    return True


def active_lane_ids(root: str) -> list[str]:
    data = load_registry(root)
    out: list[str] = []
    for lane_id, row in (data.get("lanes") or {}).items():
        if isinstance(row, dict) and row.get("active"):
            out.append(str(lane_id))
    return sorted(out)


def resolve_lane_for_session(
    root: str,
    session_id: str,
    *,
    active_only: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    matches: list[str] = []
    for lane_id, row in (load_registry(root).get("lanes") or {}).items():
        if not isinstance(row, dict):
            continue
        if str(row.get("session_id") or "") != str(session_id):
            continue
        if active_only and not row.get("active"):
            continue
        matches.append(str(lane_id))
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "Session id is registered to multiple lanes."
    return None, "Session id is not registered in this workspace."


def resolve_active_lane(root: str) -> tuple[Optional[str], Optional[str]]:
    """Return one exact lane or an actionable ambiguity error."""
    session_id = current_tool_governed_session_id(root)
    if session_id:
        return resolve_lane_for_session(root, session_id, active_only=False)
    spec_id = current_tool_spec_id()
    if spec_id:
        return spec_id, None
    active = active_lane_ids(root)
    if len(active) == 1:
        return active[0], None
    if len(active) > 1:
        return None, (
            "Ambiguous active lanes: {}. Provide governed_session_id and "
            "session_token.".format(", ".join(active))
        )
    return None, None
