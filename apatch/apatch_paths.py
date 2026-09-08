"""RFP-016 Phase 5 — .apatch path resolution (flat + structured layouts)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Tuple

LAYOUT_FLAT = "flat"
LAYOUT_STRUCTURED = "structured"

STATE_DIR_REL = ".apatch/state"

# Flat relative path -> structured relative path (files).
FLAT_TO_STRUCTURED_FILES: Dict[str, str] = {
    ".apatch/enforcement.json": ".apatch/state/enforcement.json",
    ".apatch/sandbox.json": ".apatch/state/sandbox.json",
    ".apatch/session_state.json": ".apatch/state/session_state.json",
    ".apatch/artifacts.jsonl": ".apatch/registry/artifacts.jsonl",
    ".apatch/provenance.jsonl": ".apatch/registry/provenance.jsonl",
    ".apatch/events.jsonl": ".apatch/debug/events.jsonl",
}

# Flat directory -> structured directory root.
FLAT_TO_STRUCTURED_DIRS: Dict[str, str] = {
    ".apatch/backups": ".apatch/history/backups",
}


@dataclass(frozen=True)
class LayoutPaths:
    workspace: str
    layout: str
    apatch_dir: str
    session_state: str
    artifacts_registry: str
    provenance_log: str
    events: str
    backups: str


def normalize_rel(path: str) -> str:
    """Normalize separators and strip './' prefixes, preserving leading dots.

    lstrip('./') strips dot *characters*: '.apatch/conformance.json' became
    'apatch/conformance.json' in ledger-derived file sets, so staleness
    hashed a non-existent path and flipped attested requirements to stale.
    Canonical implementation — control-plane paths must keep their dot.
    Shared by sandbox, coverage, interference, plan, and git matchers.
    """
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def detect_layout(root: str) -> str:
    """Structured when `.apatch/state/` exists."""
    state_dir = os.path.join(os.path.abspath(root), STATE_DIR_REL)
    if os.path.isdir(state_dir):
        return LAYOUT_STRUCTURED
    return LAYOUT_FLAT


def workspace_paths(root: str) -> LayoutPaths:
    """Resolved absolute paths for session state, registry, events, backups."""
    workspace = os.path.abspath(root)
    apatch_dir = os.path.join(workspace, ".apatch")
    layout = detect_layout(workspace)
    if layout == LAYOUT_STRUCTURED:
        return LayoutPaths(
            workspace=workspace,
            layout=layout,
            apatch_dir=apatch_dir,
            session_state=os.path.join(apatch_dir, "state", "session_state.json"),
            artifacts_registry=os.path.join(apatch_dir, "registry", "artifacts.jsonl"),
            provenance_log=os.path.join(apatch_dir, "registry", "provenance.jsonl"),
            events=os.path.join(apatch_dir, "debug", "events.jsonl"),
            backups=os.path.join(apatch_dir, "history", "backups"),
        )
    return LayoutPaths(
        workspace=workspace,
        layout=layout,
        apatch_dir=apatch_dir,
        session_state=os.path.join(apatch_dir, "session_state.json"),
        artifacts_registry=os.path.join(apatch_dir, "artifacts.jsonl"),
        provenance_log=os.path.join(apatch_dir, "provenance.jsonl"),
        events=os.path.join(apatch_dir, "events.jsonl"),
        backups=os.path.join(apatch_dir, "backups"),
    )


def rel_workspace_path(root: str, abs_path: str) -> str:
    return os.path.relpath(abs_path, os.path.abspath(root)).replace("\\", "/")


def migration_pairs(root: str) -> Tuple[str, str, list]:
    """Return (layout_before, layout_after, moves) without mutating disk."""
    workspace = os.path.abspath(root)
    layout_before = detect_layout(workspace)
    if layout_before == LAYOUT_STRUCTURED:
        return layout_before, LAYOUT_STRUCTURED, []

    moves: list = []
    for src_rel, dst_rel in FLAT_TO_STRUCTURED_FILES.items():
        src_abs = os.path.join(workspace, src_rel)
        if os.path.isfile(src_abs):
            moves.append({"from": src_rel, "to": dst_rel})

    for src_dir_rel, dst_dir_rel in FLAT_TO_STRUCTURED_DIRS.items():
        src_dir = os.path.join(workspace, src_dir_rel)
        if not os.path.isdir(src_dir):
            continue
        for dirpath, _, filenames in os.walk(src_dir):
            for fname in filenames:
                src_abs = os.path.join(dirpath, fname)
                rel_under = os.path.relpath(src_abs, src_dir).replace("\\", "/")
                dst_rel = f"{dst_dir_rel}/{rel_under}"
                moves.append(
                    {
                        "from": rel_workspace_path(workspace, src_abs),
                        "to": dst_rel,
                    }
                )
    return layout_before, LAYOUT_STRUCTURED, moves