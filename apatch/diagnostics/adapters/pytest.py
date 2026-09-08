"""Pytest log → unified Diagnostic."""

from __future__ import annotations

import re
from typing import List

from apatch.diagnostics.schema import normalize_diagnostic
from apatch.failure_taxonomy import ACTION_FIX_FORWARD

_FAILED_RE = re.compile(
    r"^FAILED\s+(?P<file>\S+?)::(?P<node>\S+)\s+-\s+(?P<message>.+)$",
    re.MULTILINE,
)


def adapt_pytest_log(log_text: str, *, recommended_action: str = ACTION_FIX_FORWARD) -> List[dict]:
    out: List[dict] = []
    for match in _FAILED_RE.finditer(log_text or ""):
        out.append(
            normalize_diagnostic(
                {
                    "source": "pytest",
                    "type": "test_failure",
                    "severity": "error",
                    "message": match.group("message").strip(),
                    "recommended_action": recommended_action,
                    "location": {
                        "file": match.group("file"),
                        "symbol": match.group("node"),
                    },
                    "evidence": {"raw_excerpt": match.group(0).strip()},
                }
            )
        )
    return out