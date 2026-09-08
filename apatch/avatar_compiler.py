"""Avatar Compiler — deterministic asset_summary from the governed ledger.

RFP-025 AF-1: a read-only, non-invasive portfolio of attested work — counts, ids,
hashes, tags, timestamps. No source content, no economics (PI/GPI/clearing): that is
an explicit HC opt-in surface (AF-3). Discovered from governed usage, not uploaded
(AF-2). Same ledger -> same summary (reproducible).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

SCHEMA_VERSION = 1


def _spec_id(artifact_key: str) -> str:
    """``spec:SPEC-X#R3`` / ``spec:SPEC-X`` -> ``SPEC-X``."""
    body = artifact_key.split(":", 1)[1] if ":" in artifact_key else artifact_key
    return body.split("#", 1)[0]


def _spec_family(spec_id: str) -> str:
    """``SPEC-INTERFERENCE-1`` -> ``SPEC-INTERFERENCE`` (drop trailing -<n>)."""
    return re.sub(r"-\d+$", "", spec_id)


def build_asset_summary(target_dir: str = ".") -> Dict[str, Any]:
    """Deterministic asset read model from the TrustChain ledger (RFP-025 AF-1)."""
    root = os.path.abspath(target_dir)
    try:
        from apatch.trustchain_helper import TrustChainHelper

        entries = TrustChainHelper(root).iter_ledger_entries()
    except Exception:
        entries = []

    from apatch.contribution import resolve_identity
    from apatch.traceability import build_traceability_index

    return _summary_from_index(
        build_traceability_index(entries or []), resolve_identity(root)
    )


def asset_summary_from_index(index: Dict[str, Any], target_dir: str = ".") -> Dict[str, Any]:
    """asset_summary from a prebuilt traceability index — avoids a second ledger
    walk when the caller (e.g. project_status) already built one (RFP-025 perf)."""
    from apatch.contribution import resolve_identity

    return _summary_from_index(index, resolve_identity(os.path.abspath(target_dir)))


def _summary_from_index(index: Dict[str, Any], identity: Dict[str, Any]) -> Dict[str, Any]:
    """Core read-model builder. `scope` is `ledger` — the avatar spans the whole
    (cross-project) signed ledger, not one project (architectural boundary note)."""
    from apatch.contribution import _to_epoch

    by_art = index.get("by_artifact", {})
    attested: List[str] = []
    attested_at: List[float] = []
    for key, bucket in by_art.items():
        cov = bucket.get("coverage", {})
        if cov.get("has_attestation") and (cov.get("has_mutations") or cov.get("has_covered_by")):
            attested.append(key)
            for att in bucket.get("attestations", []):
                ts = _to_epoch(att.get("timestamp"))
                if ts:
                    attested_at.append(ts)

    spec_ids = sorted({_spec_id(k) for k in attested if k.startswith("spec:")})
    requirement_states: Dict[str, List[str]] = {}
    for k in attested:
        if k.startswith("spec:") and "#" in k:
            sid = _spec_id(k)
            rk = k.split("#", 1)[1]
            bucket_rks = requirement_states.setdefault(sid, [])
            if rk not in bucket_rks:
                bucket_rks.append(rk)
    for sid in requirement_states:
        requirement_states[sid] = sorted(requirement_states[sid])

    kinds = {k.split(":", 1)[0] for k in attested if ":" in k}
    families = {_spec_family(sid) for sid in spec_ids}
    methodology_tags = sorted(kinds | families)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "asset_summary",
        "scope": "ledger",
        "identity": {
            "key_id": identity.get("key_id"),
            "cert_fingerprint": identity.get("cert_fingerprint"),
            "agent_id": identity.get("agent_id"),
            "ca": identity.get("ca"),
        },
        "trust_level": identity.get("trust_level"),
        "artifact_count": len(attested),
        "attested_artifacts": sorted(attested),
        "spec_ids": spec_ids,
        "spec_count": len(spec_ids),
        "requirement_states": requirement_states,
        "attested_requirement_count": sum(len(v) for v in requirement_states.values()),
        "methodology_tags": methodology_tags,
        "attested_at_range": {
            "first": min(attested_at) if attested_at else None,
            "last": max(attested_at) if attested_at else None,
        },
    }
