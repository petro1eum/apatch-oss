"""SPEC-CONCEPT-CLARIFY-1 — RFP-034 §4a the human-in-loop clarify loop.

Loose human input is not silently accepted as fact (§3.9): a person can mark a concept or
claim ``needs_clarification`` with a note, the machine records it and surfaces it *not-green*
on the graph, and only a person resolves it. The store is a small JSON file next to the
other ``.apatch`` maps; the act is a deliberate human edit, never an agent's silent default.

This is the inverse of an invariant: an invariant says "the machine proved this"; a
clarification says "a human says this is not yet settled — do not treat it as done".
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

STORE_REL = os.path.join(".apatch", "concept_clarify.json")


def _store_path(target_dir: str) -> str:
    return os.path.join(target_dir, STORE_REL)


def clarifications(target_dir: str = ".") -> Dict[str, Dict[str, Any]]:
    """Return ``{id: {note, by}}`` of open clarifications (empty if none)."""
    path = _store_path(target_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh) or {}


def _write(target_dir: str, data: Dict[str, Dict[str, Any]]) -> None:
    path = _store_path(target_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)


def set_clarification(cid: str, note: str, target_dir: str = ".",
                      by: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Mark ``cid`` as needing clarification with ``note`` (a human note). Returns the store."""
    data = clarifications(target_dir)
    data[cid] = {"note": note, "by": by}
    _write(target_dir, data)
    return data


def resolve_clarification(cid: str, target_dir: str = ".") -> bool:
    """Clear ``cid``'s clarification (a human decision). Returns True if one was open."""
    data = clarifications(target_dir)
    existed = data.pop(cid, None) is not None
    _write(target_dir, data)
    return existed


def needs_clarification(target_dir: str = ".") -> Dict[str, str]:
    """``{id: note}`` overlay for the renderer — the open clarifications as id->note."""
    return {cid: (v.get("note") or "") for cid, v in clarifications(target_dir).items()}