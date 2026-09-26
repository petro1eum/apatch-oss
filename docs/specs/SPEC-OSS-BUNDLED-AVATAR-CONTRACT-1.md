# SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1 — Bundled canonical Avatar contract in APatch OSS

> **apatch artifact:** `spec:SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1`  
> **Anchors:** RFP-049  
> **Owner decision:** 2026-09-26 — "include avatar_contract in APatch OSS"  
> **ownership mode:** strict

## R0 RFP traceability gate (meta)

owns: docs/RFP-049-OSS-BUNDLED-AVATAR-CONTRACT.md, docs/specs/SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1.md, tests/test_vendored_avatar_contract.py

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| BAC-1 | R1 | covered |
| BAC-2 | R2 | covered |
| BAC-3 | R3 | covered |
| BAC-4 | R4 | covered |
| BAC-5 | R5 | covered |
| BAC-6 | R6 | covered |
| BAC-7 | R7 | covered |
| BAC-8 | R8 | covered |
| BAC-9 | R9 | covered |

Require exactly nine unique acceptance ids, one-to-one R1–R9 mappings, strict
ownership declarations and one requirement-specific verification command each.

(verify: python3 -m pytest tests/test_vendored_avatar_contract.py::test_r0_traceability_and_ownership -q)

## R1 Byte-identical vendored canonical contract

