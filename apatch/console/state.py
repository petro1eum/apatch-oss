"""Console UI persistence (.apatch/console.json)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

CONSOLE_STATE_REL = ".apatch/console.json"


def console_state_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), CONSOLE_STATE_REL)


def load_console_state(root: str) -> Dict[str, Any]:
    path = console_state_path(root)
    if not os.path.isfile(path):
        return {"logs_path": None, "last_message": None}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"logs_path": None}
    except (OSError, json.JSONDecodeError):
        return {"logs_path": None, "last_message": None}


def save_console_state(root: str, data: Dict[str, Any]) -> str:
    path = console_state_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path
