# SPEC-AGENT-UX-1 — Agent UX & recovery hardening

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-AGENT-UX-1`  
> **Anchors:** [RFP-027](../RFP-027-agent-ux-recovery.md) · [RFP-016](../RFP-016-runtime-hygiene.md) (gc) · [RFP-021](../RFP-021-agent-reliability-design-partner.md)

## 0. Motivation

Close the sharp edges an agent hits driving governed cycles: a red test should not
brick the session, gc must not destroy rollback, and apply must never silently no-op.
Field evidence in RFP-027 §1 (hit live during SPEC-CONTRIB-TIMESHEET-1). Phase 1
implements the recovery + safety MUSTs; SHOULD/MAY rows are waived to Phase 2.

## R0 RFP traceability gate (meta)

RFP-027 Acceptance rows map to requirements below (covers U27-I).

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| U27-A | R1 | covered |
| U27-B | R2 | covered |
| U27-C | R3 | covered |
| U27-D | R4 | covered |
| U27-E | R5 | covered |
| U27-F | — | waiver: Phase 2 — noop-attest needs governed attest-without-apply design |
| U27-G | — | waiver: Phase 2 — playbook payload dedupe is cross-cutting (own spec) |
| U27-H | — | waiver: MAY — self-edit staleness signal deferred |
| U27-I | R0 | covered |

(verify: python3 -m pytest tests/test_agent_ux_recovery.py::test_r0_self_coverage_rfp_027 -q)

## R1 Recoverable lifecycle

`MutationRuntime.resume_session()` transitions a session from `failed`/`verifying`
back to a state where `verify`/`attest` are allowed: clears the persisted `failure`,
re-derives phase to `verify`. Exposed as MCP `apatch_resume_session`.

(verify: python3 -m pytest tests/test_agent_ux_recovery.py::test_r1_resume_from_failed -q)

## R2 Recovery hint is executable

The failure surfaced from a blocked op names `resume_session` as `recommended_action`,
and that op is actually allowed/working from the blocked lifecycle (no dead-end hint).

(verify: python3 -m pytest tests/test_agent_ux_recovery.py::test_r2_recovery_hint_executable -q)

## R3 gc preserves active-session backups

gc (`safe`/`rotate`) does not delete `.apatch/backups/<session>/` for a session that
is still active (present in `session_state` with no `ended_at`), preserving rollback.

(verify: python3 -m pytest tests/test_agent_ux_recovery.py::test_r3_gc_preserves_active_backups -q)

## R4 Honest rollback result

When a rollback target's backup dir is gone, the result carries a typed
`BACKUPS_PRUNED` cause with remediation — not a bare `Session metadata not found`.
When backups are present, rollback returns each file to the state the session
started from, including files the session edited more than once and files it
created; a partial restore reported as success is the same lie in another form.

(verify: python3 -m pytest tests/test_agent_ux_recovery.py::test_r4_rollback_backups_pruned_typed tests/test_agent_ux_recovery.py::test_r4_rollback_returns_a_twice_edited_file_to_its_pre_session_state tests/test_agent_ux_recovery.py::test_r4_rollback_removes_a_file_the_session_created -q)

## R5 apply_session never silently no-ops

Re-calling `apply_session` on an already-complete session returns an explicit
`status: SESSION_ALREADY_COMPLETE` with a hint (reset to re-apply, or proceed to
verify/attest) instead of reporting success having applied nothing.

(verify: python3 -m pytest tests/test_agent_ux_recovery.py::test_r5_apply_session_already_complete -q)

## Non-goals

- Rewriting the lifecycle state machine — only add the recovery transition + honest hints.
- Phase 2 items (noop-attest, payload dedupe, staleness signal) — see waivers.
- Touching the apply/matcher core, which works well.
