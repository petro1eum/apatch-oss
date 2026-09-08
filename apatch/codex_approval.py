"""Codex client approval helpers for apatch MCP."""

from __future__ import annotations

import json
import os
import re
import sys
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback for downstreams
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

from typing import Any, Dict, Iterable, List, Optional, Tuple


REMOTE_CODEX_APPROVAL_TOOLS = [
    "apatch_doctor",
    "apatch_remote_task_run",
    "apatch_remote_source_handoff",
    "apatch_remote_service_action",
    "apatch_session_state",
]

AUTOPILOT_CODEX_APPROVAL_TOOLS = [
    "apatch_doctor",
    "apatch_extension_list",
    "apatch_extension_inspect",
    "apatch_extension_validate",
    "apatch_remote_task_run",
    "apatch_remote_source_handoff",
    "apatch_remote_service_action",
    "apatch_session_state",
    "apatch_session_start",
    "apatch_generate_batch",
    "apatch_simulate",
    "apatch_apply_session",
    "apatch_verify_run",
    "apatch_attest",
    "apatch_session_end",
    "apatch_resume_session",
    "apatch_spec_lint",
    "apatch_spec_status",
    "apatch_spec_next",
    "apatch_execute_next",
    "apatch_spec_run",
    "apatch_slug_intake",
    "apatch_slug_close",
    "apatch_slug_cockpit",
    "apatch_slug_feedback_lint",
    "apatch_plan_batch",
    "apatch_generate",
    "apatch_apply",
    "apatch_view",
    "apatch_rfp_lint",
]


def codex_approval_tools(preset: str = "autopilot") -> List[str]:
    """Return the apatch tools covered by a Codex approval preset."""

    if preset == "remote":
        return list(REMOTE_CODEX_APPROVAL_TOOLS)
    if preset == "autopilot":
        return list(AUTOPILOT_CODEX_APPROVAL_TOOLS)
    if preset == "all":
        try:
            from apatch.mcp import server as mcp_server

            tm = getattr(mcp_server.mcp, "_tool_manager", None)
            if tm is not None and hasattr(tm, "_tools"):
                return sorted(str(name) for name in tm._tools.keys() if str(name).startswith("apatch_"))
        except Exception:
            pass
        return list(AUTOPILOT_CODEX_APPROVAL_TOOLS)
    raise ValueError("Unsupported Codex approval preset: {}".format(preset))


