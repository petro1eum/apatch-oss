"""SPEC-CONCEPT-COMPILE-1 — RFP-034 §B.1 concept/claim fence compiler.

Turns a flat markdown pile into the canonical concept graph: parses
``<!-- @cid:X -->`` + a ```concept``` block (entity node) and
``<!-- @sid:Y -->`` + a ```claim``` block (documentation claim), builds the
graph, and validates reference integrity — a relation or claim pointing at an
undefined concept, or a duplicate id, is an error, never silent (RFP-034 §A.4).

This is Level 2's foundation: the step that turns docs from a pile into a web.
Wiring into the generic ``apatch compile`` + dedup/coverage/C4 render are later phases.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

_CID_RE = re.compile(r"<!--\s*@cid:(\S+?)\s*-->\s*\n```concept\n(.*?)\n```", re.S)
_SID_RE = re.compile(r"<!--\s*@sid:(\S+?)\s*-->\s*\n```claim\n(.*?)\n```", re.S)
_NEXT_MARK = re.compile(r"\n<!--\s*@(?:cid|sid):")


def _load(body: str) -> Dict[str, Any]:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML required to parse concept/claim fences")
    return yaml.safe_load(body) or {}


def compile_concept_graph(text: str) -> Dict[str, Any]:
    """Parse concept/claim fences into a graph with reference-integrity errors.

    Returns ``{schema_version, concepts: {cid: node}, claims: {sid: claim}, errors: [...]}``.
    Silence is forbidden: a dangling ``relation.to`` / ``about``, or a duplicate id,
    appears in ``errors``.
    """
    concepts: Dict[str, Any] = {}
    claims: Dict[str, Any] = {}
    errors: List[str] = []

    for m in _CID_RE.finditer(text):
        cid, body = m.group(1), m.group(2)
        data = _load(body)
        tail = text[m.end():]
        nxt = _NEXT_MARK.search(tail)
        definition = (tail[: nxt.start()] if nxt else tail).strip()
        rb = data.get("realized_by")
        if cid in concepts:
            errors.append("duplicate cid: " + cid)
        concepts[cid] = {
            "cid": cid,
            "name": data.get("name"),
            "aliases": data.get("aliases") or [],
            "definition": definition,
            "realized_by": [rb] if isinstance(rb, str) else (rb or []),
            "relations": data.get("relations") or [],
            "invariants": data.get("invariants") or [],
            "c4": data.get("c4"),
        }

    for m in _SID_RE.finditer(text):
        sid, body = m.group(1), m.group(2)
        data = _load(body)
        if sid in claims:
            errors.append("duplicate sid: " + sid)
        claims[sid] = {
            "sid": sid,
            "type": data.get("type"),
            "about": data.get("about") or [],
            "edges": data.get("edges") or {},
            "verify": data.get("verify"),
        }

    known = set(concepts)
    for cid, node in concepts.items():
        for rel in node["relations"]:
            to = rel.get("to") if isinstance(rel, dict) else None
            if to and to not in known:
                errors.append(cid + ": relation -> unknown concept '" + str(to) + "'")
    for sid, claim in claims.items():
        for ref in claim["about"]:
            if ref not in known:
                errors.append(sid + ": about -> unknown concept '" + str(ref) + "'")
    for cid, node in concepts.items():
        if node.get("c4") and node["c4"] not in C4_LEVELS:
            errors.append(cid + ": unknown c4 level '" + str(node["c4"]) + "'")

    _first = re.search(r"<!--\s*@(?:cid|sid):", text)
    preamble = text[: _first.start()].strip() if _first else ""

    return {"schema_version": 1, "concepts": concepts, "claims": claims,
            "errors": errors, "preamble": preamble}


def write_concept_map(text: str, out_dir: str = ".apatch") -> Dict[str, Any]:
    """Compile and write ``concept_map.json`` (next to ``knowledge_map.json``).

    Returns the graph with an added ``_path`` key pointing at the written file.
    """
    graph = compile_concept_graph(text)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "concept_map.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in graph.items()}, fh, ensure_ascii=False, indent=2, sort_keys=True)
    graph["_path"] = out
    return graph

_REL_ARROW = {
    "depends_on": "-->|depends on|",
    "consumes": "-->|consumes|",
    "produces": "-->|produces|",
    "constrains": "-->|constrains|",
    "part_of": "-->|part of|",
    "supersedes": "-->|supersedes|",
    "conflicts_with": "-.conflicts.->",
    "same_as": "===|same as|",
}


C4_LEVELS = ["context", "container", "component", "code"]
C4_LABEL = {"context": "Context", "container": "Container", "component": "Component", "code": "Code"}


def _node_id(cid: str) -> str:
    return cid.replace("-", "_")


def concept_status(node: Dict[str, Any]) -> str:
    """guarded if the concept has a node invariant or an invariant-bearing edge, else grey."""
    node_inv = bool(node.get("invariants"))
    edge_inv = any(isinstance(r, dict) and r.get("invariant") for r in node.get("relations", []))
    return "guarded" if (node_inv or edge_inv) else "grey"


def concept_coverage(graph: Dict[str, Any]) -> Dict[str, Any]:
    """RFP-034 §B.5 — guarded (checkable invariant) vs grey (none; can silently rot).
    Silence forbidden: grey is counted and named."""
    concepts = graph.get("concepts", {})
    guarded = sorted(c for c, n in concepts.items() if concept_status(n) == "guarded")
    grey = sorted(c for c, n in concepts.items() if concept_status(n) == "grey")
    total = len(concepts)
    return {"total": total, "guarded": guarded, "grey": grey,
            "percent": round(100.0 * len(guarded) / total, 1) if total else 0.0}


def render_concept_graph_md(graph: Dict[str, Any], title: str = "Концепт-граф",
                            statuses: Optional[Dict[str, str]] = None,
                            clarify: Optional[Dict[str, str]] = None) -> str:
    """Render a compiled concept graph as a navigable, *readable* MkDocs page (RFP-034 §3.8,
    §B.7): the page leads with the author's human preamble and each concept leads with its
    name + definition; the rigorous "guts" (realized_by, relations, backlinks, invariants, C4)
    go under a collapsible — the reader sees meaning, the developer expands the proof.

    Node colour: ``needs_clarification`` (amber) for any id in ``clarify`` (§4a), else live
    verify ``statuses`` (green/red/broken/grey), else coverage (guarded/grey). C4 layers §3.7.
    """
    concepts = graph.get("concepts", {})
    claims = graph.get("claims", {})
    clarify = clarify or {}

    def _st(cid: str, n: Dict[str, Any]) -> str:
        if cid in clarify:
            return "needs_clarification"
        if statuses is not None:
            return statuses.get(cid, "grey")
        return concept_status(n)

    def _link(cid: str) -> str:
        return "[%s](#%s)" % ((concepts[cid].get("name") or cid), cid)

    back: Dict[str, List[Any]] = {}
    for cid, n in concepts.items():
        for rel in n.get("relations", []):
            to = rel.get("to") if isinstance(rel, dict) else None
            if to and to in concepts:
                back.setdefault(to, []).append((cid, rel.get("rel")))
    claims_about: Dict[str, List[str]] = {}
    for sid, c in claims.items():
        for ref in c.get("about") or []:
            if ref in concepts:
                claims_about.setdefault(ref, []).append(sid)

    lines = ["# " + title, ""]
    # human lead: the author's free-text preamble (§3.8) — a leading H1 is dropped (we add ours)
    preamble = (graph.get("preamble") or "").splitlines()
    if preamble and preamble[0].lstrip().startswith("# "):
        preamble = preamble[1:]
    pre_body = "\n".join(preamble).strip()
    if pre_body:
        lines.append(pre_body)
        lines.append("")

    if statuses is not None:
        order = ("green", "red", "broken", "grey")
        counts = {s: sum(1 for c, n in concepts.items() if _st(c, n) == s) for s in order}
        lines.append("> **Verify:** " + (", ".join("%d %s" % (counts[s], s) for s in order if counts[s]) or "—"))
    else:
        cov = concept_coverage(graph)
        lines.append("> **Покрытие:** %d/%d концептов под инвариантом (%.0f%%); grey (могут "
                     "протухнуть): %s" % (
                         len(cov["guarded"]), cov["total"], cov["percent"],
                         ", ".join("`%s`" % g for g in cov["grey"]) or "—"))
    c4_counts = {lvl: sum(1 for n in concepts.values() if n.get("c4") == lvl) for lvl in C4_LEVELS}
    if any(c4_counts.values()):
        lines.append("> **C4 (§3.7):** " + ", ".join("%d %s" % (c4_counts[l], C4_LABEL[l])
                                                      for l in C4_LEVELS if c4_counts[l]))
    if clarify:
        n_open = sum(1 for c in concepts if c in clarify)
        lines.append("> **Требуют уточнения (§4a):** %d — %s" % (
            n_open, ", ".join("`%s`" % c for c in concepts if c in clarify)))
    lines.append("")

    def _emit_node(cid: str, n: Dict[str, Any], indent: str) -> str:
        label = (n.get("name") or cid).replace('"', "'")
        return '%s%s["%s"]' % (indent, _node_id(cid), label)

    lines += ["```mermaid", "graph LR"]
    leveled = {lvl: [(cid, n) for cid, n in concepts.items() if n.get("c4") == lvl]
               for lvl in C4_LEVELS}
    for lvl in C4_LEVELS:
        if leveled[lvl]:
            lines.append('  subgraph c4_%s["%s"]' % (lvl, C4_LABEL[lvl]))
            for cid, n in leveled[lvl]:
                lines.append(_emit_node(cid, n, "    "))
            lines.append("  end")
    for cid, n in concepts.items():
        if n.get("c4") not in C4_LEVELS:
            lines.append(_emit_node(cid, n, "  "))
    for cid, n in concepts.items():
        for rel in n.get("relations", []):
            to = rel.get("to") if isinstance(rel, dict) else None
            if to and to in concepts:
                arrow = _REL_ARROW.get(rel.get("rel"), "-->")
                lines.append("  %s %s %s" % (_node_id(cid), arrow, _node_id(to)))
    lines.append("  classDef guarded fill:#1b5e20,stroke:#2e7d32,color:#fff;")
    lines.append("  classDef grey fill:#9e9e9e,stroke:#616161,color:#fff;")
    lines.append("  classDef green fill:#1b5e20,stroke:#2e7d32,color:#fff;")
    lines.append("  classDef red fill:#b71c1c,stroke:#e53935,color:#fff;")
    lines.append("  classDef broken fill:#e65100,stroke:#fb8c00,color:#fff;")
    lines.append("  classDef needs_clarification fill:#f9a825,stroke:#f57f17,color:#000;")
    by_status: Dict[str, List[str]] = {}
    for cid, n in concepts.items():
        by_status.setdefault(_st(cid, n), []).append(_node_id(cid))
    for status, ids in by_status.items():
        lines.append("  class %s %s;" % (",".join(ids), status))
    for cid, n in concepts.items():
        tip = (n.get("name") or cid).replace('"', "'")
        lines.append('  click %s "#%s" "%s"' % (_node_id(cid), cid, tip))
    lines += ["```", ""]

    for cid, n in concepts.items():
        lines.append("## %s {#%s}" % ((n.get("name") or cid), cid))
        lines.append("")
        # human first: clarify banner (if any), then the definition prose
        if cid in clarify:
            lines.append('!!! warning "Требует уточнения (§4a)"')
            lines.append("    " + (clarify[cid] or "помечено человеком как несогласованное"))
            lines.append("")
        if n.get("definition"):
            lines.append(n["definition"]); lines.append("")
        # rigour under a collapsible — the developer expands it (§3.8 reader-depth)
        det: List[str] = ['??? info "Детали — для разработчика"']
        meta = "    `%s` · **%s**" % (cid, _st(cid, n))
        if n.get("c4") in C4_LEVELS:
            meta += " · C4: %s" % C4_LABEL[n["c4"]]
        det.append(meta)
        det.append("")
        if n.get("realized_by"):
            det.append("    **Воплощён в:** " + ", ".join("`%s`" % r for r in n["realized_by"]))
        rels = [r for r in n.get("relations", []) if isinstance(r, dict) and r.get("to") in concepts]
        if rels:
            det.append("    **Связи:** " + ", ".join("%s → %s" % (r["rel"], _link(r["to"])) for r in rels))
        if back.get(cid):
            det.append("    **Упоминается в:** " + ", ".join("%s ← %s" % (_link(src), rel) for src, rel in back[cid]))
        if claims_about.get(cid):
            det.append("    **Утверждения:** " + ", ".join("`%s`" % s for s in claims_about[cid]))
        if n.get("invariants"):
            inv = ", ".join("`%s`" % (i.get("ref") if isinstance(i, dict) else i) for i in n["invariants"])
            det.append("    **Инварианты:** " + inv)
        lines += det
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _concept_names(node: Dict[str, Any]) -> set:
    out = set()
    if node.get("name"):
        out.add(str(node["name"]).strip().lower())
    for a in node.get("aliases") or []:
        out.add(str(a).strip().lower())
    return out


def dedup_candidates(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """RFP-034 §B.2/§3.3 — raise merge CANDIDATES to the human; never auto-merge.

    Signals a candidate when two concepts share a name/alias (case-insensitive) or an
    entry in ``realized_by``. Returns ``[{a, b, reason, evidence}]`` sorted. The graph is
    left untouched: merging records a ``same_as`` edge and collapses ids, and that is a
    human decision — the machine shows asymmetry and candidates, the human cuts.
    """
    concepts = graph.get("concepts", {})
    cids = sorted(concepts)
    out: List[Dict[str, Any]] = []
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            a, b = cids[i], cids[j]
            shared_name = _concept_names(concepts[a]) & _concept_names(concepts[b])
            shared_rb = (set(concepts[a].get("realized_by") or [])
                         & set(concepts[b].get("realized_by") or []))
            if shared_name:
                out.append({"a": a, "b": b, "reason": "shared name/alias",
                            "evidence": sorted(shared_name)})
            elif shared_rb:
                out.append({"a": a, "b": b, "reason": "shared realized_by",
                            "evidence": sorted(shared_rb)})
    return out

