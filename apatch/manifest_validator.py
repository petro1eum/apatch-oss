"""Validate strip manifests before apply (overlap, shared boundaries)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from apatch.strip import StripSpec, _find_line


@dataclass
class BlockRange:
    label: str
    start_line: int  # 1-based inclusive (first removed line)
    end_line: int  # 1-based exclusive (until marker line)


class ManifestValidationError(Exception):
    """Raised when manifest validation fails."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _resolve_end_idx(lines: Sequence[str], spec: StripSpec, start_idx: int) -> int:
    if spec.until in ("next_else_if", "@next_else_if"):
        for i in range(start_idx + 1, len(lines)):
            if "else if" in lines[i]:
                return i
        raise ValueError("dynamic marker 'next_else_if' not found forward from start marker")
    return _find_line(lines, spec.until, start=start_idx + 1, suggest_until=False)


def compute_block_ranges(lines: Sequence[str], specs: Sequence[StripSpec]) -> List[BlockRange]:
    """Compute 1-based line spans for each spec on the original file (no mutation)."""
    ranges: List[BlockRange] = []
    for spec in specs:
        start_idx = _find_line(lines, spec.start)
        end_idx = _resolve_end_idx(lines, spec, start_idx)
        label = spec.label or spec.start[:40]
        ranges.append(
            BlockRange(
                label=label,
                start_line=start_idx + 1,
                end_line=end_idx + 1,
            )
        )
    return ranges


def validate_manifest(
    lines: Sequence[str],
    specs: Sequence[StripSpec],
) -> List[str]:
    """Return human-readable errors; empty list means OK."""
    errors: List[str] = []
    try:
        ranges = compute_block_ranges(lines, specs)
    except ValueError as e:
        return [str(e)]

    for i in range(len(ranges)):
        for j in range(i + 1, len(ranges)):
            a, b = ranges[i], ranges[j]
            spec_a, spec_b = specs[i], specs[j]
            # Convert to 0-based half-open [start, end) for removed content
            a0, a1 = a.start_line - 1, a.end_line - 1
            b0, b1 = b.start_line - 1, b.end_line - 1
            if a0 < b1 and b0 < a1:
                lo = max(a.start_line, b.start_line)
                hi = min(a.end_line, b.end_line) - 1
                errors.append(
                    f'manifest overlap: "{a.label}" ({a.start_line}-{a.end_line - 1}) '
                    f'intersects "{b.label}" ({b.start_line}-{b.end_line - 1}) '
                    f"at lines {lo}-{max(lo, hi)}.\n"
                    f"HINT: merge into one strip OR use non-overlapping until markers."
                )
                continue

            # Strips are applied bottom-up; a later block must not consume an earlier
            # block's until marker line (including when until text == start text).
            if a.start_line < b.start_line and b.start_line <= a.end_line:
                errors.append(
                    f'manifest boundary conflict: "{b.label}" start (line {b.start_line}) '
                    f'touches or consumes the until marker of "{a.label}" (line {a.end_line}).\n'
                    f"HINT: insert a dedicated until marker between blocks, or merge strips."
                )
            elif b.start_line < a.start_line and a.start_line <= b.end_line:
                errors.append(
                    f'manifest boundary conflict: "{a.label}" start (line {a.start_line}) '
                    f'touches or consumes the until marker of "{b.label}" (line {b.end_line}).\n'
                    f"HINT: insert a dedicated until marker between blocks, or merge strips."
                )

            if spec_a.until == spec_b.start:
                errors.append(
                    f'manifest boundary conflict: until marker of "{a.label}" equals start marker '
                    f'of "{b.label}" ({spec_a.until!r}). Bottom-up strip removes it before '
                    f'"{a.label}" can resolve its until boundary.'
                )
            elif spec_b.until == spec_a.start:
                errors.append(
                    f'manifest boundary conflict: until marker of "{b.label}" equals start marker '
                    f'of "{a.label}" ({spec_b.until!r}). Bottom-up strip removes it before '
                    f'"{b.label}" can resolve its until boundary.'
                )
    return errors


def assert_valid_manifest(lines: Sequence[str], specs: Sequence[StripSpec]) -> None:
    errors = validate_manifest(lines, specs)
    if errors:
        raise ManifestValidationError(errors)
