# SPEC-AVATAR-CONTRACT-1 — Shared ContributionEvent contract & identity anchor

> **Status:** Implemented; reconciled 2026-08-28 — R1–R5 verify green (`avatar-contract` 0.5.0, 83 tests); lint + RFP coverage remain governed separately. · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-AVATAR-CONTRACT-1`
> **Anchors:** [RFP-028](../RFP-028-avatar-wiring.md) · [Avatar Architecture Canon §7–§8](../AVATAR-ARCHITECTURE-CANON.md)

## 0. Motivation

One contract, imported by both producer (apatch) and consumer (HC), is the spine of
the avatar layer. This spec locks the shared `ContributionEvent` schema and the
identity anchor (`key_id`-primary alias resolver) so no layer redefines them. The
contract lives in the `avatar-contract` package. Producer and consumer pin the same
Git commit; a sibling checkout is used only by the local executable verification
commands below.

## R0 RFP traceability gate (meta)

RFP-028 acceptance rows map to the requirements below. This spec owns A28-A and
A28-C; the rest are waivered to sibling specs (multi-spec RFP).

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A28-A | R1 | covered |
| A28-A | R2 | covered |
| A28-A | R3 | covered |
| A28-C | R5 | covered |
| A28-E | R4 | covered |
| A28-B | — | waiver: implemented in SPEC-CONTRIB-TIMESHEET-1 |
| A28-D | — | waiver: implemented in SPEC-CONTRIB-PIPE-1 |
| A28-F | — | waiver: governed re-attestation in SPEC-CONTRIB-TIMESHEET-1 |
| A28-G | — | waiver: implemented in HC SPEC-AVATAR-VIEW-1 |
| A28-H | — | waiver: live owner/counterparty delivery proof is tracked by RFP-037 and the HC actor E2E matrix |

(verify: apatch rfp coverage --rfp RFP-028 --spec SPEC-AVATAR-CONTRACT-1)

## R1 Current ContributionEvent v3 schema round-trips

The shared `avatar_contract.ContributionEvent` serializes to / parses from the
canonical v3 wire shape (`proof_ref` + `avatar_id`, `schema_version` 3), stable
sorted-key JSON. Version 3 requires a signed, timezone-aware producer `created_at`;
it is evidence time, not an independent timestamp authority.

(verify: python3 -m pytest ../avatar-contract/tests/test_contribution_event_contract.py::test_round_trip_current_version ../avatar-contract/tests/test_contribution_event_contract.py::test_v3_requires_timezone_aware_signed_creation_time -q)

## R2 Backward-compatible read of v1 and v2

`from_wire` accepts v2 without `created_at` and a v1 producer event — proof pointer under the legacy
`attestation` key, `kind="contribution"`, no `avatar_id` — and normalizes it
(`attestation`→`proof_ref`, `contribution`→`fact`, `avatar_id` from `identity.key_id`).

(verify: python3 -m pytest ../avatar-contract/tests/test_contribution_event_contract.py::test_reads_v2_without_created_at_for_backward_compatibility ../avatar-contract/tests/test_contribution_event_contract.py::test_reads_legacy_v1_attestation_and_kind -q)

## R3 Signing invariant — version-aware, v1 byte-stable

Verification canonicalizes the RAW dict per version: a v1 receipt hashes WITH
`attestation`; a v2 event hashes WITH `proof_ref` and carries NO legacy mirror.
`canonical_unsigned` is byte-compatible with apatch's `_canonical`, so already-signed
v1 receipts keep verifying.

(verify: python3 -m pytest "../avatar-contract/tests/test_contribution_event_contract.py" -k "signed_digest or no_legacy_mirror" -q)

## R4 Economic barrier

A `ContributionEvent` never carries economic-layer keys (pi/gpi/creator_bonus/
clearing/marketplace/…); `assert_economic_barrier` rejects them. Money is Layer 3.

(verify: python3 -m pytest ../avatar-contract/tests/test_contribution_event_contract.py::test_economic_barrier_rejects_money_keys -q)

## R5 Identity anchor — `key_id` primary, aliases resolve

`AliasResolver` maps external handles (github_login, org_user_id, professional_uuid,
contributor_id, enrolled_cn, legacy_agent_id) TO the canonical `key_id`, preserving
ADR-004 namespaces (identity of action is not collapsed into attribution). Closes the
`avatar_id ≡ contributor_id ≡ key_id` binding.

(verify: python3 -m pytest ../avatar-contract/tests/test_identity_resolver.py -q)
