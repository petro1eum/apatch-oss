"""Start apatch MCP from ``{workspace}/.apatch/mcp.json`` — IDE-agnostic entry point."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def find_workspace_root(start: Optional[Path] = None) -> Optional[Path]:
    """Resolve project root — CURSOR_PROJECT_DIR wins over stale APATCH_WORKSPACE."""
    from apatch.mcp.bound_workspace import discover_bound_workspace

    root = discover_bound_workspace(start=start)
    return Path(root) if root else None


def load_canonical_mcp_config(workspace: Path) -> Tuple[Dict[str, Any], str]:
    path = workspace / ".apatch" / "mcp.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"invalid MCP config: {path}")
    servers = data.get("mcpServers") or {}
    apatch_cfg = servers.get("apatch")
    if not isinstance(apatch_cfg, dict):
        raise ValueError(f"mcpServers.apatch missing in {path}")
    return apatch_cfg, str(path)


def apply_server_env(env: Dict[str, Any]) -> None:
    for key, val in (env or {}).items():
        if val is None:
            continue
        os.environ[str(key)] = str(val)


def canonical_exec_argv(cfg: Dict[str, Any]) -> Tuple[str, list[str]]:
    """Build an isolated argv for the canonical server declared by the workspace."""
    command = str(cfg.get("command") or "").strip()
    args = [str(arg) for arg in (cfg.get("args") or [])]
    if not command:
        raise ValueError("canonical MCP command is empty")
    resolved = command if os.path.isabs(command) else shutil.which(command)
    if not resolved:
        raise ValueError(f"canonical MCP command not found: {command}")
    if "apatch.mcp.workspace_launcher" in args:
        raise ValueError("canonical MCP config recursively invokes workspace_launcher")
    is_python_module = len(args) >= 2 and args[0] == "-m"
    if (os.path.basename(resolved).lower().startswith("python") or is_python_module) and "-I" not in args:
        args.insert(0, "-I")
    return resolved, [resolved, *args]


def main() -> None:
    from apatch.warn_filters import configure_apatch_warnings

    configure_apatch_warnings()

    ws = find_workspace_root()
    if ws is None:
        sys.stderr.write(
            "apatch workspace_launcher: no .apatch/mcp.json found "
            "(checked env APATCH_WORKSPACE/CURSOR_PROJECT_DIR and cwd walk).\n"
            "Run: apatch mcp sync --target-dir <project>\n"
        )
        raise SystemExit(1)

    try:
        cfg, cfg_path = load_canonical_mcp_config(ws)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"apatch workspace_launcher: {e}\n")
        raise SystemExit(1) from e

    os.chdir(ws)
    child_env = dict(os.environ)
    for key, val in (cfg.get("env") or {}).items():
        if val is not None:
            child_env[str(key)] = str(val)
    # workspace_launcher is the secure roaming entry point: off-bound targets
    # must use a human-authorized @alias instead of arbitrary paths.
    child_env["APATCH_MCP_TARGET_POLICY"] = "alias_only"
    child_env["APATCH_MCP_BOUND"] = str(ws)
    child_env["APATCH_CANONICAL_RUNTIME"] = "1"
    child_env["APATCH_MCP_BOOTSTRAPPED"] = "1"
    # A source checkout in PYTHONPATH must not shadow the immutable runtime
    # selected by .apatch/mcp.json.
    child_env.pop("PYTHONPATH", None)
    child_env.pop("PYTHONHOME", None)
    try:
        command, argv = canonical_exec_argv(cfg)
    except ValueError as exc:
        sys.stderr.write(f"apatch workspace_launcher ({cfg_path}): {exc}\n")
        raise SystemExit(1) from exc
    os.execve(command, argv, child_env)


if __name__ == "__main__":
    main()
