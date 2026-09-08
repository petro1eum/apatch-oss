# SPEC-GC-1 — Safe deletion & rotation (RFP-016 Phase 3)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-GC-1`
> **Anchors:** [RFP-016](../RFP-016-runtime-hygiene.md) Phase 3 · [SPEC-HYGIENE-CORE](./SPEC-HYGIENE-CORE.md) · [SPEC-HYGIENE-2](./SPEC-HYGIENE-2.md)

## 0. Motivation

Phase 3 enables **bounded entropy removal** under SPEC-HYGIENE-CORE invariants.
`gc_safe` deletes only paths where `gc_delete_allowed` is true. `gc_rotate` prunes HISTORY
and DEBUG per policy. Violation aborts with zero partial deletes.

Success criterion:

```text
apatch gc --safe --json
# → reclaims GARBAGE + eligible EPHEMERAL; STATIC_REPLAY_CRITICAL untouched
```

Non-goals: layout migrate (SPEC-LAYOUT-1); APG explain (RFP-017).

## R1 gc_safe deletes only allowed candidates

`gc_safe(workspace)` builds delete set from registry + infer scanner, filters with
`gc_delete_allowed` and lineage dependency rules (CORE R7). `gc reconcile` also
releases legacy `apply_session` leases when the same lane's governed session is already
ended, making abandoned run state safely collectible without rollback. Missing registry-only
paths are not reported as active. Removes files; returns `deleted[]` and `skipped[]`.
If any planned delete would violate replay closure, raises `GC_INVARIANT_VIOLATION` and
deletes **zero** files.

(verify: python3 -m pytest tests/test_gc_cli.py::test_gc_safe_respects_gc_allowed tests/test_gc_cli.py::test_gc_reconcile_releases_finished_lane_run_state -q)

## R2 gc_safe never touches static replay-critical paths

Fixture places decoy files under `.trustchain/`, `.apatch/events.jsonl`,
`.apatch/session_state.json` with `gc_allowed=true` in registry (invalid contract).
`gc_safe` must skip them via static closure regardless of registry lie.

(verify: python3 -m pytest tests/test_gc_cli.py::test_gc_safe_static_replay_block -q)

## R3 gc_rotate HISTORY and DEBUG

`gc_rotate(workspace)` runs `gc_safe` then rotates `backups/` beyond N=50 (oldest first)
and prunes DEBUG files older than 7 days or above 10MB aggregate cap. HISTORY entries with
active `run_lease_id` on related registry rows are skipped.

(verify: python3 -m pytest tests/test_gc_cli.py::test_gc_rotate_history_debug -q)

## R4 MCP apatch_gc safe and rotate modes

`apatch_gc(mode="safe")` and `apatch_gc(mode="rotate")` mirror CLI. Response includes
`deleted_count`, `reclaimed_bytes`, and `state_update`. Works without write lease.

(verify: python3 -m pytest tests/test_gc_cli.py::test_mcp_gc_safe_rotate -q)

## R5 Inference sunset blocks governed mutations

When `InferenceSunsetPolicy.should_block_governed` is true, `doctor.hygiene.status` is
`critical` and `MutationRuntime` / governed apply paths return `HYGIENE_CRITICAL` with
`recommended_action: apatch gc --dry-run` (or resolve inferred artifacts). Does not block
read-only tools (`doctor`, `gc` report).

(verify: python3 -m pytest tests/test_gc_cli.py::test_inference_sunset_blocks_when_reconcile_fails tests/test_gc_cli.py::test_inference_sunset_auto_reconcile -q)

## Non-goals

- `session_end` automatic cleanup (SPEC-SESSION-LIFECYCLE-1)
- Ledger byte deletion
