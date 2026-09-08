"""Neutral exact SPEC ownership resolution for typed workspace contracts."""

from __future__ import annotations

import glob
import os
import re
from typing import Any, Dict, List, Optional, Tuple

CONTRACT_DIR_REL = os.path.join("docs", "specs", "slug_contracts")
_MAX_CANDIDATES = 15


# ── resolve helpers ──────────────────────────────────────────────────────────


def _slug_spec_token(slug_n: str) -> str:
    """Canonical spec-id token for a slug: uppercased, non-alnum → hyphen."""
    return re.sub(r"[^A-Za-z0-9]+", "-", slug_n).strip("-").upper() or "SLUG"


def _resolve_spec_id(
    root: str,
    slug_n: str,
    explicit: Optional[str],
    contract_data: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Exact-ownership resolution: (spec_id, via, error). Never fuzzy-matches."""
    token = _slug_spec_token(slug_n)
    if explicit:
        sid = str(explicit).strip()
        if _spec_loads(root, sid):
            return sid, "explicit spec param", None
        return None, None, _resolve_failure(root, slug_n, token, f"spec {sid!r} not found")

    generation = (contract_data or {}).get("spec_generation")
    contract_sid = str((generation or {}).get("spec_id") or "").strip() if isinstance(generation, dict) else ""
    if contract_sid:
        if _spec_loads(root, contract_sid):
            return contract_sid, "contract yaml spec_generation.spec_id", None
        return None, None, _resolve_failure(
            root, slug_n, token,
            f"contract yaml owns spec {contract_sid!r} but it does not load")

    family = re.compile(r"SPEC-{}-\d+".format(re.escape(token)))
    contract_entries = _conformance_contract_entries(root)
    owned = sorted(e for e in contract_entries if family.fullmatch(e))
    if len(owned) == 1:
        if _spec_loads(root, owned[0]):
            return owned[0], "conformance contract entry", None
        return None, None, _resolve_failure(
            root, slug_n, token,
            f"conformance contract owns spec {owned[0]!r} but it does not load")
    if len(owned) > 1:
        err = _resolve_failure(root, slug_n, token, "ambiguous ownership in the conformance contract")
        err["candidates"] = owned
        return None, None, err

    default_sid = f"SPEC-{token}-1"
    if os.path.isfile(os.path.join(root, "docs", "specs", f"{default_sid}.md")):
        return default_sid, "exact default docs/specs id", None
    return None, None, _resolve_failure(root, slug_n, token, "no spec owns this slug")


def _resolve_failure(root: str, slug_n: str, token: str, reason: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "stage": "resolve",
        "error": f"cannot resolve spec for slug {slug_n!r}: {reason}",
        "hint": (
            "Pass spec= explicitly, add spec_generation.spec_id to "
            f"{CONTRACT_DIR_REL}/{slug_n}.yaml, or create docs/specs/SPEC-{token}-1.md. "
            "Exact ownership only — candidates are listed, never auto-picked."
        ),
        "candidates": _candidate_specs(root, token),
    }


def _candidate_specs(root: str, token: str) -> List[str]:
    ids = set(_conformance_contract_entries(root))
    for path in glob.glob(os.path.join(root, "docs", "specs", "SPEC-*.md")):
        ids.add(os.path.basename(path)[:-3])
    ids = {i for i in ids if "TEMPLATE" not in i.upper()}
    family = re.compile(r"SPEC-{}-\d+".format(re.escape(token)))
    exact = sorted(i for i in ids if family.fullmatch(i))
    return exact or sorted(ids)[:_MAX_CANDIDATES]


def _conformance_contract_entries(root: str) -> List[str]:
    from apatch.conformance import load_conformance_config

    cfg = load_conformance_config(root)
    contract = cfg.get("contract") or {}
    return [str(s) for s in (contract.get("specs") or []) if s]


def _spec_loads(root: str, spec_id: str) -> bool:
    from apatch.spec import _load_spec

    try:
        _load_spec(root, spec=spec_id)
        return True
    except (FileNotFoundError, ValueError):
        return False


def _load_contract_yaml(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(parsed mapping or None, error string or None). Absent file → (None, None)."""
    if not os.path.isfile(path):
        return None, None
    try:
        import yaml
    except ImportError:
        return None, "PyYAML is not installed but a slug contract yaml exists"
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        return None, f"contract yaml does not parse: {exc}"
    return (data if isinstance(data, dict) else {}), None
