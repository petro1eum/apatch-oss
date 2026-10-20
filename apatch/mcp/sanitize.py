"""Make MCP tool payloads safe for JSON-RPC (valid UTF-8, bounded size)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

DEFAULT_MAX_STR = 48_000
DEFAULT_MAX_LIST = 40


def sanitize_text(value: str, *, max_len: int = DEFAULT_MAX_STR) -> str:
    """Replace invalid UTF-8 surrogates and cap very long strings."""
    if not isinstance(value, str):
        return str(value)
    clean = value.encode("utf-8", errors="replace").decode("utf-8")
    if len(clean) <= max_len:
        return clean
    return clean[:max_len] + f"\n…[truncated {len(clean) - max_len} chars]"


def sanitize_for_json(
    obj: Any,
    *,
    max_str: int = DEFAULT_MAX_STR,
    max_list: int = DEFAULT_MAX_LIST,
    _depth: int = 0,
) -> Any:
    """Recursively sanitize structures returned to MCP clients."""
    if _depth > 24:
        return "[max depth]"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return sanitize_text(obj, max_len=max_str)
    if isinstance(obj, bytes):
        return sanitize_text(obj.decode("utf-8", errors="replace"), max_len=max_str)
    if isinstance(obj, dict):
        return {
            sanitize_text(str(k), max_len=256): sanitize_for_json(
                v, max_str=max_str, max_list=max_list, _depth=_depth + 1
            )
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        items = list(obj)
        if len(items) > max_list:
            head = [
                sanitize_for_json(x, max_str=max_str, max_list=max_list, _depth=_depth + 1)
                for x in items[:max_list]
            ]
            head.append(f"…[truncated {len(items) - max_list} items]")
            return head
        return [
            sanitize_for_json(x, max_str=max_str, max_list=max_list, _depth=_depth + 1)
            for x in items
        ]
    return sanitize_text(str(obj), max_len=max_str)


def sanitize_tool_result(
    result: Any,
    *,
    max_str: int = DEFAULT_MAX_STR,
    max_list: int = DEFAULT_MAX_LIST,
) -> Any:
    """Sanitize and verify the payload is JSON-serializable."""
    clean = sanitize_for_json(result, max_str=max_str, max_list=max_list)
    json.dumps(clean, ensure_ascii=False)
    return clean


def compact_apply_result(result: Dict[str, Any], *, max_entries: int = 25) -> Dict[str, Any]:
    """Keep MCP apply responses small; full detail lives in report_path."""
    if not isinstance(result, dict):
        return result
    if type(max_entries) is not int or max_entries < 1:
        raise ValueError("max_entries must be a positive integer")
    entries = result.get("entries")
    if not isinstance(entries, list) or len(entries) <= max_entries:
        return result
    out = dict(result)
    def priority(entry: Any) -> int:
        if not isinstance(entry, dict):
            return 2
        states = {str(entry.get(key, "")).lower() for key in ("outcome", "status", "result_state")}
        if (entry.get("ok") is False or entry.get("success") is False
                or states & {"failed", "error", "denied", "blocked", "rejected", "timeout", "timed_out", "rolled_back"}
                or any(entry.get(key) for key in ("error", "error_type", "error_code"))):
            return 0
        return 1 if states & {"unknown", "incomplete", "cancelled", "canceled", "pending", "skipped", "not_run"} else 2

    ordered = sorted(range(len(entries)), key=lambda i: priority(entries[i]))
    selected = ordered[:max_entries]
    out["entries"] = [entries[i] for i in selected]
    previous_indices = result.get("entries_retained_indices")
    if (not isinstance(previous_indices, list) or len(previous_indices) != len(entries)
            or any(type(i) is not int or i < 0 for i in previous_indices)):
        previous_indices = list(range(len(entries)))
    out["entries_retained_indices"] = [previous_indices[i] for i in selected]
    out["entries_truncated"] = True
    previous_total = result.get("entries_total", len(entries))
    previous_total = previous_total if type(previous_total) is int and previous_total >= len(entries) else len(entries)
    previous_critical_omitted = result.get("entries_critical_omitted", 0)
    if type(previous_critical_omitted) is not int or previous_critical_omitted < 0:
        previous_critical_omitted = 0
    out["entries_total"] = previous_total
    out["entries_omitted"] = previous_total - len(selected)
    out["entries_critical_total"] = previous_critical_omitted + sum(priority(entry) < 2 for entry in entries)
    out["entries_critical_omitted"] = previous_critical_omitted + sum(priority(entries[i]) < 2 for i in ordered[max_entries:])
    unknown_omitted = any(not isinstance(entries[i], dict) or not any(
        key in entries[i] for key in ("outcome", "status", "result_state", "ok", "success", "error", "error_type")
    ) for i in ordered[max_entries:])
    out["summary_incomplete"] = bool(result.get("summary_incomplete") or out["entries_critical_omitted"] or unknown_omitted)
    if out["summary_incomplete"] or any(priority(entries[i]) == 1 for i in selected):
        out["do_not_auto_retry"] = True
    if out["summary_incomplete"]:
        out.setdefault("recommended_action", "inspect_report" if out.get("report_path") else "inspect_saved_result")
    if out.get("report_path"):
        out["entries_note"] = "Full per-file outcomes are in report_path."
    else:
        out["entries_note"] = "Details omitted; no report_path was supplied. Do not repeat the operation to recover its response."
    return out
