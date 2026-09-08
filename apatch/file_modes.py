"""Helpers for file permission mutation needles."""

from __future__ import annotations

import stat
import os
from typing import Any


def normalize_file_mode(value: Any) -> str:
    """Return a three-digit octal permission string (for example ``755``).

    Accepts common forms used by agents and Git output: ``755``, ``0755``,
    ``0o755``, ``100755`` and integer modes. Only permission bits are retained;
    file-type bits from Git modes are stripped.
    """
    if isinstance(value, bool):
        raise ValueError("file mode must be an octal permission, not bool")
    if isinstance(value, int):
        if 0 <= value <= 0o777:
            mode = value
        else:
            raw = str(value)
            if len(raw) == 6 and raw.startswith("100"):
                raw = raw[3:]
            if not raw.isdigit() or any(ch not in "01234567" for ch in raw):
                raise ValueError(f"invalid octal file mode {value!r}")
            mode = int(raw, 8)
    else:
        raw = str(value).strip().lower()
        if raw.startswith("0o"):
            raw = raw[2:]
        if not raw:
            raise ValueError("file mode is empty")
        if len(raw) == 6 and raw.startswith("100"):
            raw = raw[3:]
        if len(raw) == 4 and raw.startswith("0"):
            raw = raw[1:]
        if not raw.isdigit() or any(ch not in "01234567" for ch in raw):
            raise ValueError(f"invalid octal file mode {value!r}")
        mode = int(raw, 8)
    return f"{stat.S_IMODE(mode):03o}"


def mode_to_int(value: Any) -> int:
    return int(normalize_file_mode(value), 8)


def stat_mode(path: str) -> str:
    return normalize_file_mode(stat.S_IMODE(os.stat(path).st_mode))
