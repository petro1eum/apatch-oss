"""MCP stdio hygiene — ghost processes and stale lease report (RFP-019 L1-8)."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, List, Optional

_MCP_MARKERS = (
    "apatch.mcp.launcher",
    "apatch-mcp",
    "apatch.mcp.server",
)


def _list_apatch_processes() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return rows
    if proc.returncode != 0:
        return rows
    me = os.getpid()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s+(.*)$", line)
        if not m:
            continue
        pid = int(m.group(1))
        cmd = m.group(2)
        if not any(marker in cmd for marker in _MCP_MARKERS):
            continue
        rows.append(
            {
                "pid": pid,
                "command": cmd,
                "is_current": pid == me,
                "ghost": pid != me,
            }
        )
    return rows


def run_mcp_hygiene(
    target_dir: str = ".",
    *,
    sweep_leases: bool = False,
) -> Dict[str, Any]:
    root = os.path.abspath(os.path.expanduser(target_dir))
    processes = _list_apatch_processes()
    ghosts = [p for p in processes if p.get("ghost")]
    lease_info: Optional[Dict[str, Any]] = None
    if os.path.isdir(os.path.join(root, ".apatch")):
        from apatch.mcp.lifecycle import sweep_stale_lease
        from apatch.sandbox import load_active_lease

        if sweep_leases:
            lease_info = sweep_stale_lease(root)
        else:
            cap = load_active_lease(root)
            if cap:
                from apatch.sandbox import _lease_valid

                lease_info = {
                    "root": root,
                    "lease_id": cap.get("lease_id"),
                    "pid": cap.get("pid"),
                    "valid": _lease_valid(cap),
                }
            else:
                lease_info = {"root": root, "lease": None}
    legacy_ghosts = [
        p
        for p in ghosts
        if "apatch-mcp" in (p.get("command") or "")
        and "launcher" not in (p.get("command") or "")
    ]
    contention: List[str] = []
    legacy_apply = os.path.join(root, ".apatch", "apply_session.json")
    lanes_dir = os.path.join(root, ".apatch", "lanes")
    if os.path.isfile(legacy_apply) and os.path.isdir(lanes_dir):
        contention.append(
            "stale root .apatch/apply_session.json — blocks parallel spec_run; "
            "remove file or apatch_apply_session(abort=true) after MCP restart"
        )
    stale_mcp_lanes: List[str] = []
    if os.path.isdir(lanes_dir):
        for name in os.listdir(lanes_dir):
            if name.startswith("mcp-"):
                stale_mcp_lanes.append(name)
    if stale_mcp_lanes:
        contention.append(
            f"obsolete mcp-pid lanes: {stale_mcp_lanes} — safe to delete under .apatch/lanes/"
        )
    healthy = not legacy_ghosts and not contention
    return {
        "ok": True,
        "healthy": healthy,
        "workspace": root,
        "process_count": len(processes),
        "ghost_count": len(ghosts),
        "legacy_ghost_count": len(legacy_ghosts),
        "ghost_processes": ghosts,
        "legacy_ghost_processes": legacy_ghosts,
        "contention": contention,
        "stale_mcp_lanes": stale_mcp_lanes,
        "current_pid": os.getpid(),
        "lease": lease_info,
        "sweep_leases": sweep_leases,
        "hint": (
            "Kill legacy apatch-mcp PIDs and restart MCP; clear contention paths above."
            if not healthy
            else "MCP process count looks healthy."
        ),
    }