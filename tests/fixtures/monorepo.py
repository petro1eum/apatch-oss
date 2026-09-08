"""Shared helpers for enterprise scale / perf tests."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def monorepo_file_target() -> int:
    """File count for synthetic monorepo (default 50k per ADR-001 §7)."""
    raw = os.environ.get("APATCH_MONOREPO_FILES", "50000")
    try:
        return max(100, int(raw))
    except ValueError:
        return 50_000


def build_synthetic_monorepo(root: Path, *, file_count: Optional[int] = None) -> Path:
    """Create a synthetic tree with ``file_count`` filler files + one needle target.

    Returns absolute path to ``needle_target.py``.
    """
    if file_count is None:
        file_count = monorepo_file_target()

    needle = root / "packages" / "alpha" / "deep" / "needle_target.py"
    needle.parent.mkdir(parents=True, exist_ok=True)
    needle.write_text("value = 1\n", encoding="utf-8")

    # Decoy with different basename (same package tree noise).
    decoy = root / "packages" / "beta" / "decoy_module.py"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text("value = 999\n", encoding="utf-8")

    files_per_dir = 100
    remaining = max(0, file_count - 2)
    d = 0
    while remaining > 0:
        dir_path = root / "noise" / f"pkg_{d:05d}"
        dir_path.mkdir(parents=True, exist_ok=True)
        batch = min(files_per_dir, remaining)
        for f in range(batch):
            (dir_path / f"mod_{f:03d}.py").write_text(
                f"# synthetic filler pkg={d} mod={f}\n",
                encoding="utf-8",
            )
        remaining -= batch
        d += 1

    return needle
