"""Automatic execution lane — isolates lease/session per spec/chat (not per MCP process)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LaneContext:
    lane_id: str
    source: str
    state_rel: str

    def to_dict(self) -> Dict[str, str]:
        return {"lane_id": self.lane_id, "source": self.source, "state_rel": self.state_rel}


def _sanitize_lane_id(raw: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.strip()).strip("-")
    return slug or "default"


def _read_lane_json(root: str) -> Optional[str]:
    path = os.path.join(root, ".apatch", "lane.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    lane_id = str(data.get("lane_id") or "").strip()
    return _sanitize_lane_id(lane_id) if lane_id else None


def _git_branch(root: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    branch = (proc.stdout or "").strip()
    if branch and branch != "HEAD":
        return branch
    return None


def _is_git_worktree(root: str) -> bool:
    git_path = os.path.join(root, ".git")
    if os.path.isfile(git_path):
        return True
    try:
        proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "true"


def _state_rel(lane_id: str) -> str:
    if lane_id == "default":
        return ".apatch"
    return os.path.join(".apatch", "lanes", lane_id)


def resolve_lane(root: str) -> LaneContext:
    """Lane for lease/session — an explicit session capability has precedence."""
    root = os.path.abspath(root)
    from apatch.lane_context import (
        current_tool_governed_session_id,
        current_tool_lane_id,
        current_tool_spec_id,
        resolve_active_lane,
    )

    if current_tool_governed_session_id(root):
        session_lane, error = resolve_active_lane(root)
        if error:
            return LaneContext(
                lane_id="ambiguous",
                source="ambiguous",
                state_rel=_state_rel("ambiguous"),
            )
        if session_lane:
            lane_id = _sanitize_lane_id(session_lane)
            return LaneContext(
                lane_id=lane_id,
                source="session",
                state_rel=_state_rel(lane_id),
            )

    explicit_lane = current_tool_lane_id(root)
    if explicit_lane:
        lane_id = _sanitize_lane_id(explicit_lane)
        return LaneContext(
            lane_id=lane_id,
            source="explicit",
            state_rel=_state_rel(lane_id),
        )

    env = os.environ.get("APATCH_LANE", "auto").strip().lower()
    if env and env not in ("", "auto"):
        lane_id = _sanitize_lane_id(env)
        return LaneContext(lane_id=lane_id, source="env", state_rel=_state_rel(lane_id))

    spec_lane, ambig = resolve_active_lane(root)
    if ambig:
        return LaneContext(lane_id="ambiguous", source="ambiguous", state_rel=_state_rel("ambiguous"))
    if spec_lane:
        lane_id = _sanitize_lane_id(spec_lane)
        return LaneContext(lane_id=lane_id, source="spec", state_rel=_state_rel(lane_id))

    tool_spec = current_tool_spec_id()
    if tool_spec:
        lane_id = _sanitize_lane_id(tool_spec)
        return LaneContext(lane_id=lane_id, source="spec", state_rel=_state_rel(lane_id))

    from_manifest = _read_lane_json(root)
    if from_manifest:
        return LaneContext(
            lane_id=from_manifest,
            source="manifest",
            state_rel=_state_rel(from_manifest),
        )

    branch = _git_branch(root)
    if branch and branch not in ("master", "main") and _is_git_worktree(root):
        lane_id = _sanitize_lane_id(branch.replace("/", "-"))
        return LaneContext(lane_id=lane_id, source="worktree", state_rel=_state_rel(lane_id))

    return LaneContext(lane_id="default", source="shared", state_rel=_state_rel("default"))


def lane_state_path_for(root: str, lane_id: str, filename: str) -> str:
    root = os.path.abspath(root)
    return os.path.join(root, _state_rel(_sanitize_lane_id(lane_id)), filename)


def lane_state_path(root: str, filename: str) -> str:
    root = os.path.abspath(root)
    ctx = resolve_lane(root)
    return os.path.join(root, ctx.state_rel, filename)


def lane_info(root: str) -> Dict[str, Any]:
    from apatch.lane_context import active_lane_ids

    ctx = resolve_lane(root)
    active = active_lane_ids(root)
    return {
        **ctx.to_dict(),
        "auto": os.environ.get("APATCH_LANE", "auto").strip().lower() in ("", "auto"),
        "active_specs": active,
        "hint": (
            "One catalog, many chats: each spec gets its own lane (SPEC-*). "
            "Pass spec= when several specs are active."
            if ctx.source == "spec"
            else "Lane from worktree/manifest/mcp process."
        ),
    }
