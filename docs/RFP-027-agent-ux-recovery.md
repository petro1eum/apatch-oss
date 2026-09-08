# RFP-027 — Agent UX & recovery hardening

> **Status:** Draft v1 · **Date:** 2026-06-18 · **Owner:** apatch core
> **Package context:** 0.7.0 — close the gaps a real agent hits driving governed cycles end-to-end
> **Depends on:** [RFP-008](./RFP-008-spec-executor.md) (executor) · [RFP-009](./RFP-009-spec-run.md) (spec run) · [RFP-016](./RFP-016-runtime-hygiene.md) (gc) · [RFP-021](./RFP-021-agent-reliability-design-partner.md) (agent reliability)
> **Origin:** Field evidence — a full RFP→SPEC→governed-implementation dogfood session (SPEC-CONTRIB-TIMESHEET-1). The friction below was hit live, not hypothesized.

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| U27-A | Recoverable lifecycle: from `failed`/`verifying` a single documented op (`resume_session` or `clear_failure`) transitions the session back to a state where re-`verify`/`attest` is allowed, without `session_end` + fresh restart | MUST |
| U27-B | Recovery hints name only operations VALID in the current lifecycle — no `recommended_action` that the state machine then rejects | MUST |
| U27-C | gc never deletes backups for sessions that are active or not yet ended/attested; rollback ability is preserved for in-flight work | MUST |
| U27-D | rollback on missing backups returns a clear cause (e.g. `BACKUPS_PRUNED`) with remediation, not a bare `Session metadata not found` | MUST |
| U27-E | apply_session never silently no-ops: when the session is already complete and `logs_path` content changed, return an explicit status (e.g. `SESSION_ALREADY_COMPLETE`) with a hint, instead of reporting success having applied nothing | MUST |
| U27-F | Noop-attest: a requirement already satisfied by another Rk's mutation can be attested without fabricating a marker fixture file | SHOULD |
| U27-G | Playbook payload dedupe: large guidance blocks (`protocol_contract`, `spec_authoring`, `autonomy_boundary`, scaffolds) appear at most once per response and once per session, with a ref thereafter | SHOULD |
| U27-H | Self-edit staleness signal: when an applied change touches a module loaded by the running MCP server, the response flags that a restart is needed for the change to take effect | MAY |
| U27-I | Executable spec `SPEC-AGENT-UX-1` attested with `## RFP traceability` to this table | MUST |

Canonical ids: this section.

---

## 1. Problem

apatch's core loop (apply → verify → attest) is fast and correct, and governed verify
genuinely catches bad work (it refused to attest a requirement whose test failed —
the system working as designed). But the **surrounding agent UX** has sharp edges that
turn the normal case — a red test — into a dead end.

### Field evidence (this session)

A single failing test (a wrong mock reference in a freshly-authored test) put the
session into an unrecoverable spiral:

```text
verify_run            -> FAILED         (correct: caught the bug)
apply_session(reset)  -> "operation 'apply_session' not allowed in lifecycle 'verifying'"
verify_run (retry)    -> "operation 'verify' not allowed in lifecycle 'failed'"
rollback              -> "Session metadata not found for: <checkpoint>"
```

- The failure objects recommended `rollback`, then `fix_forward`, then `resume_session`
  — **none of which were executable in the state the session was in.**
- `rollback` failed because an earlier `gc rotate` had deleted that session's backups.
- Only `session_end` + a fresh session + a fabricated `rebind-*.txt` marker file got
  the work attested.

Each of these is a small thing; together they make the happy-path-only governance feel
brittle exactly when an agent needs it most (mid-iteration on a failing test).

### Secondary friction

- **Silent no-op apply:** after regenerating `patches.jsonl` with a fix, `apply_session`
  returned "Complete" having applied nothing (session already complete) — the new needle
  was silently ignored; only a manual `grep` revealed the file was unchanged.
- **Payload bloat:** every MCP response embeds large playbooks; `spec_lint` returned
  `plan_scaffold` / `execution_plan` / `needles_scaffold` with near-identical content
  three times in one response. Real context/token cost for the agent.
- **One-Rk-one-mutation mismatch:** a single module (`timesheet.py`) satisfies R4–R8;
  attesting the later Rk required creating meaningless marker files just to have a
  mutation to attest.

---

## 2. Solution

Harden the agent-facing edges around the (already solid) core:

1. **Real recovery verb.** A `resume_session` / `clear_failure` operation that moves
   `failed`/`verifying` back to a re-attemptable state, surfaced in the failure hint —
   and the hint must only ever name an op that is actually allowed next (U27-A, U27-B).
2. **gc/rollback safety.** gc protects backups of non-ended sessions; rollback degrades
   loudly with a typed cause when backups are gone (U27-C, U27-D).
3. **Honest apply.** No silent no-op — completed-session re-apply returns a typed status
   and a remediation hint (U27-E).
4. **Noop-attest.** First-class way to attest a requirement covered by another Rk's
   change, replacing the marker-file workaround (U27-F).
5. **Leaner responses.** Emit heavy guidance once per session; reference it afterward
   (U27-G). Optional self-edit staleness flag for dogfooding apatch itself (U27-H).

---

## 3. Non-goals

- Rewriting the lifecycle state machine — only add recovery transitions and fix hints.
- Removing guidance payloads entirely — they help fresh agents; dedupe, don't delete.
- Auto-restarting the MCP server (human-only; only surface the signal — U27-H).
- Changing the apply/matcher core, which works well.

---

## 4. References

- [rfp-authoring.md](./rfp-authoring.md) · [spec-authoring.md](./spec-authoring.md)
- [RFP-016](./RFP-016-runtime-hygiene.md) — gc modes (interaction with backups)
- [RFP-021](./RFP-021-agent-reliability-design-partner.md) — agent reliability baseline
