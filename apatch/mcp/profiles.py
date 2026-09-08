"""MCP tool surface tiers (RFP-019 L1-7, RFP-041 GO-P1-1).

Default is ``compact``: intent-level governed operations. Set ``core``, ``spec``,
or ``full`` only when the task needs those specialist packs.

``compact`` also carries read-only orientation — ``apatch_impact`` before a target
is chosen and ``apatch_build_diagnose`` after a failed verify — so the default
surface bounds work without hiding the short correct path. It never carries a tool
that mutates source outside a governed session.
"""

from __future__ import annotations

import os
from typing import Any, Dict, FrozenSet, Optional, Set

PROFILE_COMPACT: FrozenSet[str] = frozenset({
    "apatch_doctor",
    "apatch_workspace_list",
    "apatch_workspace_inspect",
    "apatch_session_start",
    "apatch_session_end",
    "apatch_session_state",
    "apatch_recover",
    "apatch_spec_lint",
    "apatch_spec_status",
    "apatch_execute_next",
    "apatch_spec_run",
    "apatch_verify_run",
    "apatch_attest",
    "apatch_rollback",
    "apatch_gc",
    "apatch_impact",
    "apatch_build_diagnose",
})

PROFILE_CORE: FrozenSet[str] = PROFILE_COMPACT | frozenset({
    "apatch_extension_list",
    "apatch_extension_inspect",
    "apatch_extension_validate",
    "apatch_extension_run",
    "apatch_generate_batch",
    "apatch_apply_session",
    "apatch_simulate",
    "apatch_plan",
    "apatch_sandbox_status",
    "apatch_verify_notarization",
    "apatch_mcp_hygiene",
})

PROFILE_SPEC_EXTRA: FrozenSet[str] = frozenset({
    "apatch_spec_lint",
    "apatch_spec_status",
    "apatch_spec_next",
    "apatch_spec_coverage",
    "apatch_execute_next",
    "apatch_spec_run",
    "apatch_spec_run_manifest_lint",
    "apatch_spec_interference",
    "apatch_spec_schedule",
    "apatch_spec_cross_verify",
    "apatch_spec_run_multi",
    "apatch_build_diagnose",
    "apatch_slug_intake",
    "apatch_slug_close",
    "apatch_slug_cockpit",
})


def mcp_profile_name() -> str:
    raw = os.environ.get("APATCH_MCP_PROFILE", "").strip().lower()
    if raw in ("compact", "core", "spec", "full"):
        return raw
    return "compact"


def allowed_tools(profile: Optional[str] = None) -> Optional[FrozenSet[str]]:
    name = (profile or mcp_profile_name()).strip().lower()
    if name == "full":
        return None
    if name == "compact":
        return PROFILE_COMPACT
    if name == "core":
        return PROFILE_CORE
    if name == "spec":
        return PROFILE_CORE | PROFILE_SPEC_EXTRA
    return None


def apply_tool_profile(mcp: Any, *, profile: Optional[str] = None) -> Dict[str, Any]:
    """Remove tools not in the active profile from FastMCP tool manager."""
    name = (profile or mcp_profile_name()).strip().lower()
    allowed = allowed_tools(name)
    if allowed is None:
        tm = getattr(mcp, "_tool_manager", None)
        count = len(getattr(tm, "_tools", {}) or {})
        return {"profile": name, "tool_count": count, "removed": []}
    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None) if tm else None
    if not tools:
        return {"profile": name, "tool_count": 0, "removed": []}
    removed: list[str] = []
    for tool_name in list(tools.keys()):
        if tool_name not in allowed:
            del tools[tool_name]
            removed.append(tool_name)
    return {"profile": name, "tool_count": len(tools), "removed": sorted(removed)}
