"""CapabilityEstimate v2: bounded inference over eligible WorkEpisodes.

Cryptography can prove provenance of an episode; it cannot prove a person's
latent ability.  This module therefore emits estimates with observed counts,
Wilson intervals and explicit uncertainty.  It never emits a financial value,
never treats project count as portability, and never combines identities.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = 2
FRESH_DAYS = 90
STALE_DAYS = 365


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now(now_ts: Optional[float]) -> datetime:
    if now_ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(now_ts), tz=timezone.utc)


def taxonomy_refs_for_episode(episode: Dict[str, Any]) -> List[str]:
    return sorted({str(value) for value in (episode.get("task") or {}).get("taxonomy_refs") or [] if value})


def _primary_taxonomy_ref(refs: Iterable[str]) -> str:
    values = sorted(set(refs))
    priority = ("onet-task:", "onet-skill:", "soc:", "cbm:", "apatch-spec:")
    for prefix in priority:
        matches = [value for value in values if value.startswith(prefix)]
        if matches:
            return matches[0]
    return values[0] if values else ""


def task_class_for_episode(episode: Dict[str, Any]) -> str:
    """Compatibility name for the primary versioned taxonomy reference."""
    return _primary_taxonomy_ref(taxonomy_refs_for_episode(episode))


def _wilson(passed: int, attempted: int, z: float = 1.96) -> Dict[str, Any]:
    if attempted <= 0:
        return {"mean": None, "lower": None, "upper": None, "method": "wilson-95"}
    p = passed / attempted
    denominator = 1.0 + z * z / attempted
    centre = (p + z * z / (2.0 * attempted)) / denominator
    spread = (
        z
        * math.sqrt((p * (1.0 - p) + z * z / (4.0 * attempted)) / attempted)
        / denominator
    )
    return {
        "mean": round(p, 4),
        "lower": round(max(0.0, centre - spread), 4),
        "upper": round(min(1.0, centre + spread), 4),
        "method": "wilson-95",
    }


def _recency(episodes: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    last = max(_parse_iso(item["occurred_at"]) for item in episodes)
    age_days = max(0.0, (now - last).total_seconds() / 86400.0)
    state = "fresh" if age_days < FRESH_DAYS else "aging" if age_days < STALE_DAYS else "stale"
    return {"last_observed_at": last.isoformat(), "state": state}


def _attribution_mix(episodes: List[Dict[str, Any]]) -> Dict[str, float]:
    counts = Counter(str((item.get("attribution") or {}).get("mode") or "unknown") for item in episodes)
    total = len(episodes) or 1
    return {name: round(count / total, 4) for name, count in sorted(counts.items())}


def _uncertainty(
    episodes: List[Dict[str, Any]],
    *,
    recency: Dict[str, Any],
    taxonomy_refs: List[str],
) -> Tuple[Dict[str, Any], str]:
    reasons: List[str] = []
    if len(episodes) < 5:
        reasons.append("n_lt_5")
    if all((item.get("evidence_quality") or {}).get("outcome") != "observed" for item in episodes):
        reasons.append("only_technical_proxy_outcomes")
    if all((item.get("evidence_quality") or {}).get("gate") != "falsified" for item in episodes):
        reasons.append("no_falsified_gates")
    if len({item.get("project_id") for item in episodes}) < 2:
        reasons.append("single_project")
    if recency["state"] == "stale":
        reasons.append("stale_evidence")
    if not any(ref.startswith(("soc:", "onet-task:", "onet-skill:", "cbm:")) for ref in taxonomy_refs):
        reasons.append("market_taxonomy_missing")
    level = "high" if len(reasons) >= 2 else "medium" if reasons else "low"
    status = (
        "estimated"
        if len(episodes) >= 2 and "only_technical_proxy_outcomes" not in reasons
        else "insufficient_evidence"
    )
    return {"level": level, "reasons": reasons}, status


def derive_capabilities(
    episodes: List[Dict[str, Any]],
    *,
    now_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    now = _now(now_ts)
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for episode in episodes:
        if not episode.get("eligible_for_capability"):
            continue
        avatar_id = str(episode.get("avatar_id") or "")
        primary = _primary_taxonomy_ref(taxonomy_refs_for_episode(episode))
        if avatar_id and primary:
            grouped.setdefault((avatar_id, primary), []).append(episode)

    estimates: List[Dict[str, Any]] = []
    for (avatar_id, primary), evidence in sorted(grouped.items()):
        evidence = sorted(evidence, key=lambda item: (item["occurred_at"], item["episode_id"]))
        taxonomy_refs = sorted({ref for item in evidence for ref in taxonomy_refs_for_episode(item)})
        passed = sum((item.get("outcome") or {}).get("status") == "passed" for item in evidence)
        failed = sum((item.get("outcome") or {}).get("status") == "failed" for item in evidence)
        attempted = passed + failed
        recency = _recency(evidence, now)
        uncertainty, status = _uncertainty(
            evidence, recency=recency, taxonomy_refs=taxonomy_refs
        )
        projects = {str(item.get("project_id") or "") for item in evidence}
        estimates.append({
            "estimate_id": _sha("capability-estimate-v2:" + avatar_id + ":" + primary),
            "subject_avatar_id": avatar_id,
            "taxonomy_refs": taxonomy_refs,
            "evidence_episode_ids": [item["episode_id"] for item in evidence],
            "status": status,
            "observations": {
                "attempted": attempted,
                "passed": passed,
                "failed": failed,
            },
            "success_estimate": _wilson(passed, attempted),
            "recency": recency,
            "cross_context": {
                "project_count": len(projects),
                "observed_across_projects": len(projects) >= 2,
            },
            "attribution_mix": _attribution_mix(evidence),
            "uncertainty": uncertainty,
        })
    return estimates


def build_capabilities(
    target_dir: str = ".",
    *,
    now_ts: Optional[float] = None,
    avatar_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from apatch.episode import build_episodes

    return derive_capabilities(
        build_episodes(target_dir, avatar_id=avatar_id), now_ts=now_ts
    )
