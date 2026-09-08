"""Slug intake cockpit: read-only context bundle for category/problem work.

The intent is deliberately narrow: collect the evidence a fresh agent needs before
touching code for a slug/category. It aggregates existing apatch surfaces
(specs, conformance, reality) and lightweight repository evidence, but it does not
mutate state and it does not mark anything green.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from apatch_search_workflows.feedback_status import CLOSED_FEEDBACK_STATUSES, canonical_status


DEFAULT_SCAN_ROOTS = ("docs/specs", "tests", "manifests", ".apatch")
DEFAULT_DICTIONARY_HINTS = (
    "atomic",
    "atomics",
    "atoms",
    "dictionary",
    "dictionaries",
    "synonym",
    "synonyms",
)
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "site",
}
TEXT_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".tsv",
}
# Closed/covered semantics come from the canonical vocabulary; legacy
# spellings (neighbor_slug) resolve through canonical_status().
CLOSED_FEEDBACK_TRIAGE_STATUSES = CLOSED_FEEDBACK_STATUSES
QUERY_FIRST_HINTS = (
    "query",
    "search",
    "live",
    "api",
    "items",
    "top",
    "feedback",
    "dislike",
    "complaint",
    "client",
)


def slug_intake_workspace(
    target_dir: str = ".",
    *,
    slug: str,
    aliases: Optional[Sequence[str]] = None,
    manifest_path: Optional[str] = None,
    live: bool = False,
    limit: int = 25,
) -> Dict[str, Any]:
    """Build a read-only slug cockpit DTO.

    ``live`` only affects conformance: when true, matching spec verifies may run.
    File scans are bounded and read-only.
    """
    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    raw_slug = (slug or "").strip()
    if not raw_slug:
        return {"ok": False, "error": "slug is required", "error_type": "SLUG_REQUIRED"}

    manifest = _load_manifest(root, manifest_path)
    all_aliases = _slug_aliases(raw_slug, aliases or (), manifest)
    specs = _matching_specs(root, all_aliases)
    spec_ids = [s["id"] for s in specs]
    reality = _matching_reality(root, all_aliases)
    conformance = _matching_conformance(root, spec_ids, live=live)
    evidence = _scan_evidence(root, all_aliases, manifest=manifest, limit=limit)
    hooks = _configured_hooks(manifest, raw_slug)
    query_first = _query_first_gate(specs, evidence, hooks)
    data_model = _data_model_gate(evidence, hooks)
    gaps = _gaps(specs, reality, conformance, query_first, data_model)
    spec_template = _spec_template(raw_slug, reality)
    agent_next = _agent_next(raw_slug, specs, query_first, data_model, gaps)

    return {
        "ok": True,
        "workspace": root,
        "slug": raw_slug,
        "slug_norm": _norm(raw_slug),
        "aliases": all_aliases,
        "live": bool(live),
        "manifest": manifest.get("path_info"),
        "specs": specs,
        "reality": reality,
        "conformance": conformance,
        "evidence": evidence,
        "hooks": hooks,
        "required_gates": {
            "query_first": query_first,
            "data_model": data_model,
            "reality_coverage": {
                "required": True,
                "passed": not reality.get("records")
                or (not reality.get("uncovered") and not reality.get("pending")),
                "uncovered": reality.get("uncovered") or [],
                "pending": reality.get("pending") or [],
            },
        },
        "gaps": gaps,
        "spec_template": spec_template,
        "agent_next": agent_next,
    }


def slug_intake_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    return enrich_tool_response(
        "apatch_slug_intake",
        slug_intake_workspace(root, **kwargs),
        target_dir=root,
    )


def _load_manifest(root: str, manifest_path: Optional[str]) -> Dict[str, Any]:
    candidates: List[str] = []
    if manifest_path:
        candidates.append(_abs(root, manifest_path))
    candidates.extend(
        [
            os.path.join(root, ".apatch", "slug_intake.json"),
            os.path.join(root, "manifests", "slug-intake.json"),
        ]
    )
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = dict(data)
                data["path_info"] = {"present": True, "path": _rel(root, path)}
                return data
            return {"path_info": {"present": True, "path": _rel(root, path), "error": "manifest is not an object"}}
        except (OSError, json.JSONDecodeError) as exc:
            return {"path_info": {"present": True, "path": _rel(root, path), "error": str(exc)}}
    return {"path_info": {"present": False}}


def _slug_aliases(raw_slug: str, aliases: Sequence[str], manifest: Mapping[str, Any]) -> List[str]:
    out: List[str] = [raw_slug]
    out.extend(str(a) for a in aliases if str(a).strip())
    configured = manifest.get("aliases")
    if isinstance(configured, Mapping):
        for key, vals in configured.items():
            keys = {str(key), _norm(str(key))}
            if raw_slug in keys or _norm(raw_slug) in keys:
                if isinstance(vals, str):
                    out.append(vals)
                elif isinstance(vals, Iterable):
                    out.extend(str(v) for v in vals if str(v).strip())
    return _unique([a.strip() for a in out if a and a.strip()])


def _matching_specs(root: str, aliases: Sequence[str]) -> List[Dict[str, Any]]:
    from apatch.spec import _ledger_entries, parse_spec_file, spec_status_from_entries

    specs: List[Dict[str, Any]] = []
    entries, _ledger_active = _ledger_entries(root)
    for path in _spec_paths(root):
        try:
            parsed = parse_spec_file(path)
        except Exception:  # noqa: BLE001 - broken specs are surfaced as skipped evidence
            continue
        requirement_titles = " ".join(req.title or "" for req in parsed.requirements)
        haystacks = [parsed.id, parsed.title or "", os.path.basename(path), requirement_titles]
        if not any(_matches(h, aliases) for h in haystacks):
            continue
        status = spec_status_from_entries(parsed, entries)
        requirements = []
        status_reqs = {
            r.get("id"): r for r in (status.get("requirements") or []) if isinstance(r, Mapping)
        }
        for req in parsed.requirements:
            row = status_reqs.get(req.id) or {}
            requirements.append(
                {
                    "id": req.id,
                    "title": req.title,
                    "state": row.get("state") or "unknown",
                    "verify": req.verify,
                    "discharges": list(req.discharges or ()),
                    "line": req.line,
                }
            )
        specs.append(
            {
                "id": parsed.id,
                "title": parsed.title,
                "source_path": _rel(root, path),
                "summary": status.get("summary") if status.get("ok") else None,
                "done": bool(status.get("done")),
                "status_ok": bool(status.get("ok")),
                "status_error": status.get("error"),
                "requirements": requirements,
            }
        )
    return sorted(specs, key=lambda s: str(s.get("id") or ""))


def _spec_paths(root: str) -> List[str]:
    patterns = [
        os.path.join(root, "docs", "specs", "SPEC-*.md"),
        os.path.join(root, "specs", "SPEC-*.md"),
        os.path.join(root, "SPEC-*.md"),
    ]
    paths: List[str] = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    return sorted(_unique(paths))


def _matching_reality(root: str, aliases: Sequence[str]) -> Dict[str, Any]:
    from apatch.reality import load_reality_records
    from apatch.spec import _ledger_entries, parse_spec_file, spec_status_from_entries

    records = []
    seen_ids: set[str] = set()
    for rec in load_reality_records(root):
        text = " ".join(str(rec.get(k) or "") for k in ("id", "summary", "source", "kind"))
        if not _matches(text, aliases):
            continue
        rid = str(rec.get("id") or "")
        if not rid:
            continue
        seen_ids.add(rid)
        row = {
            "id": rid,
            "summary": rec.get("summary"),
            "source": rec.get("source"),
            "kind": rec.get("kind"),
            "status": rec.get("status"),
            "coverage": "ignored" if rec.get("status") == "closed_wontfix" else "uncovered",
        }
        records.append(row)
    for row in _feedback_triage_reality_records(root, aliases):
        rid = str(row.get("id") or "")
        if not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)
        records.append(row)
    if not records:
        return {
            "total_matching": 0,
            "records": [],
            "covered": [],
            "pending": [],
            "uncovered": [],
        }

    rec_ids = {
        str(r["id"])
        for r in records
        if r.get("coverage") not in {"covered", "ignored"}
    }
    discharge_map: Dict[str, List[str]] = {}
    attested: set[str] = set()
    entries, _ledger_active = _ledger_entries(root)
    for path in _spec_paths(root):
        try:
            parsed = parse_spec_file(path)
        except Exception:  # noqa: BLE001
            continue
        reqs = [req for req in parsed.requirements if set(req.discharges or ()).intersection(rec_ids)]
        if not reqs:
            continue
        status = spec_status_from_entries(parsed, entries)
        status_reqs = {
            r.get("id"): r for r in (status.get("requirements") or []) if isinstance(r, Mapping)
        }
        for req in reqs:
            key = f"{parsed.id}#{req.id}"
            if (status_reqs.get(req.id) or {}).get("state") == "attested":
                attested.add(key)
            for rec_id in req.discharges or ():
                if rec_id in rec_ids:
                    discharge_map.setdefault(rec_id, []).append(key)

    for row in records:
        rid = row["id"]
        if row.get("coverage") in {"covered", "ignored"}:
            continue
        claimers = discharge_map.get(rid) or []
        if not claimers:
            row["coverage"] = "uncovered"
        elif any(c in attested for c in claimers):
            row["coverage"] = "covered"
        else:
            row["coverage"] = "pending"
    return {
        "total_matching": len(records),
        "records": records,
        "covered": [r["id"] for r in records if r["coverage"] == "covered"],
        "pending": [r["id"] for r in records if r["coverage"] == "pending"],
        "uncovered": [r["id"] for r in records if r["coverage"] == "uncovered"],
    }


def _feedback_triage_reality_records(root: str, aliases: Sequence[str]) -> List[Dict[str, Any]]:
    paths = sorted(glob.glob(os.path.join(root, "tests", "regressions", "*_feedback_triage.tsv")))
    records: List[Dict[str, Any]] = []
    for path in paths:
        rel = _rel(root, path)
        slug_hint = os.path.basename(path).removesuffix("_feedback_triage.tsv")
        if not (_matches(slug_hint, aliases) or _matches(rel, aliases)):
            continue
        for idx, row in enumerate(_read_tsv_dicts(path), start=1):
            rid = str(row.get("triage_id") or row.get("id") or "").strip()
            if not rid:
                rid = "{}#{}".format(os.path.basename(path), idx)
            status = str(row.get("status") or "").strip()
            query = str(row.get("query") or "").strip()
            coverage = "covered" if canonical_status(status) in CLOSED_FEEDBACK_TRIAGE_STATUSES else "uncovered"
            records.append(
                {
                    "id": rid,
                    "summary": query or row.get("user_comment") or rid,
                    "source": rel,
                    "kind": "feedback_triage",
                    "status": status,
                    "coverage": coverage,
                    "query": query,
                    "expected_slug": row.get("expected_slug") or "",
                    "expected_jde": row.get("expected_jde") or "",
                    "expected_top": row.get("expected_top") or "",
                    "decision_note": row.get("decision_note") or "",
                }
            )
    return records


def _read_tsv_dicts(path: str) -> List[Dict[str, str]]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return [
                {str(k): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(handle, delimiter="\t")
                if any((v or "").strip() for v in row.values())
            ]
    except OSError:
        return []


def _matching_conformance(root: str, spec_ids: Sequence[str], *, live: bool) -> Dict[str, Any]:
    if not spec_ids:
        return {"ok": True, "enabled": False, "skipped": "no matching specs"}
    try:
        from apatch.conformance import (
            _BUCKETS,
            classify_attestation,
            conformance_status,
            load_conformance_config,
        )
        from apatch.spec import _ledger_entries, parse_spec_file, spec_status_from_entries

        if live:
            return conformance_status(root, specs=list(spec_ids), live=True)
        cfg = load_conformance_config(root)
        if not cfg.get("enabled"):
            return {
                "ok": True,
                "enabled": False,
                "live": False,
                "skipped": cfg.get("error") or "conformance disabled (no .apatch/conformance.json)",
            }
        buckets: Dict[str, int] = {b: 0 for b in _BUCKETS}
        per_spec: List[Dict[str, Any]] = []
        entries, _ledger_active = _ledger_entries(root)
        parsed_by_id = {}
        for path in _spec_paths(root):
            try:
                parsed = parse_spec_file(path)
            except Exception:  # noqa: BLE001
                continue
            parsed_by_id[parsed.id] = parsed
        for sid in spec_ids:
            parsed = parsed_by_id.get(sid)
            st = spec_status_from_entries(parsed, entries) if parsed else {"ok": False, "summary": {}}
            bucket = classify_attestation(st.get("summary") or {}) if st.get("ok") else "unproven"
            buckets[bucket] = buckets.get(bucket, 0) + 1
            per_spec.append({"spec": sid, "conformance": bucket, "summary": st.get("summary")})
        return {
            "ok": True,
            "enabled": True,
            "live": False,
            "verified_live": 0,
            "note": "slug_intake live=false never runs verify, even if conformance config live_verify=true.",
            "gated": len(spec_ids),
            "buckets": buckets,
            "per_spec": per_spec,
        }
    except Exception as exc:  # noqa: BLE001 - intake must not crash on conformance rot
        return {
            "ok": False,
            "enabled": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _scan_evidence(
    root: str,
    aliases: Sequence[str],
    *,
    manifest: Mapping[str, Any],
    limit: int,
) -> Dict[str, Any]:
    limit = max(1, int(limit or 25))
    scan_roots = _string_list(manifest.get("scan_roots")) or list(DEFAULT_SCAN_ROOTS)
    evidence = _scan_roots(root, scan_roots, aliases, limit=limit)
    dictionary = _dictionary_evidence(root, aliases, manifest=manifest, limit=limit)
    return {
        "files": evidence,
        "file_count": len(evidence),
        "dictionary_files": dictionary,
        "dictionary_file_count": len(dictionary),
    }


def _scan_roots(root: str, roots: Sequence[str], aliases: Sequence[str], *, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for rel_root in roots:
        base = _abs(root, rel_root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                if path in seen:
                    continue
                seen.add(path)
                row = _file_match(root, path, aliases)
                if row:
                    out.append(row)
                    if len(out) >= limit:
                        return out
    return out


def _dictionary_evidence(
    root: str,
    aliases: Sequence[str],
    *,
    manifest: Mapping[str, Any],
    limit: int,
) -> List[Dict[str, Any]]:
    configured = _string_list(manifest.get("dictionary_roots"))
    out: List[Dict[str, Any]] = []
    roots = configured or _default_dictionary_roots(root)
    seen = set()
    for rel_root in roots:
        base = _abs(root, rel_root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                if path in seen:
                    continue
                seen.add(path)
                row = _file_match(root, path, aliases)
                if row:
                    out.append(row)
                    if len(out) >= limit:
                        return out
    return out


def _default_dictionary_roots(root: str, *, max_depth: int = 5, max_dirs: int = 2000) -> List[str]:
    """Find likely dictionary roots without walking the whole workspace deeply."""
    found: List[str] = []
    visited = 0
    root = os.path.abspath(root)
    for dirpath, dirnames, _filenames in os.walk(root):
        visited += 1
        if visited > max_dirs:
            break
        rel = _rel(root, dirpath)
        depth = 0 if rel == "." else len(rel.split(os.sep))
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if depth >= max_depth:
            dirnames[:] = []
        parts = [p.lower() for p in ([] if rel == "." else rel.split(os.sep))]
        if any(hint in part for part in parts for hint in DEFAULT_DICTIONARY_HINTS):
            found.append(rel)
            dirnames[:] = []
    return _unique(found)


def _file_match(root: str, path: str, aliases: Sequence[str]) -> Optional[Dict[str, Any]]:
    rel = _rel(root, path)
    name_match = _matches(rel, aliases)
    content_match = False
    if not name_match and _is_text_file(path):
        content_match = _matches(_read_text(path, limit=200_000), aliases)
    if not name_match and not content_match:
        return None
    return {
        "path": rel,
        "matched_by": "path" if name_match else "content",
    }


def _configured_hooks(manifest: Mapping[str, Any], slug: str) -> Dict[str, Any]:
    raw = manifest.get("hooks") or manifest.get("commands") or {}
    if not isinstance(raw, Mapping):
        return {"present": False, "commands": {}}
    commands: Dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            commands[str(key)] = _format_hook(value, slug)
    return {
        "present": bool(commands),
        "commands": commands,
        "note": "Commands are reported only; slug_intake does not execute hooks.",
    }


def _format_hook(value: str, slug: str) -> str:
    return value.replace("{slug}", slug).replace("{slug_norm}", _norm(slug))


def _query_first_gate(
    specs: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Any],
    hooks: Mapping[str, Any],
) -> Dict[str, Any]:
    found: List[Dict[str, Any]] = []
    for spec in specs:
        for req in spec.get("requirements") or []:
            text = " ".join(str(req.get(k) or "") for k in ("title", "verify"))
            if _has_query_hint(text):
                found.append(
                    {
                        "spec": spec.get("id"),
                        "requirement": req.get("id"),
                        "title": req.get("title"),
                        "verify": req.get("verify"),
                    }
                )
    for row in evidence.get("files") or []:
        if _has_query_hint(str(row.get("path") or "")):
            found.append({"file": row.get("path"), "matched_by": row.get("matched_by")})
    commands = hooks.get("commands") if isinstance(hooks.get("commands"), Mapping) else {}
    for name, cmd in commands.items():
        if _has_query_hint(" ".join([str(name), str(cmd)])):
            found.append({"hook": name, "command": cmd})
    return {
        "required": True,
        "present": bool(found),
        "evidence": found[:10],
        "rule": "Green requires an end-to-end client query/search verify, not only parser/block checks.",
    }


def _data_model_gate(evidence: Mapping[str, Any], hooks: Mapping[str, Any]) -> Dict[str, Any]:
    commands = hooks.get("commands") if isinstance(hooks.get("commands"), Mapping) else {}
    data_hooks = {
        k: v
        for k, v in commands.items()
        if any(token in k.lower() or token in v.lower() for token in ("data", "catalog", "enrich", "dict", "synonym"))
    }
    files = list(evidence.get("dictionary_files") or [])
    return {
        "required": True,
        "present": bool(files or data_hooks),
        "dictionary_files": files[:10],
        "hooks": data_hooks,
        "rule": "Before code changes, prove where slug type/subtype/synonym data lives or add an audit hook.",
    }


def _gaps(
    specs: Sequence[Mapping[str, Any]],
    reality: Mapping[str, Any],
    conformance: Mapping[str, Any],
    query_first: Mapping[str, Any],
    data_model: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    if not specs:
        gaps.append(
            {
                "id": "missing_spec",
                "severity": "high",
                "message": "No SPEC matched this slug.",
                "recommended_action": "Create docs/specs/SPEC-<SLUG>-1.md from spec_template, then apatch_spec_lint.",
            }
        )
    if not reality.get("records"):
        gaps.append(
            {
                "id": "missing_reality",
                "severity": "medium",
                "message": "No matching reality/feedback records were found.",
                "recommended_action": "Import user complaints as apatch_reality records before attesting the slug spec.",
            }
        )
    if reality.get("uncovered") or reality.get("pending"):
        gaps.append(
            {
                "id": "reality_not_closed",
                "severity": "high",
                "message": "Matching reality records are not fully covered by attested requirements.",
                "uncovered": reality.get("uncovered") or [],
                "pending": reality.get("pending") or [],
                "recommended_action": "Add discharges: REC-* to query-first requirements and attest only after green verify.",
            }
        )
    if not query_first.get("present"):
        gaps.append(
            {
                "id": "missing_query_first_verify",
                "severity": "high",
                "message": "No query-first/end-to-end search verify was detected.",
                "recommended_action": "Add an Rk whose verify sends the original client query to the runtime/API and checks expected item(s).",
            }
        )
    if not data_model.get("present"):
        gaps.append(
            {
                "id": "missing_data_model_audit",
                "severity": "medium",
                "message": "No dictionary/data-model evidence or hook was detected.",
                "recommended_action": "Add a data audit hook in manifests/slug-intake.json or a spec R0 verify for catalog/enrich coverage.",
            }
        )
    if conformance.get("enabled") is False:
        gaps.append(
            {
                "id": "conformance_disabled_or_unscoped",
                "severity": "medium",
                "message": conformance.get("skipped") or "Conformance did not run for matching specs.",
                "recommended_action": "Add the slug spec to .apatch/conformance.json after its query-first verify exists.",
            }
        )
    return gaps


def _spec_template(slug: str, reality: Mapping[str, Any]) -> Dict[str, Any]:
    spec_id = "SPEC-{}-1".format(re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-").upper() or "SLUG")
    recs = ", ".join(r["id"] for r in reality.get("records") or [] if r.get("id")) or "REC-..."
    content = f"""# {spec_id}

