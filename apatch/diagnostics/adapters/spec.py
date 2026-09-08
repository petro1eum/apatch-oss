"""Spec lint errors → unified Diagnostic."""

from __future__ import annotations

from typing import Any, Dict, List

from apatch.diagnostics.schema import normalize_diagnostic
from apatch.failure_taxonomy import ACTION_FIX_FORWARD


def adapt_spec_lint_errors(errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for err in errors or []:
        req = err.get("requirement") or err.get("rk")
        requirements = [str(req)] if req else []
        out.append(
            normalize_diagnostic(
                {
                    "source": "spec",
                    "type": "spec_violation",
                    "severity": "error",
                    "message": str(err.get("message") or err.get("code") or "spec violation"),
                    "recommended_action": ACTION_FIX_FORWARD,
                    "edges": {"requirements": requirements},
                    "evidence": {"raw_excerpt": str(err)},
                }
            )
        )
    return out