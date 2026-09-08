#!/usr/bin/env python3
"""Extract marker-bounded blocks from a source file (inverse of apatch strip)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apatch.strip import StripSpec, _find_line, load_strip_manifest  # noqa: E402


def extract_block(lines: list[str], spec: StripSpec) -> tuple[int, int, list[str]]:
    start_idx = _find_line(lines, spec.start)
    end_idx = _find_line(lines, spec.until, start=start_idx + 1)
    return start_idx, end_idx, lines[start_idx:end_idx]


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract apatch strip manifest blocks")
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--source", type=Path, help="Source file (default: git rev path)")
    ap.add_argument("--git-rev", default="b2784928")
    ap.add_argument("--git-path", required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=Path.cwd(),
                    help="Repo root used to resolve --git-rev/--git-path (default: current directory).")
    args = ap.parse_args()

    if args.source:
        lines = args.source.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        blob = subprocess.check_output(
            ["git", "show", f"{args.git_rev}:{args.git_path}"],
            cwd=args.repo,
            text=True,
        )
        lines = blob.splitlines(keepends=True)

    specs = load_strip_manifest(str(args.manifest))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        label = spec.label or spec.start[:48].replace("/", "_")
        try:
            start, end, block = extract_block(lines, spec)
        except ValueError as e:
            print(f"SKIP {label}: {e}", file=sys.stderr)
            continue
        out = args.out_dir / f"{label}.extracted.cpp"
        out.write_text("".join(block), encoding="utf-8")
        print(f"{label}: lines {start + 1}-{end} -> {out} ({len(block)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
