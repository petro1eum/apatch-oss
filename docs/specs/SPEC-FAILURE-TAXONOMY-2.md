# SPEC-FAILURE-TAXONOMY-2 — fix_forward vs rollback (RFP-021 AR-3)

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-FAILURE-TAXONOMY-2`  
> **Anchors:** [RFP-021 §AR-3](../RFP-021-agent-reliability-design-partner.md) · depends on AR-4 baseline fields, AR-1 reconcile facts

## 0. Motivation

Agents roll back **correct** patches when verify fails for pre-existing or unrelated tests
because `VERIFY_FAILED` always mapped to `recommended_action: rollback`.

AR-3 rule: **rollback only when mutation integrity is at risk**; verify-only red → `fix_forward`.

## R1 VERIFY_FAILED default fix_forward

`classify_failure()` for verify tool failures (no `verify_rollback`):

- `recommended_action` = **`fix_forward`**
- `recoverable` = true

(verify: python3 -m pytest tests/test_failure_taxonomy_v2.py::test_verify_failed_defaults_fix_forward -q)

## R2 rollback when verify_rollback or chunk integrity

When `verify_rollback=true` on result or `chunk_result.verify_rollback`:

- `recommended_action` = **`rollback`** (unchanged)

(verify: python3 -m pytest tests/test_failure_taxonomy_v2.py::test_verify_rollback_stays_rollback -q)

## R3 baseline attribution in failure details

Verify failures include `details.baseline` subset when present:

- `new_failures`, `pre_existing_failures`, `unparsed_output`

(verify: python3 -m pytest tests/test_failure_taxonomy_v2.py::test_verify_failure_includes_baseline_details -q)

## R4 APPLY_FAILED and NOTARIZATION unchanged

Chunk apply failed / notarization failed still → `rollback`.

(verify: python3 -m pytest tests/test_failure_taxonomy_v2.py::test_apply_failed_still_rollback tests/test_failure_taxonomy.py -q)

## R5 AGENTS.template parity

Failure table documents `fix_forward`, `resume_session`, and verify_rollback exception (done in docs pass).

(verify: python3 -c "import pathlib; t=pathlib.Path('docs/AGENTS.template.md').read_text(); assert 'fix_forward' in t and 'resume_session' in t")

## R6 session_status blocker respects fix_forward

`build_blocker_summary` must not say «rollback» when action is `fix_forward` (regression).

(verify: python3 -m pytest tests/test_status_blocked.py::test_blocker_from_verify_failure -q)

## Non-goals

- Auto-fixing tests; changing sandbox error mapping; hosted MCP policy
