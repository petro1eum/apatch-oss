"""WorkAsset / Наработка read model from governed apatch evidence.

RFP-031 Phase 1 turns existing TrustChain-backed spec evidence into a deterministic,
metadata-only library of reusable professional methods. The module is deliberately
read-only: no lifecycle promotion, no use tracking, no suggestions, and no economics.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1
EXPORT_CONTRACT_ID = "apatch.work_asset_export.v1"
EXPORT_CONTRACT_VERSION = 1

_FORBIDDEN_EXPORT_TERMS = (
    "private_key",
    "password",
    "ssh://",
    "creator_bonus",
    "creator bonus",
    "clearing",
    "escrow",
    "marketplace",
    "price",
    '"pi"',
    '"gpi"',
)

_STOP_WORDS = {
    "and", "for", "the", "with", "from", "into", "over", "that", "this", "read", "only",
    "schema", "spec", "workasset", "work", "asset", "rfp", "verify", "tests", "test",
    "для", "или", "что", "как", "это", "над", "без", "при", "через", "слой", "спека",
}

_PRIVACY_BOUNDARY = {
    "policy_version": 1,
    "scope": "metadata_proof_summary",
    "raw_work_exported": False,
    "source_code_exported": False,
    "private_prompts_exported": False,
    "credentials_exported": False,
    "ssh_topology_exported": False,
    "raw_logs_exported": False,
    "customer_data_exported": False,
}

_DATA_ACCESS_MATRIX = {
    "policy_version": 1,
    "raw_workspace": "not_exported",
    "ledger_detail": "workspace_owner",
    "export_bundle": "explicit_export_only",
    "avatar_summary": "consumer_content_safe",
    "commercial_lens": "consumer_policy_only",
    "external_training": "not_allowed_without_separate_consent",
}

_CONSENT_RETENTION_POLICY = {
    "policy_version": 1,
    "export_requires_explicit_action": True,
    "external_training_allowed": False,
    "consumer_cache_owner": "consumer_workspace",
    "retention": "consumer_policy",
    "revocation": "remove_exports_and_consumer_caches_keep_local_audit_hashes",
}

_PORTABILITY_POLICY = {
    "content_safe": True,
    "exports_code": False,
    "exports_private_prompts": False,
    "exports_credentials": False,
    "exports_ssh_topology": False,
    "raw_logs": False,
    "portability_class": "portable_method_review_required",
    "rights_claim": "evidence_record_not_ownership_transfer",
    "employer_review": "required_when_private_context_or_contract_applies",
    "redaction": "strict",
}


def _copy_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(policy, ensure_ascii=False))


def work_asset_governance_policy() -> Dict[str, Any]:
    """Machine-readable privacy/access/consent policy for WorkAsset exports."""
    return {
        "privacy_boundary": _copy_policy(_PRIVACY_BOUNDARY),
        "data_access": _copy_policy(_DATA_ACCESS_MATRIX),
        "consent_retention": _copy_policy(_CONSENT_RETENTION_POLICY),
        "portability_policy_default": _copy_policy(_PORTABILITY_POLICY),
    }


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return value or "asset"


def _hash_id(prefix: str, payload: Dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:10]
    return f"wa_{_slug(prefix)}_{digest}"


def _spec_id(artifact_key: str) -> str:
    body = artifact_key.split(":", 1)[1] if ":" in artifact_key else artifact_key
    return body.split("#", 1)[0]


def _spec_requirement(artifact_key: str) -> Optional[str]:
    if "#" not in artifact_key:
        return None
    return artifact_key.split("#", 1)[1]


def _spec_family(spec_id: str) -> str:
    return re.sub(r"-\d+$", "", spec_id)


def _tokens(*values: str, limit: int = 10) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        for tok in re.findall(r"[A-Za-zА-Яа-я0-9]{3,}", value or ""):
            key = tok.lower()
            if key in _STOP_WORDS or key in seen:
                continue
            seen.add(key)
            out.append(tok)
            if len(out) >= limit:
                return out
    return out


def _ledger_entries(root: str) -> List[Dict[str, Any]]:
    try:
        from apatch.trustchain_helper import TrustChainHelper

        return list(TrustChainHelper(root).iter_ledger_entries() or [])
    except Exception:
        return []


def _spec_path(root: str, spec_id: str) -> str:
    return os.path.join(root, "docs", "specs", f"{spec_id}.md")


def _read_spec_metadata(root: str, spec_id: str) -> Dict[str, Any]:
    path = _spec_path(root, spec_id)
    title = spec_id
    verify_refs: List[str] = []
    rfp_refs: List[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("# ") and title == spec_id:
                    title = stripped[2:].split("—", 1)[-1].strip() or spec_id
                if "(verify:" in stripped:
                    ticked = re.findall(r"`([^`]+)`", stripped)
                    if ticked:
                        verify_refs.extend(ticked)
                    else:
                        # verify lines without backticks (e.g. SPEC-TENANT-1 style):
                        # capture the raw command so bundle evidence isn't lost
                        bare = re.search(r"\(verify:\s*([^)]+)\)", stripped)
                        if bare and bare.group(1).strip():
                            verify_refs.append(bare.group(1).strip())
                for rfp in re.findall(r"\bRFP-\d+\b", stripped):
                    ref = f"rfp:{rfp}"
                    if ref not in rfp_refs:
                        rfp_refs.append(ref)
    return {
        "spec_id": spec_id,
        "title": title,
        "verify_refs": verify_refs,
        "rfp_refs": rfp_refs,
        "path": os.path.relpath(path, root) if os.path.exists(path) else None,
    }


def _complete_spec_groups(trace_index: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for artifact_key, bucket in sorted((trace_index.get("by_artifact") or {}).items()):
        if not artifact_key.startswith("spec:") or "#" not in artifact_key:
            continue
        coverage = bucket.get("coverage") or {}
        if not coverage.get("complete"):
            continue
        spec_id = _spec_id(artifact_key)
        group = groups.setdefault(
            spec_id,
            {
                "artifact_keys": [],
                "requirements": [],
                "attestation_op_ids": [],
                "mutation_count": 0,
                "attestation_count": 0,
            },
        )
        group["artifact_keys"].append(artifact_key)
        req = _spec_requirement(artifact_key)
        if req and req not in group["requirements"]:
            group["requirements"].append(req)
        group["mutation_count"] += int(coverage.get("mutation_count") or 0)
        group["attestation_count"] += int(coverage.get("attestation_count") or 0)
        for att in bucket.get("attestations") or []:
            op_id = att.get("op_id")
            if op_id and op_id not in group["attestation_op_ids"]:
                group["attestation_op_ids"].append(op_id)
    for group in groups.values():
        group["artifact_keys"] = sorted(group["artifact_keys"])
        group["requirements"] = sorted(group["requirements"])
        group["attestation_op_ids"] = sorted(group["attestation_op_ids"])
    return groups


def _proof_refs(op_ids: Iterable[str]) -> List[Dict[str, Any]]:
    ids = [op for op in op_ids if op]
    return [{"ledger": "trustchain", "op_ids": ids}] if ids else []


def _asset_from_spec(root: str, identity: Dict[str, Any], spec_id: str,
                     group: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _read_spec_metadata(root, spec_id)
    title = metadata["title"]
    requirements = list(group.get("requirements") or [])
    spec_refs = [f"spec:{spec_id}#{req}" for req in requirements]
    signals = _tokens(title, spec_id, _spec_family(spec_id), limit=8)
    payload = {
        "spec_id": spec_id,
        "requirements": requirements,
        "attestations": group.get("attestation_op_ids") or [],
        "key_id": identity.get("key_id"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "work_asset",
        "asset_id": _hash_id(spec_id, payload),
        "asset_kind": "spec_bundle",
        "title": title,
        "owner": {
            "key_id": identity.get("key_id"),
            "cert_fingerprint": identity.get("cert_fingerprint"),
            "agent_id": identity.get("agent_id"),
            "ca": identity.get("ca"),
            "trust_level": identity.get("trust_level"),
        },
        "problem_signature": {
            "label": title,
            "tags": signals,
            "spec_family": _spec_family(spec_id),
        },
        "applicability": {
            "scope": "governed apatch workflow",
            "signals": signals,
            "required_context": ["apatch ledger", "spec requirement coverage", "verification command"],
        },
        "trigger_signals": signals,
        "outputs": [
            {"type": "spec_requirements", "count": len(requirements)},
            {"type": "attestations", "count": len(group.get("attestation_op_ids") or [])},
        ],
        "spec_refs": spec_refs,
        "rfp_refs": metadata["rfp_refs"],
        "proof_refs": _proof_refs(group.get("attestation_op_ids") or []),
        "verification_refs": metadata["verify_refs"],
        "source_evidence": {
            "metadata_only": True,
            "spec_path": metadata["path"],
            "artifact_keys": list(group.get("artifact_keys") or []),
            "mutation_count": group.get("mutation_count", 0),
            "attestation_count": group.get("attestation_count", 0),
        },
        "confidentiality_class": "metadata_only",
        "reuse": {"count": 0, "last_used_at": None},
        "portability_policy": _copy_policy(_PORTABILITY_POLICY),
        "dependency_lens": {
            "human_method": 1.0,
            "project_specific_context": 0.0,
            "vendor_specific_context": 0.0,
            "requires_private_data": False,
        },
        "lifecycle": "candidate",
    }


def _sort_assets(assets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(assets, key=lambda item: (item.get("title") or "", item.get("asset_id") or ""))


def _lifecycle_counts(assets: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for asset in assets:
        lifecycle = asset.get("lifecycle") or "unknown"
        counts[lifecycle] = counts.get(lifecycle, 0) + 1
    return counts


def _with_assets(index: Dict[str, Any], assets: List[Dict[str, Any]]) -> Dict[str, Any]:
    index = dict(index)
    index["assets"] = assets
    index["asset_count"] = len(assets)
    index["lifecycle_counts"] = _lifecycle_counts(assets)
    return index


def build_work_asset_index(target_dir: str = ".") -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    from apatch.avatar_compiler import asset_summary_from_index
    from apatch.contribution import resolve_identity
    from apatch.traceability import build_traceability_index

    entries = _ledger_entries(root)
    trace_index = build_traceability_index(entries)
    identity = resolve_identity(root)
    asset_summary = asset_summary_from_index(trace_index, root)
    groups = _complete_spec_groups(trace_index)
    assets = _sort_assets(_asset_from_spec(root, identity, sid, group) for sid, group in groups.items())
    # Phase 2 (SPEC-WORK-ASSET-LIFECYCLE-1 / AUC-1): overlay ledger-derived lifecycle
    # state, reuse counters and recallability — state lives in signed events, never
    # in a writable field; with no events the Phase-1 defaults pass through as-is.
    from apatch.work_asset_lifecycle import apply_lifecycle_overlay

    assets = apply_lifecycle_overlay(assets, entries)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "work_asset_index",
        "scope": "ledger",
        "identity": asset_summary.get("identity") or identity,
        "source_summary": {
            "asset_summary_kind": asset_summary.get("kind"),
            "artifact_count": asset_summary.get("artifact_count", 0),
            "spec_count": asset_summary.get("spec_count", 0),
            "attested_requirement_count": asset_summary.get("attested_requirement_count", 0),
        },
        "asset_count": len(assets),
        "lifecycle_counts": _lifecycle_counts(assets),
        "assets": assets,
    }


def _asset_blob(asset: Dict[str, Any]) -> str:
    parts = [
        asset.get("asset_id"),
        asset.get("title"),
        asset.get("asset_kind"),
        asset.get("lifecycle"),
        " ".join(asset.get("spec_refs") or []),
        " ".join(asset.get("rfp_refs") or []),
        " ".join(asset.get("trigger_signals") or []),
        _canonical(asset.get("problem_signature") or {}),
    ]
    return " ".join(str(p or "") for p in parts).lower()


def _score_asset(asset: Dict[str, Any], terms: List[str]) -> Tuple[int, List[str]]:
    blob = _asset_blob(asset)
    reasons = [term for term in terms if term in blob]
    return len(reasons), reasons


def search_work_assets(target_dir: str = ".", query: str = "", limit: Optional[int] = 10) -> Dict[str, Any]:
    terms = [t.lower() for t in re.findall(r"[A-Za-zА-Яа-я0-9_:-]{2,}", query or "")]
    index = build_work_asset_index(target_dir)
    rows = []
    for asset in index["assets"]:
        score, reasons = _score_asset(asset, terms)
        if not terms or score:
            rows.append({"asset": asset, "score": score, "reasons": reasons})
    rows.sort(key=lambda row: (-row["score"], row["asset"].get("title") or ""))
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "work_asset_search",
        "ok": True,
        "query": query or "",
        "count": len(rows),
        "results": rows,
    }


def list_work_assets(target_dir: str = ".", query: str = "", limit: Optional[int] = None) -> Dict[str, Any]:
    if query:
        result = search_work_assets(target_dir, query=query, limit=limit)
        assets = [row["asset"] for row in result["results"]]
        return _with_assets(build_work_asset_index(target_dir), assets)
    index = build_work_asset_index(target_dir)
    if limit is not None and limit >= 0:
        return _with_assets(index, index["assets"][:limit])
    return index


def show_work_asset(target_dir: str = ".", asset_id: str = "") -> Dict[str, Any]:
    needle = str(asset_id or "").strip()
    for asset in build_work_asset_index(target_dir)["assets"]:
        if asset.get("asset_id") == needle:
            return {"schema_version": SCHEMA_VERSION, "kind": "work_asset_show", "ok": True, "asset": asset}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "work_asset_show",
        "ok": False,
        "error_type": "WORK_ASSET_NOT_FOUND",
        "asset_id": needle,
    }


def work_asset_export_schema() -> Dict[str, Any]:
    """JSON Schema for the stable WorkAsset export contract consumed by HC."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": EXPORT_CONTRACT_ID,
        "title": "apatch WorkAsset export v1",
        "type": "object",
        "additionalProperties": True,
        "required": [
            "schema_version",
            "contract_id",
            "contract_version",
            "kind",
            "content_safe",
            "redaction",
            "privacy_boundary",
            "data_access",
            "consent_retention",
            "source_summary",
            "asset_count",
            "assets",
        ],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "contract_id": {"const": EXPORT_CONTRACT_ID},
            "contract_version": {"const": EXPORT_CONTRACT_VERSION},
            "kind": {"const": "work_asset_export"},
            "content_safe": {"const": True},
            "redaction": {
                "type": "object",
                "required": [
                    "exports_code",
                    "exports_private_prompts",
                    "exports_credentials",
                    "exports_ssh_topology",
                    "raw_logs",
                ],
                "properties": {
                    "exports_code": {"const": False},
                    "exports_private_prompts": {"const": False},
                    "exports_credentials": {"const": False},
                    "exports_ssh_topology": {"const": False},
                    "raw_logs": {"const": False},
                },
            },
            "privacy_boundary": {
                "type": "object",
                "required": [
                    "scope",
                    "raw_work_exported",
                    "source_code_exported",
                    "private_prompts_exported",
                    "credentials_exported",
                    "ssh_topology_exported",
                    "raw_logs_exported",
                    "customer_data_exported",
                ],
            },
            "data_access": {
                "type": "object",
                "required": [
                    "raw_workspace",
                    "ledger_detail",
                    "export_bundle",
                    "avatar_summary",
                    "commercial_lens",
                    "external_training",
                ],
            },
            "consent_retention": {
                "type": "object",
                "required": [
                    "export_requires_explicit_action",
                    "external_training_allowed",
                    "consumer_cache_owner",
                    "retention",
                    "revocation",
                ],
            },
            "identity": {"type": ["object", "null"]},
            "source_summary": {"type": "object"},
            "asset_count": {"type": "integer", "minimum": 0},
            "assets": {"type": "array", "items": {"type": "object"}},
        },
    }


