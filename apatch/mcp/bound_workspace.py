"""MCP-bound workspace — default ``target_dir`` without agent/user passthrough."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# IDE hosts set these per window; must win over stale APATCH_WORKSPACE in ~/.cursor/mcp.json.
_WORKSPACE_ENV_KEYS = (
    "CURSOR_PROJECT_DIR",
    "CURSOR_WORKSPACE",
    "VSCODE_CWD",
    "PROJECT_DIR",
    "WORKSPACE_FOLDER",
    "APATCH_WORKSPACE",
    "APATCH_TARGET_DIR",
)

_BOUND: Optional[str] = None


def _canonical_at(root: Path) -> bool:
    return (root / ".apatch" / "mcp.json").is_file()


def discover_bound_workspace(*, start: Optional[Path] = None) -> Optional[str]:
    """Resolve workspace root for MCP tool defaults."""
    for key in _WORKSPACE_ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        candidate = Path(raw).expanduser().resolve()
        if _canonical_at(candidate):
            return str(candidate)
    cur = (start or Path.cwd()).resolve()
    for _ in range(40):
        if _canonical_at(cur):
            return str(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def bind_mcp_workspace(root: Optional[str] = None) -> Optional[str]:
    """Call once at MCP startup; pins default ``target_dir='.'`` for all tools."""
    global _BOUND
    resolved = os.path.abspath(root) if root else discover_bound_workspace()
    if resolved:
        _BOUND = resolved
        os.environ.setdefault("APATCH_MCP_BOUND", resolved)
    return _BOUND


def bound_mcp_workspace() -> Optional[str]:
    if _BOUND:
        return _BOUND
    env = (os.environ.get("APATCH_MCP_BOUND") or "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return discover_bound_workspace()


def resolve_target_dir(target_dir: str = ".") -> str:
    """Resolve the bound root or a human-authorized local roaming alias."""
    raw = (target_dir or ".").strip()
    if raw in (".", ""):
        bound = bound_mcp_workspace()
        if bound:
            return bound
        return os.path.abspath(".")

    if raw.startswith("@"):
        from apatch.mcp.workspace_registry import resolve_local_workspace

        return resolve_local_workspace(raw[1:])

    resolved = os.path.abspath(os.path.expanduser(raw))
    if os.environ.get("APATCH_MCP_TARGET_POLICY", "").strip().lower() == "alias_only":
        bound = bound_mcp_workspace()
        if bound and os.path.realpath(resolved) != os.path.realpath(bound):
            from apatch.mcp.workspace_registry import WorkspaceRegistryError

            raise WorkspaceRegistryError(
                "Cross-workspace target_dir must use a registered @alias, not {!r}.".format(
                    raw
                ),
                error_type="WORKSPACE_ALIAS_REQUIRED",
                recommended_action=(
                    "Ask a human to run 'apatch workspace add <alias> <root>', "
                    "then retry with target_dir='@<alias>'."
                ),
            )
    return resolved


def resolve_target_dir_kwargs(kwargs: dict) -> dict:
    if "target_dir" not in kwargs:
        kwargs["target_dir"] = resolve_target_dir(".")
        return kwargs
    kwargs["target_dir"] = resolve_target_dir(str(kwargs.get("target_dir") or "."))
    return kwargs