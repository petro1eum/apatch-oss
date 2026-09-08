"""Slug feedback closure assistant.

This module is intentionally read-only. It replays owner-triage feedback rows
through the live search API, reads ``debug.decision_graph`` when present, and
returns deterministic closure suggestions plus apatch mutation needles. The
caller still applies those needles through the governed lifecycle.
"""

from __future__ import annotations

import csv
import json
import os
import re
import urllib.request
from collections import Counter
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from apatch_search_workflows.feedback_status import (
    OBLIGATION_STATUSES,
    OWNER_PRESERVED_STATUSES,
    STRICT_OWNER_PRESERVED_STATUSES,
    canonical_status,
)


DEFAULT_API_URL = "http://127.0.0.1:8004/api/os/smart-search"
CODE_RE = re.compile(r"\b\d{3}-\d{4}\b")
RAW_LINE_KEY = "__apatch_raw_line"
DEFAULT_SLUG_ALIASES = {
    "flanets": "flanec",
}

ApiFunc = Callable[[str, str, int, float], Dict[str, Any]]


def slug_close_workspace(
    target_dir: str = ".",
    *,
    slug: str,
    api_url: str = DEFAULT_API_URL,
    triage_path: Optional[str] = None,
    approved_path: Optional[str] = None,
    size: int = 5,
    timeout: float = 30.0,
    limit: int = 0,
    include_rows: bool = True,
    api_func: Optional[ApiFunc] = None,
) -> Dict[str, Any]:
    """Replay slug owner feedback and propose closure mutations.

    The function never writes files. ``proposed_needles`` are ready for
    apatch_generate_batch / apatch_remote_task_run.
    """
    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    slug_n = _norm(slug)
    if not slug_n:
        return {"ok": False, "error": "slug is required", "error_type": "SLUG_REQUIRED"}

    triage_abs = _abs(root, triage_path or os.path.join("tests", "regressions", f"{slug_n}_feedback_triage.tsv"))
    approved_abs = _abs(root, approved_path or os.path.join("tests", "regressions", "feedback_approved_contract.tsv"))
    if not os.path.isfile(triage_abs):
        return {
            "ok": False,
            "error": f"triage file not found: {_rel(root, triage_abs)}",
            "error_type": "TRIAGE_NOT_FOUND",
        }

    fieldnames, rows = _read_tsv(triage_abs)
    if limit and limit > 0:
        rows = rows[: int(limit)]

    live = api_func or _post_search_api
    slug_aliases = _slug_aliases(root)
    approved_by_query = _approved_obligations(root=root, path=approved_abs, slug=slug_n)
    suggestions: List[Dict[str, Any]] = []
    needles: List[Dict[str, Any]] = []
    status_counts: Counter[str] = Counter()

    for row in rows:
        original = dict(row)
        query = (row.get("query") or "").strip()
        if not query:
            suggestion = _suggest_without_query(row, slug_n)
        else:
            try:
                data = live(api_url, query, int(size), float(timeout))
                suggestion = _suggest_from_live(
                    row,
                    slug_n,
                    data,
                    approved_by_query.get(query),
                    slug_aliases=slug_aliases,
                )
            except Exception as exc:  # noqa: BLE001 - assistant-facing diagnostic
                suggestion = _suggest_error(row, exc)

        status_counts[suggestion["suggested_status"]] += 1
        updated = _updated_row(original, suggestion, fieldnames)
        needle = _row_needle(triage_abs, root, fieldnames, original, updated)
        if needle:
            needles.append(needle)

        record = {
            "triage_id": row.get("triage_id") or row.get("id") or "",
            "query": query,
            "current_status": row.get("status") or "",
            **suggestion,
        }
        if include_rows:
            record["updated_row"] = updated
        suggestions.append(record)

    approved_conflicts, approved_needles = _approved_conflicts(
        root=root,
        path=approved_abs,
        slug=slug_n,
        suggestions=suggestions,
    )

    return {
        "ok": True,
        "workspace": root,
        "slug": slug_n,
        "api_url": api_url,
        "triage_path": _rel(root, triage_abs),
        "approved_path": _rel(root, approved_abs) if os.path.exists(approved_abs) else None,
        "rows_scanned": len(rows),
        "summary": {
            "suggested_status_counts": dict(sorted(status_counts.items())),
            "updates": len(needles),
            "approved_conflicts": len(approved_conflicts),
        },
        "suggestions": suggestions,
        "proposed_needles": needles,
        "approved_conflicts": approved_conflicts,
        "approved_needles": approved_needles,
        "agent_next": _agent_next(needles, approved_conflicts),
    }


