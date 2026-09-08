"""Spec registry for planned needles (RFP-014 Phase 1.5).

Persists inline ``requirements`` from ``apatch_spec_run`` to
``.apatch/specs/<SPEC-ID>.json`` for ``apatch_spec_interference`` planned needles.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

REGISTRY_DIR = os.path.join(".apatch", "specs")


def _registry_abs(target_dir: str, spec_id: str) -> str:
    root = os.path.abspath(target_dir)
    return os.path.join(root, REGISTRY_DIR, f"{spec_id}.json")


def needles_sha256(manifest: Dict[str, Any]) -> str:
    reqs = manifest.get("requirements") or {}
    payload = json.dumps(reqs, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def update_spec_registry(
    target_dir: str,
    spec_id: str,
    manifest: Dict[str, Any],
    *,
    source_path: Optional[str] = None,
    manifest_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Write or refresh registry entry; returns written record."""
    reqs = manifest.get("requirements") or {}
    if not isinstance(reqs, dict) or not reqs:
        return {"ok": False, "error": "manifest has no requirements"}

    record: Dict[str, Any] = {
        "schema_version": 1,
        "id": spec_id,
        "source_path": source_path or manifest.get("source_path"),
        "manifest_sha256": manifest_sha256 or manifest.get("manifest_sha256"),
        "last_needles_sha256": needles_sha256(manifest),
        "requirements": reqs,
    }
    tags = manifest.get("tags")
    if isinstance(tags, list) and tags:
        record["tags"] = [str(t) for t in tags]
    path = _registry_abs(target_dir, spec_id)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    return {"ok": True, "path": path, "record": record}


def load_spec_registry(target_dir: str, spec_id: str) -> Optional[Dict[str, Any]]:
    path = _registry_abs(target_dir, spec_id)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)