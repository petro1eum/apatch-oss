# SPEC-HYGIENE-CORE — AGL machine invariants (RFP-016)

> **Status:** attested (2026-06-10, 7/7) · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-HYGIENE-CORE`
> **Anchors:** [RFP-016](../RFP-016-runtime-hygiene.md) §1.2, §3.5, §10 · [RFP-016-IMPLEMENTATION](./RFP-016-IMPLEMENTATION.md)

## 0. Motivation

RFP-016 defines Artifact Governance Layer (AGL) as a **deterministic policy engine**.
This spec lands the **pure library** invariants first — no CLI, no doctor wiring — so
`gc_report` / `gc_safe` and write-path registration share one tested core.

Success: `pytest tests/test_artifact_governance.py -k core` green before HYGIENE-1 touches
production write-paths.

Non-goals: registry persistence (SPEC-HYGIENE-1), CLI/MCP (SPEC-HYGIENE-2), delete
(SPEC-GC-1).

## R1 Static replay-critical path closure

`STATIC_REPLAY_CRITICAL_PATHS` is a versioned `frozenset` of workspace-relative globs
covering flat and structured `.apatch/` layouts plus `.trustchain/**`. Helper
`is_static_replay_critical(path) -> bool` uses glob match only — no file reads.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_static_replay_critical_closure -q)

## R2 gc_allowed delete predicate

`gc_delete_allowed(registry_entry, path) -> bool` returns true only when all hold:
registered entry exists, `replay_critical` is false, `gc_allowed` is true,
`run_lease_id` is null, and `path` is not static replay-critical. Pure function over
dict/registry view — no filesystem.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_gc_allowed_gate -q)

## R3 GC planning never opens RUN_STATE payloads

`plan_gc_deletes(candidates, registry, *, parse_run_state=False)` must not open
`apply_session.json`, `spec_run.json`, or backup files. When `parse_run_state=True` is
passed (test-only), tests assert the production default is false. Active RUN_STATE
protection uses registry lease fields only.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_gc_does_not_parse_run_state_files -q)

## R4 Inference sunset policy object

`InferenceSunsetPolicy(max_governed_ops=100)` tracks governed-op count in workspace state
file `.apatch/registry/inference_sunset.json` (flat: `.apatch/inference_sunset.json`).
`should_block_governed(inferred_count, ops) -> bool` is true when `inferred_count > 0`
and `ops >= max_governed_ops`.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_inference_sunset -q)

## R5 RUN_STATE lease transition helpers

`acquire_run_lease(entry, lease_id)` sets non-null `run_lease_id`, `gc_allowed=false`,
`replay_critical=true` for RUN_STATE class. `release_run_lease(entry)` clears
`run_lease_id`, sets `gc_allowed=true`, `replay_critical=false`. Used by execution layer
(SPEC-SESSION-LIFECYCLE-1), not by GC.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_run_state_lease -q)

## R6 Lineage contract validation

`validate_lineage(lineage: dict) -> None` requires non-empty `created_by_tool` and
`reason`; raises `LineageContractError` otherwise. Optional fields: `created_by_spec`,
`requirement`, `run_id`, `depends_on[]`, `produces[]`.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_lineage_required_on_register -q)

## R7 Lineage edge blocks delete when dependency leased

Given registry entries where artifact B lists `depends_on: [A]` and A has active
`run_lease_id`, `gc_delete_allowed(B)` is false even if B has `gc_allowed=true`.
Uses recorded edges only — no graph inference.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_lineage_dependency_blocks_delete -q)

## Non-goals

- `register_artifact` I/O (SPEC-HYGIENE-1)
- Filesystem delete (SPEC-GC-1)
- APG query / `provenance why` (RFP-017)
