"""Cross-spec interference detection (RFP-014 / SPEC-INTERFERENCE-1)."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from apatch.apatch_paths import normalize_rel as _normalize_rel
from apatch.spec import _ledger_entries, _load_spec
from apatch.spec_coverage import requirement_file_sets


def _read_file_content(target_dir: str, rel_path: str) -> str:
    abs_path = os.path.join(os.path.abspath(target_dir), _normalize_rel(rel_path))
    if not os.path.isfile(abs_path):
        return ""
    with open(abs_path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _simulate_replace(
    content: str,
    find_text: str,
    replace_text: str,
    *,
    replace_all: bool = False,
) -> str:
    if not find_text:
        return content
    if replace_all:
        return content.replace(find_text, replace_text)
    return content.replace(find_text, replace_text, 1)


def _needle_target_file(needle: Dict[str, Any]) -> str:
    tf = needle.get("target_file") or needle.get("file") or ""
    if tf:
        return _normalize_rel(str(tf))
    glob_pattern = needle.get("glob_pattern") or ""
    if glob_pattern and "/" in glob_pattern and "**" not in glob_pattern:
        return _normalize_rel(str(glob_pattern))
    return ""


def _needle_find_text(needle: Dict[str, Any]) -> str:
    return str(needle.get("find_text") or needle.get("find") or "")


def _needle_replace_text(needle: Dict[str, Any]) -> str:
    rt = needle.get("replace_text")
    if rt is None:
        rt = needle.get("replace")
    return "" if rt is None else str(rt)


def _needle_match_mode(needle: Dict[str, Any]) -> str:
    return str(needle.get("match_mode") or "literal")


def _replace_span(content: str, find_text: str) -> Optional[Tuple[int, int]]:
    if not find_text or find_text not in content:
        return None
    start = content.find(find_text)
    return start, start + len(find_text)


def _spans_overlap(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def is_wr_conflict(
    needle_a: Dict[str, Any],
    needle_b: Dict[str, Any],
    file_content: str,
) -> bool:
    if _needle_match_mode(needle_a) != "literal" or _needle_match_mode(needle_b) != "literal":
        return False
    find_b = _needle_find_text(needle_b)
    if not find_b or find_b not in file_content:
        return False
    find_a = _needle_find_text(needle_a)
    replace_a = _needle_replace_text(needle_a)
    if not find_a:
        return False
    after_a = _simulate_replace(
        file_content,
        find_a,
        replace_a,
        replace_all=bool(needle_a.get("replace_all")),
    )
    return find_b not in after_a


def is_ww_conflict(
    needle_a: Dict[str, Any],
    needle_b: Dict[str, Any],
    file_content: str,
) -> bool:
    if _needle_match_mode(needle_a) != "literal" or _needle_match_mode(needle_b) != "literal":
        return False
    find_a = _needle_find_text(needle_a)
    find_b = _needle_find_text(needle_b)
    if find_a and find_a == find_b:
        return True
    span_a = _replace_span(file_content, find_a) if find_a else None
    span_b = _replace_span(file_content, find_b) if find_b else None
    if span_a and span_b and _spans_overlap(span_a, span_b):
        return True
    after_a = _simulate_replace(file_content, find_a, _needle_replace_text(needle_a))
    span_a2 = _replace_span(file_content, find_a)
    span_b2 = _replace_span(after_a, find_b) if find_b else None
    if span_a2 and span_b2 and _spans_overlap(span_a2, span_b2):
        return True
    return False


def _collect_needles_from_manifest(
    spec_id: str,
    requirements: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rk, entry in (requirements or {}).items():
        if not isinstance(entry, dict):
            continue
        for needle in entry.get("needles") or []:
            if not isinstance(needle, dict):
                continue
            n = dict(needle)
            n["spec_id"] = spec_id
            n["requirement"] = rk
            out.append(n)
    return out


def load_planned_needles(target_dir: str, spec_id: str) -> List[Dict[str, Any]]:
    root = os.path.abspath(target_dir)
    reg_path = os.path.join(root, ".apatch", "specs", f"{spec_id}.json")
    if os.path.isfile(reg_path):
        with open(reg_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return _collect_needles_from_manifest(spec_id, data.get("requirements") or {})
    return []


def load_needles_for_spec(
    target_dir: str,
    spec_id: str,
    entries: List[Dict[str, Any]],
    *,
    include_attested: bool = True,
    include_planned: bool = True,
    planned_override: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    sources: List[str] = []
    needles: List[Dict[str, Any]] = []

    if include_planned:
        if planned_override is not None:
            needles.extend(_collect_needles_from_manifest(spec_id, planned_override))
            sources.append("planned_override")
        else:
            planned = load_planned_needles(target_dir, spec_id)
            if planned:
                needles.extend(planned)
                sources.append("registry")

    if include_attested:
        from apatch.traceability import build_traceability_index

        attested_start = len(needles)
        index = build_traceability_index(entries or [])
        by_art = index.get("by_artifact") or {}
        for key, bucket in by_art.items():
            if not key.startswith("spec:") or "#" not in key[5:]:
                continue
            parent, rk = key[5:].split("#", 1)
            if parent != spec_id:
                continue
            for mut in bucket.get("mutations") or []:
                payload = mut.get("payload") or {}
                for cand in payload.get("candidates") or payload.get("patches") or []:
                    if not isinstance(cand, dict):
                        continue
                    n = dict(cand)
                    n["spec_id"] = spec_id
                    n["requirement"] = rk
                    tf = _needle_target_file(n)
                    if not tf and isinstance(payload.get("files"), dict):
                        files = list(payload["files"].keys())
                        if len(files) == 1:
                            n["target_file"] = _normalize_rel(files[0])
                    needles.append(n)
        if len(needles) > attested_start and "ledger" not in sources:
            sources.append("ledger")
        file_sets = requirement_file_sets(entries, spec_id)
        if file_sets and "ledger" not in sources:
            sources.append("ledger_file_sets")

    return needles, sources or ["none"]


def l1_file_overlaps(
    spec_ids: List[str],
    entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    per_spec_files: Dict[str, Set[str]] = {}
    for sid in spec_ids:
        fs = requirement_file_sets(entries, sid)
        files: Set[str] = set()
        for meta in fs.values():
            files.update(_normalize_rel(f) for f in (meta.get("files") or {}))
        per_spec_files[sid] = files

    conflicts: List[Dict[str, Any]] = []
    for i, spec_a in enumerate(spec_ids):
        for spec_b in spec_ids[i + 1 :]:
            shared = per_spec_files.get(spec_a, set()) & per_spec_files.get(spec_b, set())
            for rel in sorted(shared):
                conflicts.append(
                    {
                        "type": "file_overlap",
                        "spec_a": spec_a,
                        "spec_b": spec_b,
                        "file": rel,
                        "detail": "both specs attested mutations on shared file (L1 candidate)",
                        "severity": "low",
                    }
                )
    return conflicts


def _pair_needles(
    needles_a: List[Dict[str, Any]],
    needles_b: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], Dict[str, Any], str]]:
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
    for na in needles_a:
        fa = _needle_target_file(na)
        for nb in needles_b:
            fb = _needle_target_file(nb)
            if fa and fb and fa == fb:
                pairs.append((na, nb, fa))
    return pairs


def l2_patch_conflicts(
    spec_a: str,
    spec_b: str,
    needles_a: List[Dict[str, Any]],
    needles_b: List[Dict[str, Any]],
    target_dir: str,
    warnings: List[str],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    conflicts: List[Dict[str, Any]] = []
    wr_edges: List[Tuple[str, str]] = []

    for na, nb, rel in _pair_needles(needles_a, needles_b):
        content = _read_file_content(target_dir, rel)
        if not content:
            continue
        for mode in (_needle_match_mode(na), _needle_match_mode(nb)):
            if mode != "literal":
                msg = f"non_literal_match_mode on {rel} ({spec_a} vs {spec_b})"
                if msg not in warnings:
                    warnings.append(msg)

        if is_wr_conflict(na, nb, content):
            conflicts.append(
                {
                    "type": "write_read",
                    "spec_a": spec_a,
                    "spec_b": spec_b,
                    "requirement_a": f"{spec_a}#{na.get('requirement', '?')}",
                    "requirement_b": f"{spec_b}#{nb.get('requirement', '?')}",
                    "file": rel,
                    "detail": f"{spec_a} write may invalidate {spec_b} find_text anchor",
                    "severity": "medium",
                    "_needle_a": na,
                    "_needle_b": nb,
                }
            )
            wr_edges.append((spec_a, spec_b))
        if is_wr_conflict(nb, na, content):
            conflicts.append(
                {
                    "type": "write_read",
                    "spec_a": spec_b,
                    "spec_b": spec_a,
                    "requirement_a": f"{spec_b}#{nb.get('requirement', '?')}",
                    "requirement_b": f"{spec_a}#{na.get('requirement', '?')}",
                    "file": rel,
                    "detail": f"{spec_b} write may invalidate {spec_a} find_text anchor",
                    "severity": "medium",
                    "_needle_a": nb,
                    "_needle_b": na,
                }
            )
            wr_edges.append((spec_b, spec_a))

        if is_ww_conflict(na, nb, content):
            conflicts.append(
                {
                    "type": "write_write",
                    "spec_a": spec_a,
                    "spec_b": spec_b,
                    "requirement_a": f"{spec_a}#{na.get('requirement', '?')}",
                    "requirement_b": f"{spec_b}#{nb.get('requirement', '?')}",
                    "file": rel,
                    "detail": "overlapping write regions (mutex — refactor needles)",
                    "severity": "high",
                    "_needle_a": na,
                    "_needle_b": nb,
                }
            )

    return conflicts, wr_edges


def build_conflict_graph(
    spec_ids: List[str],
    wr_edges: List[Tuple[str, str]],
) -> Dict[str, List[str]]:
    graph: Dict[str, List[str]] = {s: [] for s in spec_ids}
    for src, dst in wr_edges:
        if src in graph and dst not in graph[src]:
            graph[src].append(dst)
    return graph


def detect_cycles(graph: Dict[str, List[str]]) -> List[List[str]]:
    cycles: List[List[str]] = []
    visited: Set[str] = set()
    stack: Set[str] = set()
    path: List[str] = []

    def dfs(node: str) -> None:
        if node in stack:
            if node in path:
                idx = path.index(node)
                cycle = path[idx:] + [node]
                if cycle not in cycles:
                    cycles.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        stack.add(node)
        path.append(node)
        for nxt in graph.get(node, []):
            dfs(nxt)
        path.pop()
        stack.remove(node)

    for node in graph:
        dfs(node)
    return cycles


def topological_safe_order(
    spec_ids: List[str],
    graph: Dict[str, List[str]],
) -> List[str]:
    indegree = {s: 0 for s in spec_ids}
    for src in spec_ids:
        for dst in graph.get(src, []):
            if dst in indegree:
                indegree[dst] += 1
    order_index = {s: i for i, s in enumerate(spec_ids)}
    ready = sorted([s for s in spec_ids if indegree[s] == 0], key=lambda x: order_index[x])
    result: List[str] = []
    while ready:
        node = ready.pop(0)
        result.append(node)
        for dst in graph.get(node, []):
            if dst not in indegree:
                continue
            indegree[dst] -= 1
            if indegree[dst] == 0:
                ready.append(dst)
                ready.sort(key=lambda x: order_index[x])
    for s in spec_ids:
        if s not in result:
            result.append(s)
    return result


def compute_risk_score(conflicts: List[Dict[str, Any]], has_cycle: bool) -> float:
    if has_cycle:
        return 1.0
    if not conflicts:
        return 0.0
    types = {c.get("type") for c in conflicts}
    if "write_write" in types:
        return 0.75
    if "write_read" in types:
        return 0.5
    if "file_overlap" in types:
        return 0.2
    return 0.1


def _canonical_needles_for_hash(needles: List[Dict[str, Any]]) -> str:
    """Stable JSON fingerprint of needles for MVCC input_hashes (SPEC-INTERFERENCE-3 R1)."""

    def _sort_key(n: Dict[str, Any]) -> tuple:
        return (
            str(n.get("requirement") or ""),
            _needle_target_file(n),
            _needle_find_text(n),
            _needle_replace_text(n),
            _needle_match_mode(n),
            str(n.get("action") or "replace"),
        )

    minimal: List[Dict[str, Any]] = []
    for n in sorted(needles, key=_sort_key):
        minimal.append(
            {
                "requirement": n.get("requirement"),
                "target_file": _needle_target_file(n),
                "find_text": _needle_find_text(n),
                "replace_text": _needle_replace_text(n),
                "match_mode": _needle_match_mode(n),
                "action": n.get("action") or "replace",
            }
        )
    return json.dumps(minimal, sort_keys=True, separators=(",", ":"))


def compute_input_hashes(needles_by_spec: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for sid, needles in needles_by_spec.items():
        digest = hashlib.sha256(_canonical_needles_for_hash(needles).encode("utf-8")).hexdigest()
        out[sid] = f"sha256:{digest}"
    return out


def classify_data_domains(data_sources: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Map per-spec loader sources to observed (ledger) vs planned (registry) domains."""
    out: Dict[str, List[str]] = {}
    for sid, sources in data_sources.items():
        domains: List[str] = []
        if any(s in ("ledger", "ledger_file_sets") for s in sources):
            domains.append("observed")
        if any(s in ("registry", "planned_override") for s in sources):
            domains.append("planned")
        out[sid] = domains or ["none"]
    return out


