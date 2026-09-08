# SPEC-EPISODE-1 — WorkEpisode v2 read model

> **Status:** Active v2 · **Owner:** apatch core · **Anchor:** [RFP-037](../RFP-037-avatar-semantic-engine.md)
> **Artifact:** `spec:SPEC-EPISODE-1`

## R1 Signed receipt is the only fact source

`episode_from_event` validates the shared `ContributionEvent` and verifies its
Ed25519 signature against the subject key. Bare ledger rows never create an episode.

(verify: python3 -m pytest tests/test_episode.py::test_signed_observed_human_episode_is_eligible tests/test_episode.py::test_bare_ledger_rows_can_never_create_an_episode -q)

## R2 Deterministic fact projection

`episode_id`, task refs, outcome, attribution, evidence quality, occurrence time and
proof pointer are deterministic. Missing legacy timestamps become epoch/stale rather
than wall-clock "now". Full-field re-derivation detects tampering.

(verify: python3 -m pytest tests/test_episode.py::test_missing_legacy_timestamp_is_deterministic_not_wall_clock tests/test_episode.py::test_full_episode_rederivation_detects_any_field_tamper -q)

## R3 Fail-closed capability eligibility

Eligibility requires verified signature, attested-or-better trust, accepted taxonomy,
observed outcome, resolved attribution, confirmed rights and proof ops. Every failed
condition is retained as an explicit exclusion reason.

(verify: python3 -m pytest tests/test_episode.py::test_invalid_signature_and_unknown_role_fail_closed -q)

## R4 Proxy outcome is not external success

A technical attestation may create a `technical_gate` proxy fact, but its evidence
quality remains `proxy`; downstream capability rules may not present it as externally
validated professional performance.

(verify: python3 -m pytest tests/test_episode.py::test_attestation_only_is_a_proxy_not_external_outcome -q)

## R5 Volume blind

Operation, file and line counts do not exist on the WorkEpisode signal surface and
cannot buy eligibility or confidence.

(verify: python3 -m pytest tests/test_episode.py::test_volume_does_not_exist_on_the_episode_signal_surface -q)

## R6 Human-readable counterparty review package

Every new governed WorkEpisode carries a deterministic `WorkReviewPackage` derived
only from an explicit completion summary, exact SPEC requirement titles, signed proof
references and verification state. The private session intent, prompts, commands,
source code and raw files are never inferred into the package. Missing inputs produce
an explicit `incomplete` package that remains visible but cannot be accepted. Only a
contractually authorized counterparty can attest the external outcome; the producer
and the Avatar owner cannot accept their own work.

(verify: python3 scripts/verify_avatar_extra_local.py)

## R7 Outcome and owner taxonomy remain independent

An `OutcomeAttestation` remains bound to the exact source `WorkEpisode` and unchanged
`WorkReviewPackage`. A later signed owner `TaxonomyDecision` may enrich the task with
O*NET refs without invalidating that counterparty outcome. The attested task must still
match either the immutable source task or the current task; owner taxonomy never means
that the counterparty approved the classification.

(verify: python3 -m pytest tests/test_taxonomy_delivery.py::test_owner_classification_does_not_invalidate_prior_counterparty_outcome tests/test_episode.py::test_outcome_attestation_must_match_exact_ready_review_package -q)

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| A37-A | R1 | covered; R2 enforces deterministic re-derivation |
| A37-B | R3 | covered |
| A37-C | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-D | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-E | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-F | R3 | covered |
| A37-G | R5 | covered |
| A37-H | — | waiver: implemented in SPEC-AVATAR-EVIDENCE-1 |
| A37-I | — | waiver: implemented in HC SPEC-AVATAR-VIEW-2 |
| A37-J | — | waiver: implemented in Avatar Architecture Canon v1.3 |
| A37-K | R7 | covered with SPEC-CAPABILITY-1 and HC owner-governed taxonomy |
| A37-L | R6 | covered; this executable spec is fully attested |
| A37-M | — | waiver: implemented in SPEC-AVATAR-EVIDENCE-1 |
