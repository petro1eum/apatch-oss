"""Executable Specifications (RFP-007): SPEC.md → Requirements → Coverage.

A specification document becomes a first-class governed object. Each requirement is
an artifact ``spec:<SPEC_ID>#<REQ_ID>@<content_hash>`` (RFP-006 token), and its
lifecycle state is **derived** from the attested TrustChain ledger — never stored as a
mutable, agent-writable field (RFP-005 reference-monitor invariant: no "checkbox the
monitored party can tick itself").

Layering::

    SPEC.md  →  Requirements  →  Intent  →  Session  →  Mutation  →  Verification  →  Attestation
    (text)      (this module)    ──────────────  reused RFP-004 / RFP-006  ──────────────

Derived requirement states (all but ``blocked`` come straight from ledger coverage):

* ``pending``      — no intent / mutation linked to ``spec:ID#R``
* ``in_progress``  — intent and/or mutation linked, but no attestation yet
* ``attested``     — mutation **and** attestation linked (== verified-and-signed in the
                     governed lifecycle, where ``apatch_attest`` only commits after verify)
* ``stale``        — was attested, but the requirement's current ``content_hash`` differs
                     from the hash recorded at attestation time (the spec text changed)
* ``blocked``      — reserved: an explicit, attested block event (not emitted in MVP)

The acceptance check of each requirement (``verify:``) is surfaced to the agent so that
"done" means *its* check is green, not merely "some code was touched".
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Heading whose first token looks like a requirement id (ends in a digit):
# `## R1`, `### FR-2 — title`, `## NFR3: title`, `## REQ-10 title`.
_REQ_HEADING_RE = re.compile(
    r"^(?P<hashes>#{2,4})\s+(?P<rid>[A-Za-z][A-Za-z0-9._-]*\d)\b\s*(?P<rest>.*)$"
)
_H1_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$")
# Inline acceptance check in the heading: `(verify: pytest tests/x.py)`.
_HEADING_VERIFY_RE = re.compile(r"\(\s*verify\s*:\s*(?P<cmd>[^)]+?)\s*\)", re.IGNORECASE)
_LEADING_MARKERS_RE = re.compile(r"^[>\-*\s]+")
_VERIFY_LINE_RE = re.compile(r"^verify\s*:\s*(?P<cmd>.+?)\s*$", re.IGNORECASE)
# A `cd <dir>` inside a verify command. spec execute runs verify from the repo root,
# so a cd into a path that does not exist there fails only at attest time (very late).
_CD_RE = re.compile(r"\bcd\s+(?P<path>[^\s&;|]+)")


def _cd_reenters_workspace(root: str, path: str) -> bool:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return False
    root_parts = [p for p in os.path.abspath(root).replace("\\", "/").split("/") if p]
    return len(root_parts) >= len(parts) and root_parts[-len(parts):] == parts
# A requirement may declare which observed-reality records it discharges (RFP-005):
# inline `(discharges: REC-1, REC-2)` in the heading, or a `discharges:` body line.
_HEADING_DISCHARGES_RE = re.compile(r"\(\s*discharges\s*:\s*(?P<ids>[^)]+?)\s*\)", re.IGNORECASE)
_DISCHARGES_LINE_RE = re.compile(r"^\s*[-*>\s]*\**discharges\**\s*:\s*(?P<ids>.+?)\s*$", re.IGNORECASE)
_HEADING_OWNS_RE = re.compile(r"\(\s*owns\s*:\s*(?P<paths>[^)]+?)\s*\)", re.IGNORECASE)
_OWNS_LINE_RE = re.compile(
    r"^\s*[-*>\s]*\**owns\**\s*:\s*(?P<paths>.+?)\s*$", re.IGNORECASE
)
_STRICT_OWNERSHIP_RE = re.compile(
    r"^\s*>?\s*\**ownership\s+mode\s*:\**\s*strict\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_STATUS_LINE_RE = re.compile(
    r"^\s*>?\s*\**status\s*:\**\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_COMPLETION_CLAIM_RE = re.compile(
    r"^(?:implemented|complete|completed|done)\b", re.IGNORECASE
)


def _block_discharges(heading: str, body_lines) -> tuple:
    raw = None
    hm = _HEADING_DISCHARGES_RE.search(heading)
    if hm:
        raw = hm.group("ids")
    if not raw:
        for bl in body_lines:
            lm = _DISCHARGES_LINE_RE.match(bl)
            if lm:
                raw = lm.group("ids")
                break
    ids = []
    if raw:
        for part in re.split(r"[,\s]+", raw.strip()):
            p = part.strip().strip("`\'\"")
            if p:
                ids.append(p)
    return tuple(dict.fromkeys(ids))


def _block_owns(heading: str, body_lines) -> tuple:
    raw = None
    hm = _HEADING_OWNS_RE.search(heading)
    if hm:
        raw = hm.group("paths")
    if not raw:
        for bl in body_lines:
            lm = _OWNS_LINE_RE.match(bl)
            if lm:
                raw = lm.group("paths")
                break
    paths = []
    if raw:
        for part in raw.split(","):
            path = part.strip().strip("`'\" ")
            if path:
                paths.append(path)
    return tuple(paths)


def _line_verify(line: str) -> Optional[str]:
    """Extract an acceptance check from one body line, tolerant of markdown.

    Accepts ``verify: <cmd>``, bold ``**verify:** <cmd>``, bullets ``- verify: <cmd>``,
    blockquote ``> verify: <cmd>``, parenthesized ``(verify: <cmd>)``, and inline-code forms.
    """
    s = line.strip()
    s = _LEADING_MARKERS_RE.sub("", s)        # bullets / blockquote
    s = s.strip("()` ")                        # wrapping parens / inline code
    s = s.replace("**", "").replace("`", "")   # emphasis markers
    s = s.strip("()` ")                        # parens revealed after emphasis strip
    m = _VERIFY_LINE_RE.match(s.strip())
    if m:
        return m.group("cmd").strip().rstrip(")").strip()
    return None
_TITLE_SEP_RE = re.compile(r"^[\s:\-\u2014\u2013.]+")


@dataclass(frozen=True)
class Requirement:
    """One acceptance-bearing requirement parsed from a spec document."""

    id: str
    title: str
    content_hash: str
    verify: Optional[str] = None
    line: int = 0
    discharges: tuple = ()
    owns: tuple = ()

    def artifact_id(self, spec_id: str) -> str:
        """``SPEC_ID#REQ_ID`` — the ``id`` part of the artifact token."""
        return f"{spec_id}#{self.id}"

    def token(self, spec_id: str, *, with_hash: bool = True) -> str:
        """RFP-006 artifact token ``spec:SPEC_ID#REQ_ID[@hash]``."""
        base = f"spec:{self.artifact_id(spec_id)}"
        if with_hash and self.content_hash:
            return f"{base}@{self.content_hash}"
        return base


