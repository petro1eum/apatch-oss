# SPEC-RESUME-LIFECYCLE-1 — resume persists 'verifying', it does not flap to draft

> **apatch artifact:** `spec:SPEC-RESUME-LIFECYCLE-1`  
> **Anchors:** [RFP-005](../RFP-005-reference-monitor.md) · discharges reality `REC-390b564f4f1a`

## 0. Motivation

`apatch_resume_session` is meant to recover a stuck session so verify/attest are allowed
again. It persists `phase='verify'` internally — but the global MCP tool wrapper enriches
every result that lacks a `state_update`, and `_derive_phase` returned the idle default for
`apatch_resume_session`, **re-saving `phase='idle'`**. So the lifecycle flapped: resume
reported `verifying` in its response while persisting `draft`, and the next attest/verify
re-derived `draft` and was rejected (`RUNTIME_TRANSITION`). The session became
un-attestable without a full revert + fresh session.

This was self-caught on the apatch dogfood (reality record `REC-390b564f4f1a`) and recurred
on every governed cycle that needed recovery — including lane sessions with no `reset=true`.

## R1 a successful resume persists the correct recovery phase through enrichment (verify: python3 -m pytest tests/test_resume_lifecycle.py -q) (discharges: REC-390b564f4f1a)

Ordinary recovery persists `phase='verify'`, so the next verify/attest is allowed.
When `VERIFY_FAILED` already rolled its chunk back (`rollback_performed=true`), resume
instead persists `phase='apply'`: corrected needles can be applied immediately and the
agent is told to use `apply_session(reset=true)`. MCP enrichment preserves either phase,
so recovery never reports `fix_forward` and then rejects the required apply operation.

## R2 the reset=true trigger recovers to attestable, not just resume (verify: python3 -m pytest tests/test_resume_lifecycle.py -q) (discharges: REC-390b564f4f1a)

The record `REC-390b564f4f1a` describes the harm via the `apply_session(reset=true)` 2nd-
apply path, not only a bare resume. With the fix, the reset mechanism
(`_reset_phase_for_reapply` → `phase='apply'`/`applying`) followed by `resume_session()` +
enrichment lands the session in `verifying` (attestable) — recovery no longer needs a git
checkout + fresh session. This gates the exact trigger the reality record names, so the
discharge is honest, not narrower than the observation.