def write_codex_approvals(
    *,
    config_path: Optional[str] = None,
    server: str = "apatch",
    target_dir: str = ".",
    python: Optional[str] = None,
    preset: str = "autopilot",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Ensure Codex approves the selected apatch MCP tools."""

    path = os.path.abspath(os.path.expanduser(config_path or "~/.codex/config.toml"))
    workspace = os.path.abspath(os.path.expanduser(target_dir or "."))
    python_cmd = python or sys.executable or "python3"
    tools = codex_approval_tools(preset)

    before = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            before = f.read()

    text = before
    server_created = False
    if not _section_exists(text, "mcp_servers.{}".format(server)):
        text = _append_block(text, _server_block(server, workspace, python_cmd))
        server_created = True

    changed_tools: List[str] = []
    for tool in tools:
        section = "mcp_servers.{}.tools.{}".format(server, tool)
        updated, changed = _ensure_section_key(text, section, "approval_mode", _toml_string("approve"))
        text = updated
        if changed:
            changed_tools.append(tool)

    changed = text != before
    if changed and not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    return {
        "ok": True,
        "path": path,
        "server": server,
        "preset": preset,
        "dry_run": dry_run,
        "changed": changed,
        "server_created": server_created,
        "changed_tools": changed_tools,
        "tool_count": len(tools),
        "tools": tools,
        "restart_required": changed,
        "agent_next": "Restart/reload the Codex MCP server for changes to take effect." if changed else "No Codex approval changes needed.",
    }


def diagnose_codex_prompt_friction(
    *,
    config_path: Optional[str] = None,
    server: str = "apatch",
    target_dir: str = ".",
    preset: str = "autopilot",
) -> Dict[str, Any]:
    """Explain likely Codex approval prompts for an apatch workspace."""

    path = os.path.abspath(os.path.expanduser(config_path or "~/.codex/config.toml"))
    target = os.path.abspath(os.path.expanduser(target_dir or "."))
    config = _load_toml(path)
    server_cfg = ((config.get("mcp_servers") or {}).get(server) or {}) if isinstance(config, dict) else {}
    env_cfg = server_cfg.get("env") if isinstance(server_cfg, dict) else {}
    configured_workspace = None
    if isinstance(env_cfg, dict):
        configured_workspace = env_cfg.get("APATCH_WORKSPACE")
    if not configured_workspace and isinstance(server_cfg, dict):
        configured_workspace = server_cfg.get("cwd")
    configured_workspace = (
        os.path.abspath(os.path.expanduser(str(configured_workspace)))
        if configured_workspace
        else None
    )

    approvals = write_codex_approvals(
        config_path=path,
        server=server,
        target_dir=target,
        preset=preset,
        dry_run=True,
    )
    workspace_mismatch = bool(configured_workspace and not _is_relative_to(target, configured_workspace))
    autopilot = _autopilot_broker_plan(
        target_dir=target,
        configured_workspace=configured_workspace,
        workspace_mismatch=workspace_mismatch,
        mcp_approvals_missing=bool(approvals.get("changed_tools")),
    )
    findings: List[Dict[str, Any]] = []
    if workspace_mismatch:
        findings.append(
            {
                "kind": "filesystem_scope",
                "severity": "high",
                "message": "Target directory is outside the Codex MCP workspace; direct file tools will prompt.",
                "configured_workspace": configured_workspace,
                "target_dir": target,
                "recommended_action": autopilot["agent_next"],
            }
        )
    if approvals.get("changed_tools"):
        findings.append(
            {
                "kind": "mcp_tool_approval",
                "severity": "medium",
                "message": "Some apatch MCP tools are not pre-approved in Codex config.",
                "changed_tools": approvals.get("changed_tools"),
                "recommended_action": "Run apatch mcp codex-approve --preset {} and reload MCP.".format(preset),
            }
        )

    return {
        "ok": True,
        "path": path,
        "server": server,
        "target_dir": target,
        "configured_workspace": configured_workspace,
        "server_configured": bool(server_cfg),
        "filesystem_prompts_likely": workspace_mismatch,
        "mcp_tool_prompts_likely": bool(approvals.get("changed_tools")),
        "findings": findings,
        "approval_preview": approvals,
        "autopilot": autopilot,
        "agent_next": autopilot["agent_next"],
    }


def _autopilot_broker_plan(
    *,
    target_dir: str,
    configured_workspace: Optional[str],
    workspace_mismatch: bool,
    mcp_approvals_missing: bool,
) -> Dict[str, Any]:
    if workspace_mismatch:
        return {
            "mode": "brokered_edits_required",
            "can_continue_without_filesystem_prompts": not mcp_approvals_missing,
            "target_dir": target_dir,
            "configured_workspace": configured_workspace,
            "must_not_use": [
                "apply_patch/direct file edit tools for this target",
                "shell redirection, tee, sed -i, perl -pi, or ad-hoc write scripts for this target",
                "raw ssh/scp/tar pipes as transport fallbacks",
            ],
            "use_instead": [
                {
                    "tool": "apatch_generate_batch",
                    "purpose": "create governed JSONL mutations inside the target workspace",
                    "required_arg": "target_dir='{}'".format(target_dir),
                },
                {
                    "tool": "apatch_apply_session",
                    "purpose": "apply governed mutations through the apatch broker",
                    "required_arg": "target_dir='{}'".format(target_dir),
                },
                {
                    "tool": "apatch_remote_task_run",
                    "purpose": "perform remote governed code work behind one intent boundary",
                },
                {
                    "tool": "apatch_remote_source_handoff",
                    "purpose": "move local source through the opaque broker, not raw shell transport",
                },
            ],
            "agent_next": (
                "Continue through pre-approved apatch broker tools; do not request filesystem approval "
                "and do not use direct file-write tools for this target."
            ),
            "human_next": (
                "Optional permanent cleanup: start Codex with this target as workspace root or add it as a writable root."
            ),
        }
    if mcp_approvals_missing:
        return {
            "mode": "mcp_approval_setup_required",
            "can_continue_without_filesystem_prompts": False,
            "target_dir": target_dir,
            "must_not_use": [],
            "use_instead": [],
            "agent_next": "Run apatch mcp codex-approve for this preset and reload the MCP server.",
            "human_next": "Reload/restart Codex MCP after approvals are written.",
        }
    return {
        "mode": "normal",
        "can_continue_without_filesystem_prompts": True,
        "target_dir": target_dir,
        "must_not_use": [],
        "use_instead": [],
        "agent_next": "Proceed normally; no apatch MCP approval or filesystem-scope issue detected.",
        "human_next": None,
    }


def _load_toml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path) or tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_relative_to(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _server_block(server: str, workspace: str, python_cmd: str) -> str:
    return "\n".join(
        [
            "[mcp_servers.{}]".format(server),
            "enabled = true",
            "command = {}".format(_toml_string(python_cmd)),
            'args = ["-m", "apatch.mcp.workspace_launcher"]',
            "cwd = {}".format(_toml_string(workspace)),
            "",
            "[mcp_servers.{}.env]".format(server),
            "APATCH_WORKSPACE = {}".format(_toml_string(workspace)),
            'PYTHONIOENCODING = "utf-8"',
            'PYTHONUTF8 = "1"',
            'LANG = "C.UTF-8"',
            'LC_ALL = "C.UTF-8"',
        ]
    )


def _section_exists(text: str, section: str) -> bool:
    pattern = r"(?m)^\[{}\]\s*$".format(re.escape(section))
    return re.search(pattern, text or "") is not None


def _ensure_section_key(text: str, section: str, key: str, value: str) -> Tuple[str, bool]:
    match = re.search(r"(?m)^\[{}\]\s*$".format(re.escape(section)), text or "")
    if not match:
        block = "[{}]\n{} = {}".format(section, key, value)
        return _append_block(text, block), True

    next_match = re.search(r"(?m)^\[", text[match.end() :])
    end = match.end() + next_match.start() if next_match else len(text)
    body = text[match.end() : end]
    key_re = re.compile(r"(?m)^({}[ \t]*=[ \t]*)(.*?)[ \t]*$".format(re.escape(key)))
    key_match = key_re.search(body)
    if key_match:
        old_line = key_match.group(0)
        new_line = "{}{}".format(key_match.group(1), value)
        if old_line == new_line:
            return text, False
        body = body[: key_match.start()] + new_line + body[key_match.end() :]
        return text[: match.end()] + body + text[end:], True

    insert = "\n{} = {}".format(key, value)
    if body.endswith("\n"):
        body = body + "{} = {}\n".format(key, value)
    else:
        body = body + insert
    return text[: match.end()] + body + text[end:], True


def _append_block(text: str, block: str) -> str:
    if not text:
        return block.rstrip() + "\n"
    suffix = "\n\n" if text.endswith("\n") else "\n\n"
    return text.rstrip() + suffix + block.rstrip() + "\n"


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)
