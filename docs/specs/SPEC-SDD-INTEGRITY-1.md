# SPEC-SDD-INTEGRITY-1 -- Frozen Contracts, Task Envelopes and Verifier Integrity

> **apatch artifact:** `spec:SPEC-SDD-INTEGRITY-1`
> **Anchors:** RFP-044 frozen by `docs/contracts/RFP-044-owner-freeze.json`
> **Implementation state:** R0 freezes traceability and tests. R1-R11 remain pending until the precommitted gates pass.
> **Upstream contract pin:** Studio RFP-012 SHA-256 `cdfad4871f81abb6c26d762ab23cf4350aa07eed7d3d69f7a0a230538913ea1d`.

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| CORE-SDD-1 | R1 | covered |
| CORE-SDD-2 | R2 | covered |
| CORE-SDD-3 | R3 | covered |
| CORE-SDD-4 | R4 | covered |
| CORE-SDD-5 | R5 | covered |
| CORE-SDD-6 | R6 | covered |
| CORE-SDD-7 | R7 | covered |
| CORE-SDD-8 | R8 | covered |
| CORE-SDD-9 | R9 | covered |
| CORE-SDD-10 | R10 | covered |
| CORE-SDD-11 | R11 | covered |

The freeze record resolves to commit `381f9acb402af188636db631e9a8e4d701f208ca`, RFP SHA-256
`deb673de57fef35d0f2369581641164c11645bc8d9a8232dab3abf172ce743ae`, and the eleven Core acceptance ids above. The source implementation
starts only after exact tests, fixtures, commands, red baseline and plan are hashed.

(verify: python3 -m pytest tests/test_sdd_integrity_contract.py::test_rfp_044_owner_freeze_and_traceability_are_exact -q)

## R1 Compatibility with the implemented baseline

The opt-in SDD integrity profile composes with existing session, mutation, rollback, sandbox, lease, probe, attestation, conformance, remote and timesheet behavior. Legacy workspaces remain valid and no canonical subsystem is duplicated.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_existing_runtime_remains_canonical_and_opt_in -q)

## R2 Canonical SDD documents

Brief, verification obligation, contract freeze, task envelope and amendment documents validate strictly and hash canonical content without source code, repository paths or secrets.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_sdd_documents_are_canonical_content_safe_and_versioned -q)

## R3 Pre-mutation freeze admission

Contract freeze requires complete RFP-to-SPEC coverage, exact judge assets and commands, independent oracle, required perspectives, baseline, falsification plan and authority while source mutation count is zero.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_contract_freeze_rejects_incomplete_or_post_mutation_judges -q)

## R4 Role-bound immutable judge

Issued implementation and verifier capabilities bind exact actor role and contract/envelope hashes. Implementation cannot write judge assets and verifier cannot mutate source or contract.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_capabilities_separate_implementation_verifier_and_authority -q)

## R5 Complete task envelope

The envelope validates exact requirement and contract hashes, path and symbol sets, tools, commands, network, remote/service effects, budgets, checks, rollback owner and containment level without automatic widening.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_task_envelope_is_exact_non_widening_and_hash_bound -q)

## R6 Unified effect admission

Apply, sandbox/lease, MCP/local runner, network, remote and service integration points consult one fail-closed admission decision before the first supported external effect.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_supported_effect_surfaces_use_one_fail_closed_admission -q)

## R7 Blocking adherence and isolated denial

Plan mismatch or out-of-envelope effect is attributed to the exact actor/session, blocks proof and cannot mutate or roll back unrelated paths.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_plan_and_scope_denial_block_proof_without_foreign_mutation -q)

## R8 Meaningful verifier quality

A non-mutating verifier rejects zero-collected, all-skipped, tautological, drifted or perspective-incomplete results and requires observed-red/restored-green falsification for material gates.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_verifier_quality_rejects_false_green_results -q)

## R9 Exact attestation binding

Attestation evidence binds contract/envelope hashes, actor, mutations, adherence, test counts and results, falsification, environment, checkpoints and ledger refs.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_attestation_evidence_binds_the_complete_causal_contract -q)

## R10 Amendment and selective invalidation

A signed amendment preserves superseded hashes and reason and invalidates exactly affected capabilities, requirements and attestations while retaining unaffected evidence.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_amendment_preserves_history_and_invalidates_only_affected_work -q)

## R11 Shared integrity floor and containment honesty

OSS and Pro expose the same integrity floor, direct channels outside a sealed runner remain unsupported rather than falsely contained, and non-opted-in workspaces keep current behavior.

(verify: python3 -m pytest tests/test_sdd_integrity.py::test_edition_floor_and_containment_claims_are_honest -q)

## R12 Mandatory strict profile admission

When `.apatch/sdd_verification_contract.json` activates the SDD profile, every new
governed session must bind the byte-exact frozen contract and a task envelope whose
`contract_hash` matches it. A legacy or mismatched client fails before a draft
session is created; a session opened before activation is denied before its next
mediated effect. Workspaces without the manifest retain existing governed behavior.
A new session never inherits a completed session's SDD binding, capability, adherence,
verification or falsification. When a new binding is supplied it starts with fresh
proof state; when no profile or binding is supplied no old SDD authority survives.
This isolation does not relax mandatory profile admission or frozen-judge protection.

(verify: python3 -m pytest tests/test_sdd_mandatory_profile.py::test_profile_manifest_requires_exact_binding_before_session_creation tests/test_sdd_mandatory_profile.py::test_existing_legacy_session_is_denied_after_profile_activation tests/test_sdd_mandatory_profile.py::test_task_envelope_must_bind_the_exact_frozen_contract tests/test_sdd_mandatory_profile.py::test_workspace_without_profile_keeps_legacy_session_and_admission tests/test_sdd_session_isolation.py -q)

## R13 Fixed-purpose MCP implementation binding

The MCP session-start surface accepts the exact frozen contract, exact task envelope
and named actor id, fixes the role to `implementation`, and exposes no caller-selected
role or generic actor object. This is the only agent-facing path into an
SDD-implementation session; authority and verifier capabilities remain separate.

(verify: python3 -m pytest tests/test_sdd_mandatory_profile.py::test_mcp_start_exposes_only_fixed_implementation_binding -q)

## Verification contract

The test files named above predate changes under `apatch/**`. A later immutable
verification-contract manifest pins their bytes, commands, observed red baseline,
implementation plan and the exact Studio consumer fixture. Test or command changes
after that point require a signed amendment and selective invalidation.

## Non-goals

This SPEC does not replace existing APatch subsystems. It does not claim control over
an agent that also has unrestricted direct shell or network access outside a sealed
APatch runner. It does not make strict correctness a Pro-only feature.
