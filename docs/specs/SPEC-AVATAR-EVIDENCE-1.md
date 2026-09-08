# SPEC-AVATAR-EVIDENCE-1 — CapabilityEvidence v2 and delivery

> **Status:** Active · **Owner:** apatch core · **Anchor:** [RFP-037](../RFP-037-avatar-semantic-engine.md)
> **Artifact:** `spec:SPEC-AVATAR-EVIDENCE-1`

## R1 Signed shared contract

`build_evidence_bundle` emits the shared, content-addressed CapabilityEvidence v2
contract. Attested/verified bundles fail closed without an Ed25519 signer; economic
fields, raw work, prompts, code and credentials are forbidden recursively.

(verify: python3 -m pytest tests/test_avatar_evidence.py::test_bundle_is_signed_shared_contract tests/test_avatar_evidence.py::test_attested_export_fails_closed_without_signer tests/test_avatar_evidence.py::test_bundle_contains_no_economic_projection -q)

## R2 Distillate, not archive

Only independently observed capability-eligible episodes feed estimates. The
metadata distillate also carries signed, taxonomy-bound proxy WorkEpisodes that can be
submitted for independent review; they remain ineligible until OutcomeAttestation.
One external result cannot unlock proxy volume. Total observed scope and aggregate
exclusion reasons remain visible, while raw activity stays local.

(verify: python3 -m pytest tests/test_avatar_evidence.py::test_bundle_exports_qualified_distillate_not_rejected_activity -q)

## R3 Deterministic re-derivation

Historical timestamps never use wall-clock fallback. Rebuilding at the bundle clock
produces the same content and signature; any field tamper fails contract or drift check.

(verify: python3 -m pytest tests/test_avatar_evidence.py::test_bundle_verify_rederives_every_field -q)

## R4 Durable outcome-first synchronization

In internal service mode `apatch avatar sync` first pulls natural-counterparty-signed
OutcomeAttestations, then recompiles/queues evidence and delivers ContributionEvents.
In owner mode the same reverse loop goes through the trust-chain.ai Avatar BFF using
a revocable `tcav_` credential: outcomes are pulled first, then local contributions
and the recompiled evidence snapshot are delivered. No HC service token is exposed to
the professional. Local stores and ACK receipts make delivery idempotent, and offline
mode preserves pending data.

Reconciliation classifies present rows into cryptographically verified and
unverified partitions. Both missing rows and present-but-unverified rows are replayed
through the canonical ingest, then a second reconciliation proves storage and
verification completeness. Present rows missing their immutable full signed envelope
are replayed by the same mechanism; this restores classification/review context without
rewriting the accepted fact. A delivery ACK alone cannot declare repair complete.
Frozen v1 receipts remain owner-scoped through their signed `identity.key_id` when
the top-level `avatar_id` field is absent.

The command reports durable local acceptance as `ok`, but reports cross-system closure
separately as `complete` and `status`. An unconfigured Tracker or an offline outbox can
never be labelled synchronized; pending item count and the required operator action are
explicit in both JSON and MCP results.
The queued evidence result includes a semantic compilation summary: classified
episodes, independently accepted episodes, capability estimates and concrete next
actions. These counters describe workflow state and never introduce economic fields.

Successful `apatch attest` invokes this outcome-first synchronization automatically
after it durably emits the session ContributionEvent. An explicit outcome/taxonomy
store passed by an embedding runtime is also passed into the same recompile; the pull
cannot write one directory while the compiler silently reads another. Sync is an
operational transport and never mutates the governed code-session lifecycle: success,
credential denial and other retryable transport outcomes preserve the exact prior
session state, never synthesize a work failure and never recommend source rollback.
Sync failures return a stable retryable status and never echo exception text or
secret-bearing paths. Before contacting Tracker, sync checks that the installed optional `avatar-contract`
generation contains evidence, taxonomy, review-package and counterparty-authority
symbols. A stale partial runtime fails once as `dependency_incompatible` and cannot
produce a misleading half-synchronized result.

Normal internal operation is restart-safe without copying a bearer into the shell.
`~/.trustchain/avatar_sync.json` contains only the Tracker URL and the non-secret,
purpose-bound TrustChain Secrets binding pointer; the resolver returns the bearer only
to the running apatch process after attended approval. `~/.trustchain/avatar_trust_policy.json`
separately pins public Tracker taxonomy keys and the exact counterparty
`key -> organization -> role` registry. Both files have a versioned, closed schema and
reject an unsafe owner/mode or unknown fields. Environment values remain explicit
break-glass overrides, not the normal persistence mechanism.

(verify: python3 -m pytest tests/test_avatar_delivery.py tests/test_avatar_attestation_receipt.py tests/test_outcome_delivery.py::test_owner_sync_pulls_outcome_recompiles_capability_and_uploads_distillate tests/test_contribution_delivery.py tests/test_contribution_export.py tests/test_session_state.py::test_avatar_sync_credential_failure_is_lifecycle_neutral tests/test_session_state.py::test_avatar_sync_success_preserves_active_session_state -q)

## R7 Trusted external outcome

An OutcomeAttestation is content-safe, money-free and bound to one subject and one
WorkEpisode. apatch accepts it only from a purpose-bound issuer registry that maps the
signing key to the exact organization and a natural-counterparty role. Association and
assessor roles are forbidden because professional certification is not work-result
acceptance. A rejected certification never becomes a failed capability outcome.

(verify: python3 -m pytest tests/test_outcome_delivery.py tests/test_episode.py::test_attestation_only_is_a_proxy_not_external_outcome tests/test_capability.py::test_one_external_outcome_never_unlocks_technical_proxy_volume -q)

## R5 Identity isolation

Contribution export and delivery are restricted to the evidence bundle's current
`avatar_id`. A global contribution store can never leak another subject into a sync.

(verify: python3 -m pytest tests/test_contribution_export.py::test_export_can_be_scoped_to_one_avatar_identity tests/test_contribution_delivery.py::test_delivery_never_crosses_avatar_identity -q)

## R6 CLI and MCP surfaces

CLI exposes episodes, capability estimates, evidence export and sync. MCP exposes the
same read/export/sync surfaces. The service credential is resolved from its attended
TrustChain Secrets binding and never appears in MCP arguments, configuration files,
argv, logs or response payloads. A direct environment token is break-glass only.

(verify: python3 -m pytest tests/test_avatar_evidence.py::test_cli_surfaces_remain_available -q)

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| A37-A | — | waiver: implemented in SPEC-EPISODE-1 |
| A37-B | — | waiver: implemented in SPEC-EPISODE-1 |
| A37-C | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-D | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-E | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-F | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-G | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-H | R1 | covered; R2, R3 and R7 enforce the export boundary |
| A37-I | — | waiver: HC SPEC-AVATAR-VIEW-2 |
| A37-J | — | waiver: implemented by Avatar Architecture Canon v1.3 amendment |
| A37-K | — | waiver: implemented in SPEC-CAPABILITY-1 |
| A37-L | R7 | covered; R1–R6 remain executable in this spec |
| A37-M | R6 | covered |
