"""WorkEpisode v2: signed facts that may support a capability estimate.

Episodes are derived from the global, per-identity ContributionEvent receipt
store.  Raw ledger rows may enrich a local receipt, but are never sufficient on
their own: an event must pass the shared contract and its Ed25519 signature must
verify before it can become capability evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = 2


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def _iso(value: Any) -> str:
    from apatch.contribution import _to_epoch

    ts = _to_epoch(value)
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    # Missing historical time must stay deterministic. Wall-clock fallback made the
    # same signed receipt change on every read and incorrectly made old evidence fresh.
    return datetime.fromtimestamp(0, tz=timezone.utc).isoformat()


def _event_time(event: Any) -> Any:
    session = event.session or {}
    proof_ref = event.proof_ref or {}
    return (
        event.created_at
        or session.get("ended_at")
        or proof_ref.get("committed_at")
        or session.get("started_at")
        or 0
    )


def _identity_config(target_dir: str) -> Dict[str, Any]:
    path = os.environ.get("APATCH_AVATAR_IDENTITY_CONFIG", "").strip()
    if not path:
        path = os.path.join(os.path.abspath(target_dir), ".apatch", "avatar_identity.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _ownership_graph():
    from avatar_contract import OwnershipGraph

    path = os.environ.get("APATCH_OWNERSHIP_GRAPH", "").strip()
    if not path:
        path = os.path.join(
            os.path.expanduser("~"), ".trustchain", "avatar_ownership.json"
        )
    return OwnershipGraph.load(path)


def _local_public_key(target_dir: str) -> Tuple[Optional[str], Optional[bytes]]:
    try:
        from apatch.trust_identity import load_local_identity

        identity = load_local_identity(target_dir)
        provider = getattr(identity, "key_provider", None) if identity else None
        if provider is None:
            return None, None
        public_key = provider.get_public_key()
        return hashlib.sha256(public_key).hexdigest()[:32], public_key
    except Exception:
        return None, None


def _signature_status(raw: Dict[str, Any], target_dir: str) -> str:
    signature = raw.get("signature")
    if not signature:
        return "missing"
    local_key_id, public_key = _local_public_key(target_dir)
    event_key_id = str(raw.get("avatar_id") or (raw.get("identity") or {}).get("key_id") or "")
    if not public_key or local_key_id != event_key_id:
        return "unverifiable"
    from apatch.contribution import verify_event

    return "verified" if verify_event(raw, public_key) else "invalid"


def _safe_refs(values: Iterable[Any]) -> List[str]:
    import re

    safe = re.compile(r"^[A-Za-z0-9._:@#+/-]+$")
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and len(text) <= 256 and safe.fullmatch(text) and text not in out:
            out.append(text)
    return out


def _task(event: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    session = event.session or {}
    payload = event.payload or {}
    artifacts: List[str] = []
    for value in session.get("artifacts") or []:
        if isinstance(value, dict):
            kind = str(value.get("kind") or "").strip()
            identifier = str(value.get("id") or "").strip()
            content_hash = str(value.get("content_hash") or "").strip()
            text = f"{kind}:{identifier}" if kind and identifier else ""
            if text and content_hash:
                text = f"{text}@{content_hash}"
        else:
            text = str(value or "").strip()
        if text:
            artifacts.append(text)
    spec_refs = _safe_refs(
        value for value in artifacts if str(value).startswith("spec:")
    )
    taxonomy_refs = _safe_refs(payload.get("accepted_taxonomy_refs") or [])
    if not taxonomy_refs:
        for spec_ref in spec_refs:
            spec_id = spec_ref.split(":", 1)[1].split("#", 1)[0]
            family = spec_id.rsplit("-", 1)[0].lower()
            candidate = "apatch-spec:" + family
            if candidate not in taxonomy_refs:
                taxonomy_refs.append(candidate)
    label = spec_refs[0] if spec_refs else "Governed work episode"
    if config.get("export_task_labels") is True:
        intent = " ".join(str(session.get("intent") or "").split())
        if intent:
            label = intent[:240]
    return {"label": label, "taxonomy_refs": taxonomy_refs, "spec_refs": spec_refs}


def _apply_taxonomy_decision(
    event: Any,
    episode_id: str,
    task: Dict[str, Any],
    taxonomy_index: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    raw = (taxonomy_index or {}).get(str(event.event_id))
    if not isinstance(raw, dict):
        return task
    try:
        from apatch.taxonomy_delivery import validate_taxonomy_decision

        parsed = validate_taxonomy_decision(raw)
    except Exception:
        return task
    session = event.session or {}
    intent_digest = hashlib.sha256(
        str(session.get("intent") or "").encode("utf-8")
    ).hexdigest()
    if (
        parsed.subject_avatar_id != str(event.avatar_id)
        or parsed.source_event_id != str(event.event_id)
        or parsed.work_episode_id != episode_id
        or parsed.proposal.get("intent_sha256") != intent_digest
        or parsed.decision.get("status") != "accepted"
    ):
        return task
    refs = _safe_refs(parsed.proposal.get("taxonomy_refs") or [])
    if not refs:
        return task
    proposal_task = parsed.proposal.get("task") or {}
    label = str(proposal_task.get("statement") or "").strip()[:240]
    return {
        "label": label or task.get("label") or "Governed work episode",
        "taxonomy_refs": refs,
        "spec_refs": list(task.get("spec_refs") or []),
    }


def _same_task_identity(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return (
        left.get("label") == right.get("label")
        and sorted(left.get("taxonomy_refs") or [])
        == sorted(right.get("taxonomy_refs") or [])
    )


def _outcome(event: Any) -> Tuple[Dict[str, Any], str]:
    """A producer receipt can prove execution, never counterparty acceptance."""
    if event.trust_level in {"attested", "verified"} and event.proof_ref:
        observed_at = _iso(_event_time(event))
        return {
            "status": "passed",
            "basis": "technical_gate",
            "observed_at": observed_at,
            "reference": "event:" + event.event_id,
        }, "proxy"
    return {
        "status": "unknown",
        "basis": "missing",
        "observed_at": None,
        "reference": None,
    }, "missing"


def _review_package(
    event: Any,
    task: Dict[str, Any],
    signature_status: str,
    gate_quality: str,
) -> Dict[str, Any]:
    from avatar_contract import build_work_review_package

    payload = event.payload or {}
    raw = payload.get("review_submission")
    expected_keys = {
        "schema_version",
        "objective",
        "delivery_summary",
        "acceptance_criteria",
        "artifact_refs",
        "limitations",
    }
    valid_submission = (
        isinstance(raw, dict)
        and set(raw) == expected_keys
        and raw.get("schema_version") == 1
    )
    limitations: List[str] = []
    if not valid_submission:
        raw = {}
        limitations.append("No valid signed public review submission was supplied.")

    def safe_text(value: Any, maximum: int) -> Optional[str]:
        if not isinstance(value, str):
            return None
        text = " ".join(value.split())
        return text if text and len(text) <= maximum else None

    def safe_texts(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        for item in value:
            text = safe_text(item, 300)
            if text and text not in out:
                out.append(text)
        return out[:20]

    objective = safe_text(raw.get("objective"), 500)
    delivery = safe_text(raw.get("delivery_summary"), 600)
    criteria = safe_texts(raw.get("acceptance_criteria"))
    signed_limitations = safe_texts(raw.get("limitations"))
    limitations.extend(signed_limitations)
    if valid_submission and delivery is None:
        limitations.append("No public completion summary was supplied.")
    if signature_status != "verified":
        limitations.append("The source event signature is not verified.")
    if gate_quality == "unprobed":
        limitations.append("No falsification probe was recorded.")
    elif gate_quality == "false_gate":
        limitations.append("The recorded verification gate did not detect failure.")

    submitted_refs = (
        raw.get("artifact_refs") if isinstance(raw.get("artifact_refs"), list) else []
    )
    artifact_refs = _safe_refs(submitted_refs)
    if not artifact_refs:
        artifact_refs = _safe_refs(task.get("spec_refs") or [])
    proof_refs = _safe_refs((event.proof_ref or {}).get("op_ids") or [])
    return build_work_review_package(
        objective=objective,
        delivery_summary=delivery,
        acceptance_criteria=criteria,
        source_signature=signature_status,
        gate=gate_quality,
        proof_refs=proof_refs,
        artifact_refs=artifact_refs,
        limitations=limitations,
    )


def _attribution(event: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    actor = str(event.avatar_id or (event.identity or {}).get("key_id") or "")
    payload = event.payload or {}
    declared = payload.get("attribution") if isinstance(payload.get("attribution"), dict) else {}
    allowed = {"direct_human", "agent_piloted", "agent_autonomous", "mixed"}
    if declared.get("mode") in allowed and declared.get("principal_key_id"):
        return {
            "actor_key_id": actor,
            "principal_key_id": str(declared["principal_key_id"]),
            "mode": str(declared["mode"]),
            "resolved": True,
            "basis": "signed-event-claim",
        }

    principal = str(
        config.get("principal_key_id")
        or os.environ.get("APATCH_PRINCIPAL_KEY_ID", "")
        or ""
    )
    identity_kind = str(config.get("identity_kind") or "").lower()
    if principal and actor == principal and identity_kind == "human":
        return {
            "actor_key_id": actor,
            "principal_key_id": principal,
            "mode": "direct_human",
            "resolved": True,
            "basis": "owner-identity-config",
        }

    graph = _ownership_graph()
    owner = graph.owner_of(actor)
    if owner:
        role = graph.role_of(owner, actor)
        return {
            "actor_key_id": actor,
            "principal_key_id": owner,
            "mode": "agent_piloted" if role == "pilot" else "agent_autonomous",
            "resolved": True,
            "basis": "ownership-graph:" + str(role or "owner"),
        }

    event_kind = str((event.identity or {}).get("subject_type") or "").lower()
    if event_kind == "human":
        return {
            "actor_key_id": actor,
            "principal_key_id": actor,
            "mode": "direct_human",
            "resolved": True,
            "basis": "signed-identity-subject-type",
        }
    if event_kind == "agent":
        return {
            "actor_key_id": actor,
            "principal_key_id": None,
            "mode": "agent_autonomous",
            "resolved": True,
            "basis": "signed-identity-subject-type",
        }
    agent_id = str((event.identity or {}).get("agent_id") or "").lower()
    if agent_id.startswith(("apatch", "codex", "claude", "cursor")):
        return {
            "actor_key_id": actor,
            "principal_key_id": None,
            "mode": "agent_autonomous",
            "resolved": True,
            "basis": "legacy-runtime-agent-identity",
        }
    return {
        "actor_key_id": actor,
        "principal_key_id": None,
        "mode": "unknown",
        "resolved": False,
        "basis": "identity-kind-not-declared",
    }


def qualify_episode(episode: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Fail-closed eligibility gate; volume is intentionally never inspected."""
    reasons: List[str] = []
    quality = episode.get("evidence_quality") or {}
    outcome = episode.get("outcome") or {}
    attribution = episode.get("attribution") or {}
    task = episode.get("task") or {}
    if quality.get("signature") != "verified":
        reasons.append("signature_not_verified")
    if (episode.get("review_package") or {}).get("status") != "ready":
        reasons.append("review_package_not_ready")
    if episode.get("trust_level") not in {"attested", "verified"}:
        reasons.append("trust_below_attested")
    if not task.get("taxonomy_refs"):
        reasons.append("taxonomy_missing")
    if outcome.get("status") == "unknown":
        reasons.append("outcome_missing")
    if (
        quality.get("outcome") != "observed"
        or outcome.get("basis") not in {"external_acceptance", "work_asset_reuse"}
    ):
        reasons.append("outcome_not_independently_observed")
    if quality.get("gate") == "false_gate":
        reasons.append("false_gate")
    if quality.get("rights") != "confirmed":
        reasons.append("rights_unconfirmed")
    if not attribution.get("resolved") or attribution.get("mode") == "unknown":
        reasons.append("attribution_unknown")
    if not (episode.get("proof_ref") or {}).get("op_ids"):
        reasons.append("proof_missing")
    return not reasons, reasons


