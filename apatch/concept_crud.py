"""SPEC-CONCEPT-CRUD-1 — RFP-034 §6 create / update / delete over concept fences.

The graph had Read (compile/graph/coverage) and two narrow human edits (clarify, merge).
This is the rest of CRUD: create a concept, edit its fields, delete it — as **pure markdown
transforms** over the canon ``docs/concepts/*.md`` fences. The functions return new doc text;
they never touch disk and never notarize. The CLI turns a transform into a governed needle,
so every C/U/D lands through apatch + TrustChain (the same path a future web UI will call).

Pure core + governed needle = the trust model holds: there is no way to mutate a concept
that skips the ledger.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

_FENCE_RE = re.compile(r"<!--\s*@cid:(\S+?)\s*-->\s*\n```concept\n(.*?)\n```", re.S)
_MARK_RE = re.compile(r"\n<!--\s*@(?:cid|sid):")


def _aslist(v: Any) -> List[Any]:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _concept_span(text: str, cid: str):
    """Return ``(start, end, body_yaml, definition)`` for ``cid`` (end exclusive, covering the
    fence plus its definition up to the next marker/EOF), or ``None``."""
    for m in _FENCE_RE.finditer(text):
        if m.group(1) == cid:
            tail = text[m.end():]
            nxt = _MARK_RE.search(tail)
            def_end = m.end() + (nxt.start() if nxt else len(tail))
            definition = (tail[: nxt.start()] if nxt else tail).strip()
            return (m.start(), def_end, m.group(2), definition)
    return None


def has_concept(text: str, cid: str) -> bool:
    return _concept_span(text, cid) is not None


def serialize_concept(cid: str, *, name: Optional[str] = None, c4: Optional[str] = None,
                      aliases: Optional[List[str]] = None, realized_by: Optional[List[str]] = None,
                      relations: Optional[List[Dict[str, Any]]] = None,
                      invariants: Optional[List[Any]] = None,
                      definition: Optional[str] = None) -> str:
    """Render a concept node as a ``<!-- @cid -->`` + ```concept``` fence (+ definition prose),
    matching the house style so compile round-trips it."""
    lines = ["<!-- @cid:%s -->" % cid, "```concept"]
    if name:
        lines.append("name: %s" % name)
    if c4:
        lines.append("c4: %s" % c4)
    if aliases:
        lines.append("aliases: [%s]" % ", ".join(str(a) for a in aliases))
    if realized_by:
        lines.append("realized_by:")
        lines += ["  - %s" % r for r in realized_by]
    if relations:
        lines.append("relations:")
        for r in relations:
            parts = ", ".join("%s: %s" % (k, v) for k, v in r.items())
            lines.append("  - {%s}" % parts)
    if invariants:
        lines.append("invariants:")
        for inv in invariants:
            if isinstance(inv, dict):
                parts = ", ".join("%s: %s" % (k, v) for k, v in inv.items())
                lines.append("  - {%s}" % parts)
            else:
                lines.append("  - %s" % inv)
    lines.append("```")
    block = "\n".join(lines)
    if definition and definition.strip():
        block += "\n" + definition.strip()
    return block


def add_concept(text: str, cid: str, **fields: Any) -> str:
    """Append a new concept fence. Raises if ``cid`` already exists (no silent overwrite)."""
    if has_concept(text, cid):
        raise ValueError("concept already exists: " + cid)
    block = serialize_concept(cid, **fields)
    base = text.rstrip()
    return (base + "\n\n" + block + "\n") if base else (block + "\n")


def remove_concept(text: str, cid: str) -> str:
    """Delete a concept fence (and its definition). Raises if ``cid`` is unknown."""
    span = _concept_span(text, cid)
    if not span:
        raise ValueError("no such concept: " + cid)
    start, end, _, _ = span
    head = text[:start].rstrip()
    tail = text[end:].lstrip("\n")
    if head and tail:
        return head + "\n\n" + tail
    return (head or tail).rstrip() + "\n" if (head or tail) else ""


def update_concept(text: str, cid: str, *, name: Optional[str] = None, c4: Optional[str] = None,
                   definition: Optional[str] = None,
                   add_relations: Optional[List[Dict[str, Any]]] = None,
                   remove_relations_to: Optional[List[str]] = None,
                   add_aliases: Optional[List[str]] = None,
                   add_realized_by: Optional[List[str]] = None) -> str:
    """Edit an existing concept's fields in place (re-serialised). Raises if ``cid`` is unknown.

    ``name``/``c4``/``definition`` replace; ``add_*`` extend; ``remove_relations_to`` drops
    relations whose ``to`` is listed. The fence is the unit; below-field history is the ledger's."""
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML required")
    span = _concept_span(text, cid)
    if not span:
        raise ValueError("no such concept: " + cid)
    start, end, body, old_def = span
    data = yaml.safe_load(body) or {}

    if name is not None:
        data["name"] = name
    if c4 is not None:
        data["c4"] = c4
    aliases = _aslist(data.get("aliases")) + list(add_aliases or [])
    realized_by = _aslist(data.get("realized_by")) + list(add_realized_by or [])
    relations = [r for r in _aslist(data.get("relations")) if isinstance(r, dict)]
    if remove_relations_to:
        relations = [r for r in relations if r.get("to") not in set(remove_relations_to)]
    relations += list(add_relations or [])
    new_def = old_def if definition is None else definition

    block = serialize_concept(
        cid, name=data.get("name"), c4=data.get("c4"),
        aliases=aliases or None, realized_by=realized_by or None,
        relations=relations or None, invariants=_aslist(data.get("invariants")) or None,
        definition=new_def)
    return text[:start] + block + text[end:]