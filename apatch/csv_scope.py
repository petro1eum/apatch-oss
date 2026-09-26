"""Fail-closed preservation for CSV tables partitioned by a slug column.

No project-specific slug list or regex semantics are embedded here.
"""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from pathlib import Path


class CsvScopeError(ValueError):
    """A replacement would remove a partition or modify unrelated partitions."""


def _rows(text):
    return list(csv.reader(io.StringIO(text), strict=True))


def validate_csv_replacement(path, before, after, replacement):
    if Path(path).suffix.lower() != ".csv":
        return
    try:
        old_rows = _rows(before)
    except csv.Error as exc:
        raise CsvScopeError("CSV_SCOPE_VIOLATION: unreadable source CSV") from exc
    if not old_rows or "slug" not in old_rows[0]:
        return
    header = old_rows[0]
    index = header.index("slug")
    try:
        new_rows = _rows(after)
        fragment = _rows(replacement)
    except csv.Error as exc:
        raise CsvScopeError("CSV_SCOPE_VIOLATION: invalid replacement CSV") from exc
    if not new_rows or new_rows[0] != header:
        raise CsvScopeError("CSV_SCOPE_VIOLATION: partitioned CSV header changed")

    def groups(rows):
        result = defaultdict(Counter)
        for row in rows:
            if not row:
                continue
            if len(row) <= index or not row[index].strip():
                raise CsvScopeError("CSV_SCOPE_VIOLATION: missing slug")
            result[row[index]][tuple(row)] += 1
        return result

    old, new = groups(old_rows[1:]), groups(new_rows[1:])
    missing = sorted(set(old) - set(new))
    if missing:
        raise CsvScopeError(
            "CSV_SCOPE_VIOLATION: removed slug partitions: " + ", ".join(missing)
        )
    # Complete replacement records declare the edited partitions. A field-only
    # literal replacement has no such declaration; partition retention still
    # applies. A full-file replacement must retain all existing partitions.
    scope = {
        row[index] for row in fragment
        if len(row) == len(header) and row != header and row[index].strip()
    }
    if scope:
        changed = sorted(
            slug for slug in set(old) - scope if old[slug] != new[slug]
        )
        if changed:
            raise CsvScopeError(
                "CSV_SCOPE_VIOLATION: changed unrelated slug partitions: "
                + ", ".join(changed)
            )