def episode_from_event(
    raw: Dict[str, Any],
    target_dir: str = ".",
    *,
    outcome_index: Optional[Dict[str, Dict[str, Any]]] = None,
    taxonomy_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    from avatar_contract import ContributionEvent

    event = ContributionEvent.from_wire(raw)
    event.validate()
    config = _identity_config(target_dir)
    episode_id = _sha("work-episode-v2:" + event.event_id)
    outcome, outcome_quality = _outcome(event)
    attribution = _attribution(event, config)
    signature_status = _signature_status(raw, target_dir)
    payload = event.payload or {}
    gate_quality = str(payload.get("gate_quality") or "unprobed")
    if gate_quality not in {"falsified", "unprobed", "false_gate", "not_applicable"}:
        gate_quality = "unprobed"
    source_task = _task(event, config)
    task = _apply_taxonomy_decision(
        event,
        episode_id,
        source_task,
        taxonomy_index,
    )
    review_package = _review_package(
        event, task, signature_status, gate_quality
    )
    attestation = (outcome_index or {}).get(episode_id)
    if attestation:
        attested_task = attestation.get("task") or {}
        if (
            review_package.get("status") == "ready"
            and attestation.get("subject_avatar_id") == str(event.avatar_id)
            and attestation.get("work_episode_id") == episode_id
            and attestation.get("review_package_id")
            == review_package.get("review_package_id")
            and (
                _same_task_identity(attested_task, task)
                or _same_task_identity(attested_task, source_task)
            )
        ):
            attested_outcome = attestation.get("outcome") or {}
            outcome = {
                "status": attested_outcome.get("status"),
                "basis": "external_acceptance",
                "observed_at": attested_outcome.get("observed_at"),
                "reference": "outcome-attestation:" + str(attestation.get("attestation_id")),
            }
            outcome_quality = "observed"
    session = event.session or {}
    project = event.project or {}
    proof_ref = dict(event.proof_ref or {})
    episode = {
        "episode_id": episode_id,
        "avatar_id": str(event.avatar_id),
        "project_id": str(project.get("id") or "unknown-project"),
        "session_id": str(session.get("session_id") or event.event_id),
        "task": task,
        "review_package": review_package,
        "outcome": outcome,
        "attribution": attribution,
        "evidence_quality": {
            "signature": signature_status,
            "gate": gate_quality,
            "outcome": outcome_quality,
            "rights": "confirmed",
        },
        "trust_level": str(event.trust_level),
        "occurred_at": _iso(_event_time(event)),
        "eligible_for_capability": False,
        "exclusion_reasons": [],
        "proof_ref": proof_ref,
    }
    eligible, reasons = qualify_episode(episode)
    episode["eligible_for_capability"] = eligible
    episode["exclusion_reasons"] = reasons
    return episode


def episodes_from_events(
    events: Iterable[Dict[str, Any]],
    *,
    target_dir: str = ".",
    avatar_id: Optional[str] = None,
    outcome_store_dir: Optional[str] = None,
    taxonomy_store_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from apatch.outcome_delivery import load_outcome_index
    from apatch.taxonomy_delivery import load_taxonomy_index

    outcome_index = load_outcome_index(
        avatar_id=avatar_id,
        store_dir=outcome_store_dir,
    )
    taxonomy_index = load_taxonomy_index(
        avatar_id=avatar_id,
        store_dir=taxonomy_store_dir,
    )
    episodes: List[Dict[str, Any]] = []
    seen: set = set()
    for raw in events:
        if raw.get("kind") == "claim":
            continue
        raw_avatar = str(raw.get("avatar_id") or (raw.get("identity") or {}).get("key_id") or "")
        if avatar_id and raw_avatar != avatar_id:
            continue
        try:
            episode = episode_from_event(
                raw,
                target_dir,
                outcome_index=outcome_index,
                taxonomy_index=taxonomy_index,
            )
        except Exception:
            continue
        if episode["episode_id"] not in seen:
            seen.add(episode["episode_id"])
            episodes.append(episode)
    return sorted(episodes, key=lambda item: (item["occurred_at"], item["episode_id"]))


def episodes_from_rows(
    all_rows: List[Dict[str, Any]],
    identity: Dict[str, Any],
    project: Dict[str, Any],
    receipts: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Compatibility shim: v2 derives only from receipts, never bare rows."""
    del all_rows, project
    return episodes_from_events(
        receipts or [], avatar_id=str(identity.get("key_id") or "") or None
    )


def build_episodes(
    target_dir: str = ".",
    *,
    avatar_id: Optional[str] = None,
    outcome_store_dir: Optional[str] = None,
    taxonomy_store_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from apatch.contribution import resolve_identity
    from apatch.timesheet import load_events

    subject = avatar_id or str(resolve_identity(target_dir).get("key_id") or "")
    return episodes_from_events(
        load_events(),
        target_dir=target_dir,
        avatar_id=subject or None,
        outcome_store_dir=outcome_store_dir,
        taxonomy_store_dir=taxonomy_store_dir,
    )


def verify_episodes(
    episodes: List[Dict[str, Any]],
    target_dir: str = ".",
) -> Dict[str, Any]:
    avatar_ids = {str(item.get("avatar_id") or "") for item in episodes}
    subject = next(iter(avatar_ids)) if len(avatar_ids) == 1 else None
    fresh = {item["episode_id"]: item for item in build_episodes(target_dir, avatar_id=subject)}
    claimed = {str(item.get("episode_id") or ""): item for item in episodes}
    drift: List[Dict[str, Any]] = []
    for episode_id in sorted(set(claimed) | set(fresh)):
        if episode_id not in fresh:
            drift.append({"episode_id": episode_id, "error": "not_derivable_from_receipts"})
        elif episode_id not in claimed:
            drift.append({"episode_id": episode_id, "error": "missing_from_bundle"})
        elif claimed[episode_id] != fresh[episode_id]:
            drift.append({"episode_id": episode_id, "error": "episode_drift"})
    return {"ok": not drift, "checked": len(episodes), "drift": drift}


def _economic_leak(value: Any) -> bool:
    """Backward-compatible helper used by older callers/tests."""
    try:
        from avatar_contract import assert_capability_evidence_content_safe

        assert_capability_evidence_content_safe({"value": value})
        return False
    except Exception:
        return True
