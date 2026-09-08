"""CapabilityEstimate v2 tests: bounded inference, no identity blending."""
import hashlib


NOW = 1_783_862_400.0


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:40]


def _episode(
    index: int,
    *,
    avatar="a" * 32,
    status="passed",
    project="project-1",
    outcome_quality="observed",
    eligible=True,
    taxonomy="soc:15-1252",
):
    return {
        "episode_id": _digest(f"episode:{avatar}:{index}"),
        "avatar_id": avatar,
        "project_id": project,
        "session_id": f"session-{index}",
        "task": {
            "label": "Governed work episode",
            "taxonomy_refs": [taxonomy, "onet-task:1234"],
            "spec_refs": ["SPEC-AVATAR-1#R1"],
        },
        "outcome": {
            "status": status,
            "basis": "external_acceptance" if outcome_quality == "observed" else "technical_gate",
            "observed_at": "2026-07-12T10:00:00+00:00",
            "reference": f"event:{index}",
        },
        "attribution": {
            "actor_key_id": avatar,
            "principal_key_id": avatar,
            "mode": "direct_human",
            "resolved": True,
            "basis": "test",
        },
        "evidence_quality": {
            "signature": "verified",
            "gate": "falsified",
            "outcome": outcome_quality,
            "rights": "confirmed",
        },
        "trust_level": "attested",
        "occurred_at": "2026-07-12T10:00:00+00:00",
        "eligible_for_capability": eligible,
        "exclusion_reasons": [] if eligible else ["attribution_unknown"],
        "proof_ref": {"op_ids": [f"op-{index}"], "head": f"op-{index}"},
    }


def test_estimate_has_observations_and_wilson_interval():
    from apatch.capability import derive_capabilities

    episodes = [_episode(1), _episode(2, status="failed")]
    estimate = derive_capabilities(episodes, now_ts=NOW)[0]
    assert estimate["status"] == "estimated"
    assert estimate["observations"] == {"attempted": 2, "passed": 1, "failed": 1}
    assert estimate["success_estimate"]["mean"] == 0.5
    assert estimate["success_estimate"]["lower"] < 0.5 < estimate["success_estimate"]["upper"]
    assert estimate["evidence_episode_ids"] == [item["episode_id"] for item in episodes]


def test_all_green_small_sample_never_looks_certain():
    from apatch.capability import derive_capabilities

    estimate = derive_capabilities([_episode(1), _episode(2)], now_ts=NOW)[0]
    assert estimate["success_estimate"]["mean"] == 1.0
    assert estimate["success_estimate"]["lower"] < 0.35
    assert estimate["uncertainty"]["level"] == "high"


def test_technical_attestations_are_insufficient_for_professional_ability():
    from apatch.capability import derive_capabilities

    episodes = [_episode(i, outcome_quality="proxy") for i in range(1, 7)]
    for episode in episodes:
        episode["eligible_for_capability"] = False
        episode["exclusion_reasons"] = ["outcome_not_independently_observed"]
    assert derive_capabilities(episodes, now_ts=NOW) == []


def test_one_external_outcome_never_unlocks_technical_proxy_volume():
    from apatch.capability import derive_capabilities

    observed = _episode(1)
    proxies = [_episode(i, outcome_quality="proxy") for i in range(2, 102)]
    for episode in proxies:
        episode["eligible_for_capability"] = False
        episode["exclusion_reasons"] = ["outcome_not_independently_observed"]
    estimate = derive_capabilities([observed, *proxies], now_ts=NOW)[0]
    assert estimate["status"] == "insufficient_evidence"
    assert estimate["observations"] == {"attempted": 1, "passed": 1, "failed": 0}
    assert estimate["evidence_episode_ids"] == [observed["episode_id"]]


def test_two_avatar_identities_are_never_combined():
    from apatch.capability import derive_capabilities

    estimates = derive_capabilities(
        [_episode(1, avatar="a" * 32), _episode(2, avatar="b" * 32)],
        now_ts=NOW,
    )
    assert len(estimates) == 2
    assert {item["subject_avatar_id"] for item in estimates} == {"a" * 32, "b" * 32}
    assert all(item["observations"]["attempted"] == 1 for item in estimates)


def test_cross_project_is_observation_not_portability_or_company_dependency():
    from apatch.capability import derive_capabilities

    estimate = derive_capabilities(
        [_episode(1, project="p1"), _episode(2, project="p2")], now_ts=NOW
    )[0]
    assert estimate["cross_context"] == {
        "project_count": 2,
        "observed_across_projects": True,
    }
    blob = str(estimate).lower()
    assert "portable" not in blob
    assert "company_dependency" not in blob


def test_ineligible_episode_never_feeds_an_estimate():
    from apatch.capability import derive_capabilities

    assert derive_capabilities([_episode(1, eligible=False)], now_ts=NOW) == []


def test_market_taxonomy_gap_is_explicit():
    from apatch.capability import derive_capabilities

    episode = _episode(1, taxonomy="apatch-spec:spec-avatar")
    episode["task"]["taxonomy_refs"] = ["apatch-spec:spec-avatar"]
    estimate = derive_capabilities([episode], now_ts=NOW)[0]
    assert "market_taxonomy_missing" in estimate["uncertainty"]["reasons"]