> **apatch artifact:** `spec:{spec_id}`

## R0 Data map (verify: <catalog/data audit command>)

Map slug semantics to real catalog fields/params before runtime changes.

## R1 Atomic dictionaries and synonyms (verify: <dictionary audit command>)

Prove abbreviations, aliases, and user spellings are present for the slug.

## R2 Query-first live search gate (verify: <client query API test command>)

The original client query must return the expected product in the accepted rank window.

discharges: {recs}

## R3 Excluding parameters (verify: <negative/strict-parameter test command>)

Strict technical parameters from the result must be constrained by the query or justified by the contract.

## R4 Feedback corpus closure (verify: <all feedback/dislikes test command>)

Every matching feedback/reality record is either covered by a green query-first gate or explicitly wontfix.

## R5 Conformance enrollment (verify: <conformance/live command>)

The slug spec is included in the standing conformance contract.
"""
    return {
        "spec": spec_id,
        "path": f"docs/specs/{spec_id}.md",
        "content": content,
    }


def _agent_next(
    slug: str,
    specs: Sequence[Mapping[str, Any]],
    query_first: Mapping[str, Any],
    data_model: Mapping[str, Any],
    gaps: Sequence[Mapping[str, Any]],
) -> str:
    if not specs:
        return f"Create a slug spec from spec_template, then apatch_spec_lint(spec='SPEC-{slug.upper()}-1')."
    if not data_model.get("present"):
        return "Fill R0/data audit first; do not change ranking/parser logic before real catalog fields are mapped."
    if not query_first.get("present"):
        return "Add query-first live search verify before implementing or attesting more parser/block requirements."
    if gaps:
        return "Close the listed gaps, then run apatch_spec_run for the matching slug spec(s)."
    return "Run apatch_spec_run for the matching slug spec(s), then conformance status with live=true."


def _has_query_hint(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in QUERY_FIRST_HINTS)


def _matches(text: str, aliases: Sequence[str]) -> bool:
    low = (text or "").lower().replace("ё", "е")
    token_class = "0-9a-zа-я"
    for alias in aliases:
        parts = re.findall(f"[{token_class}]+", str(alias).lower().replace("ё", "е"))
        if not parts:
            continue
        body = f"[^{token_class}]+".join(re.escape(part) for part in parts)
        if re.search(f"(?<![{token_class}]){body}(?![{token_class}])", low):
            return True
    return False


def _norm(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", "", text)


def _read_text(path: str, *, limit: int) -> str:
    try:
        if os.path.getsize(path) > limit:
            return ""
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _is_text_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in TEXT_EXTENSIONS


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in value if str(v).strip()]
    return []


def _unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _abs(root: str, path: str) -> str:
    raw = os.path.expanduser(str(path))
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    return os.path.abspath(os.path.join(root, raw))


def _rel(root: str, path: str) -> str:
    try:
        return os.path.relpath(os.path.abspath(path), root)
    except ValueError:
        return os.path.abspath(path)