@dataclass(frozen=True)
class Spec:
    """Parsed specification: an id, its source, and its requirements."""

    id: str
    requirements: List[Requirement]
    source_path: Optional[str] = None
    content_hash: str = ""
    title: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    strict_ownership: bool = False


def _hash_text(text: str) -> str:
    """Stable short content hash of normalized text (trailing ws stripped per line)."""
    norm = "\n".join(line.rstrip() for line in (text or "").strip().splitlines())
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _clean_title(rest: str) -> str:
    rest = _HEADING_VERIFY_RE.sub("", rest).strip()
    rest = _TITLE_SEP_RE.sub("", rest).strip()
    return rest


def parse_spec(
    text: str,
    *,
    spec_id: Optional[str] = None,
    source_path: Optional[str] = None,
) -> Spec:
    """Parse spec markdown into a :class:`Spec`.

    Conventions:

    * Spec id: explicit ``spec_id`` > first ``# H1`` token > source filename stem.
    * Requirement: an ``##``/``###``/``####`` heading whose first token ends in a digit
      (``R1``, ``FR-2``, ``NFR3``, ``REQ-10``). Title is the rest of the heading.
    * Acceptance check (optional): ``(verify: <cmd>)`` in the heading, or a ``verify:``
      line in the requirement body.
    * ``content_hash`` is computed per requirement over its block (heading → next
      requirement/section heading), so editing one requirement only marks that one stale.
    """
    lines = (text or "").splitlines()
    warnings: List[str] = []

    doc_title: Optional[str] = None
    for ln in lines:
        m = _H1_RE.match(ln)
        if m:
            doc_title = m.group("title").strip()
            break

    resolved_id = (spec_id or "").strip()
    if not resolved_id and doc_title:
        # First whitespace token of the H1 (e.g. "# SPEC-42 — Avatar Economics").
        resolved_id = doc_title.split()[0].strip()
    if not resolved_id and source_path:
        resolved_id = os.path.splitext(os.path.basename(source_path))[0]
    if not resolved_id:
        resolved_id = "SPEC"

    # Collect requirement heading positions first.
    heads: List[Tuple[int, str, str]] = []  # (line_idx, rid, rest)
    for i, ln in enumerate(lines):
        m = _REQ_HEADING_RE.match(ln)
        if m:
            heads.append((i, m.group("rid"), m.group("rest")))

    requirements: List[Requirement] = []
    seen: Dict[str, int] = {}
    for idx, (line_idx, rid, rest) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        block = "\n".join(lines[line_idx:end])
        verify = None
        hm = _HEADING_VERIFY_RE.search(lines[line_idx])
        if hm:
            verify = hm.group("cmd").strip()
        if not verify:
            for body_line in lines[line_idx + 1:end]:
                cmd = _line_verify(body_line)
                if cmd:
                    verify = cmd
                    break
        discharges = _block_discharges(lines[line_idx], lines[line_idx + 1:end])
        owns = _block_owns(lines[line_idx], lines[line_idx + 1:end])
        if rid in seen:
            warnings.append(f"duplicate requirement id {rid!r} (line {line_idx + 1})")
            continue
        seen[rid] = line_idx
        requirements.append(
            Requirement(
                id=rid,
                title=_clean_title(rest),
                content_hash=_hash_text(block),
                verify=verify,
                line=line_idx + 1,
                discharges=discharges,
                owns=owns,
            )
        )

    if not requirements:
        warnings.append(
            "no requirements found — use '## R1', '## FR-2', '## NFR3' headings"
        )

    return Spec(
        id=resolved_id,
        requirements=requirements,
        source_path=source_path,
        content_hash=_hash_text(text),
        title=doc_title,
        warnings=warnings,
        strict_ownership=bool(_STRICT_OWNERSHIP_RE.search(text or "")),
    )


