"""RFP-016 Phase 5 — flat → structured .apatch layout migrate (separate from GC)."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict

from apatch.apatch_paths import (
    LAYOUT_STRUCTURED,
    migration_pairs,
    rel_workspace_path,
)

MANIFEST_REL = ".apatch/registry/layout_migrate.json"
SCHEMA_VERSION = 1


def layout_migrate_plan(workspace: str) -> Dict[str, Any]:
    """Dry-run plan: moves without disk mutation."""
    layout_before, layout_after, moves = migration_pairs(workspace)
    return {
        "ok": True,
        "workspace": os.path.abspath(workspace),
        "layout_before": layout_before,
        "layout_after": layout_after,
        "moves": moves,
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def layout_migrate(workspace: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """Migrate flat `.apatch/` files into structured layout."""
    root = os.path.abspath(workspace)
    plan = layout_migrate_plan(root)
    if plan["layout_before"] == LAYOUT_STRUCTURED:
        return {
            **plan,
            "dry_run": dry_run,
            "moved_count": 0,
            "message": "already structured",
        }

    if dry_run:
        return {**plan, "dry_run": True, "moved_count": 0}

    moved: list = []
    for move in plan["moves"]:
        src_abs = os.path.join(root, move["from"])
        dst_abs = os.path.join(root, move["to"])
        if not os.path.exists(src_abs):
            continue
        os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
        shutil.move(src_abs, dst_abs)
        moved.append(move)

    # Ensure state/ marker exists for structured detection.
    os.makedirs(os.path.join(root, ".apatch", "state"), exist_ok=True)

    for src_dir_rel in (".apatch/backups",):
        src_dir = os.path.join(root, src_dir_rel)
        if os.path.isdir(src_dir) and not os.listdir(src_dir):
            os.rmdir(src_dir)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": _utcnow_iso(),
        "layout_before": plan["layout_before"],
        "layout_after": plan["layout_after"],
        "moves": moved,
    }
    manifest_abs = os.path.join(root, MANIFEST_REL)
    os.makedirs(os.path.dirname(manifest_abs), exist_ok=True)
    with open(manifest_abs, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return {
        **plan,
        "ok": True,
        "dry_run": False,
        "moved_count": len(moved),
        "manifest": rel_workspace_path(root, manifest_abs),
    }


def run_layout_migrate(
    target_dir: str = ".",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """CLI/MCP entrypoint."""
    return layout_migrate(target_dir, dry_run=dry_run)