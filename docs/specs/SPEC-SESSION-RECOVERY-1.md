# SPEC-SESSION-RECOVERY-1 — MCP disconnect session reconcile

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-SESSION-RECOVERY-1`  
> **Anchors:** [RFP-021 §AR-1](../RFP-021-agent-reliability-design-partner.md) · complements [SPEC-SESSION-LIFECYCLE-1](./SPEC-SESSION-LIFECYCLE-1.md) (registry leases, not MCP crash)

## 0. Motivation

When the MCP process dies mid-governed cycle, `session_state.json` and the runtime
state machine diverge (`phase=verify` but `lifecycle=draft`). Agents hit
`RUNTIME_TRANSITION` / `blocked` and humans patch JSON by hand. AR-1 requires
**reconcile on every governed tool entry** using authoritative facts:

```text
TrustChain ledger op_ids  →  apply checkpoint  →  session_state.json hints
```

Success criterion: all six disconnect scenarios in [RFP-021 §4.3](../RFP-021-agent-reliability-design-partner.md)
pass without noop mutations or manual state edits.

Non-goals: killing/restarting MCP from agent (human-only); async verify (SPEC-VERIFY-ASYNC-1);
failure taxonomy mapping (SPEC-FAILURE-TAXONOMY-2) except `resume_session` hint.

## R1 reconcile_session_on_entry

New `apatch/runtime/reconcile.py` with `reconcile_governed_session(target_dir) -> ReconcileResult`:

1. Load `session_state`, `apply_session.json`, ledger entries for `governed_session_id`.
2. Derive **effective lifecycle** (may differ from stale `derive_lifecycle(phase)`).
3. If open session + `phase ∈ {verify, complete}` → lifecycle **must not** stay `draft`.
4. Clear stale `failure` when reconcile proves last tool succeeded on disk (e.g. attest op exists).
5. Persist reconciled fields before `assert_operation` runs.

Called from `MutationRuntime.__init__` or first line of each public facade method.

(verify: python3 -m pytest tests/test_session_recovery.py::test_reconcile_verify_phase_not_draft -q)

## R2 apply_session resume after disconnect

When `.apatch/apply_session.json` has `continue: true` and matching `logs_path`:

- `next_action` hints `apatch_apply_session(session_path=…)` — not rollback.
- Lifecycle → `applying`; budget from progress fields restored.
- Does not re-apply completed chunks (checkpoint list authoritative).

(verify: python3 -m pytest tests/test_session_recovery.py::test_resume_apply_session_mid_chunk -q)

## R3 attest idempotency via ledger

After MCP death during `apatch_attest`:

| Ledger fact | Action |
|-------------|--------|
| Attestation op for `session_id` exists | Set `attested=true`, phase `complete`, `next_action=session_end` |
| No attestation op; verify was ok | Allow idempotent `apatch_attest` |
| Files changed but no attest op | Do **not** rollback; surface `resume_session` → attest |

(verify: python3 -m pytest tests/test_session_recovery.py::test_attest_reconcile_from_ledger -q)

## R4 session_end idempotency

After MCP death during `session_end`:

- Retry `apatch_session_end` completes EPHEMERAL registry flags + purge (SPEC-SESSION-LIFECYCLE-1 R3).
- No duplicate attest; `ended_at` idempotent.
- Partial purge from first attempt → second call finishes cleanup.

(verify: python3 -m pytest tests/test_session_recovery.py::test_session_end_idempotent_after_partial -q)

## R5 verify resume

After MCP death during sync `verify_run`:

- Reconcile phase → `verify`; clear illegal `failure` if verify job output file shows success (future AR-2) or allow re-run.
- Must not return `operation 'verify' not allowed in lifecycle 'draft'`.

(verify: python3 -m pytest tests/test_session_recovery.py::test_verify_allowed_after_reconcile -q)

## R6 RUNTIME_TRANSITION auto-heal

When `assert_operation` would raise for illegal lifecycle **after** reconcile ran:

- If reconcile fixed state → retry transition once.
- If still illegal → `error_type=RUNTIME_TRANSITION`, `recommended_action=resume_session` (not rollback).
- Include `reconcile_applied: true` and `effective_lifecycle` in error details.

(verify: python3 -m pytest tests/test_session_recovery.py::test_runtime_transition_resume_hint -q)

## R7 Integration matrix (§4.3 acceptance)

Single module test file `tests/test_session_recovery_mcp.py` parametrizes:

| # | Scenario |
|---|----------|
| 1 | Mid-apply_session chunk 2/5 |
| 2 | Mid-sync verify_run |
| 3 | Post-verify, pre-attest |
| 4 | Mid-attest (ledger reconcile) |
| 5 | Post-attest, pre-session_end |
| 6 | Mid-session_end partial purge |

Each simulates stale `session_state.json` + on-disk artifacts; next tool call succeeds.

(verify: python3 -m pytest tests/test_session_recovery_mcp.py -q)

## Non-goals

- MCP process lifecycle hooks (RFP-019 L1-1 — separate)
- Automatic rollback on reconnect
- Cross-worktree session merge
