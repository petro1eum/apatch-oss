# SPEC-CAPABILITY-1 — CapabilityEstimate v2

> **Status:** Implemented v2 · **Owner:** apatch core · **Anchor:** [RFP-037](../RFP-037-avatar-semantic-engine.md)
> **Artifact:** `spec:SPEC-CAPABILITY-1`

## R1 Evidence-linked estimate

Every estimate names one `subject_avatar_id`, accepted taxonomy refs and the exact
eligible episode ids. Ineligible episodes never feed an estimate and identities are
never blended through ownership or aggregation.

(verify: python3 -m pytest tests/test_capability.py::test_ineligible_episode_never_feeds_an_estimate tests/test_capability.py::test_two_avatar_identities_are_never_combined -q)

## R2 Honest outcome interval

Observations contain attempted/passed/failed counts. Success is represented by an
empirical mean plus Wilson-95 lower/upper bounds; a small all-green sample remains
visibly uncertain.

(verify: python3 -m pytest tests/test_capability.py::test_estimate_has_observations_and_wilson_interval tests/test_capability.py::test_all_green_small_sample_never_looks_certain -q)

## R3 Proxy-only evidence is insufficient

Technical attestation alone yields `status=insufficient_evidence`, regardless of its
green rate. `estimated` requires evidence that can support a professional outcome.

(verify: python3 -m pytest tests/test_capability.py::test_technical_attestations_are_insufficient_for_professional_ability -q)

## R4 No invented portability

The estimate reports only observed cross-project context. It does not infer economic
portability or company dependency; those remain L3 models with separate evidence.

(verify: python3 -m pytest tests/test_capability.py::test_cross_project_is_observation_not_portability_or_company_dependency -q)

## R5 Market taxonomy gap is explicit

Internal `apatch-spec:*` classes remain internal. Missing accepted O*NET/SOC mapping is
an uncertainty reason and blocks HC shadow application; no lexical heuristic silently
creates a market profession.

(verify: python3 -m pytest tests/test_capability.py::test_market_taxonomy_gap_is_explicit -q)

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| A37-C | R1, R2 | covered |
| A37-D | R2, R3, R5 | covered |
| A37-E | R2, R3 | covered |
| A37-F | R1 | covered |
| A37-G | R1, R3 | covered |
| A37-K | R5 | covered; governed external bridge remains next increment |
| A37-L | R1–R5 | covered |
