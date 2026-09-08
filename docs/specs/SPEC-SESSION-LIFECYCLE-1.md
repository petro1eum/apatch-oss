# SPEC-SESSION-LIFECYCLE-1 — Session end & RUN_STATE leases (RFP-016 Phase 4)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-SESSION-LIFECYCLE-1`
> **Anchors:** [RFP-016](../RFP-016-runtime-hygiene.md) Phase 4 · [SPEC-HYGIENE-CORE](./SPEC-HYGIENE-CORE.md) · [SPEC-HYGIENE-1](./SPEC-HYGIENE-1.md)

## 0. Motivation

Execution owns runtime semantics; AGL consumes registry flags. Phase 4 wires lease
acquire/release on apply/spec_run and `session_end` registry updates so GC can retire
EPHEMERAL without parsing RUN_STATE files.

Success criterion:

```text
session_end → EPHEMERAL registry rows have gc_allowed=true, run_lease_id=null
apply_session mid-chunk → apply_session.json row has active run_lease_id
```

Non-goals: automatic filesystem delete on session_end (SPEC-GC-1 `gc_safe` consumes flags);
layout paths (SPEC-LAYOUT-1).

## R1 apply_session acquires and releases run lease

On chunk start, `apply_session.json` inherits the exact governed `session_id` from
its lane state, and its registry row calls `acquire_run_lease` with a stable `lease_id`
derived from session + checkpoint. When chunk
completes with `continue=false`, calls `release_run_lease`. Mid-chunk: `gc_delete_allowed`
is false for that path.

(verify: python3 -m pytest tests/test_session_hygiene.py::test_apply_session_lease tests/test_session_hygiene.py::test_apply_session_inherits_lane_session_id -q)

## R2 spec_run RUN_STATE lease lifecycle

`apatch_spec_run` registers `spec_run.json` as RUN_STATE with lease for active run.
Successful run completion or explicit abort releases lease and sets `gc_allowed=true` on
run-scoped EPHEMERAL JSONL registered under same `run_id`.

(verify: python3 -m pytest tests/test_session_hygiene.py::test_spec_run_lease -q)

## R3 session_end registry cleanup

`end_session()` after persisting `ended_at`: releases write-lease registry row; for all
registry entries with matching `session_id` and class EPHEMERAL, calls `release_run_lease`
where applicable and sets `gc_allowed=true`. Does **not** invoke `gc_safe` inline.

(verify: python3 -m pytest tests/test_session_hygiene.py::test_session_end_registry_cleanup -q)

## R4 ORPHAN_EPHEMERAL doctor detection

After `session_end`, if EPHEMERAL files remain on disk for that `session_id` with
registry `gc_allowed=true`, `doctor.hygiene` includes issue `ORPHAN_EPHEMERAL` and
`status=degraded`.

(verify: python3 -m pytest tests/test_session_hygiene.py::test_orphan_ephemeral_doctor -q)

## Non-goals

- Deleting orphan files automatically (agent runs `apatch gc --safe`)
- Parsing apply_session JSON inside GC (CORE R3)