def parse_spec_file(path: str, *, spec_id: Optional[str] = None) -> Spec:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return parse_spec(text, spec_id=spec_id, source_path=path)


# --- status derivation (pure, from ledger entries) ---------------------------------

_OPEN_STATES = ("pending", "in_progress", "stale")


def _recorded_hashes(bucket: Dict[str, Any], artifact_key: str) -> set:
    hashes: set = set()
    for summ in (bucket.get("attestations") or []) + (bucket.get("mutations") or []):
        for a in summ.get("artifacts") or []:
            if f"{a.get('kind')}:{a.get('id')}" != artifact_key:
                continue
            h = a.get("content_hash")
            if h:
                hashes.add(str(h))
    return hashes


def _requirement_state(spec_id: str, req: Requirement, bucket: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    has_intent = bool(bucket and bucket.get("intents"))
    has_mut = bool(bucket and bucket.get("mutations"))
    has_att = bool(bucket and bucket.get("attestations"))
    has_covered_by = bool(bucket and bucket.get("covered_by"))

    state = "pending"
    if has_att and (has_mut or has_covered_by):
        state = "attested"
    elif has_mut or has_intent:
        state = "in_progress"

    stale = False
    attested_hashes: List[str] = []
    artifact_key = f"spec:{req.artifact_id(spec_id)}"
    if state == "attested" and bucket is not None:
        recorded = _recorded_hashes(bucket, artifact_key)
        attested_hashes = sorted(recorded)
        if req.content_hash and recorded and req.content_hash not in recorded:
            stale = True
            state = "stale"

    return {
        "id": req.id,
        "title": req.title,
        "verify": req.verify,
        "state": state,
        "artifact": artifact_key,
        "content_hash": req.content_hash,
        "has_intent": has_intent,
        "has_mutations": has_mut,
        "has_attestation": has_att,
        "stale": stale,
        "attested_hashes": attested_hashes,
    }


def spec_status_from_entries(spec: Spec, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive per-requirement status from raw ledger entries (pure / testable)."""
    from apatch.traceability import build_traceability_index

    index = build_traceability_index(entries or [])
    by_art = index.get("by_artifact") or {}

    rows: List[Dict[str, Any]] = []
    for req in spec.requirements:
        key = f"spec:{req.artifact_id(spec.id)}"
        rows.append(_requirement_state(spec.id, req, by_art.get(key)))

    buckets: Dict[str, List[str]] = {
        "pending": [],
        "in_progress": [],
        "attested": [],
        "stale": [],
        "blocked": [],
    }
    for r in rows:
        buckets.setdefault(r["state"], []).append(r["id"])

    total = len(rows)
    done = len(buckets["attested"])
    return {
        "ok": True,
        "spec": spec.id,
        "title": spec.title,
        "source_path": spec.source_path,
        "spec_content_hash": spec.content_hash,
        "requirements": rows,
        "summary": {
            "total": total,
            "attested": done,
            "in_progress": len(buckets["in_progress"]),
            "pending": len(buckets["pending"]),
            "stale": len(buckets["stale"]),
            "blocked": len(buckets["blocked"]),
            "percent_complete": round(100.0 * done / total, 1) if total else 0.0,
        },
        "complete": buckets["attested"],
        "in_progress": buckets["in_progress"],
        "pending": buckets["pending"],
        "stale": buckets["stale"],
        "blocked": buckets["blocked"],
        "done": total > 0 and done == total,
        "warnings": spec.warnings,
    }


_DECLARED_SPEC_RE = re.compile(r"\bspec:([A-Za-z][A-Za-z0-9._-]*)", re.IGNORECASE)
_BUILD_ONLY_RE = re.compile(
    r"^(?:npm\s+run\s+build|yarn\s+build|pnpm\s+build|tsc|cmake|make)\b", re.IGNORECASE
)


def spec_lint(spec: Spec, *, raw_text: str = "", root: Optional[str] = None) -> Dict[str, Any]:
    """Validate a spec against the authoring standard (advisory, ``passed`` = no errors).

    Catches the failure modes that silently break executable-spec tracking:

    * ``id_mismatch`` (error): the ``spec:<id>`` declared in the doc body differs from the
      id apatch parses from the H1 — anchors like ``spec:<declared>#R1`` would never match.
    * ``missing_verify`` (warn): a requirement with no acceptance check ('attested' would
      only mean "code touched", not "requirement satisfied").
    * ``generic_verify`` (warn): the same verify command reused across requirements.
    * ``weak_verify`` (info): a build/compile-only verify that cannot prove the requirement.
    * ``parse`` (warn): duplicate ids / no requirements (from the parser).
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    info: List[Dict[str, Any]] = []

    declared = {m.group(1) for m in _DECLARED_SPEC_RE.finditer(raw_text or "")}
    mismatched = sorted(d for d in declared if d != spec.id)
    if mismatched:
        errors.append(
            {
                "code": "id_mismatch",
                "message": (
                    f"document declares spec:{mismatched} but apatch parses id {spec.id!r} "
                    "from the H1 — align the H1 first token with the spec: artifact, else "
                    "requirement anchors will never match coverage"
                ),
                "detail": {"parsed": spec.id, "declared": mismatched},
            }
        )

    for w in spec.warnings:
        warnings.append({"code": "parse", "message": w})

    if spec.strict_ownership:
        for requirement in spec.requirements:
            if not requirement.owns:
                errors.append(
                    {
                        "code": "missing_ownership",
                        "requirement": requirement.id,
                        "message": (
                            f"{requirement.id} is in a strict ownership SPEC but has no "
                            "owns: declaration"
                        ),
                    }
                )
                continue
            seen_owns = set()
            for declared_path in requirement.owns:
                normalized = declared_path.replace("\\", "/")
                invalid = (
                    not normalized
                    or normalized.startswith(("/", "./", "../"))
                    or "/../" in f"/{normalized}/"
                    or "\\" in declared_path
                    or any(ch in normalized[:-3] for ch in "*?[")
                    or ("*" in normalized and not normalized.endswith("/**"))
                )
                if invalid:
                    errors.append(
                        {
                            "code": "invalid_ownership_path",
                            "requirement": requirement.id,
                            "message": (
                                f"{requirement.id} owns invalid repository-relative path "
                                f"{declared_path!r}"
                            ),
                        }
                    )
                if normalized in seen_owns:
                    errors.append(
                        {
                            "code": "duplicate_ownership_path",
                            "requirement": requirement.id,
                            "message": (
                                f"{requirement.id} repeats ownership path {declared_path!r}"
                            ),
                        }
                    )
                seen_owns.add(normalized)

    by_verify: Dict[str, List[str]] = {}
    for r in spec.requirements:
        if not r.title:
            warnings.append(
                {"code": "missing_title", "requirement": r.id, "message": f"{r.id} has no title"}
            )
        if not r.verify:
            warnings.append(
                {
                    "code": "missing_verify",
                    "requirement": r.id,
                    "message": (
                        f"{r.id} has no acceptance check — add '(verify: <cmd>)' so "
                        "'attested' means its test is green, not just that code was touched"
                    ),
                }
            )
            continue
        by_verify.setdefault(r.verify, []).append(r.id)
        if root:
            for _m in _CD_RE.finditer(r.verify):
                _p = _m.group("path").strip().strip("'\"")
                if _p in (".", "..") or _p.startswith(("/", "~", "$")):
                    continue
                if _cd_reenters_workspace(root, _p):
                    warnings.append(
                        {
                            "code": "verify_cwd_reenters_workspace",
                            "requirement": r.id,
                            "message": (
                                f"{r.id} verify cd's into {_p!r}, but the workspace already "
                                "appears to be that nested root — this commonly turns "
                                "search-root conformance into 'can't cd to opensearch/search'. "
                                "Drop the redundant cd or run conformance from the parent root."
                            ),
                        }
                    )
                    continue
                if not os.path.isdir(os.path.join(os.path.abspath(root), _p)):
                    warnings.append(
                        {
                            "code": "verify_cwd_mismatch",
                            "requirement": r.id,
                            "message": (
                                f"{r.id} verify cd's into {_p!r}, which does not exist from "
                                "the workspace root — spec execute runs verify from the repo "
                                "root, not the spec's directory, so this fails only at attest "
                                "time (very late). Use a path relative to the repo root, or "
                                "drop 'cd' from verify."
                            ),
                        }
                    )
        if _BUILD_ONLY_RE.match(r.verify.strip()):
            info.append(
                {
                    "code": "weak_verify",
                    "requirement": r.id,
                    "message": (
                        f"{r.id} verify {r.verify!r} is build/compile-only; a passing build "
                        "does not prove the requirement — prefer a requirement-specific test"
                    ),
                }
            )
    for cmd, ids in by_verify.items():
        if len(ids) > 1:
            warnings.append(
                {
                    "code": "generic_verify",
                    "message": (
                        f"verify {cmd!r} is reused by {ids}; acceptance checks should be "
                        "requirement-specific so each requirement is independently provable"
                    ),
                    "detail": {"command": cmd, "requirements": ids},
                }
            )

    return {
        "ok": True,
        "spec": spec.id,
        "passed": not errors,
        "counts": {"errors": len(errors), "warnings": len(warnings), "info": len(info)},
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "requirements": len(spec.requirements),
    }


def next_open_requirement(status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """First requirement (document order) that still needs work."""
    for r in status.get("requirements") or []:
        if r.get("state") in _OPEN_STATES:
            return r
    return None


# --- workspace facades (load ledger + spec, used by MCP/CLI) ------------------------


_SPEC_REGISTRY_REL = os.path.join(".apatch", "specs")


def _registry_path(target_dir: str, spec_id: str) -> str:
    return os.path.join(os.path.abspath(target_dir), _SPEC_REGISTRY_REL, f"{spec_id}.json")


def _remember_spec(target_dir: str, spec: Spec) -> None:
    """Cache spec_id → source_path so later calls can omit the path.

    Merges into an existing RFP-014 needles registry (``.apatch/specs/<ID>.json``)
    when present — must not wipe ``requirements`` written by ``update_spec_registry``.
    """
    if not spec.source_path:
        return
    try:
        import json

        path = _registry_path(target_dir, spec.id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rel = os.path.relpath(spec.source_path, os.path.abspath(target_dir))
        data: Dict[str, Any] = {}
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    data = existing
            except (OSError, json.JSONDecodeError):
                data = {}
        data["id"] = spec.id
        data["source_path"] = rel
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError:
        pass


def _registered_source(target_dir: str, spec_id: str) -> Optional[str]:
    import json

    path = _registry_path(target_dir, spec_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        src = (data or {}).get("source_path")
        if src:
            abs_src = src if os.path.isabs(src) else os.path.join(os.path.abspath(target_dir), src)
            return abs_src if os.path.isfile(abs_src) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _discover_spec_path(target_dir: str, spec_id: str) -> Optional[str]:
    root = os.path.abspath(target_dir)
    registered = _registered_source(target_dir, spec_id)
    if registered:
        return registered
    candidates = [
        os.path.join(root, "docs", "specs", f"{spec_id}.md"),
        os.path.join(root, "specs", f"{spec_id}.md"),
        os.path.join(root, f"{spec_id}.md"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _load_spec(
    target_dir: str,
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Spec:
    if spec_path:
        abs_path = spec_path if os.path.isabs(spec_path) else os.path.join(os.path.abspath(target_dir), spec_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f"spec file not found: {spec_path}")
        parsed = parse_spec_file(abs_path, spec_id=spec or None)
        _remember_spec(target_dir, parsed)
        return parsed
    if spec:
        found = _discover_spec_path(target_dir, spec)
        if not found:
            raise FileNotFoundError(
                f"spec {spec!r} not found; pass spec_path or place it at docs/specs/{spec}.md"
            )
        parsed = parse_spec_file(found, spec_id=spec)
        _remember_spec(target_dir, parsed)
        return parsed
    raise ValueError("provide spec (id) or spec_path")


def _ledger_entries(target_dir: str) -> Tuple[List[Dict[str, Any]], bool]:
    from apatch.trustchain_helper import TrustChainHelper

    tc = TrustChainHelper(target_dir, auto_init=False)
    if not tc.has_trustchain():
        return [], False
    return list(tc.iter_ledger_entries()), True


def spec_status_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Coverage matrix for a spec: per-requirement derived state + summary."""
    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    try:
        from apatch.spec_coverage import spec_status_with_coverage  # R4 integration

        return spec_status_with_coverage(
            target_dir, spec=spec, spec_path=spec_path
        )
    except ImportError:
        pass
    entries, ledger_active = _ledger_entries(target_dir)
    out = spec_status_from_entries(parsed, entries)
    out["ledger_active"] = ledger_active
    return out


def _claims_completion(value: str) -> bool:
    normalized = re.sub(r"[`*_]", "", value or "").strip()
    return bool(_COMPLETION_CLAIM_RE.match(normalized))


def _document_completion_claims(raw_text: str, spec: Spec) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    """Return editable completion claims; ledger remains the only state authority."""
    top_level = None
    match = _STATUS_LINE_RE.search(raw_text or "")
    if match and _claims_completion(match.group("value")):
        top_level = match.group("value").strip()

    known = {requirement.id: requirement.id for requirement in spec.requirements}
    known.update({requirement.id.lower(): requirement.id for requirement in spec.requirements})
    requirement_claims: List[Tuple[str, str]] = []
    status_column: Optional[int] = None
    for line in (raw_text or "").splitlines():
        if not line.lstrip().startswith("|"):
            status_column = None
            continue
        cells = [cell.strip().strip("`*_ ") for cell in line.strip().strip("|").split("|")]
        normalized_cells = [cell.lower().replace("_", " ").strip() for cell in cells]
        if status_column is None:
            status_column = next(
                (
                    index
                    for index, cell in enumerate(normalized_cells)
                    if cell in {"status", "state", "implementation status"}
                ),
                None,
            )
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in normalized_cells):
            continue
        if status_column >= len(cells):
            continue
        requirement_id = next(
            (known[cell] for cell in cells if cell in known),
            None,
        )
        if not requirement_id:
            continue
        claim = cells[status_column]
        if _claims_completion(claim):
            requirement_claims.append((requirement_id, claim))
    return top_level, requirement_claims


def spec_lint_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    include_needles_scaffold: bool = True,
    include_rfp_gates: bool = True,
) -> Dict[str, Any]:
    """Lint SPEC.md and return the agent onboarding bundle when ``passed``.

    Always: format lint (H1 id, ``spec:`` artifact, per-Rk ``(verify:)``).

    When ``include_needles_scaffold`` (default) and ``passed``: ``plan_scaffold``,
    ``needles_scaffold``, ``agent_next`` (RFP-024).

    When ``include_rfp_gates`` (default) and SPEC has ``## RFP traceability``:
    ``rfp_lint`` + ``rfp_coverage`` (RFP-023) — same gates as standalone MCP tools.

    Agents: one ``apatch_spec_lint(spec=…)`` call replaces separate rfp/coverage/scaffold
    steps. See ``docs/agent-onboarding.md``.
    """
    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    raw = ""
    if parsed.source_path and os.path.isfile(parsed.source_path):
        try:
            with open(parsed.source_path, encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            raw = ""
    out = spec_lint(parsed, raw_text=raw, root=target_dir)
    derived_status = spec_status_workspace(
        target_dir,
        spec=parsed.id,
        spec_path=parsed.source_path,
    )
    if derived_status.get("ok"):
        top_claim, requirement_claims = _document_completion_claims(raw, parsed)
        if top_claim and not derived_status.get("done"):
            out["errors"].append(
                {
                    "code": "unattested_spec_completion",
                    "message": (
                        f"editable status {top_claim!r} claims {parsed.id} is complete, "
                        "but the TrustChain ledger does not derive every requirement as attested"
                    ),
                }
            )
        states = {
            row.get("id"): row.get("state")
            for row in derived_status.get("requirements") or []
        }
        for requirement_id, claim in requirement_claims:
            if states.get(requirement_id) == "attested":
                continue
            out["errors"].append(
                {
                    "code": "unattested_requirement_completion",
                    "requirement": requirement_id,
                    "message": (
                        f"editable table claim {claim!r} marks {requirement_id} complete, "
                        f"but the ledger-derived state is {states.get(requirement_id, 'unknown')!r}"
                    ),
                }
            )
        out["passed"] = not out["errors"]
        out["counts"]["errors"] = len(out["errors"])
        out["derived_status"] = {
            "done": bool(derived_status.get("done")),
            "requirements": states,
        }
    if include_needles_scaffold and out.get("ok") and out.get("passed"):
        try:
            from apatch.spec_needles_scaffold import spec_needles_scaffold_workspace

            scaffold = spec_needles_scaffold_workspace(
                target_dir,
                spec=parsed.id,
                spec_path=parsed.source_path,
                skip_lint=True,
            )
            if scaffold.get("ok"):
                out["needles_scaffold"] = scaffold
                out["plan_scaffold"] = scaffold.get("plan_scaffold")
                out["autonomy_boundary"] = scaffold.get("autonomy_boundary")
                if scaffold.get("agent_next"):
                    out["agent_next"] = scaffold.get("agent_next")
        except Exception:
            pass
    if include_rfp_gates and out.get("ok") and out.get("passed") and raw:
        try:
            from apatch.rfp_coverage import (
                infer_rfp_id_from_spec,
                rfp_lint_workspace,
                rfp_spec_coverage_workspace,
                spec_has_rfp_traceability,
            )

            if spec_has_rfp_traceability(raw):
                rfp_id = infer_rfp_id_from_spec(raw)
                if rfp_id:
                    rfp_l = rfp_lint_workspace(target_dir, rfp=rfp_id)
                    out["rfp_lint"] = rfp_l
                    if not rfp_l.get("passed"):
                        out["passed"] = False
                        out.setdefault("errors", []).append(
                            {
                                "code": "rfp_lint",
                                "message": f"RFP {rfp_id} Acceptance lint failed",
                                "detail": rfp_l,
                            }
                        )
                    cov = rfp_spec_coverage_workspace(
                        target_dir,
                        rfp=rfp_id,
                        spec=parsed.id,
                        spec_path=parsed.source_path,
                    )
                    out["rfp_coverage"] = cov
                    if not cov.get("passed"):
                        out["passed"] = False
                        out.setdefault("errors", []).append(
                            {
                                "code": "rfp_coverage_gap",
                                "message": "RFP Acceptance rows not covered by SPEC traceability",
                                "detail": {"gaps": cov.get("gaps") or []},
                            }
                        )
        except Exception:
            pass
    return out


def spec_next_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Next open requirement + its acceptance check + ready-to-run session_start."""
    status = spec_status_workspace(target_dir, spec=spec, spec_path=spec_path)
    if not status.get("ok"):
        return status
    nxt = next_open_requirement(status)
    spec_id = status["spec"]
    remaining = [
        r["id"]
        for r in status["requirements"]
        if r["state"] in _OPEN_STATES
    ]
    result = {
        "ok": True,
        "spec": spec_id,
        "next": nxt,
        "remaining": remaining,
        "done": status.get("done", False),
        "summary": status.get("summary"),
    }
    if nxt:
        token = f"{spec_id}#{nxt['id']}"
        result["session_start"] = f"apatch_session_start(requirement={token!r})"
        result["verify"] = nxt.get("verify")
        if len(remaining) >= 2:
            result["recommended_tool"] = "apatch_spec_run"
            result["recommended_workflow"] = (
                f"RFP-009: apatch_spec_run(spec={spec_id!r}, dry_run=true) then "
                f"requirements={{Rk: {{needles: [...]}}}} — whole spec, not N× manual jsonl"
            )
            result["anti_pattern"] = (
                "Do NOT hand-stage patches.jsonl per Rk; spec_run runs governed cycles internally."
            )
        result["hint"] = (
            f"Start a governed session for {token}, implement, run its verify"
            + (f" ({nxt['verify']})" if nxt.get("verify") else "")
            + ", then apatch_attest. Status will flip to 'attested' once linked in the ledger."
        )
        if len(remaining) >= 2:
            result["hint"] = (
                f"{len(remaining)} pending Rk — prefer apatch_spec_run for whole spec (RFP-009). "
                f"Single-Rk path: {result['hint']}"
            )
    else:
        result["hint"] = "All requirements attested — nothing pending."
    return result


def resolve_requirement(
    target_dir: str,
    requirement: str,
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a requirement ref → (intent, artifact token with current hash).

    ``requirement`` accepts ``SPEC-42#R3``, ``spec:SPEC-42#R3``, or bare ``R3`` when
    ``spec`` is provided. Returns the data needed to open a governed session anchored to
    the requirement (the live ``content_hash`` is baked into the token so drift is
    detectable later).
    """
    req_ref = (requirement or "").strip()
    if req_ref.lower().startswith("spec:"):
        req_ref = req_ref[5:]
    spec_id: Optional[str] = spec
    req_id = req_ref
    if "#" in req_ref:
        spec_id_part, req_id = req_ref.split("#", 1)
        spec_id = spec or spec_id_part.strip() or spec_id
    if not spec_id:
        return {
            "ok": False,
            "error": f"cannot resolve spec for requirement {requirement!r}; "
            "use 'SPEC-ID#REQ-ID' or pass spec=",
        }
    try:
        parsed = _load_spec(target_dir, spec=spec_id, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}

    match = next((r for r in parsed.requirements if r.id == req_id), None)
    if match is None:
        return {
            "ok": False,
            "error": f"requirement {req_id!r} not found in spec {parsed.id}",
            "available": [r.id for r in parsed.requirements],
        }
    token = match.token(parsed.id)
    intent = f"{parsed.id}#{match.id}: {match.title}".strip().rstrip(":")
    return {
        "ok": True,
        "spec": parsed.id,
        "spec_title": parsed.title,
        "requirement": match.id,
        "requirement_title": match.title,
        "intent": intent,
        "artifact": token,
        "verify": match.verify,
        "content_hash": match.content_hash,
    }


def spec_lint_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_lint", spec_lint_workspace(root, **kwargs), target_dir=root
    )


def spec_status_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_status", spec_status_workspace(root, **kwargs), target_dir=root
    )


def spec_next_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_next", spec_next_workspace(root, **kwargs), target_dir=root
    )