def slug_close_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    return enrich_tool_response(
        "apatch_slug_close",
        slug_close_workspace(root, **kwargs),
        target_dir=root,
    )


def _slug_aliases(root: str) -> Dict[str, str]:
    aliases = dict(DEFAULT_SLUG_ALIASES)
    contract = os.path.join(root, "docs", "search_engine", "SMART_SEARCH_RUNTIME_CONTRACT.md")
    if not os.path.isfile(contract):
        return aliases
    try:
        with open(contract, encoding="utf-8") as handle:
            for line in handle:
                if "|" not in line or "`" not in line:
                    continue
                lower = line.lower()
                if not any(marker in lower for marker in ("drift", "alias", "алиас", "не отдельная категория")):
                    continue
                values = re.findall(r"`([^`]+)`", line)
                if len(values) >= 2:
                    src = _norm(values[0])
                    dst = _norm(values[1])
                    if src and dst:
                        aliases[src] = aliases.get(dst, dst)
    except OSError:
        return aliases
    return aliases


def _canonical_slug(slug: str, aliases: Optional[Mapping[str, str]] = None) -> str:
    current = _norm(slug)
    seen = set()
    mapping = aliases or DEFAULT_SLUG_ALIASES
    while current and current in mapping and current not in seen:
        seen.add(current)
        current = _norm(mapping[current])
    return current


