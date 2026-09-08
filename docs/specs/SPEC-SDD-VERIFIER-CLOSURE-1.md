# SPEC-SDD-VERIFIER-CLOSURE-1 -- Executable Frozen Judge

> **apatch artifact:** `spec:SPEC-SDD-VERIFIER-CLOSURE-1`
> **RFP:** RFP-045
> **Baseline:** APatch 0.8.40 at `c7bdbe571188773f84aab5812b03a682e99a2683`
> **Rule:** tests and this SPEC are frozen before `apatch/**` implementation changes.

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| SDD-CLOSE-1 | R1 | covered |
| SDD-CLOSE-2 | R2 | covered |
| SDD-CLOSE-3 | R3 | covered |
| SDD-CLOSE-4 | R4 | covered |
| SDD-CLOSE-5 | R5 | covered |
| SDD-CLOSE-6 | R6 | covered |
| SDD-CLOSE-7 | R7 | covered |
| SDD-CLOSE-8 | R8 | covered |
| SDD-CLOSE-9 | R9 | covered |

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_rfp_traceability_is_exact -q)

## R1 Exact contract retained locally

An SDD session stores the exact hash-bound verification contract needed by the
fixed verifier. The public attestation projection still exposes only approved hashes
and outcomes.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_session_retains_exact_contract_but_public_projection_does_not -q)

## R2 Fixed-purpose capability

The verifier operation accepts only target directory, exact session id and runtime
timeout. It rejects a mismatched session before executing and has no command, result,
count, perspective or role parameters.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_fixed_verifier_has_no_caller_supplied_judge_or_result -q)

## R3 Judge pin admission

The verifier checks the canonical argv hash and complete judge-asset path/hash mapping
before first execution. Command or asset drift fails closed and causes no marker side
effect.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_command_or_judge_drift_blocks_before_execution -q)

## R4 Shell-free content-safe execution

Every command runs as an argv list without shell expansion. Evidence records bounded
outcome hashes and counts and excludes raw output, absolute paths and source content.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_command_executes_as_argv_and_evidence_is_content_safe -q)

## R5 Reversible falsification

A material gate turns red under the frozen built-in perturbation, restores exact
target bytes and executable mode, then returns green.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_material_gate_observes_red_and_restores_exact_target -q)

## R6 Atomic exact-session proof

Accepted verification and falsification are persisted together on the exact active
session. Rejected verification cannot satisfy attestation.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_proof_is_persisted_atomically_on_exact_session -q)

## R7 Finalize routing and legacy compatibility

SDD-bound `execute_next(finalize=true)` invokes the fixed verifier before attest;
legacy sessions continue through `verify_run`.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_finalize_routes_sdd_and_legacy_sessions_to_their_own_verifiers -q)

## R8 Fixed MCP surface

Full MCP registers `apatch_sdd_verify` with exact governed session id and token and
without caller-supplied command, result or role.

(verify: python3 -m pytest tests/test_sdd_verifier_closure.py::test_mcp_surface_is_fixed_purpose -q)

## R9 Regression across the SDD surface

The focused closure suite and the whole existing SDD suite remain green, including the
deterministic conflict contract of concurrent session admission.

Whether the FULL APatch suite is green is a release gate, not this requirement's gate:
a global run proves nothing about this requirement, exceeds the contract-gate budget,
and left the check unrunnable, so the standing gate reported it broken rather than red.
The whole suite is owned by continuous conformance and CI.

(verify: python3 -m pytest -q tests/test_sdd_verifier_closure.py tests/test_sdd_integrity.py tests/test_sdd_integrity_amendment_a2.py tests/test_sdd_integrity_contract.py tests/test_sdd_mandatory_profile.py tests/test_sdd_session_isolation.py)
