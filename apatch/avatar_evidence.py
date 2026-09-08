"""CapabilityEvidence v2 export for HC Tracker and HC Capital.

The bundle is content-addressed and signed fail-closed for enrolled identities.
It contains WorkEpisode facts and CapabilityEstimate inferences, never prices or
valuation.  HC may verify it without access to the producer's local workspace.
"""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 2


class EvidenceBarrierError(RuntimeError):
    """An evidence bundle violated the shared content/economic boundary."""


def _generated_at(now_ts: Optional[float]) -> str:
    if now_ts is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(float(now_ts), tz=timezone.utc).isoformat()


def _reason_counts(episodes: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = Counter(
        reason
        for episode in episodes
        for reason in episode.get("exclusion_reasons") or []
    )
    return dict(sorted(counts.items()))


def _is_outcome_candidate(episode: Dict[str, Any]) -> bool:
    """Metadata-safe episode that an independent verifier could review."""
    quality = episode.get("evidence_quality") or {}
    task = episode.get("task") or {}
    attribution = episode.get("attribution") or {}
    proof_ref = episode.get("proof_ref") or {}
    return bool(
        episode.get("trust_level") in {"attested", "verified"}
        and quality.get("signature") == "verified"
        and quality.get("rights") == "confirmed"
        and quality.get("outcome") != "observed"
        and task.get("taxonomy_refs")
        and attribution.get("resolved")
        and attribution.get("mode") != "unknown"
        and proof_ref.get("op_ids")
    )


def _sign_bundle(bundle: Dict[str, Any], target_dir: str) -> None:
    from avatar_contract import capability_evidence_signing_bytes
    from apatch.trust_identity import load_local_identity

    identity = load_local_identity(target_dir)
    provider = getattr(identity, "key_provider", None) if identity else None
    if provider is None:
        raise EvidenceBarrierError(
            "attested CapabilityEvidence requires the enrolled Ed25519 signer"
        )
    public_key = provider.get_public_key()
    from hashlib import sha256

    key_id = sha256(public_key).hexdigest()[:32]
    if key_id != bundle["avatar_id"]:
        raise EvidenceBarrierError("signing key does not match bundle avatar_id")
    signature = provider.sign(capability_evidence_signing_bytes(bundle))
    bundle["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "public_key": base64.b64encode(public_key).decode("ascii"),
        "value": base64.b64encode(signature).decode("ascii"),
    }


def build_evidence_bundle(
    target_dir: str = ".",
    *,
    now_ts: Optional[float] = None,
    avatar_id: Optional[str] = None,
    allow_unsigned: bool = False,
    outcome_store_dir: Optional[str] = None,
    taxonomy_store_dir: Optional[str] = None,
) -> Dict[str, Any]:
    from avatar_contract import (
        CAPABILITY_EVIDENCE_KIND,
        CAPABILITY_EVIDENCE_SCHEMA_VERSION,
        CapabilityEvidenceBundle,
        CapabilityEvidenceError,
        compute_capability_evidence_bundle_id,
    )
    from apatch.capability import derive_capabilities
    from apatch.contribution import resolve_identity
    from apatch.episode import build_episodes

    identity = resolve_identity(target_dir)
    subject = avatar_id or str(identity.get("key_id") or "")
    observed_episodes = build_episodes(
        target_dir,
        avatar_id=subject or None,
        outcome_store_dir=outcome_store_dir,
        taxonomy_store_dir=taxonomy_store_dir,
    )
    # HC consumes a metadata distillate, not the activity archive. It needs both
    # independently observed evidence and signed proxy episodes that a professional
    # may submit for independent review. Candidates remain explicitly ineligible for
    # capability inference until an OutcomeAttestation returns.
    episodes = [
        episode for episode in observed_episodes
        if episode.get("eligible_for_capability") or _is_outcome_candidate(episode)
    ]
    generated_at = _generated_at(now_ts)
    estimates = derive_capabilities(episodes, now_ts=now_ts)
    occurred = [episode["occurred_at"] for episode in observed_episodes]
    reasons = _reason_counts(observed_episodes)
    bundle: Dict[str, Any] = {
        "schema_version": CAPABILITY_EVIDENCE_SCHEMA_VERSION,
        "kind": CAPABILITY_EVIDENCE_KIND,
        "bundle_id": "",
        "avatar_id": subject,
        "source": "apatch",
        "trust_level": str(identity.get("trust_level") or "claimed"),
        "generated_at": generated_at,
        "evidence_scope": {
            "event_count": len(observed_episodes),
            "project_count": len({
                episode["project_id"] for episode in observed_episodes
            }),
            "from": min(occurred) if occurred else None,
            "to": max(occurred) if occurred else None,
        },
        "episodes": episodes,
        "capability_estimates": estimates,
        "exclusions": {"count": sum(reasons.values()), "reasons": reasons},
    }
    bundle["bundle_id"] = compute_capability_evidence_bundle_id(bundle)
    if bundle["trust_level"] in {"attested", "verified"}:
        try:
            _sign_bundle(bundle, target_dir)
        except Exception:
            if not allow_unsigned:
                raise
            bundle["trust_level"] = "claimed"
            bundle["bundle_id"] = compute_capability_evidence_bundle_id(bundle)
    try:
        CapabilityEvidenceBundle.from_wire(
            bundle,
            verify_signature=bundle["trust_level"] in {"attested", "verified"},
        )
    except CapabilityEvidenceError as exc:
        raise EvidenceBarrierError(str(exc)) from exc
    return bundle


def verify_evidence_bundle(
    bundle: Dict[str, Any],
    target_dir: str = ".",
) -> Dict[str, Any]:
    from avatar_contract import CapabilityEvidenceBundle, CapabilityEvidenceError

    errors: List[Dict[str, Any]] = []
    try:
        CapabilityEvidenceBundle.from_wire(
            bundle,
            verify_signature=str(bundle.get("trust_level")) in {"attested", "verified"},
        )
    except CapabilityEvidenceError as exc:
        errors.append({"error": "contract_or_signature_invalid", "detail": str(exc)})
        return {"ok": False, "errors": errors}
    try:
        generated = datetime.fromisoformat(
            str(bundle["generated_at"]).replace("Z", "+00:00")
        ).timestamp()
        fresh = build_evidence_bundle(
            target_dir,
            now_ts=generated,
            avatar_id=str(bundle.get("avatar_id") or ""),
            allow_unsigned=str(bundle.get("trust_level")) == "claimed",
        )
    except Exception as exc:
        errors.append({"error": "rederivation_failed", "detail": str(exc)})
        return {"ok": False, "errors": errors}
    if fresh != bundle:
        errors.append({"error": "bundle_drift"})
    return {"ok": not errors, "errors": errors}


def capabilities_summary(
    target_dir: str = ".",
    *,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    from apatch.capability import build_capabilities

    estimates = build_capabilities(target_dir, now_ts=now_ts)
    return {
        "count": len(estimates),
        "estimated": sum(item["status"] == "estimated" for item in estimates),
        "insufficient_evidence": sum(
            item["status"] == "insufficient_evidence" for item in estimates
        ),
        "classes": [
            {
                "taxonomy_refs": item["taxonomy_refs"],
                "status": item["status"],
                "attempted": item["observations"]["attempted"],
                "mean": item["success_estimate"]["mean"],
                "interval": [
                    item["success_estimate"]["lower"],
                    item["success_estimate"]["upper"],
                ],
                "recency": item["recency"]["state"],
                "uncertainty": item["uncertainty"],
            }
            for item in sorted(
                estimates,
                key=lambda value: -int(value["observations"]["attempted"]),
            )[:5]
        ],
    }