def _post_search_api(api_url: str, query: str, size: int, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(
        api_url,
        data=json.dumps({"q": query, "size": size, "debug": True}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured local/prod API
        return json.loads(resp.read().decode("utf-8"))


def _read_tsv(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        lines = handle.read().splitlines()
    if not lines:
        return [], []
    fieldnames = next(csv.reader([lines[0]], delimiter="\t"), [])
    rows: List[Dict[str, str]] = []
    for raw in lines[1:]:
        if not raw.strip():
            continue
        cols = next(csv.reader([raw], delimiter="\t"), [])
        row = {
            field: (cols[idx] if idx < len(cols) else "").strip()
            for idx, field in enumerate(fieldnames)
        }
        row[RAW_LINE_KEY] = raw
        rows.append(row)
    return fieldnames, rows


def _suggest_without_query(row: Mapping[str, str], slug: str) -> Dict[str, Any]:
    return {
        "suggested_status": "open_runtime_bug",
        "suggested_slug": row.get("expected_slug") or slug,
        "suggested_jde": row.get("expected_jde") or "",
        "root_cause": "missing_query",
        "confidence": 1.0,
        "evidence": ["triage row has empty query"],
    }


def _suggest_error(row: Mapping[str, str], exc: Exception) -> Dict[str, Any]:
    return {
        "suggested_status": "open_runtime_bug",
        "suggested_slug": row.get("expected_slug") or "",
        "suggested_jde": row.get("expected_jde") or "",
        "root_cause": "live_replay_error",
        "confidence": 0.0,
        "evidence": [f"{type(exc).__name__}: {exc}"],
    }


def _suggest_from_live(
    row: Mapping[str, str],
    slug: str,
    data: Mapping[str, Any],
    approved: Optional[Mapping[str, str]] = None,
    *,
    slug_aliases: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    items = data.get("items") or []
    top = items[0] if items else {}
    actual_slug = _actual_slug(data)
    canonical_slug = _canonical_slug(slug, slug_aliases)
    canonical_actual_slug = _canonical_slug(actual_slug, slug_aliases) if actual_slug else ""
    top_jde = _item_jde(top)
    graph = _decision_graph(data)
    summary = (graph.get("summary") or {}) if isinstance(graph, Mapping) else {}
    primary_issue = summary.get("primary_issue")
    diagnosis_status = summary.get("diagnosis_status")
    expected_codes = _expected_codes(row)
    expected_top = _expected_top(row)
    approved_status = _norm(str((approved or {}).get("status") or ""))
    approved_fragment = str((approved or {}).get("fragment") or "").strip()
    current_status = canonical_status(row.get("status"))

    evidence = _graph_evidence(graph)
    if primary_issue:
        evidence.append(f"graph.primary_issue={primary_issue}")
    if data.get("ai_escalation_reason"):
        evidence.append(f"ai_escalation_reason={data.get('ai_escalation_reason')}")

    if actual_slug and canonical_actual_slug != canonical_slug and items:
        return {
            "suggested_status": "other_slug",
            "suggested_slug": canonical_actual_slug or actual_slug,
            "suggested_jde": top_jde,
            "root_cause": "other_slug_route",
            "confidence": 0.95,
            "evidence": evidence + [f"top_slug={actual_slug}", f"top_jde={top_jde}"],
            "live": _live_summary(data, top),
        }

    if current_status in STRICT_OWNER_PRESERVED_STATUSES and not expected_top:
        return _suggest_preserved_owner_status(
            row,
            slug,
            data,
            top,
            evidence,
            root_cause="owner_terminal_status_preserved",
        )

    if (
        current_status in OWNER_PRESERVED_STATUSES
        and not expected_codes
        and not expected_top
        and approved_status in {"", current_status}
    ):
        return _suggest_preserved_owner_status(
            row,
            slug,
            data,
            top,
            evidence,
            root_cause="owner_status_preserved_without_positive_oracle",
        )

    if not items:
        status = "catalog_gap" if _looks_like_honest_gap(data, graph) else "open_runtime_bug"
        suggested_slug = canonical_actual_slug if canonical_actual_slug and canonical_actual_slug != canonical_slug else slug
        other_slug_empty = suggested_slug != slug
        if status == "catalog_gap" and other_slug_empty:
            root_cause = "other_slug_catalog_gap_zero_results_after_applied_filters"
        elif status == "catalog_gap":
            root_cause = "catalog_gap_zero_results_after_applied_filters"
        else:
            root_cause = "empty_result_without_hard_gap_evidence"
        return {
            "suggested_status": status,
            "suggested_slug": suggested_slug,
            "suggested_jde": "",
            "root_cause": root_cause,
            "confidence": 0.9 if status == "catalog_gap" else 0.45,
            "evidence": evidence + [f"diagnosis_status={diagnosis_status}", "items=[]"],
            "live": _live_summary(data, top),
        }

    if expected_codes and top_jde and top_jde not in expected_codes:
        return {
            "suggested_status": "open_runtime_bug",
            "suggested_slug": slug,
            "suggested_jde": row.get("expected_jde") or "",
            "root_cause": "expected_code_not_top1",
            "confidence": 0.9,
            "evidence": evidence + [f"expected_codes={sorted(expected_codes)}", f"top_jde={top_jde}"],
            "live": _live_summary(data, top),
        }

    positive_oracle = False
    if expected_top:
        positive_oracle = True
        if not _top_name_matches(top, expected_top):
            return {
                "suggested_status": "open_runtime_bug",
                "suggested_slug": slug,
                "suggested_jde": row.get("expected_jde") or "",
                "root_cause": "expected_top_not_top1",
                "confidence": 0.9,
                "evidence": evidence + [f"expected_top={expected_top}", f"top_name={top.get('name') or ''}"],
                "live": _live_summary(data, top),
            }

    if approved_status in OBLIGATION_STATUSES and approved_fragment and not expected_codes and not expected_top:
        positive_oracle = True
        approved_matches, missing_query_tokens = _approved_oracle_matches(top, approved_fragment, str(row.get("query") or ""))
        if not approved_matches:
            if approved_status == "must_find_brand_soft" and missing_query_tokens:
                return _brand_soft_verdict(
                    row, slug, data, top, items, missing_query_tokens, approved_fragment, evidence
                )
            root_cause = "approved_query_signal_not_top1" if missing_query_tokens else "approved_fragment_not_top1"
            if current_status in OWNER_PRESERVED_STATUSES:
                return _suggest_preserved_owner_status(
                    row,
                    slug,
                    data,
                    top,
                    evidence
                    + [
                        f"approved_fragment={approved_fragment}",
                        f"missing_query_tokens={missing_query_tokens}",
                    ],
                    root_cause=f"{root_cause}_{current_status}_preserved",
                )
            return {
                "suggested_status": "open_runtime_bug",
                "suggested_slug": slug,
                "suggested_jde": row.get("expected_jde") or "",
                "root_cause": root_cause,
                "confidence": 0.9,
                "evidence": evidence + [
                    f"approved_fragment={approved_fragment}",
                    f"top_name={top.get('name') or ''}",
                    f"missing_query_tokens={missing_query_tokens}",
                ],
                "live": _live_summary(data, top),
            }

    if not expected_codes and not positive_oracle:
        return _suggest_missing_positive_oracle(row, slug, data, top, evidence)

    return {
        "suggested_status": "fixed",
        "suggested_slug": slug,
        "suggested_jde": row.get("expected_jde") or top_jde,
        "root_cause": "live_top1_matches_expected_or_slug",
        "confidence": 0.85 if not expected_codes else 0.98,
        "evidence": evidence + [f"top_jde={top_jde}", f"top_slug={actual_slug or slug}"],
        "live": _live_summary(data, top),
    }


def _brand_soft_verdict(
    row: Mapping[str, str],
    slug: str,
    data: Mapping[str, Any],
    top: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    missing_tokens: Sequence[str],
    fragment: str,
    evidence: Sequence[str],
) -> Dict[str, Any]:
    """Grade a must_find_brand_soft obligation whose brand tokens are missing.

    The fragment (technical identity) already matched top-1; the contract
    allows dropping the brand ONLY when the drop is observable and no branded
    candidate was outranked. Anything else stays an open runtime bug with a
    precise diagnosis for the repair map.
    """
    lost = sorted({t for t in missing_tokens if any(t in _item_blob(item) for item in items)})
    if lost:
        return {
            "suggested_status": "open_runtime_bug",
            "suggested_slug": slug,
            "suggested_jde": row.get("expected_jde") or "",
            "root_cause": "brand_candidate_lost",
            "confidence": 0.9,
            "evidence": list(evidence)
            + [
                f"approved_fragment={fragment}",
                f"brand_tokens_present_but_not_top1={lost}",
            ],
            "live": _live_summary(data, top),
        }
    markers = _brand_drop_markers(data)
    if not markers:
        return {
            "suggested_status": "open_runtime_bug",
            "suggested_slug": slug,
            "suggested_jde": row.get("expected_jde") or "",
            "root_cause": "brand_drop_not_observable",
            "confidence": 0.9,
            "evidence": list(evidence)
            + [
                f"approved_fragment={fragment}",
                f"missing_query_tokens={list(missing_tokens)}",
                "fallback_dropped_filters has no stage=brand entry",
            ],
            "live": _live_summary(data, top),
        }
    return {
        "suggested_status": "accepted_brand_fallback",
        "suggested_slug": slug,
        "suggested_jde": "",
        "root_cause": "observable_brand_fallback",
        "confidence": 0.9,
        "evidence": list(evidence)
        + [
            f"approved_fragment={fragment}",
            f"missing_query_tokens={list(missing_tokens)}",
            f"fallback_dropped_filters[brand]={markers[0]}",
        ],
        "live": _live_summary(data, top),
    }


def _brand_drop_markers(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    debug = data.get("debug") or {}
    rows = debug.get("fallback_dropped_filters") if isinstance(debug, Mapping) else None
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, Mapping) and str(r.get("stage") or "").lower() == "brand"]


def _suggest_missing_positive_oracle(
    row: Mapping[str, str],
    slug: str,
    data: Mapping[str, Any],
    top: Mapping[str, Any],
    evidence: Sequence[str],
) -> Dict[str, Any]:
    current = canonical_status(row.get("status"))
    if current in OWNER_PRESERVED_STATUSES or current in {"catalog_gap", "other_slug", "needs_human_review"}:
        status = current
    else:
        status = "needs_review"
    suggested_slug = _norm(row.get("expected_slug") or "") or slug
    return {
        "suggested_status": status,
        "suggested_slug": suggested_slug,
        "suggested_jde": row.get("expected_jde") or "",
        "root_cause": "missing_positive_oracle_top1_slug_only",
        "confidence": 0.6,
        "evidence": list(evidence) + [
            "top1 slug match is not enough to close feedback",
            f"top_jde={_item_jde(top)}",
            f"top_slug={_actual_slug(data) or slug}",
        ],
        "live": _live_summary(data, top),
    }


def _suggest_preserved_owner_status(
    row: Mapping[str, str],
    slug: str,
    data: Mapping[str, Any],
    top: Mapping[str, Any],
    evidence: Sequence[str],
    *,
    root_cause: str,
) -> Dict[str, Any]:
    current = canonical_status(row.get("status"))
    return {
        "suggested_status": current,
        "suggested_slug": _norm(row.get("expected_slug") or "") or slug,
        "suggested_jde": row.get("expected_jde") or "",
        "root_cause": root_cause,
        "confidence": 0.7,
        "evidence": list(evidence)
        + [
            f"top_name={top.get('name') or ''}",
            f"current_status={current}",
            "owner triage status is not a runtime bug without expected_jde/expected_top",
        ],
        "live": _live_summary(data, top),
    }


def _looks_like_honest_gap(data: Mapping[str, Any], graph: Mapping[str, Any]) -> bool:
    summary = (graph.get("summary") or {}) if isinstance(graph, Mapping) else {}
    primary_issue = summary.get("primary_issue")
    ai_reason = str(data.get("ai_escalation_reason") or "")
    total = data.get("total")
    total_value = total.get("value") if isinstance(total, Mapping) else None
    return (
        primary_issue == "zero_results_after_applied_filters"
        or ai_reason == "l1_required_filter_catalog_gap"
        or total_value == 0
    )


def _top_name_matches(item: Mapping[str, Any], fragment: str) -> bool:
    codes = {m.group(0) for m in CODE_RE.finditer(fragment)}
    if codes:
        top_jde = _item_jde(item)
        return bool(top_jde and top_jde in codes)
    name = _item_blob(item)
    tokens = [token for token in _norm_text(fragment).split() if token]
    return bool(tokens) and all(token in name for token in tokens)


def _approved_oracle_matches(item: Mapping[str, Any], fragment: str, query: str) -> Tuple[bool, List[str]]:
    if not _top_name_matches(item, fragment):
        return False, []
    name = _norm_text(str(item.get("name") or ""))
    missing = [token for token in _distinctive_query_tokens(query) if token not in name]
    return not missing, missing


def _distinctive_query_tokens(query: str) -> List[str]:
    stop = {
        "артикул",
        "диаметр",
        "клапан",
        "кран",
        "муфта",
        "отвод",
        "резьбовой",
        "сантехнический",
        "труба",
        "фланец",
        "хомут",
    }
    tokens = re.findall(r"[0-9a-zа-яё]+", _norm_text(query))
    out: List[str] = []
    for token in tokens:
        if token in stop or token.isdigit():
            continue
        if re.fullmatch(r"(?:dn|du|дн|ду)?\d+(?:[xх]\d+)?(?:гр|mm|мм)?", token):
            continue
        if len(token) >= 4 or re.fullmatch(r"[a-z]{3,}", token):
            out.append(token)
    return out


def _updated_row(row: Mapping[str, str], suggestion: Mapping[str, Any], fieldnames: Sequence[str]) -> Dict[str, str]:
    out = {field: row.get(field, "") for field in fieldnames}
    status = str(suggestion.get("suggested_status") or "")
    slug = str(suggestion.get("suggested_slug") or row.get("expected_slug") or "")
    jde = str(suggestion.get("suggested_jde") or "")
    if "status" in out:
        out["status"] = status
    if "expected_slug" in out:
        out["expected_slug"] = slug
    if "expected_jde" in out:
        out["expected_jde"] = jde if status in {"fixed", "other_slug"} else ""
    if "expected_top" in out and status in {"catalog_gap", "other_slug"}:
        out["expected_top"] = ""
    if "decision_note" in out:
        out["decision_note"] = (
            f"slug_close suggested={status} root_cause={suggestion.get('root_cause')} "
            f"confidence={suggestion.get('confidence')}"
        )
    return out


def _row_needle(path: str, root: str, fieldnames: Sequence[str], old: Mapping[str, str], new: Mapping[str, str]) -> Optional[Dict[str, Any]]:
    if all(str(old.get(field, "") or "") == str(new.get(field, "") or "") for field in fieldnames):
        return None
    old_line = str(old.get(RAW_LINE_KEY) or _tsv_line(fieldnames, old))
    new_line = _tsv_line(fieldnames, new)
    if old_line == new_line:
        return None
    return {
        "action": "replace",
        "target_file": _rel(root, path),
        "find_text": old_line,
        "replace_text": new_line,
    }


def _approved_conflicts(
    *,
    root: str,
    path: str,
    slug: str,
    suggestions: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not os.path.isfile(path):
        return [], []
    by_query = {str(s.get("query") or "").strip(): s for s in suggestions if s.get("query")}
    conflicts: List[Dict[str, Any]] = []
    needles: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw in handle.read().splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            cols = raw.split("\t")
            if len(cols) < 5:
                continue
            rec_id = cols[0]
            query = "\t".join(cols[1:-3]) if len(cols) > 5 else cols[1]
            status, row_slug, fragment = cols[-3], cols[-2], cols[-1]
            if _norm(row_slug) != slug:
                continue
            suggestion = by_query.get(query.strip())
            suggested_status = str((suggestion or {}).get("suggested_status") or "")
            if (
                not suggestion
                or not suggested_status
                or _approved_status_compatible(status, suggested_status, fragment)
            ):
                continue
            conflict = {
                "id": rec_id,
                "query": query,
                "approved_status": status,
                "suggested_status": suggested_status,
                "fragment": fragment,
                "root_cause": suggestion.get("root_cause"),
            }
            conflicts.append(conflict)
            if suggested_status == "catalog_gap" and status == "must_find":
                new_cols = list(cols)
                new_cols[-3] = "catalog_gap"
                new_cols[-1] = ""
                needles.append(
                    {
                        "action": "replace",
                        "target_file": _rel(root, path),
                        "find_text": raw,
                        "replace_text": "\t".join(new_cols),
                    }
                )
    return conflicts, needles


def _approved_obligations(*, root: str, path: str, slug: str) -> Dict[str, Dict[str, str]]:
    if not os.path.isfile(path):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw in handle.read().splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            cols = raw.split("\t")
            if len(cols) < 5:
                continue
            query = "\t".join(cols[1:-3]) if len(cols) > 5 else cols[1]
            status, row_slug, fragment = cols[-3], cols[-2], cols[-1]
            if _norm(row_slug) != slug:
                continue
            out[query.strip()] = {
                "status": status,
                "fragment": fragment,
            }
    return out


def _approved_status_compatible(
    approved_status: str,
    suggested_status: str,
    fragment: str = "",
) -> bool:
    approved = _norm(approved_status)
    suggested = _norm(suggested_status)
    if approved == suggested:
        return True
    # approved_contract uses must_find as the acceptance obligation; a live fixed
    # row satisfies that obligation and must not be reported as a conflict.
    if approved == "must_find" and suggested in {"accepted_feedback", "duplicate", "fixed"}:
        return True
    # must_find_brand_soft additionally accepts an OBSERVABLE brand fallback;
    # plain must_find does not — the owner opts in via the contract status.
    if approved == "must_find_brand_soft" and suggested in {
        "accepted_brand_fallback",
        "accepted_feedback",
        "duplicate",
        "fixed",
    }:
        return True
    # A catalog_gap row with no expected fragment/JDE is not a positive
    # acceptance obligation. If live search later returns the same slug, that is
    # useful evidence, but not enough to prove the client's intended top-1.
    return approved == "catalog_gap" and suggested == "fixed" and not str(fragment or "").strip()


def _expected_codes(row: Mapping[str, str]) -> set[str]:
    fields = [
        row.get("expected_jde") or "",
        row.get("expected_top") or "",
        row.get("user_comment") or "",
        _legacy_note_oracle(row),
    ]
    return {m.group(0) for text in fields for m in CODE_RE.finditer(text)}


def _expected_top(row: Mapping[str, str]) -> str:
    return str(row.get("expected_top") or _legacy_note_oracle(row) or "").strip()


def _legacy_note_oracle(row: Mapping[str, str]) -> str:
    """Old owner triage stores the acceptance oracle in notes."""
    note = str(row.get("notes") or "").strip()
    if not note:
        return ""
    status = canonical_status(row.get("status"))
    if status not in {"fixed", "must_find", "must_find_brand_soft"}:
        return ""
    if "actual_scope" not in row and _norm(row.get("source") or "") != "approved_contract":
        return ""
    lowered = note.lower()
    if lowered.startswith(("auto-owned", "catalog_gap:", "catalog gap:", "triaged")):
        return ""
    for prefix in ("top1:", "top1_contains:"):
        if lowered.startswith(prefix):
            return note.split(":", 1)[1].strip()
    return note


def _actual_slug(data: Mapping[str, Any]) -> str:
    # Top-level category_slug is the runtime answer. Legacy debug fields may still
    # expose old index aliases such as smes while the API returns smesitel.
    for key in ("category_slug", "resolved_slug", "category", "index_category_slug"):
        value = str(data.get(key) or "").strip()
        if value:
            return _norm(value)
    graph = _decision_graph(data)
    summary = graph.get("summary") if isinstance(graph, Mapping) else {}
    if isinstance(summary, Mapping):
        value = str(summary.get("category_slug") or "").strip()
        if value:
            return _norm(value)
    debug = data.get("debug") or {}
    if isinstance(debug, Mapping):
        for key in ("category_slug", "resolved_slug", "index_category_slug"):
            value = str(debug.get(key) or "").strip()
            if value:
                return _norm(value)
    return ""


def _item_jde(item: Mapping[str, Any]) -> str:
    for key in ("article", "jde_code", "code", "vendor_code"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _item_blob(item: Mapping[str, Any]) -> str:
    params = item.get("params") or {}
    parts = [
        item.get("name"),
        item.get("article"),
        item.get("jde_code"),
    ]
    if isinstance(params, Mapping):
        parts.extend(params.get(key) for key in ("Номер JDE", "Артикул", "Модель"))
    return _norm_text(" ".join(str(part or "") for part in parts))


def _decision_graph(data: Mapping[str, Any]) -> Mapping[str, Any]:
    debug = data.get("debug") or {}
    if isinstance(debug, Mapping) and isinstance(debug.get("decision_graph"), Mapping):
        return debug["decision_graph"]
    graph = data.get("decision_graph")
    return graph if isinstance(graph, Mapping) else {}


def _graph_evidence(graph: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    if not isinstance(graph, Mapping):
        return out
    summary = graph.get("summary")
    if isinstance(summary, Mapping):
        for key in ("category_slug", "total", "applied_filter_count", "diagnosis_status"):
            if key in summary:
                out.append(f"graph.{key}={summary[key]}")
    probes = graph.get("diagnostic_probes")
    if isinstance(probes, list) and probes:
        out.append(f"diagnostic_probes={len(probes)}")
    return out


def _live_summary(data: Mapping[str, Any], top: Mapping[str, Any]) -> Dict[str, Any]:
    graph = _decision_graph(data)
    summary = graph.get("summary") if isinstance(graph, Mapping) else {}
    return {
        "actual_slug": _actual_slug(data),
        "total": data.get("total"),
        "top_jde": _item_jde(top),
        "top_name": top.get("name") if isinstance(top, Mapping) else None,
        "search_method": data.get("search_method"),
        "ai_escalation_reason": data.get("ai_escalation_reason"),
        "graph_primary_issue": (summary or {}).get("primary_issue") if isinstance(summary, Mapping) else None,
    }


def _tsv_line(fieldnames: Sequence[str], row: Mapping[str, str]) -> str:
    return "\t".join(str(row.get(field, "") or "") for field in fieldnames)


def _agent_next(needles: Sequence[Mapping[str, Any]], conflicts: Sequence[Mapping[str, Any]]) -> str:
    if not needles and not conflicts:
        return "No triage updates proposed; run scoped conformance verify for the slug."
    parts = []
    if needles:
        parts.append("Review proposed_needles, then apply via apatch_remote_task_run/apatch_generate_batch.")
    if conflicts:
        parts.append("Resolve approved_conflicts before marking R13/R14 conformant.")
    return " ".join(parts)


def _abs(root: str, path: str) -> str:
    expanded = os.path.expanduser(path)
    return expanded if os.path.isabs(expanded) else os.path.join(root, expanded)


def _rel(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