def compute_interference_validity(
    data_domains: Dict[str, List[str]],
    *,
    input_hashes: Dict[str, str],
    previous_input_hashes: Optional[Dict[str, str]] = None,
) -> str:
    if previous_input_hashes is not None and previous_input_hashes != input_hashes:
        return "stale"
    observed_any = any("observed" in d for d in data_domains.values())
    planned_any = any("planned" in d for d in data_domains.values())
    if not observed_any and planned_any:
        return "planned_only"
    return "current"


def attach_mvcc_fields(
    report: Dict[str, Any],
    *,
    needles_by_spec: Dict[str, List[Dict[str, Any]]],
    data_sources: Dict[str, List[str]],
    previous_input_hashes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    input_hashes = compute_input_hashes(needles_by_spec)
    data_domains = classify_data_domains(data_sources)
    validity = compute_interference_validity(
        data_domains,
        input_hashes=input_hashes,
        previous_input_hashes=previous_input_hashes,
    )
    report["computed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    report["input_hashes"] = input_hashes
    report["validity"] = validity
    report["data_domains"] = data_domains
    if validity == "stale":
        msg = "interference_input_hashes_changed: needles drifted since previous_input_hashes"
        warnings = list(report.get("warnings") or [])
        if msg not in warnings:
            warnings.append(msg)
        report["warnings"] = warnings
        report["recommended_action"] = "re_run_interference"
    return report


def _reorder_feasible_wr(
    writer: str,
    reader: str,
    spec_ids: List[str],
    wr_edges: List[Tuple[str, str]],
) -> bool:
    """True when running reader before writer keeps the WR graph acyclic."""
    new_edges = [(s, d) for s, d in wr_edges if not (s == writer and d == reader)]
    if (reader, writer) not in new_edges:
        new_edges.append((reader, writer))
    return not detect_cycles(build_conflict_graph(spec_ids, new_edges))


def generate_structural_options(
    conflict: Dict[str, Any],
    *,
    spec_ids: List[str],
    wr_edges: List[Tuple[str, str]],
    file_content: str = "",
    needle_a: Optional[Dict[str, Any]] = None,
    needle_b: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    ctype = str(conflict.get("type") or "")
    spec_a = str(conflict.get("spec_a") or "")
    spec_b = str(conflict.get("spec_b") or "")
    options: List[Dict[str, Any]] = []

    if ctype == "write_read":
        writer, reader = spec_a, spec_b
        feasible = _reorder_feasible_wr(writer, reader, spec_ids, wr_edges)
        options.append(
            {
                "option": "reorder",
                "feasible": feasible,
                "description": f"Execute {reader} before {writer}",
                "side_effect": f"{writer} must update find_text to post-{reader} state",
            }
        )
        anchor = _needle_find_text(needle_b or {})
        options.append(
            {
                "option": "rewrite_anchor",
                "feasible": True,
                "description": f"Regenerate {reader} find_text anchor (currently {anchor[:40]!r}…)"
                if len(anchor) > 40
                else f"Regenerate {reader} find_text anchor ({anchor!r})",
                "side_effect": f"{reader} needle must be regenerated",
            }
        )
    elif ctype == "write_write":
        na = needle_a or {}
        nb = needle_b or {}
        find_a = _needle_find_text(na)
        find_b = _needle_find_text(nb)
        partial = False
        if find_a and find_b and find_a != find_b and file_content:
            span_a = _replace_span(file_content, find_a)
            span_b = _replace_span(file_content, find_b)
            partial = bool(span_a and span_b and _spans_overlap(span_a, span_b))
        options.append(
            {
                "option": "split_region",
                "feasible": partial,
                "description": "Split WW conflict into disjoint replace regions",
                "side_effect": "Both specs need non-overlapping find_text anchors",
            }
        )

    options.append(
        {
            "option": "merge",
            "feasible": "unknown",
            "description": "Combine mutations into one atomic needle",
            "side_effect": "Requires semantic analysis (out of scope)",
        }
    )
    return options


def enrich_l2_conflicts(
    conflicts: List[Dict[str, Any]],
    *,
    target_dir: str,
    spec_ids: List[str],
    wr_edges: List[Tuple[str, str]],
) -> None:
    for conflict in conflicts:
        if conflict.get("type") not in ("write_read", "write_write"):
            continue
        needle_a = conflict.pop("_needle_a", None)
        needle_b = conflict.pop("_needle_b", None)
        rel = str(conflict.get("file") or "")
        content = _read_file_content(target_dir, rel) if rel else ""
        conflict["confidence"] = "structural"
        conflict["structural_options"] = generate_structural_options(
            conflict,
            spec_ids=spec_ids,
            wr_edges=wr_edges,
            file_content=content,
            needle_a=needle_a,
            needle_b=needle_b,
        )


def compute_interference_hash(report: Dict[str, Any]) -> str:
    payload = {
        "specs": report.get("specs") or [],
        "input_hashes": report.get("input_hashes") or {},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_blocked_pairs(
    conflicts: List[Dict[str, Any]],
    cycles: List[List[str]],
    has_cycle: bool,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_ww: Set[Tuple[str, str]] = set()
    for conflict in conflicts:
        if conflict.get("type") != "write_write":
            continue
        key = tuple(sorted((str(conflict.get("spec_a")), str(conflict.get("spec_b")))))
        if key in seen_ww:
            continue
        seen_ww.add(key)
        out.append(
            {
                "spec_a": conflict.get("spec_a"),
                "spec_b": conflict.get("spec_b"),
                "reason": "write_write_mutex",
            }
        )
    if has_cycle:
        for cycle in cycles:
            for sid in cycle:
                out.append({"spec_a": sid, "spec_b": sid, "reason": "cycle_member", "cycle": cycle})
    return out


def compute_risk_per_step(
    spec_ids: List[str],
    graph: Dict[str, List[str]],
    conflicts: List[Dict[str, Any]],
    safe_order: List[str],
) -> List[Dict[str, Any]]:
    severity_risk = {"low": 0.2, "medium": 0.5, "high": 0.75, "critical": 0.9}
    spec_risk: Dict[str, float] = {sid: 0.0 for sid in spec_ids}
    for conflict in conflicts:
        sev = severity_risk.get(str(conflict.get("severity") or ""), 0.3)
        for sid in (conflict.get("spec_a"), conflict.get("spec_b")):
            if sid in spec_risk:
                spec_risk[str(sid)] = max(spec_risk[str(sid)], sev)
    depends: Dict[str, List[str]] = {sid: [] for sid in spec_ids}
    for src, dsts in graph.items():
        for dst in dsts:
            if dst in depends and src not in depends[dst]:
                depends[dst].append(src)
    order = safe_order or list(spec_ids)
    steps: List[Dict[str, Any]] = []
    for spec in order:
        steps.append(
            {
                "spec": spec,
                "risk": round(spec_risk.get(spec, 0.0), 2),
                "depends_on": sorted(depends.get(spec, [])),
            }
        )
    return steps


def enrich_schedule_payload(out: Dict[str, Any], *, strategy: str = "safe") -> Dict[str, Any]:
    schedulable = not bool(out.get("has_cycle"))
    safe_order = list(out.get("safe_order") or [])
    spec_ids = list(out.get("specs") or [])
    risk_steps = compute_risk_per_step(
        spec_ids,
        out.get("conflict_graph") or {},
        out.get("conflicts") or [],
        safe_order,
    )
    order = list(safe_order) if schedulable else []
    if schedulable and strategy == "risk_first":
        order = sorted(
            safe_order,
            key=lambda s: (
                -next((step["risk"] for step in risk_steps if step["spec"] == s), 0.0),
                safe_order.index(s),
            ),
        )
    ihash = compute_interference_hash(out)
    blocked = build_blocked_pairs(
        out.get("conflicts") or [],
        out.get("cycles") or [],
        bool(out.get("has_cycle")),
    )
    out["schedulable"] = schedulable
    out["order"] = order
    out["blocked_pairs"] = blocked
    out["risk_per_step"] = risk_steps
    out["interference_hash"] = ihash
    out["interference_validity"] = out.get("validity")
    out["strategy"] = strategy
    out["schedule"] = {
        "safe_order": safe_order,
        "order": order,
        "risk_score": out.get("risk_score", 0.0),
        "irreconcilable": not schedulable,
        "conflicts": out.get("conflicts") or [],
        "interference_validity": out.get("validity"),
        "interference_hash": ihash,
        "input_hashes": out.get("input_hashes") or {},
        "computed_at": out.get("computed_at"),
        "schedulable": schedulable,
        "blocked_pairs": blocked,
        "risk_per_step": risk_steps,
        "strategy": strategy,
    }
    return out


def _discover_spec_ids(target_dir: str) -> List[str]:
    specs_dir = os.path.join(os.path.abspath(target_dir), "docs", "specs")
    if not os.path.isdir(specs_dir):
        return []
    skip = frozenset({"SPEC-TEMPLATE"})
    return sorted(
        name[:-3]
        for name in os.listdir(specs_dir)
        if name.startswith("SPEC-") and name.endswith(".md") and name[:-3] not in skip
    )


def spec_interference_from_data(
    target_dir: str,
    spec_ids: List[str],
    entries: List[Dict[str, Any]],
    *,
    level: int = 2,
    include_planned: bool = True,
    include_attested: bool = True,
    planned_by_spec: Optional[Dict[str, Dict[str, Any]]] = None,
    previous_input_hashes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    warnings: List[str] = []
    data_sources: Dict[str, List[str]] = {}
    needles_by_spec: Dict[str, List[Dict[str, Any]]] = {}

    for sid in spec_ids:
        override = (planned_by_spec or {}).get(sid)
        needles, sources = load_needles_for_spec(
            target_dir,
            sid,
            entries,
            include_attested=include_attested,
            include_planned=include_planned,
            planned_override=override,
        )
        needles_by_spec[sid] = needles
        data_sources[sid] = sources

    from apatch.spec_interference_l0 import (
        build_l0_routing_matrix,
        l0_pair_lookup,
        l0_pair_allows,
        l1_planned_file_overlaps,
        load_spec_tags,
        needle_planned_paths,
    )

    tags_by_spec: Dict[str, List[str]] = {}
    paths_by_spec: Dict[str, Set[str]] = {}
    for sid in spec_ids:
        override = (planned_by_spec or {}).get(sid)
        tags_by_spec[sid] = load_spec_tags(target_dir, sid, planned_override=override)
        paths_by_spec[sid] = needle_planned_paths(needles_by_spec.get(sid, []))
    l0_routing = build_l0_routing_matrix(spec_ids, tags_by_spec, paths_by_spec)
    l0_pairs = l0_routing.get("pairs") or []
    l0_enabled = bool(l0_routing.get("enabled"))

    conflicts: List[Dict[str, Any]] = []
    wr_edges: List[Tuple[str, str]] = []

    if level >= 1:
        conflicts.extend(l1_file_overlaps(spec_ids, entries))
        conflicts.extend(l1_planned_file_overlaps(spec_ids, needles_by_spec, l0_pairs))
        if l0_enabled:
            filtered: List[Dict[str, Any]] = []
            for conflict in conflicts:
                if conflict.get("type") != "file_overlap":
                    filtered.append(conflict)
                    continue
                row = l0_pair_lookup(
                    l0_pairs,
                    str(conflict.get("spec_a")),
                    str(conflict.get("spec_b")),
                )
                if l0_pair_allows(row, for_l2=False):
                    filtered.append(conflict)
            conflicts = filtered

    if level >= 2:
        for i, spec_a in enumerate(spec_ids):
            for spec_b in spec_ids[i + 1 :]:
                if l0_enabled:
                    row = l0_pair_lookup(l0_pairs, spec_a, spec_b)
                    if not l0_pair_allows(row, for_l2=True):
                        continue
                l2, edges = l2_patch_conflicts(
                    spec_a,
                    spec_b,
                    needles_by_spec.get(spec_a, []),
                    needles_by_spec.get(spec_b, []),
                    target_dir,
                    warnings,
                )
                conflicts.extend(l2)
                wr_edges.extend(edges)

    if level >= 2:
        enrich_l2_conflicts(
            conflicts,
            target_dir=target_dir,
            spec_ids=spec_ids,
            wr_edges=wr_edges,
        )

    graph = build_conflict_graph(spec_ids, wr_edges)
    cycles = detect_cycles(graph)
    has_cycle = bool(cycles)
    safe_order = topological_safe_order(spec_ids, graph) if not has_cycle else []
    risk = compute_risk_score(conflicts, has_cycle)

    by_type: Dict[str, int] = defaultdict(int)
    for c in conflicts:
        by_type[str(c.get("type") or "unknown")] += 1

    report: Dict[str, Any] = {
        "ok": True,
        "specs": spec_ids,
        "analysis_level": level,
        "l0_routing": l0_routing,
        "data_sources": data_sources,
        "conflicts": conflicts,
        "conflict_graph": graph,
        "has_cycle": has_cycle,
        "cycles": cycles,
        "safe_order": safe_order,
        "risk_score": risk,
        "summary": {
            "total_specs": len(spec_ids),
            "total_conflicts": len(conflicts),
            "by_type": dict(by_type),
            "irreconcilable": has_cycle,
        },
        "warnings": warnings,
    }
    return attach_mvcc_fields(
        report,
        needles_by_spec=needles_by_spec,
        data_sources=data_sources,
        previous_input_hashes=previous_input_hashes,
    )


def spec_interference_workspace(
    target_dir: str = ".",
    *,
    specs: Optional[List[str]] = None,
    level: int = 2,
    include_planned: bool = True,
    include_attested: bool = True,
    planned_by_spec: Optional[Dict[str, Dict[str, Any]]] = None,
    previous_input_hashes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    spec_ids = list(specs or [])
    if not spec_ids:
        return {"ok": False, "error": "at least two spec ids required (pass specs=[...])"}
    if len(spec_ids) == 1 and spec_ids[0] in ("*", "all"):
        spec_ids = _discover_spec_ids(root)
    if len(spec_ids) < 2:
        return {"ok": False, "error": "at least two spec ids required"}

    for sid in spec_ids:
        try:
            _load_spec(root, spec=sid)
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": str(e), "spec": sid}

    entries, ledger_active = _ledger_entries(root)
    out = spec_interference_from_data(
        root,
        spec_ids,
        entries,
        level=level,
        include_planned=include_planned,
        include_attested=include_attested,
        planned_by_spec=planned_by_spec,
        previous_input_hashes=previous_input_hashes,
    )
    out["ledger_active"] = ledger_active
    return out


def spec_interference_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root_dir = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_interference",
        spec_interference_workspace(root_dir, **kwargs),
        target_dir=root_dir,
    )


def is_spec_fully_attested(target_dir: str, spec_id: str) -> bool:
    """True when every requirement row for *spec_id* is ``attested`` in ledger status."""
    from apatch.spec import spec_status_workspace

    st = spec_status_workspace(target_dir, spec=spec_id)
    if not st.get("ok"):
        return False
    if st.get("done"):
        return True
    rows = st.get("requirements") or []
    if not rows:
        return False
    return all(r.get("state") == "attested" for r in rows)


def spec_schedule_workspace(
    target_dir: str = ".",
    *,
    specs: Optional[List[str]] = None,
    level: int = 2,
    include_planned: bool = True,
    include_attested: bool = True,
    strategy: str = "safe",
    planned_by_spec: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Schedule view over interference analysis (RFP-014 Phase 1.5)."""
    out = spec_interference_workspace(
        target_dir,
        specs=specs,
        level=level,
        include_planned=include_planned,
        include_attested=include_attested,
        planned_by_spec=planned_by_spec,
    )
    if not out.get("ok"):
        return out
    out = enrich_schedule_payload(out, strategy=strategy)
    if not out.get("schedulable"):
        out["error_type"] = "SPEC_SCHEDULE_BLOCKED"
        out["recommended_action"] = "resolve_conflicts"
    elif out.get("validity") == "stale":
        out["warning"] = "interference snapshot stale; re-run recommended"
        out["recommended_action"] = "re_run_interference"
    return out


def spec_schedule_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root_dir = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_schedule",
        spec_schedule_workspace(root_dir, **kwargs),
        target_dir=root_dir,
    )
