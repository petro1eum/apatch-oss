"""SCIP cross-file reference impact (RFP-033 / SPEC-SCIP-IMPACT-1).

Ingests a SCIP index (`.scip`, Sourcegraph Code Intelligence Protocol, Apache-2.0)
with a minimal in-process protobuf-wire decoder and surfaces, for a changed
symbol, the attested requirements whose anchored symbol references it across
files. Advisory only: reference impact is reported as a warning and never marks
a requirement stale. No `protobuf` runtime dependency — only the small subset of
the wire format we need is decoded, and unknown fields are skipped.
"""

from __future__ import annotations

import glob
import os
from typing import Any, Dict, List, Optional, Tuple

# SymbolRole.Definition bit from scip.proto.
_ROLE_DEFINITION = 0x1  # SymbolRole.Definition (scip.proto)

# Field numbers from scip.proto (stable by protobuf contract).
_F_INDEX_DOCUMENTS = 2
_F_DOC_RELATIVE_PATH = 1
_F_DOC_OCCURRENCES = 2
_F_OCC_RANGE = 1
_F_OCC_SYMBOL = 2
_F_OCC_SYMBOL_ROLES = 3


def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    n = len(buf)
    while pos < n:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def _skip(buf: bytes, pos: int, wire_type: int) -> int:
    if wire_type == 0:
        _, pos = _read_varint(buf, pos)
        return pos
    if wire_type == 2:
        length, pos = _read_varint(buf, pos)
        return pos + length
    if wire_type == 5:
        return pos + 4
    if wire_type == 1:
        return pos + 8
    raise ValueError("unsupported wire type " + str(wire_type))


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for one protobuf message.

    value is bytes for length-delimited fields (wire 2) and int for varints
    (wire 0); other wire types are consumed and skipped.
    """
    pos = 0
    n = len(buf)
    while pos < n:
        tag, pos = _read_varint(buf, pos)
        field = tag >> 3
        wt = tag & 0x7
        if wt == 2:
            length, pos = _read_varint(buf, pos)
            yield field, 2, buf[pos:pos + length]
            pos += length
        elif wt == 0:
            value, pos = _read_varint(buf, pos)
            yield field, 0, value
        else:
            pos = _skip(buf, pos, wt)


def _unpack_int32(buf: bytes) -> List[int]:
    out: List[int] = []
    pos = 0
    while pos < len(buf):
        value, pos = _read_varint(buf, pos)
        out.append(value)
    return out


class Occurrence:
    """One SCIP occurrence: a symbol used or defined at a source range."""

    __slots__ = ("symbol", "roles", "range")

    def __init__(self, symbol: str, roles: int, rng: List[int]):
        self.symbol = symbol
        self.roles = roles
        self.range = rng

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & _ROLE_DEFINITION)

    @property
    def start_line(self) -> int:
        return self.range[0] if self.range else -1


def decode_scip(blob: bytes) -> Dict[str, List[Occurrence]]:
    """Decode a `.scip` blob to {relative_path: [Occurrence, ...]}.

    Reads only the documents/occurrences subset and skips unknown fields, so it
    tolerates indexer-specific extras and future proto additions. Malformed
    input never raises: the documents read so far are returned.
    """
    docs: Dict[str, List[Occurrence]] = {}
    try:
        for field, wt, val in _iter_fields(blob):
            if field == _F_INDEX_DOCUMENTS and wt == 2:
                rel = None
                occs: List[Occurrence] = []
                for dfield, dwt, dval in _iter_fields(val):
                    if dfield == _F_DOC_RELATIVE_PATH and dwt == 2:
                        rel = dval.decode("utf-8", "replace")
                    elif dfield == _F_DOC_OCCURRENCES and dwt == 2:
                        symbol = ""
                        roles = 0
                        rng: List[int] = []
                        for ofield, owt, oval in _iter_fields(dval):
                            if ofield == _F_OCC_SYMBOL and owt == 2:
                                symbol = oval.decode("utf-8", "replace")
                            elif ofield == _F_OCC_SYMBOL_ROLES and owt == 0:
                                roles = oval
                            elif ofield == _F_OCC_RANGE and owt == 2:
                                rng = _unpack_int32(oval)
                        occs.append(Occurrence(symbol, roles, rng))
                if rel is not None:
                    docs.setdefault(rel, []).extend(occs)
    except (ValueError, IndexError):
        return docs
    return docs


class ScipModel:
    """Cross-file reference graph over decoded SCIP documents."""

    def __init__(self, documents: Dict[str, List[Occurrence]]):
        self.documents = documents

    def definitions(self, symbol: str) -> List[str]:
        out = [
            rel for rel, occs in self.documents.items()
            if any(o.symbol == symbol and o.is_definition for o in occs)
        ]
        return sorted(out)

    def references(self, symbol: str) -> List[str]:
        out = [
            rel for rel, occs in self.documents.items()
            if any(o.symbol == symbol and not o.is_definition for o in occs)
        ]
        return sorted(out)

    def references_in_file(self, rel_path: str) -> List[Tuple[str, int]]:
        occs = self.documents.get(rel_path, [])
        return [(o.symbol, o.start_line) for o in occs if not o.is_definition]

    def definition_occurrence(self, rel_path: str, name: str) -> Optional[Occurrence]:
        for o in self.documents.get(rel_path, []):
            if o.is_definition and symbol_local_name(o.symbol) == name:
                return o
        return None


def symbol_local_name(scip_symbol: str) -> str:
    """Trailing identifier of a SCIP symbol descriptor.

    Example: `scip-python python repo 1.0 ` + the descriptor `a`/foo(). yields
    `foo`; a type descriptor `Beta#` yields `Beta`; a method `Beta#m().` yields
    `m`. No regex is used so the source stays backslash-free.
    """
    if not scip_symbol:
        return ""
    # Drop method parameter groups "(...)".
    s = scip_symbol
    while "(" in s:
        i = s.index("(")
        j = s.find(")", i + 1)
        if j == -1:
            s = s[:i]
            break
        s = s[:i] + s[j + 1:]
    # Last non-empty token between descriptor terminators / # .
    best = ""
    token = ""
    for ch in s:
        if ch in "/#.":
            if token.strip():
                best = token
            token = ""
        else:
            token += ch
    if token.strip():
        best = token
    return best.strip().strip("`").strip()


def resolve_symbol(model: "ScipModel", rel_path: str, name: str) -> Optional[str]:
    """SCIP symbol id whose Definition occurrence is in rel_path with local name."""
    occ = model.definition_occurrence(rel_path, name)
    return occ.symbol if occ else None


def load_scip_model(root: str) -> Optional["ScipModel"]:
    """Load a `.scip` index from known locations, or None. Never raises.

    Searches `.apatch/scip/index.scip`, `index.scip`, then any `*.scip` at root.
    Returns None when nothing parses to a non-empty document set.
    """
    candidates = [
        os.path.join(root, ".apatch", "scip", "index.scip"),
        os.path.join(root, "index.scip"),
    ]
    try:
        for extra in sorted(glob.glob(os.path.join(root, "*.scip"))):
            if extra not in candidates:
                candidates.append(extra)
    except OSError:
        pass
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                blob = fh.read()
            docs = decode_scip(blob)
            if docs:
                return ScipModel(docs)
        except (OSError, ValueError):
            continue
    return None


def scip_impacted_requirements(
    changed_anchors: List[Tuple[str, str]],
    requirement_anchors: Dict[str, Dict[str, Dict[str, str]]],
    model: Optional["ScipModel"],
    root: str,
) -> List[Dict[str, Any]]:
    """Advisory cross-file impact of changed symbols on attested requirements.

    changed_anchors: [(rel_path, symbol_name), ...] that drifted.
    requirement_anchors: {req_id: {rel_path: {symbol_name: hash}}}.

    Returns warnings [{requirement, impacted_by, via_symbol, reference_file}] for
    requirements whose anchored symbol body encloses a reference to a changed
    symbol. Never marks anything stale; returns [] when model is None.
    """
    if model is None:
        return []
    from apatch.symbol_anchor import extract_file_symbols

    warnings: List[Dict[str, Any]] = []
    for changed_path, changed_name in changed_anchors:
        symbol_id = resolve_symbol(model, changed_path, changed_name)
        if not symbol_id:
            continue
        ref_files = set(model.references(symbol_id))
        if not ref_files:
            continue
        for req_id, files in (requirement_anchors or {}).items():
            for rel_path, names in (files or {}).items():
                if rel_path not in ref_files:
                    continue
                # SCIP ranges are 0-based; extract_file_symbols spans are 1-based.
                ref_lines = [
                    ln + 1
                    for sym, ln in model.references_in_file(rel_path)
                    if sym == symbol_id
                ]
                if not ref_lines:
                    continue
                spans = extract_file_symbols(os.path.join(root, rel_path))
                for name in names:
                    span = spans.get(name)
                    if not span:
                        continue
                    if any(span["start_line"] <= ln <= span["end_line"] for ln in ref_lines):
                        warnings.append({
                            "requirement": req_id,
                            "impacted_by": changed_path + "::" + changed_name,
                            "via_symbol": rel_path + "::" + name,
                            "reference_file": rel_path,
                        })
    return warnings