owns: apatch/_vendor/**, scripts/vendor_avatar_contract.py, tests/test_vendored_avatar_contract.py

Vendor the canonical `avatar-contract` package at commit
`44c8f9ada8a50fb8b7c94346a4103c09a19f15c2` (0.7.2, MIT) from Git objects into
`apatch/_vendor/avatar_contract/`: all 17 package files, the upstream `LICENSE` and
`UPSTREAM.json` (repository, commit, version, license, rewrite rule, SHA-256 of every
upstream file). The only transformation is the reversible import rewrite
`^(\s*)from avatar_contract(\.| )` → `\1from apatch._vendor.avatar_contract\2`.
`scripts/vendor_avatar_contract.py --check` proves identity through the inverse
rewrite and fails on a changed byte, an extra or missing file, a non-mechanical edit
or a changed manifest; `--checkout PATH --commit SHA` regenerates the copy.

(verify: python3 -m pytest tests/test_vendored_avatar_contract.py::test_r1_vendored_copy_is_byte_identical_to_upstream tests/test_vendored_avatar_contract.py::test_r1_check_rejects_drifted_vendored_copy -q)

## R2 APatch uses only the bundled contract

owns: apatch/avatar_delivery.py, apatch/avatar_evidence.py, apatch/avatar_identity_gate.py, apatch/concept_verify.py, apatch/contribution.py, apatch/edge_lockstep.py, apatch/episode.py, apatch/outcome_delivery.py, apatch/taxonomy_delivery.py, apatch/timesheet.py, tests/test_avatar_contract_optional.py, tests/test_avatar_evidence.py, tests/test_episode.py, tests/test_outcome_delivery.py, tests/test_taxonomy_delivery.py

Every APatch import of the Avatar contract names `apatch._vendor.avatar_contract`.
No runtime module imports a top-level `avatar_contract`, statically or through
`importlib`. In a fresh interpreter where that name is blocked, and in one where it
is bound to a stale incompatible module, contribution building, the timesheet key
allowlist, the schema lockstep edge and every Avatar module work from the bundled
classes and no top-level module is loaded.

(verify: python3 -m pytest tests/test_avatar_contract_optional.py -q)

## R3 Runtime report names the bundled contract

owns: apatch/avatar_delivery.py, tests/test_avatar_delivery.py

`avatar_runtime_compatibility()` reports `ok: true`, `status: ready`,
`source: bundled`, `bundled: true`, `installed_version` and `upstream_commit` from
`UPSTREAM.json` and the vendored `module_path`. A separately installed distribution is
only named in `external_installed_version` with `external_used: false`; a stale external
module changes nothing. A damaged bundled copy reports `dependency_incompatible` with
`reinstall_apatch`, and Avatar sync stops before partial contract use with the outbox
preserved.

(verify: python3 -m pytest tests/test_avatar_delivery.py::test_runtime_reports_bundled_contract_and_ignores_stale_external_module tests/test_avatar_delivery.py::test_damaged_bundled_contract_fails_before_partial_contract_use -q)

## R4 Bundled resources and unconditional contract tests

owns: tests/test_vendored_avatar_contract.py, tests/test_transport_contract_avatar_bff.py, tests/test_contribution_event.py, tests/test_edge_lockstep.py, tests/test_concept_verify.py, tests/test_concept_status.py, tests/test_concept_cli.py

Transport and schema JSON documents load from the bundled package through
`importlib.resources`, with raw bytes equal to the recorded upstream hashes. Tests
that previously skipped or failed collection without an installed peer import the
bundled package and run unconditionally.

(verify: python3 -m pytest tests/test_vendored_avatar_contract.py::test_r4_bundled_resources_load_through_importlib_resources tests/test_transport_contract_avatar_bff.py tests/test_edge_lockstep.py -q)

## R5 End-to-end contribution without the external package

owns: tests/test_vendored_avatar_contract.py

In a temporary workspace and a fresh interpreter with the top-level
`avatar_contract` name blocked, a governed session with one ledger mutation is
attested through `MutationRuntime.attest`. The result reports
`contribution_receipt.status == "emitted"`, the ContributionEvent written to the
store validates against the bundled class, and no `AVATAR_CONTRACT_UNAVAILABLE` or
top-level module appears.

(verify: python3 -m pytest tests/test_vendored_avatar_contract.py::test_r5_attest_emits_contribution_without_top_level_package -q)

## R6 Distribution ships the bundled contract

owns: tests/test_vendored_avatar_contract.py

Package data declares the vendored schema, transport, `LICENSE` and `UPSTREAM.json`
files. Wheel and sdist contain every vendored file byte-identical to the checkout and
no top-level `avatar_contract` package; the project has no `avatar` extra and no
direct-URL requirement.

(verify: python3 -m pytest tests/test_vendored_avatar_contract.py::test_r6_distribution_declares_bundled_contract tests/test_distribution.py::test_wheel_and_sdist_publish_mit_without_private_inputs -q)

## R7 OSS qualification without Avatar waivers

owns: tests/test_vendored_avatar_contract.py

The OSS qualification inventory contains no Avatar absent-peer failures, no
peer requirements and no Avatar skips; only the Java grammar skip remains optional.
The Avatar profile reads the pin from `UPSTREAM.json`, archives the canonical checkout
at that commit and accepts only a vendored copy equal to it; the canonical
sibling-repository contract checks remain the single unproven external specification.

(verify: python3 -m pytest tests/test_vendored_avatar_contract.py::test_r7_qualification_inventory_has_no_avatar_waivers tests/test_oss_verification_profiles.py::test_avatar_requires_canonical_peer -q)

## R8 Documentation records the bundled contract

owns: docs/RFP-028-avatar-wiring.md, tests/test_avatar_docs_contract.py

The Avatar wiring reconciliation note, README, CHANGELOG and MCP setup notes state
that APatch bundles the canonical contract as `apatch._vendor.avatar_contract` and no
longer ask for a separate install. The Avatar canon already names one shared,
versioned contract package and needs no change. A docs contract test pins that
wording and rejects the retired install instructions.

(verify: python3 -m pytest tests/test_avatar_docs_contract.py -q)

## R9 Emitted contributions carry measured work time

owns: apatch/contribution.py, tests/test_contribution_active_time.py, tests/test_governed_work_compatibility.py

`apatch_attest` emits the ContributionEvent before `session_end`, so the event's
`duration_sec` is the sub-second ceremony described by SPEC-CONTRIB-TIMESHEET-1 R8 and
governed evidence, which claims `active_sec` first, claimed zero work time. Every newly
built event sets `session.active_sec` to the measured span from the session's
`started_at` through every ledger operation of that session (the attestation included),
excluding gaps longer than 30 minutes; it is deterministic from signed timestamps and
zero without a span. `duration_sec`, `ended_at` and timesheet hours are unchanged. The
wire golden hash moves only because of this field; an event whose `active_sec` is null
keeps the previous golden bytes, so stored events keep their bytes and signatures.

(verify: python3 -m pytest tests/test_contribution_active_time.py tests/test_governed_work_compatibility.py -q)
