# SPEC-CONFORMANCE-GATE-1 — Continuous conformance: the standing contract-gate

> **apatch artifact:** `spec:SPEC-CONFORMANCE-GATE-1`
> **Anchors:** [RFP-035](../RFP-035-continuous-conformance.md), [conformance.md](../conformance.md)

The self-referential capstone of RFP-035: the conformance gate is itself a spec
under the contract it enforces. Each requirement maps to an RFP-035 acceptance
criterion and is verified by a self-contained (ci-safe) test in
`tests/test_conformance.py`.

## R1 Opt-in (A35-A)

Without `.apatch/conformance.json` the gate is inert — no behaviour change.

(verify: python3 -m pytest tests/test_conformance.py::test_disabled_without_config tests/test_conformance.py::test_gate_skips_when_disabled -q)

## R2 All buckets at once (A35-B, A35-D)

One run classifies every gated spec and reports all buckets together; coverage
holes (`unproven`) are surfaced, not hidden.

(verify: python3 -m pytest tests/test_conformance.py::test_status_reports_all_buckets_at_once -q)

## R3 drifted ≠ stale via live verify (A35-C)

A live red verify is `drifted` (blocks); a stale attestation with a green verify
is `stale` (advisory). Never conflated.

(verify: python3 -m pytest tests/test_conformance.py::test_attestation_stale_is_stale_not_drifted tests/test_conformance.py::test_live_verify_red_is_drifted tests/test_conformance.py::test_live_verify_green_keeps_stale -q)

## R4 Blocking vs advisory (A35-F)

`blocking` fails on a red contract; `advisory` reports only. Stale alone never blocks.
The CLI honors configured blocking without an extra flag, in both text and JSON.
`--blocking` may strengthen advisory mode; a disabled gate remains a no-op.

(verify: python3 -m pytest tests/test_conformance.py::test_gate_blocking_fails_only_on_live_red tests/test_conformance.py::test_gate_passes_with_only_stale_advisory tests/test_conformance.py::test_conformance_gate_cli_honors_configured_mode tests/test_conformance.py::test_conformance_gate_cli_disabled_remains_successful -q)

## R5 Baseline-aware live verify (A35-H)

With `--base`, only specs whose files changed are re-verified; untouched specs keep
their attestation state.

(verify: python3 -m pytest tests/test_conformance.py::test_baseline_aware_skips_untouched_specs tests/test_conformance.py::test_baseline_aware_verifies_touched_specs -q)

## R6 Ledger-free CI blocking (A35-E, pragmatic)

`ci_safe` blocks only on self-contained pure-pytest verifies; env-dependent ones
(sibling repo / ledger / shell chains) are skipped — safe in a fresh CI checkout.

(verify: python3 -m pytest tests/test_conformance.py::test_ci_safe_accepts_pure_pytest tests/test_conformance.py::test_ci_safe_rejects_sibling_repo_and_shell_chains tests/test_conformance.py::test_ci_safe_runner_skips_unsafe_verifies -q)

## R7 Blocking policy is the project's choice

`block_on` lets the project choose which buckets fail the gate (default `drifted`).
apatch ships the lever; the project decides what to enforce.

(verify: python3 -m pytest tests/test_conformance.py::test_block_on_is_the_projects_choice -q)

## R8 Scoped and non-recursive conformance runs

`apatch conformance status|gate --spec SPEC-X` limits a run to one spec, and
`--no-live` overrides `live_verify=true`. Enrollment requirements must use this
bounded static form instead of recursively invoking their own live verify.

(verify: python3 -m pytest tests/test_conformance.py::test_conformance_status_cli_can_limit_to_one_spec tests/test_conformance.py::test_conformance_status_cli_no_live_overrides_config -q)

## R9 Timeout cleanup for live verifies

Timed-out live verifies terminate their whole process group before apatch reports
`broken` (`Rk:timeout`); remote/prod agents must not leave orphan pytest or
conformance processes behind.

(verify: python3 -m pytest tests/test_conformance.py::test_run_spec_verify_timeout_kills_process_group -q)

## R10 Bounded and parallel live conformance

Live conformance exposes bounded execution knobs: per-Rk timeout, per-spec budget,
parallel spec workers, optional parallel requirement workers, and fail-fast
classification. Requirement-level parallelism is opt-in because live/API suites may
slow down under nested concurrency.

(verify: python3 -m pytest tests/test_conformance.py::test_run_spec_verify_spec_budget_bounds_a_slow_requirement tests/test_conformance.py::test_run_spec_verify_fail_fast_stops_after_first_red_requirement tests/test_conformance.py::test_run_spec_verify_parallelizes_requirements tests/test_conformance.py::test_run_spec_verify_parallel_fail_fast_does_not_schedule_later_requirements tests/test_conformance.py::test_conformance_uses_configured_jobs_timeout_and_budget tests/test_conformance.py::test_conformance_fail_fast_defaults_true_but_can_be_disabled -q)

## RFP traceability

RFP-035 Acceptance rows map to requirements below.

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A35-A | R1 | covered |
| A35-B | R2 | covered |
| A35-C | R3 | covered |
| A35-D | R2 | covered |
| A35-E | R6 | covered |
| A35-F | R4 | covered |
| A35-G | — | waiver: merge-result awareness deferred (Phase 2) |
| A35-H | R5 | covered |
| A35-I | R8 | covered: scoped and non-recursive self-runs |
| A35-I | R9 | covered: timeout cleanup |
| A35-I | R10 | covered: bounded and parallel live runs |
