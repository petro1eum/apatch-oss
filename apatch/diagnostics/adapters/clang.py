"""Clang/gcc log → unified Diagnostic."""

from __future__ import annotations

from typing import Any, Dict, List

from apatch.build_diagnose import _enrich_diagnostic, parse_compiler_output
from apatch.diagnostics.schema import normalize_diagnostic
from apatch.failure_taxonomy import ACTION_FIX_FORWARD


def _clang_type(raw: Dict[str, Any]) -> str:
    if raw.get("error_type") == "missing_member":
        return "missing_member"
    return "compiler_error"


def _location(raw: Dict[str, Any]) -> Dict[str, Any]:
    loc: Dict[str, Any] = {"file": raw.get("file"), "line": raw.get("line")}
    member = raw.get("member")
    qualified = raw.get("type")
    if member and qualified:
        loc["symbol"] = f"{qualified}::{member}"
    elif member:
        loc["symbol"] = member
    return loc


def adapt_clang_log(log_text: str, target_dir: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in parse_compiler_output(log_text or ""):
        enriched = _enrich_diagnostic(raw, target_dir)
        suggestions = enriched.get("suggestions") or []
        norm_suggestions = [
            {
                "strategy": s.get("strategy"),
                "hint": s.get("detail") or s.get("hint") or "",
                "needles_hint": s.get("needles_hint"),
            }
            for s in suggestions
        ]
        out.append(
            normalize_diagnostic(
                {
                    "source": "clang",
                    "type": _clang_type(enriched),
                    "severity": "error",
                    "message": enriched.get("message") or "",
                    "recommended_action": ACTION_FIX_FORWARD,
                    "location": _location(enriched),
                    "evidence": {"raw_excerpt": enriched.get("raw")},
                    "suggestions": norm_suggestions,
                }
            )
        )
    return out