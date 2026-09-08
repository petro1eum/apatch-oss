"""RFP→SPEC traceability coverage (RFP-023 / SPEC-RFP-COVERAGE-1)."""

from __future__ import annotations

import glob
import os
import re
from typing import Any, Dict, List, Optional, Tuple

_RFP_ID_TOKEN = re.compile(r"^(RFP-\d+)$")
_ACCEPTANCE_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_SPEC_ID_IN_WAIVER_RE = re.compile(r"\b(SPEC-[A-Z0-9-]+)\b")


def _strip_fenced_blocks(text: str) -> str:
    out: List[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _section_blocks(text: str) -> List[Tuple[str, int, int]]:
    """Return [(heading_lower, start, end), ...] excluding fenced code."""
    clean = _strip_fenced_blocks(text)
    sections: List[Tuple[str, int, int]] = []
    matches = list(_SECTION_RE.finditer(clean))
    for i, sm in enumerate(matches):
        start = sm.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(clean)
        sections.append((sm.group(1).strip().lower(), start, end))
    return sections


def _split_table_row(line: str) -> List[str]:
    line = line.strip()
    if not line.startswith("|"):
        return []
    parts = [p.strip() for p in line.strip("|").split("|")]
    return parts


def _is_separator_row(cells: List[str]) -> bool:
    if not cells:
        return False
    return all(re.match(r"^:?-+:?$", c.replace(" ", "")) for c in cells if c)


def _parse_table_after_heading(text: str, heading_contains: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """Return table under the last ## heading whose title contains ``heading_contains``."""
    clean = _strip_fenced_blocks(text)
    block = ""
    for title, start, end in _section_blocks(text):
        if heading_contains in title:
            block = clean[start:end]
    if not block:
        return [], []

    headers: List[str] = []
    rows: List[Dict[str, str]] = []
    for line in block.splitlines():
        cells = _split_table_row(line)
        if not cells:
            continue
        if _is_separator_row(cells):
            continue
        if not headers:
            headers = [c.lower() for c in cells]
            continue
        row: Dict[str, str] = {}
        for i, h in enumerate(headers):
            if i < len(cells):
                row[h] = cells[i]
        rows.append(row)
    return headers, rows


def parse_rfp_id_from_h1(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            token = line[2:].split(None, 1)[0]
            if _RFP_ID_TOKEN.match(token):
                return token
            return None
    return None


def parse_rfp_acceptance(text: str) -> Dict[str, Any]:
    headers, rows = _parse_table_after_heading(text, "acceptance")
    if not headers:
        return {"ok": False, "error": "missing ## Acceptance table", "rows": []}
    id_key = next((h for h in headers if h in ("id", "ids")), None)
    level_key = next((h for h in headers if h == "level"), None)
    crit_key = next((h for h in headers if h.startswith("criter")), None)
    if not id_key:
        return {"ok": False, "error": "Acceptance table missing Id column", "rows": []}
    parsed: List[Dict[str, str]] = []
    for row in rows:
        rid = (row.get(id_key) or "").strip()
        if not rid or rid.lower() == "id":
            continue
        level = (row.get(level_key) or "MUST").strip().upper() if level_key else "MUST"
        parsed.append(
            {
                "id": rid,
                "criterion": (row.get(crit_key) or "").strip() if crit_key else "",
                "level": level or "MUST",
            }
        )
    return {"ok": True, "rows": parsed}


def parse_spec_traceability(text: str) -> Dict[str, Any]:
    headers, rows = _parse_table_after_heading(text, "rfp traceability")
    if not headers:
        return {"ok": False, "error": "missing ## RFP traceability table", "map": {}}
    id_key = next((h for h in headers if "rfp" in h and "id" in h), None) or next(
        (h for h in headers if h == "id"), None
    )
    rk_key = next((h for h in headers if "rk" in h or h == "spec rk"), None)
    disp_key = next((h for h in headers if "disposition" in h), None)
    if not id_key:
        return {"ok": False, "error": "traceability table missing RFP id column", "map": {}}
    mapping: Dict[str, Dict[str, str]] = {}
    for row in rows:
        rid = (row.get(id_key) or "").strip()
        if not rid:
            continue
        mapping[rid] = {
            "spec_rk": (row.get(rk_key) or "").strip() if rk_key else "",
            "disposition": (row.get(disp_key) or "").strip() if disp_key else "",
        }
    return {"ok": True, "map": mapping}


def spec_has_rfp_traceability(text: str) -> bool:
    clean = _strip_fenced_blocks(text).lower()
    return bool(re.search(r"^##\s+.*rfp traceability", clean, re.MULTILINE))


def infer_rfp_id_from_spec(text: str) -> Optional[str]:
    for m in re.finditer(r"\b(RFP-\d+)\b", text):
        return m.group(1)
    return None


def sibling_spec_waiver_target(disposition: str) -> Optional[str]:
    """Extract SPEC id from ``waiver: implemented in SPEC-…`` (multi-spec RFP)."""
    disp = (disposition or "").strip()
    if not disp.lower().startswith("waiver:"):
        return None
    m = _SPEC_ID_IN_WAIVER_RE.search(disp)
    return m.group(1) if m else None


def parse_spec_requirement_ids(spec_text: str, spec_id: Optional[str] = None) -> set[str]:
    from apatch.spec import parse_spec

    try:
        parsed = parse_spec(spec_text, spec_id=spec_id or "")
    except (ValueError, TypeError):
        return set()
    return {r.id for r in parsed.requirements}


def _split_specs_csv(specs: Optional[str] = None, specs_list: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    if specs_list:
        out.extend(str(s).strip() for s in specs_list if str(s).strip())
    if specs:
        out.extend(p.strip() for p in specs.split(",") if p.strip())
    seen: set[str] = set()
    ordered: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def resolve_rfp_path(target_dir: str, rfp_id: str) -> Optional[str]:
    root = os.path.abspath(target_dir)
    patterns = [
        os.path.join(root, "docs", f"{rfp_id}-*.md"),
        os.path.join(root, "docs", f"{rfp_id}.md"),
        os.path.join(root, "tests", "fixtures", "rfp_coverage", f"{rfp_id}.md"),
    ]
    hits: List[str] = []
    for pat in patterns:
        hits.extend(sorted(glob.glob(pat)))
    hits = [h for h in hits if os.path.isfile(h)]
    if not hits:
        return None
    if len(hits) > 1:
        docs_hits = [h for h in hits if h.startswith(os.path.join(root, "docs") + os.sep)]
        if len(docs_hits) == 1:
            return docs_hits[0]
    return hits[0]


def resolve_rfp_input_path(target_dir: str, rfp: str) -> Optional[str]:
    """Resolve an RFP id or an explicit absolute/workspace-relative path."""
    root = os.path.abspath(target_dir)
    value = os.path.expanduser(str(rfp or "").strip())
    if not value:
        return None
    candidate = value if os.path.isabs(value) else os.path.join(root, value)
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)
    return resolve_rfp_path(root, value)


def _load_spec_path(target_dir: str, spec: str, spec_path: Optional[str] = None) -> Tuple[Optional[str], str]:
    if spec_path and os.path.isfile(spec_path):
        with open(spec_path, encoding="utf-8") as f:
            return spec_path, f.read()
    root = os.path.abspath(target_dir)
    for rel in (
        os.path.join("docs", "specs", f"{spec}.md"),
        os.path.join("tests", "fixtures", "rfp_coverage", f"{spec}.md"),
    ):
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return path, f.read()
    return None, ""


def rfp_lint(text: str, *, rfp_id: Optional[str] = None) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    parsed_id = parse_rfp_id_from_h1(text)
    if rfp_id and parsed_id and parsed_id != rfp_id:
        errors.append(
            {
                "code": "id_mismatch",
                "message": f"H1 id {parsed_id!r} != requested {rfp_id!r}",
            }
        )
    acc = parse_rfp_acceptance(text)
    if not acc.get("ok"):
        errors.append({"code": "missing_acceptance", "message": acc.get("error", "no acceptance")})
        rows: List[Dict[str, str]] = []
    else:
        rows = acc.get("rows") or []
    if not rows and acc.get("ok"):
        errors.append({"code": "empty_acceptance", "message": "Acceptance table has no data rows"})
    for row in rows:
        rid = row.get("id", "")
        if not _ACCEPTANCE_ID.match(rid):
            warnings.append(
                {
                    "code": "unstable_id",
                    "message": f"acceptance id {rid!r} — prefer P1-A / R23-C style tokens",
                    "id": rid,
                }
            )
        lvl = (row.get("level") or "MUST").upper()
        if lvl not in ("MUST", "MAY", "SHOULD"):
            warnings.append(
                {"code": "unknown_level", "message": f"{rid}: level {lvl!r} — use MUST|MAY|SHOULD", "id": rid}
            )
    effective_id = rfp_id or parsed_id
    return {
        "ok": True,
        "rfp": effective_id,
        "passed": not errors,
        "acceptance_count": len(rows),
        "errors": errors,
        "warnings": warnings,
        "counts": {"errors": len(errors), "warnings": len(warnings)},
    }


def _evaluate_traceability(
    acc_rows: List[Dict[str, str]],
    mapping: Dict[str, Dict[str, str]],
    *,
    spec_req_ids: set[str],
) -> Dict[str, Any]:
    covered: List[str] = []
    waivers: List[str] = []
    gaps: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    unknown_trace_ids: List[str] = []
    acc_ids = {r["id"] for r in acc_rows}

    for rid in mapping:
        if rid not in acc_ids:
            unknown_trace_ids.append(rid)

    for row in acc_rows:
        rid = row["id"]
        level = (row.get("level") or "MUST").upper()
        entry = mapping.get(rid)
        if not entry:
            if level == "MUST":
                gaps.append({"id": rid, "level": level, "reason": "no traceability row"})
            continue
        disp = (entry.get("disposition") or "").strip()
        disp_lower = disp.lower()
        rk = (entry.get("spec_rk") or "").strip()
        if disp_lower.startswith("waiver:"):
            waivers.append(rid)
            continue
        if rk and rk not in ("—", "-", ""):
            if spec_req_ids and rk not in spec_req_ids:
                errors.append(
                    {
                        "code": "unknown_spec_rk",
                        "message": f"{rid}: SPEC Rk {rk!r} not found in document",
                        "id": rid,
                        "spec_rk": rk,
                    }
                )
                continue
            covered.append(rid)
            continue
        if disp_lower == "covered" and rk:
            if spec_req_ids and rk not in spec_req_ids:
                errors.append(
                    {
                        "code": "unknown_spec_rk",
                        "message": f"{rid}: SPEC Rk {rk!r} not found in document",
                        "id": rid,
                        "spec_rk": rk,
                    }
                )
                continue
            covered.append(rid)
            continue
        if level == "MUST":
            gaps.append({"id": rid, "level": level, "reason": "not covered and not waived"})

    warnings: List[Dict[str, Any]] = []
    for rid in unknown_trace_ids:
        warnings.append(
            {
                "code": "unknown_rfp_id",
                "message": f"traceability row {rid!r} not in RFP Acceptance table",
                "id": rid,
            }
        )

    return {
        "covered": covered,
        "waivers": waivers,
        "gaps": gaps,
        "errors": errors,
        "warnings": warnings,
    }


def rfp_spec_coverage(
    rfp_text: str,
    spec_text: str,
    *,
    rfp_id: Optional[str] = None,
    spec_id: Optional[str] = None,
) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    lint = rfp_lint(rfp_text, rfp_id=rfp_id)
    if not lint.get("passed"):
        return {
            "ok": False,
            "passed": False,
            "error": "rfp lint failed",
            "rfp_lint": lint,
            "errors": lint.get("errors") or [],
        }
    acc_rows = parse_rfp_acceptance(rfp_text).get("rows") or []
    tr = parse_spec_traceability(spec_text)
    if not tr.get("ok"):
        errors.append({"code": "missing_traceability", "message": tr.get("error", "no traceability")})
        mapping: Dict[str, Dict[str, str]] = {}
    else:
        mapping = tr.get("map") or {}

    spec_req_ids = parse_spec_requirement_ids(spec_text, spec_id)
    ev = _evaluate_traceability(acc_rows, mapping, spec_req_ids=spec_req_ids)
    errors.extend(ev["errors"])
    warnings.extend(ev["warnings"])

    effective_rfp = rfp_id or lint.get("rfp")
    gaps = ev["gaps"]
    return {
        "ok": True,
        "passed": not errors and not gaps,
        "rfp": effective_rfp,
        "spec": spec_id,
        "covered": ev["covered"],
        "waivers": ev["waivers"],
        "gaps": gaps,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "acceptance": len(acc_rows),
            "covered": len(ev["covered"]),
            "waivers": len(ev["waivers"]),
            "gaps": len(gaps),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }


def rfp_spec_coverage_aggregate(
    rfp_text: str,
    spec_entries: List[Tuple[str, str]],
    *,
    rfp_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Union coverage across multiple SPECs (multi-phase RFP). MUST row satisfied if covered in any SPEC."""
    lint = rfp_lint(rfp_text, rfp_id=rfp_id)
    if not lint.get("passed"):
        return {
            "ok": False,
            "passed": False,
            "error": "rfp lint failed",
            "rfp_lint": lint,
            "errors": lint.get("errors") or [],
        }
    if not spec_entries:
        return {"ok": False, "passed": False, "error": "specs list required"}

    acc_rows = parse_rfp_acceptance(rfp_text).get("rows") or []
    per_spec: List[Dict[str, Any]] = []
    covered_by: Dict[str, str] = {}
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    trace_by_spec: Dict[str, Dict[str, Dict[str, str]]] = {}
    for spec_id, spec_text in spec_entries:
        cov = rfp_spec_coverage(
            rfp_text,
            spec_text,
            rfp_id=rfp_id or lint.get("rfp"),
            spec_id=spec_id,
        )
        per_spec.append({"spec": spec_id, **{k: cov.get(k) for k in ("passed", "covered", "waivers", "gaps", "errors", "warnings")}})
        errors.extend(cov.get("errors") or [])
        warnings.extend(cov.get("warnings") or [])
        tr = parse_spec_traceability(spec_text)
        trace_by_spec[spec_id] = tr.get("map") or {} if tr.get("ok") else {}
        for rid in cov.get("covered") or []:
            covered_by.setdefault(rid, spec_id)

    spec_ids = {sid for sid, _ in spec_entries}
    gaps: List[Dict[str, Any]] = []
    aggregate_covered: List[str] = []
    aggregate_waivers: List[str] = []

    for row in acc_rows:
        rid = row["id"]
        level = (row.get("level") or "MUST").upper()
        if rid in covered_by:
            aggregate_covered.append(rid)
            continue
        if level != "MUST":
            continue
        satisfied = False
        for spec_id, tmap in trace_by_spec.items():
            entry = tmap.get(rid)
            if not entry:
                continue
            disp = (entry.get("disposition") or "").strip()
            if not disp.lower().startswith("waiver:"):
                continue
            target = sibling_spec_waiver_target(disp)
            if target:
                if target in spec_ids and covered_by.get(rid) == target:
                    satisfied = True
                    aggregate_waivers.append(rid)
                    break
            else:
                satisfied = True
                aggregate_waivers.append(rid)
                break
        if not satisfied:
            gaps.append({"id": rid, "level": level, "reason": "not covered in any SPEC"})

    effective_rfp = rfp_id or lint.get("rfp")
    return {
        "ok": True,
        "passed": not errors and not gaps,
        "mode": "aggregate",
        "rfp": effective_rfp,
        "specs": [sid for sid, _ in spec_entries],
        "per_spec": per_spec,
        "covered": aggregate_covered,
        "covered_by": covered_by,
        "waivers": aggregate_waivers,
        "gaps": gaps,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "acceptance": len(acc_rows),
            "covered": len(aggregate_covered),
            "waivers": len(aggregate_waivers),
            "gaps": len(gaps),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }


def read_spec_text(
    target_dir: str, spec: Optional[str] = None, spec_path: Optional[str] = None
) -> Tuple[Optional[str], str]:
    """Public wrapper for loading SPEC markdown text."""
    return _load_spec_path(target_dir, spec or "", spec_path=spec_path)


def rfp_lint_workspace(
    target_dir: str = ".",
    *,
    rfp: Optional[str] = None,
    rfp_path: Optional[str] = None,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    path = rfp_path
    if not path:
        if not rfp:
            return {"ok": False, "error": "rfp or rfp_path required"}
        path = resolve_rfp_path(root, rfp)
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": f"RFP not found: {rfp or rfp_path}"}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    out = rfp_lint(text, rfp_id=rfp)
    out["source_path"] = path
    return out


def rfp_spec_coverage_workspace(
    target_dir: str = ".",
    *,
    rfp: Optional[str] = None,
    spec: Optional[str] = None,
    specs: Optional[str] = None,
    specs_list: Optional[List[str]] = None,
    rfp_path: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    if not rfp and not rfp_path:
        return {"ok": False, "error": "rfp or rfp_path required"}

    spec_ids = _split_specs_csv(specs, specs_list)
    if spec_ids and (spec or spec_path):
        return {"ok": False, "error": "pass either --spec or --specs, not both"}
    if spec:
        spec_ids = [spec]
    if spec_path and not spec_ids:
        spec_ids = []

    rfp_file = rfp_path or resolve_rfp_path(root, rfp or "")
    if not rfp_file or not os.path.isfile(rfp_file):
        return {"ok": False, "error": f"RFP not found: {rfp or rfp_path}"}
    with open(rfp_file, encoding="utf-8") as f:
        rfp_text = f.read()

    if spec_path and not spec_ids:
        with open(spec_path, encoding="utf-8") as f:
            spec_text = f.read()
        out = rfp_spec_coverage(
            rfp_text,
            spec_text,
            rfp_id=rfp or parse_rfp_id_from_h1(rfp_text),
            spec_id=None,
        )
        out["rfp_path"] = rfp_file
        out["spec_path"] = spec_path
        return out

    if len(spec_ids) > 1:
        entries: List[Tuple[str, str]] = []
        for sid in spec_ids:
            spec_file, spec_text = _load_spec_path(root, sid)
            if not spec_text:
                return {"ok": False, "error": f"SPEC not found: {sid}"}
            entries.append((sid, spec_text))
        out = rfp_spec_coverage_aggregate(
            rfp_text,
            entries,
            rfp_id=rfp or parse_rfp_id_from_h1(rfp_text),
        )
        out["rfp_path"] = rfp_file
        return out

    if not spec_ids and not spec_path:
        return {"ok": False, "error": "spec, spec_path, or specs required"}

    sid = spec_ids[0]
    spec_file, spec_text = _load_spec_path(root, sid, spec_path=spec_path)
    if not spec_text:
        return {"ok": False, "error": f"SPEC not found: {sid or spec_path}"}
    out = rfp_spec_coverage(
        rfp_text,
        spec_text,
        rfp_id=rfp or parse_rfp_id_from_h1(rfp_text),
        spec_id=sid,
    )
    out["rfp_path"] = rfp_file
    out["spec_path"] = spec_file
    return out


def rfp_spec_coverage_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    out = rfp_spec_coverage_workspace(root, **kwargs)
    return enrich_tool_response("apatch_rfp_spec_coverage", out, target_dir=root)


def _scaffold_short(text: str, n: int = 70) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


def scaffold_spec_from_rfp(
    rfp_text: str,
    *,
    spec_id: str,
    rfp_id: Optional[str] = None,
    title: str = "",
    mandatory_only: bool = False,
) -> Dict[str, Any]:
    """Emit a SPEC.md skeleton from an RFP Acceptance table (RFP-023, authoring side).

    The inverse of ``rfp_spec_coverage`` (which CHECKS a spec): one ``## Rk`` stub per
    acceptance row + an ``## R0 RFP traceability gate`` table mapping every acceptance id
    to its Rk, so the scaffold is contract-complete by construction (0 coverage gaps) and
    the author only fills the ``(verify:)`` commands instead of inventing the whole
    checklist + traceability."""
    if not spec_id:
        return {"ok": False, "error": "spec_id required", "spec_markdown": ""}
    acc = parse_rfp_acceptance(rfp_text)
    if not acc.get("ok"):
        return {"ok": False, "error": acc.get("error"), "spec_markdown": ""}
    rows = acc["rows"]
    total = len(rows)
    if mandatory_only:
        rows = [r for r in rows if (r.get("level") or "MUST").upper() == "MUST"]
    if not rows:
        return {"ok": False, "error": "no acceptance rows to scaffold", "spec_markdown": ""}
    rfp = rfp_id or infer_rfp_id_from_spec(rfp_text) or "RFP-XXX"
    ttl = title or f"{spec_id} (scaffolded from {rfp})"
    reqs = [{"rk": f"R{i}", "rfp_id": r["id"], "level": (r.get("level") or "MUST").upper(),
             "criterion": r.get("criterion") or ""} for i, r in enumerate(rows, start=1)]

    lines = [
        f"# {spec_id} \u2014 {ttl}",
        "",
        f"> **apatch artifact:** `spec:{spec_id}`  ",
        f"> **Anchors:** {rfp}  ",
        f"> _Scaffolded from {rfp} acceptance by `apatch spec scaffold --from-contract`. "
        "Fill each `(verify:)` with a real command (one green test per requirement)._",
        "",
        "## R0 RFP traceability gate (meta)",
        "",
        "| RFP id | SPEC Rk | Disposition |",
        "|--------|---------|-------------|",
    ]
    for rq in reqs:
        lines.append(f"| {rq['rfp_id']} | {rq['rk']} | covered |")
    lines += ["", f"(verify: TODO \u2014 assert every {rfp} acceptance id maps to an Rk)", ""]
    for rq in reqs:
        opt = "" if rq["level"] == "MUST" else f" _({rq['level']})_"
        lines.append(f"## {rq['rk']} {_scaffold_short(rq['criterion']) or rq['rfp_id']}{opt}")
        lines.append("")
        if rq["criterion"]:
            lines += [rq["criterion"], ""]
        lines.append(f"(verify: TODO \u2014 a command that FAILS when {rq['rk']} ({rq['rfp_id']}) is violated)")
        lines.append("")
    md = "\n".join(lines).rstrip() + "\n"
    return {"ok": True, "spec_id": spec_id, "rfp": rfp, "spec_markdown": md,
            "requirements": reqs,
            "counts": {"acceptance": total, "scaffolded": len(reqs),
                       "skipped_optional": total - len(reqs)}}


def scaffold_spec_from_rfp_workspace(
    target_dir: str = ".",
    *,
    rfp: Optional[str] = None,
    spec_id: str = "",
    rfp_path: Optional[str] = None,
    title: str = "",
    mandatory_only: bool = False,
    out_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve an RFP by id/path under ``target_dir`` and scaffold a SPEC skeleton.

    When ``out_path`` is given, the skeleton is written there (``spec_markdown`` still
    returned). Shared by the CLI (``apatch spec scaffold``) and MCP (``apatch_spec_scaffold``)."""
    root = os.path.abspath(target_dir)
    if not spec_id:
        return {"ok": False, "error": "spec id required (--spec SPEC-ID)"}
    rfp_file = rfp_path or resolve_rfp_input_path(root, rfp or "")
    if not rfp_file or not os.path.isfile(rfp_file):
        return {"ok": False, "error": f"RFP not found: {rfp or rfp_path}"}
    with open(rfp_file, encoding="utf-8") as f:
        rfp_text = f.read()
    res = scaffold_spec_from_rfp(
        rfp_text, spec_id=spec_id,
        rfp_id=rfp or parse_rfp_id_from_h1(rfp_text),
        title=title, mandatory_only=mandatory_only,
    )
    res["rfp_path"] = rfp_file
    if res.get("ok") and out_path:
        dest = out_path if os.path.isabs(out_path) else os.path.join(root, out_path)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(res["spec_markdown"])
        res["written"] = dest
    return res


def rfp_lint_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    out = rfp_lint_workspace(root, **kwargs)
    return enrich_tool_response("apatch_rfp_lint", out, target_dir=root)