"""Repair map: route machine diagnoses to their designated edit surface.

The map is consumer data (``manifests/repair-map.json``, fallback
``.apatch/repair_map.json``): a finite list of rules keyed by the machine's
own diagnosis vocabulary — slug_close root causes, decision-graph primary
issues, feedback-lint finding ids. Each rule names the allowed edit surface
and the forbidden shortcuts, so "where does this fix go" is an answer, not a
guess. A diagnosis with no matching rule is itself a signal (cockpit raises
``repair_map_gap``): extend the map through review or file a platform RFC —
never improvise a new place.

Per-slug overrides (typed slug contracts) are a planned extension; today the
map is global.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

DEFAULT_PATHS = ("manifests/repair-map.json", os.path.join(".apatch", "repair_map.json"))
# "manual" marks documentation-only rows (triggered by humans, not diagnoses).
MATCH_KEYS = ("root_cause", "graph_primary_issue", "lint_finding", "manual")
RULE_FIELDS = ("id", "action", "edit_surfaces")


def load_repair_map(root: str, path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate the repair map. Never raises; errors become data."""
    root = os.path.abspath(os.path.expanduser(root or "."))
    candidates = [path] if path else list(DEFAULT_PATHS)
    found: Optional[str] = None
    for rel in candidates:
        abs_path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if os.path.isfile(abs_path):
            found = abs_path
            break
    if not found:
        return {"present": False, "path": None, "rules": [], "errors": []}

    rel_found = os.path.relpath(found, root)
    try:
        with open(found, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return {"present": True, "path": rel_found, "rules": [], "errors": [f"unreadable: {exc}"]}

    errors: List[str] = []
    rules_raw = data.get("rules") if isinstance(data, Mapping) else None
    if not isinstance(rules_raw, list) or not rules_raw:
        return {"present": True, "path": rel_found, "rules": [], "errors": ["rules must be a non-empty list"]}

    rules: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(rules_raw):
        label = f"rules[{idx}]"
        if not isinstance(raw, Mapping):
            errors.append(f"{label}: not an object")
            continue
        rule_id = str(raw.get("id") or "").strip()
        if not rule_id:
            errors.append(f"{label}: missing id")
            continue
        if rule_id in seen_ids:
            errors.append(f"{label}: duplicate id {rule_id!r}")
            continue
        seen_ids.add(rule_id)
        action = str(raw.get("action") or "").strip()
        if not action:
            errors.append(f"{rule_id}: missing action")
            continue
        surfaces = raw.get("edit_surfaces")
        if not isinstance(surfaces, list) or not all(isinstance(s, str) and s.strip() for s in surfaces):
            errors.append(f"{rule_id}: edit_surfaces must be a list of non-empty strings")
            continue
        match = raw.get("match") or {}
        if not isinstance(match, Mapping):
            errors.append(f"{rule_id}: match must be an object")
            continue
        unknown = sorted(set(match) - set(MATCH_KEYS))
        if unknown:
            errors.append(f"{rule_id}: unknown match keys {unknown}; allowed: {list(MATCH_KEYS)}")
            continue
        norm_match: Dict[str, List[str]] = {}
        bad_values = False
        for key, values in match.items():
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                errors.append(f"{rule_id}: match.{key} must be a list of non-empty strings")
                bad_values = True
                break
            norm_match[str(key)] = [v.strip() for v in values]
        if bad_values:
            continue
        rules.append(
            {
                "id": rule_id,
                "match": norm_match,
                "action": action,
                "edit_surfaces": [s.strip() for s in surfaces],
                "forbidden": [str(f) for f in (raw.get("forbidden") or []) if str(f).strip()],
                "contract_ref": str(raw.get("contract_ref") or ""),
            }
        )
    return {"present": True, "path": rel_found, "rules": rules, "errors": errors}


def route(repair_map: Mapping[str, Any], keys: Mapping[str, Optional[str]]) -> List[Dict[str, Any]]:
    """Match diagnosis keys against the map.

    A rule matches when any provided key value appears in the rule's match
    list for that key. Root-cause hits outrank graph-only hits; ties keep map
    order. ``manual`` rows never match automatically.
    """
    scored: List[tuple[int, int, Dict[str, Any]]] = []
    for order, rule in enumerate(repair_map.get("rules") or []):
        match = rule.get("match") or {}
        score = 0
        if keys.get("root_cause") and keys["root_cause"] in (match.get("root_cause") or []):
            score = max(score, 2)
        if keys.get("graph_primary_issue") and keys["graph_primary_issue"] in (match.get("graph_primary_issue") or []):
            score = max(score, 1)
        if keys.get("lint_finding") and keys["lint_finding"] in (match.get("lint_finding") or []):
            score = max(score, 2)
        if score:
            scored.append((-score, order, rule))
    scored.sort()
    return [
        {
            "rule_id": rule["id"],
            "action": rule["action"],
            "edit_surfaces": rule["edit_surfaces"],
            "forbidden": rule["forbidden"],
            "contract_ref": rule["contract_ref"],
        }
        for _neg, _order, rule in scored
    ]
