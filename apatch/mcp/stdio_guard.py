"""Harden MCP stdio transport — stdout must stay pure JSON-RPC for IDE clients.

Some IDE hosts merge the child process stderr into the JSON-RPC stdout stream or
run Python without UTF-8 locales. Any emoji / warning on stderr then injects
bytes like 0xf0 (UTF-8 lead byte for 🛡️) and breaks the client parser with:
  invalid character 'ð' looking for beginning of value

This module runs before the rest of apatch imports when serving MCP.
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from typing import Optional, TextIO

_ACTIVE = False
_STDERR_LOG: Optional[TextIO] = None
_SAVED_STDERR_FD: Optional[int] = None


def is_mcp_stdio_mode() -> bool:
    return os.environ.get("APATCH_MCP_STDIO", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ) or os.environ.get("APATCH_MCP_STDIO_ACTIVE") == "1"


def stderr_log_path(workspace: Optional[str] = None) -> str:
    root = (
        workspace
        or os.environ.get("CURSOR_PROJECT_DIR")
        or os.environ.get("APATCH_WORKSPACE")
        or os.getcwd()
    )
    return os.path.join(os.path.abspath(root), ".apatch", "mcp_stderr.log")


def _ensure_utf8_env() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("LC_ALL", "C.UTF-8")
    os.environ.setdefault("LANG", "C.UTF-8")


def _reconfigure_stdio_text_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError, ValueError):
            pass


def _quiet_third_party_noise() -> None:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
    logging.root.handlers.clear()
    logging.root.addHandler(logging.NullHandler())
    logging.root.setLevel(logging.CRITICAL)
    for name in ("mcp", "fastmcp", "httpx", "httpcore", "asyncio"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def _redirect_stderr_fd(log_path: str) -> None:
    global _STDERR_LOG, _SAVED_STDERR_FD
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _SAVED_STDERR_FD = os.dup(2)
    log_fd = os.open(
        log_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    os.dup2(log_fd, 2)
    os.close(log_fd)
    _STDERR_LOG = open(2, "w", encoding="utf-8", errors="replace", closefd=False)
    sys.stderr = _STDERR_LOG


def activate_stdio_guard(*, workspace: Optional[str] = None) -> None:
    """Call once at MCP process start, before Rich / workflows import."""
    global _ACTIVE
    if _ACTIVE:
        return
    _ACTIVE = True
    os.environ["APATCH_MCP_STDIO"] = "1"
    os.environ["APATCH_MCP_STDIO_ACTIVE"] = "1"
    _ensure_utf8_env()
    _reconfigure_stdio_text_streams()
    _quiet_third_party_noise()
    log_path = os.environ.get("APATCH_MCP_STDERR_LOG") or stderr_log_path(workspace)
    try:
        _redirect_stderr_fd(log_path)
    except OSError:
        # Last resort: in-process devnull (does not stop fd=2 writers, but helps)
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
