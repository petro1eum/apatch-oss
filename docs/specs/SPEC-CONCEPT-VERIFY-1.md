# SPEC-CONCEPT-VERIFY-1 — Concept verify: run the invariants

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-VERIFY-1`
> **Anchors:** [RFP-034 §B.3 / §3.6](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-COVERAGE-1](./SPEC-CONCEPT-COVERAGE-1.md) · [SPEC-EDGE-LOCKSTEP-1](./SPEC-EDGE-LOCKSTEP-1.md) · [SPEC-AVATAR-CONCEPTS-1](./SPEC-AVATAR-CONCEPTS-1.md)

## 0. Motivation

Coverage asks "is there a checkable invariant"; this asks **"does it pass"**.
`verify_concept_invariants(graph, target_dir)` dispatches each invariant `ref` to a real
apatch check — the same primitives the canon references — and returns a verdict per
invariant and per concept. An unknown ref is `broken` (a check that does not exist),
never silently green (RFP-034 §3.6).

This generalises the edge sentinel: the `SCHEMA-LOCKSTEP` runner is `contribution_lockstep`
(SPEC-EDGE-LOCKSTEP-1); `economic_barrier` runs the contract's own assertion. The registry
grows as more refs get wired.

## R1 Invariants actually run (green)

On the real avatar graph, `cpt_contribution_event`'s invariants **execute**: the
`SCHEMA-LOCKSTEP` sentinel runs and the economic barrier is asserted; both pass, so the
concept is `green`.

(verify: python3 -m pytest tests/test_concept_verify.py::test_r1_runs_real_checks_green -q)

## R2 No invariant is grey

A concept with no invariants is `grey` — counted, never reported as passing.

(verify: python3 -m pytest tests/test_concept_verify.py::test_r2_no_invariant_is_grey -q)

## R3 Unknown ref is broken

An invariant whose `ref` has no registered runner is `broken` — a check that does not
exist is never silently green (§3.6).

(verify: python3 -m pytest tests/test_concept_verify.py::test_r3_unknown_ref_is_broken -q)

## R4 Failing check is red

When a check fails, the concept is `red` with the failure detail — verify reflects
reality, not intent.

(verify: python3 -m pytest tests/test_concept_verify.py::test_r4_failing_check_is_red -q)

## Non-goals

- Not a full predicate language — refs dispatch to existing apatch checks via a registry (RFP-034 non-goal: no new predicate language).
- Not auto-wiring every ref — `arch_rule`/`index_query`/`semantic` runners are added as concepts need them; until then those refs are honestly `broken`.
- Not surfacing red/broken on the rendered graph yet — coverage colours guarded/grey; live verify status on the graph is a follow-up.