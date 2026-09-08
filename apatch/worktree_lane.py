"""Auto worktree routing for spec execution — user opens any clone; apatch picks the lane path."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class WorktreeManifest:
    main_clone: str
    lanes_parent: str
    lanes: List[Dict[str, Any]]


def _load_yaml(path: str) -> Optional[Dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def load_worktree_manifest(root: str) -> Optional[WorktreeManifest]:
    root = os.path.abspath(root)
    for name in ("worktrees.yaml", "worktrees.json"):
        path = os.path.join(root, "manifests", name)
        if not os.path.isfile(path):
            continue
        if name.endswith(".json"):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
        else:
            data = _load_yaml(path)
        if not data:
            continue
        main_clone = str(data.get("main_clone") or root).strip()
        lanes_parent = str(data.get("lanes_parent") or "").strip()
        lanes = data.get("lanes") or []
        if not lanes_parent or not isinstance(lanes, list):
            continue
        return WorktreeManifest(
            main_clone=os.path.abspath(os.path.expanduser(main_clone)),
            lanes_parent=os.path.abspath(os.path.expanduser(lanes_parent)),
            lanes=[lane for lane in lanes if isinstance(lane, dict) and lane.get("id")],
        )
    return None


def _lane_matches_spec(lane: Dict[str, Any], spec: str) -> bool:
    spec_u = spec.strip().upper()
    primary = str(lane.get("spec") or "").strip().upper()
    if primary and primary == spec_u:
        return True
    for extra in lane.get("specs_extra") or []:
        if str(extra).strip().upper() == spec_u:
            return True
    return False


def find_lane_for_spec(manifest: WorktreeManifest, spec: str) -> Optional[Dict[str, Any]]:
    for lane in manifest.lanes:
        if _lane_matches_spec(lane, spec):
            return lane
    return None


def lane_worktree_path(manifest: WorktreeManifest, lane: Dict[str, Any]) -> str:
    return os.path.join(manifest.lanes_parent, str(lane["id"]))


def _git_run(args: List[str], *, cwd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


def ensure_lane_worktree(manifest: WorktreeManifest, lane: Dict[str, Any]) -> str:
    """Create git worktree + lane bootstrap if missing; return absolute worktree path."""
    path = lane_worktree_path(manifest, lane)
    if os.path.isdir(path):
        return path

    main = manifest.main_clone
    branch = str(lane.get("branch") or f"work/{lane['id']}")
    os.makedirs(manifest.lanes_parent, exist_ok=True)

    show = _git_run(["show-ref", "--verify", f"refs/heads/{branch}"], cwd=main, check=False)
    if show.returncode != 0:
        _git_run(["branch", branch], cwd=main)

    _git_run(["worktree", "add", path, branch], cwd=main)

    apatch_dir = os.path.join(path, ".apatch")
    os.makedirs(apatch_dir, exist_ok=True)
    meta = {
        "lane_id": lane["id"],
        "branch": branch,
        "spec": lane.get("spec"),
        "specs_extra": lane.get("specs_extra") or [],
        "agent_id": lane.get("agent_id"),
        "description": lane.get("description", ""),
        "auto_created": True,
    }
    with open(os.path.join(apatch_dir, "lane.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    for name in ("enforcement.json", "sandbox.json", "agent-identity.json"):
        src = os.path.join(main, ".apatch", name)
        dst = os.path.join(apatch_dir, name)
        if os.path.isfile(src) and not os.path.isfile(dst):
            with open(src, encoding="utf-8") as sf:
                content = sf.read()
            with open(dst, "w", encoding="utf-8") as df:
                df.write(content)

    sync = os.path.join(path, "scripts", "apatch-sandbox-sync.py")
    if os.path.isfile(sync):
        subprocess.run(
            [os.environ.get("APATCH_PYTHON", "python3"), sync],
            cwd=path,
            check=False,
        )

    return path


def _default_lanes_parent(root: str) -> str:
    return os.path.join(os.path.dirname(root), os.path.basename(root) + "-lanes")


def create_worktree_lane(
    root: str,
    lane_id: str,
    *,
    branch: Optional[str] = None,
    lanes_parent: Optional[str] = None,
) -> Dict[str, Any]:
    """Create (or reuse) an isolated git-worktree lane for parallel agent work.

    Each lane is a separate git worktree — its own working tree, branch and ``.apatch``
    runtime state — so a second agent never shares the first agent's working tree or
    write-lease. Reuses the spec-execution primitive (``ensure_lane_worktree``); no
    ``manifests/worktrees.yaml`` is required for the general/parallel-agents case.
    """
    from apatch.lane import _sanitize_lane_id

    root = os.path.abspath(root)
    lane_id = _sanitize_lane_id(lane_id)
    parent = os.path.abspath(os.path.expanduser(lanes_parent or _default_lanes_parent(root)))
    manifest = WorktreeManifest(main_clone=root, lanes_parent=parent, lanes=[])
    lane = {"id": lane_id, "branch": branch or f"work/{lane_id}"}
    path = ensure_lane_worktree(manifest, lane)
    return {"lane_id": lane_id, "branch": lane["branch"], "path": path}


def remove_worktree_lane(
    root: str,
    lane_id: str,
    *,
    lanes_parent: Optional[str] = None,
    force: bool = False,
) -> bool:
    """Remove a worktree lane (``git worktree remove``); the branch is kept."""
    from apatch.lane import _sanitize_lane_id

    root = os.path.abspath(root)
    lane_id = _sanitize_lane_id(lane_id)
    parent = os.path.abspath(os.path.expanduser(lanes_parent or _default_lanes_parent(root)))
    args = ["worktree", "remove", os.path.join(parent, lane_id)]
    if force:
        args.append("--force")
    return _git_run(args, cwd=root, check=False).returncode == 0


def prepare_execution_root(
    target_dir: str,
    *,
    spec: Optional[str] = None,
) -> Dict[str, Any]:
    """Pick the real workspace root for mutations (auto worktree when manifest + spec)."""
    from apatch.lane import lane_info

    root = os.path.abspath(target_dir)
    manifest = load_worktree_manifest(root)

    if not spec or not manifest:
        return {
            "path": root,
            "requested": root,
            "retargeted": False,
            "lane": lane_info(root),
        }

    lane = find_lane_for_spec(manifest, spec)
    if not lane:
        return {
            "path": root,
            "requested": root,
            "retargeted": False,
            "lane": lane_info(root),
            "worktree_manifest": True,
            "worktree_note": f"no lane for {spec} in manifests/worktrees.yaml",
        }

    wt_path = ensure_lane_worktree(manifest, lane)
    retargeted = os.path.abspath(wt_path) != os.path.abspath(root)
    return {
        "path": os.path.abspath(wt_path),
        "requested": root,
        "retargeted": retargeted,
        "spec": spec,
        "lane_id": lane.get("id"),
        "branch": lane.get("branch"),
        "lane": lane_info(wt_path),
        "agent_next": (
            f"Executing on auto worktree {wt_path} (branch {lane.get('branch')}). "
            "No separate Cursor window required."
            if retargeted
            else None
        ),
    }