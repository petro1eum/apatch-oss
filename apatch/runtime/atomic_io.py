"""Small cross-process file transactions used by APatch control-plane state."""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from typing import Any, Dict, Iterator

try:  # pragma: no cover - platform branch
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform branch
    import msvcrt
except ImportError:  # pragma: no cover - Unix
    msvcrt = None  # type: ignore[assignment]


@contextmanager
def exclusive_file_lock(path: str) -> Iterator[None]:
    """Serialize a read-check-write transaction across APatch processes."""
    lock_path = os.path.abspath(path) + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            if os.path.getsize(lock_path) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


def read_json_file(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    """Durably replace a JSON object without exposing a partial file."""
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    os.makedirs(parent, exist_ok=True)
    tmp = "{}.{}.{}.tmp".format(abs_path, os.getpid(), secrets.token_hex(6))
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, abs_path)
        try:
            dir_fd = os.open(parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
