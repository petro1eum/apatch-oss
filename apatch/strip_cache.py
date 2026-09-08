"""mtime cache for strip dry-run previews."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional


def _cache_dir(workspace: str) -> Path:
    d = Path(workspace) / ".apatch" / "cache" / "strip_preview"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_key(file_path: str, manifest_path: Optional[str], specs_fingerprint: str) -> str:
    parts = [os.path.abspath(file_path), str(os.path.getmtime(file_path))]
    if manifest_path and os.path.exists(manifest_path):
        parts.extend([os.path.abspath(manifest_path), str(os.path.getmtime(manifest_path))])
    parts.append(specs_fingerprint)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def load_cached(workspace: str, key: str) -> Optional[dict]:
    path = _cache_dir(workspace) / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_cached(workspace: str, key: str, payload: dict) -> None:
    path = _cache_dir(workspace) / f"{key}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
