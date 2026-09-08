# SPEC-INTERFERENCE-3 — Coordination hardening (RFP-014 Phase 3)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-INTERFERENCE-3`
> **Anchors:** [RFP-014](../RFP-014-spec-interference-detection.md), [SPEC-INTERFERENCE-1](./SPEC-INTERFERENCE-1.md), [SPEC-INTERFERENCE-2](./SPEC-INTERFERENCE-2.md)

## 0. Motivation

Phase 1–2 deliver passive L1/L2 detection, schedule wrapper, cycle gate, and Level-3
cross-verify. The Antigravity detailed analysis (RFP-014 §15) identified gaps that turn
interference from **advisory report** into **coordination runtime**:

- MVCC snapshots (`input_hashes`, `validity`) so stale plans do not silently drive scheduling
- Actionable `structural_options` on each L2 conflict (computable resolutions, not LLM merge)
- Enriched schedule (`risk_per_step`, `interference_hash`) for multi-agent handoff
- Execution ordering: `spec_run(B)` blocked until predecessors in `safe_order` are attested
- `apatch_spec_run_multi` — orchestrate N specs with incremental re-analysis and auto-stop

**Ownership (in scope vs out):** apatch resolves *execution feasibility* (structural reorder,
split, anchor rewrite hints). apatch does **not** resolve intent priority ("UI vs API") or
semantic merge — see RFP-014 §7.

Success criterion:

```text
apatch_spec_run_multi(
  specs=['SPEC-CV-SOURCE', 'SPEC-CV-VICTIM'],
  target_dir='tests/fixtures/cross_verify',
)
# → runs in safe_order; stops on cross-verify failure; workspace consistent
```

Non-goals: LLM needle rewriting; domain-tag antagonism (Phase 4 research); replacing
`apatch_orchestrate` for non-spec pipelines.

## R1 MVCC validity on interference reports

Every `apatch_spec_interference` / `apatch_spec_schedule` response includes snapshot fields:

- `computed_at` — ISO-8601 UTC timestamp
- `input_hashes` — `{spec_id: sha256(canonical_json(needles))}` per analyzed spec
- `validity` — `current` | `stale` | `planned_only`
- `data_domains` — per spec: which needles came from `ledger` (observed) vs
  `registry` / `spec_run` (planned)

Rules:

- Recompute `input_hashes` when needles change; if cached report hash ≠ live hash →
  `validity: stale`, `warning`, `recommended_action: re_run_interference`
- `planned_only` when no spec has ledger-attested needles and all data is pre-apply manifest
- Stale is **warning**, not hard block (re-run is explicit agent action)

(verify: python3 -m pytest tests/test_spec_interference_mvcc.py::test_input_hashes_and_validity -q)

## R2 Structural options on L2 conflicts

Each L2 conflict (`write_read`, `write_write`) includes `structural_options[]` — computable
resolution hints, not semantic judgments:

| option | When | `feasible` |
|--------|------|------------|
| `reorder` | WR conflict | `true` if reversing edge yields acyclic graph |
| `rewrite_anchor` | WR — B's `find_text` destroyed by A | `true` (hint only; agent regenerates needle) |
| `split_region` | WW mutex — non-overlapping line regions | `true` when char-span overlap is partial |
| `merge` | Combined atomic needle | `"unknown"` — out of scope (semantic) |

Each option carries `description` and optional `side_effect`. `confidence` on conflict remains
`structural` for L2; L3 cross-verify conflicts use `empirical`.

(verify: python3 -m pytest tests/test_spec_interference_options.py::test_structural_options_wr_and_ww -q)

## R3 Enriched schedule response

`apatch_spec_schedule` extends Phase 1.5 thin wrapper with:

- `schedulable` — `false` when `has_cycle`
- `order` — alias of `safe_order` when schedulable
- `blocked_pairs` — WW mutex pairs and cycle members with `reason`
- `risk_per_step` — `[{spec, risk, depends_on[]}]` derived from conflict graph
- `interference_hash` — sha256 of canonical interference payload (specs + input_hashes)
- `interference_validity` — propagated from R1
- `strategy` parameter — `safe` (default, topo sort) | `risk_first` (fail-fast: highest-risk spec first for review)

(verify: python3 -m pytest tests/test_spec_schedule.py::test_enriched_schedule_risk_per_step -q)

## R4 spec_run execution ordering gate

When `spec_run(..., peer_specs=[...], interference_check=true)` (default `true` when
`peer_specs` non-empty):

1. Run interference among `{spec} ∪ peer_specs`
2. If `has_cycle` → `SPEC_INTERFERENCE_CYCLE` (existing)
3. If acyclic: block start when any **predecessor** in `safe_order` (relative to target
   spec) is not fully attested in ledger (`spec_status` all Rk = `attested`)
4. Response includes `blocked_by: [spec_id, ...]` and `recommended_action: reorder` or
   `complete_predecessor_first`

Cycle gate (Phase 1.5) remains; this adds **ordering** gate without requiring full multi-run.

(verify: python3 -m pytest tests/test_spec_run_interference_gate.py::test_blocks_until_predecessor_attested -q)

## R5 Failure taxonomy — stale and schedule blocked

Register in `failure_taxonomy.py` and MCP `error_type` / `recommended_action`:

| `error_type` | `recommended_action` | When |
|--------------|----------------------|------|
| `SPEC_INTERFERENCE_STALE` | `re_run_interference` | `validity == stale` on gate or schedule |
| `SPEC_SCHEDULE_BLOCKED` | `resolve_conflicts` | `schedulable == false` (cycle or empty order) |
| `SPEC_RUN_ORDER_BLOCKED` | `complete_predecessor_first` | R4 ordering gate |

(verify: python3 -m pytest tests/test_spec_interference_taxonomy.py::test_stale_and_order_blocked_types -q)

## R6 MCP tool `apatch_spec_run_multi`

Orchestrate N specs in schedule `order`:

1. `apatch_spec_schedule(specs=...)` → `order`, abort if not schedulable
2. For each spec in order: `spec_run` with `peer_specs=remaining`
3. After each successful run: optional L1/L2 re-interference on remaining specs
4. Optional `cross_verify=true`: before each source apply, sandbox its exact inline/registry work list against every later spec, then restore each sandbox
5. Stop on first failure; rollback every recorded chunk checkpoint in reverse order and report `workspace_restored=true` only when every restore succeeded

Returns: `steps[]`, `completed_specs[]`, `failed_at`, `final_interference`.

CLI: `apatch spec run-multi --spec A --spec B`. MCP tool count **70**.

(verify: python3 -m pytest tests/test_spec_run_multi.py::test_run_multi_safe_order tests/test_spec_run_multi.py::test_multi_failure_restores_all_completed_checkpoints tests/test_spec_run_multi.py::test_apply_backup_session_uses_returned_checkpoint tests/test_spec_run_multi.py::test_missing_checkpoint_never_reports_workspace_restored -q)

## R7 Integration — fixture multi-spec with stop on semantic conflict

Using `tests/fixtures/cross_verify/`: when SOURCE and VICTIM are scheduled together with
`cross_verify=true`, multi-run stops before VICTIM apply (or after cross-verify failure),
workspace restored, `error_type: SPEC_CROSS_VERIFY_FAILED`.

(verify: python3 -m pytest tests/test_spec_run_multi.py::test_fixture_stops_on_cross_verify -q)

## Non-goals

- Automatic needle merge or LLM anchor rewrite (agent applies `structural_options` manually).
- Domain tags / intent antagonism (RFP-014 Phase 4).
- Persisting interference reports to disk (optional future; v1 is compute-on-demand).