def _extension_payload_paths(value: Any, path: str = "") -> List[str]:
    """Find fields reserved for extension-owned payloads in a WorkAsset export."""

    found: List[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = key if not path else path + "." + key
            normalized = key.lower()
            if normalized == "extensions" or normalized.startswith("extension_"):
                found.append(child_path)
            found.extend(_extension_payload_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = str(index) if not path else path + "." + str(index)
            found.extend(_extension_payload_paths(child, child_path))
    return found


def validate_work_asset_export_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight contract validation without a jsonschema runtime dependency."""
    errors: List[str] = [
        "extension_payload." + path for path in _extension_payload_paths(bundle)
    ]
    if bundle.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version")
    if bundle.get("contract_id") != EXPORT_CONTRACT_ID:
        errors.append("contract_id")
    if bundle.get("contract_version") != EXPORT_CONTRACT_VERSION:
        errors.append("contract_version")
    if bundle.get("kind") != "work_asset_export":
        errors.append("kind")
    if bundle.get("content_safe") is not True:
        errors.append("content_safe")
    redaction = bundle.get("redaction") or {}
    for key in ("exports_code", "exports_private_prompts", "exports_credentials", "exports_ssh_topology", "raw_logs"):
        if redaction.get(key) is not False:
            errors.append(f"redaction.{key}")
    privacy = bundle.get("privacy_boundary") or {}
    for key in (
        "raw_work_exported",
        "source_code_exported",
        "private_prompts_exported",
        "credentials_exported",
        "ssh_topology_exported",
        "raw_logs_exported",
        "customer_data_exported",
    ):
        if privacy.get(key) is not False:
            errors.append(f"privacy_boundary.{key}")
    data_access = bundle.get("data_access") or {}
    if data_access.get("raw_workspace") != "not_exported":
        errors.append("data_access.raw_workspace")
    if data_access.get("export_bundle") != "explicit_export_only":
        errors.append("data_access.export_bundle")
    if data_access.get("external_training") != "not_allowed_without_separate_consent":
        errors.append("data_access.external_training")
    consent = bundle.get("consent_retention") or {}
    if consent.get("export_requires_explicit_action") is not True:
        errors.append("consent_retention.export_requires_explicit_action")
    if consent.get("external_training_allowed") is not False:
        errors.append("consent_retention.external_training_allowed")
    if not isinstance(bundle.get("assets"), list):
        errors.append("assets")
    if not isinstance(bundle.get("asset_count"), int) or bundle.get("asset_count", -1) < 0:
        errors.append("asset_count")
    for idx, asset in enumerate(bundle.get("assets") or []):
        if not isinstance(asset, dict):
            errors.append(f"assets.{idx}")
            continue
        portability = asset.get("portability_policy") or {}
        if portability.get("content_safe") is not True:
            errors.append(f"assets.{idx}.portability_policy.content_safe")
        for key in ("exports_code", "exports_private_prompts", "exports_credentials", "exports_ssh_topology", "raw_logs"):
            if portability.get(key) is not False:
                errors.append(f"assets.{idx}.portability_policy.{key}")
        if not portability.get("portability_class"):
            errors.append(f"assets.{idx}.portability_policy.portability_class")
        if portability.get("rights_claim") != "evidence_record_not_ownership_transfer":
            errors.append(f"assets.{idx}.portability_policy.rights_claim")
    try:
        _assert_export_boundary(bundle)
    except ValueError as exc:
        errors.append(str(exc))
    return {"ok": not errors, "contract_id": EXPORT_CONTRACT_ID, "contract_version": EXPORT_CONTRACT_VERSION, "errors": errors}


def export_work_assets(target_dir: str = ".", query: str = "") -> Dict[str, Any]:
    index = list_work_assets(target_dir, query=query)
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": EXPORT_CONTRACT_ID,
        "contract_version": EXPORT_CONTRACT_VERSION,
        "kind": "work_asset_export",
        "content_safe": True,
        "redaction": {
            "exports_code": False,
            "exports_private_prompts": False,
            "exports_credentials": False,
            "exports_ssh_topology": False,
            "raw_logs": False,
        },
        "privacy_boundary": _copy_policy(_PRIVACY_BOUNDARY),
        "data_access": _copy_policy(_DATA_ACCESS_MATRIX),
        "consent_retention": _copy_policy(_CONSENT_RETENTION_POLICY),
        "identity": index.get("identity"),
        "source_summary": index.get("source_summary"),
        "asset_count": index.get("asset_count", 0),
        "assets": index.get("assets", []),
    }
    validation = validate_work_asset_export_bundle(bundle)
    if not validation["ok"]:
        raise ValueError("invalid WorkAsset export bundle: " + ", ".join(validation["errors"]))
    return bundle


def _assert_export_boundary(bundle: Dict[str, Any]) -> None:
    blob = json.dumps(bundle, sort_keys=True, ensure_ascii=False).lower()
    for term in _FORBIDDEN_EXPORT_TERMS:
        if term in blob:
            raise ValueError(f"forbidden WorkAsset export term: {term}")
