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
    entries = result.get("entries")
    if not isinstance(entries, list) or len(entries) <= max_entries:
        return result
    out = dict(result)
    out["entries"] = entries[:max_entries]
    out["entries_truncated"] = True
    out["entries_total"] = len(entries)
    if out.get("report_path"):
        out["entries_note"] = "Full per-file outcomes are in report_path."
    return out
