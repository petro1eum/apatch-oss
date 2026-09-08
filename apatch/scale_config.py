"""Environment-driven scale tuning (ADR-001 SCALE-5 / SCALE-8)."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple


def get_ast_window_config() -> Tuple[int, int]:
    """Return (window_bytes, full_parse_max_bytes).

    ``window_bytes=0`` disables windowed parse (full file always).
    """
    window = int(os.environ.get("APATCH_AST_WINDOW_BYTES", "0"))
    full_max = int(os.environ.get("APATCH_AST_FULL_PARSE_MAX", "524288"))
    return max(0, window), max(0, full_max)


def resolve_path_backend(explicit: Optional[str] = None) -> str:
    raw = (explicit or os.environ.get("APATCH_PATH_BACKEND", "auto")).strip().lower()
    if raw in ("auto", "git", "walk", "fd", "watchman"):
        return raw
    return "auto"


def get_plan_workers(explicit: Optional[int] = None) -> int:
    """Worker count for parallel ``plan --json`` (0 or 1 = sequential, -1 = all CPUs)."""
    if explicit is not None:
        return explicit
    raw = os.environ.get("APATCH_PLAN_WORKERS", "0").strip()
    try:
        return int(raw)
    except ValueError:
        return 0
