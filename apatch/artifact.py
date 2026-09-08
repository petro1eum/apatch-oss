"""Artifact-anchored intent (RFP-006): typed engineering decision references.

MCP: pass ``artifacts`` to ``apatch_session_start`` (tokens ``kind:id@hash`` or dicts).
CLI: ``session start --artifact`` (repeatable). Persisted in ``session_state.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

_ARTIFACT_TOKEN_RE = re.compile(
    r"^(?P<kind>[a-z][a-z0-9_-]*):(?P<id>[^@]+)(?:@(?P<hash>.+))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Artifact:
    """Typed reference to an engineering decision (spec, ADR, ticket, …)."""

    kind: str
    id: str
    ref: Optional[str] = None
    content_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out = {"kind": self.kind, "id": self.id}
        if self.ref:
            out["ref"] = self.ref
        if self.content_hash:
            out["content_hash"] = self.content_hash
        return out

    def token(self) -> str:
        """CLI/MCP shorthand: ``kind:id@hash``."""
        base = f"{self.kind}:{self.id}"
        if self.content_hash:
            return f"{base}@{self.content_hash}"
        return base

    def matches_filter(self, artifact_filter: str) -> bool:
        """Match ``kind:id`` or full token (hash ignored for filter)."""
        f = (artifact_filter or "").strip()
        if not f:
            return True
        if "@" in f:
            f = f.split("@", 1)[0]
        parts = f.split(":", 1)
        if len(parts) != 2:
            return False
        kind, ident = parts[0].strip().lower(), parts[1].strip()
        return self.kind.lower() == kind and self.id == ident


def artifact_from_dict(raw: Dict[str, Any]) -> Optional[Artifact]:
    if not isinstance(raw, dict):
        return None
    kind = (raw.get("kind") or "").strip()
    ident = (raw.get("id") or "").strip()
    if not kind or not ident:
        return None
    ref = raw.get("ref")
    content_hash = raw.get("content_hash")
    return Artifact(
        kind=kind,
        id=ident,
        ref=str(ref).strip() if ref else None,
        content_hash=str(content_hash).strip() if content_hash else None,
    )


def parse_artifact_token(token: str) -> Artifact:
    """Parse ``kind:id`` or ``kind:id@content_hash``."""
    t = (token or "").strip()
    if not t:
        raise ValueError("artifact token is empty")
    m = _ARTIFACT_TOKEN_RE.match(t)
    if not m:
        raise ValueError(
            f"invalid artifact token {token!r}; expected kind:id or kind:id@hash"
        )
    return Artifact(
        kind=m.group("kind").lower(),
        id=m.group("id").strip(),
        content_hash=(m.group("hash") or "").strip() or None,
    )


def coerce_artifacts(
    raw: Optional[Union[Sequence[Any], Any]] = None,
) -> List[Dict[str, Any]]:
    """Normalize CLI tokens, dicts, or Artifact instances to persisted dicts."""
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        art: Optional[Artifact] = None
        if isinstance(item, Artifact):
            art = item
        elif isinstance(item, str):
            art = parse_artifact_token(item)
        elif isinstance(item, dict):
            art = artifact_from_dict(item)
        if art is None:
            continue
        key = f"{art.kind}:{art.id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(art.to_dict())
    return out


def normalize_manifest_artifacts(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract ``artifacts[]`` from a pipeline manifest; legacy ``adr`` → ``{kind:adr}``."""
    raw_items = manifest.get("artifacts") or []
    artifacts = coerce_artifacts(raw_items if isinstance(raw_items, list) else [])
    adr = manifest.get("adr")
    if adr:
        adr_id = str(adr).strip()
        if adr_id and not any(
            a.get("kind") == "adr" and a.get("id") == adr_id for a in artifacts
        ):
            artifacts.append({"kind": "adr", "id": adr_id})
    return artifacts


def payload_artifacts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Artifacts from a TrustChain ledger payload (new field + legacy ``adr``)."""
    raw = payload.get("artifacts")
    if isinstance(raw, list) and raw:
        return coerce_artifacts(raw)
    adr = payload.get("adr")
    if adr:
        return [{"kind": "adr", "id": str(adr).strip()}]
    return []


def entry_matches_artifact_filter(
    payload: Dict[str, Any],
    artifact_filter: Optional[str],
) -> bool:
    if not artifact_filter or not str(artifact_filter).strip():
        return True
    arts = payload_artifacts(payload)
    if not arts:
        return False
    return any(
        Artifact(
            kind=str(a.get("kind") or ""),
            id=str(a.get("id") or ""),
            ref=a.get("ref"),
            content_hash=a.get("content_hash"),
        ).matches_filter(str(artifact_filter))
        for a in arts
    )


def entry_search_blob(payload: Dict[str, Any]) -> str:
    """Lowercase blob for legacy ``--query`` substring search."""
    parts: List[str] = []
    for key in ("intent", "adr", "action", "manifest"):
        val = payload.get(key)
        if val:
            parts.append(str(val))
    for art in payload_artifacts(payload):
        parts.extend(
            str(x or "")
            for x in (art.get("kind"), art.get("id"), art.get("ref"), art.get("content_hash"))
        )
    return " ".join(parts).lower()
