# RFP-021 — Agent Reliability & Design Partner Release

> **Status:** Draft v1.2 · **Package:** 0.7.0 Design Partner Ready (2026-06-12)  
> **Date:** 2026-06-11  
> **Owner:** apatch product  
> **Depends on:** RFP-004 (MutationRuntime), RFP-016 (runtime hygiene), RFP-019 L1 (MCP lifecycle, lanes, `doctor_only`), RFP-020 (project status / reports)  
> **Blocks:** Design Partner Program (external repos), external pip release (with [RFP-019 L2](./RFP-019-mcp-scale-lifecycle.md))

---

## 0. Positioning — agent-first, not human-first

apatch is **primarily a runtime for AI agents**, not a simplified CLI for humans.

| Assumption | Consequence |
|------------|-------------|
| The **operator** is an IDE agent with MCP | 15 intent-level tools are the default; the 110-tool full profile is an opt-in specialist surface |
| The **human** sets policy once | `init-consumer`, sandbox, enforcement, MCP config — then supervises outcomes |
| Agents read playbooks **once per session** | `apatch_doctor` + `APATCH_MCP_GUIDANCE=doctor_only` + `apatch://playbook/*` (RFP-019 L1-5/L1-10) |
| Complexity lives in **orchestration**, not UX chrome | Spec run, apply_session chunking, interference — reduce round-trips for agents |

**Non-problem (explicit):** «слишком много MCP tools / крутая кривая входа для человека».  
Сужать surface до «10 команд для новичков» — **не цель** этого RFP.

**Real problem:** agent cannot **finish a governed cycle autonomously** when MCP hiccups, verify is slow, or failure taxonomy sends it to rollback for unrelated red tests.

---

## 1. Problem statement

Dogfood and power-user feedback (2026-06) show the product is **technically capable** but **operationally fragile** for unsupervised agents:

| Symptom | Agent impact | Human impact |
|---------|--------------|--------------|
| MCP process dies mid-session (`Connection closed`) | Lifecycle desync (`phase=verify` vs `lifecycle=draft`); next `verify_run` → `blocked` | Must manually repair `.apatch/session_state.json` or rollback innocent work |
| Full test suite via MCP verify (~3+ min) | MCP timeout / connection drop before result | Thinks agent failed; restarts MCP → AR-1 |
| `recommended_action: rollback` on any verify failure | Agent rolls back **correct** patches because an old test or missing param description failed | Loses trust in governed workflow |
| Pre-existing failing tests in consumer repo | Was: verify red → blocked → rollback innocent edits | Fixed partially — see AR-4 ✅ |

These are **reliability** gaps, not documentation gaps. An agent that read the full playbook still gets stuck without human surgery.

---

## 2. Release maturity model

```text
L0 — Internal dogfood (today)
  Authors run apatch on apatch repo; human nearby to fix state

L1 — Design Partner Program (target of this RFP)
  External repo + enforce + MCP; agent completes Intent→Attest cycles
  Human: policy setup + read apatch status / report; no session_state surgery

L2 — External pip / marketplace (RFP-019 L2)
  L1 gates + packaging, version pinning, support playbook

L3 — Hosted MCP (RFP-019 L3)
  L1 gates + async verify at scale, tenant isolation
```

**This RFP defines L1.** L2/L3 inherit L1 acceptance criteria.

---

## 3. Goals (deliverables)

| ID | Goal | Priority | Status |
|----|------|----------|--------|
| **AR-1** | **Session recovery** — MCP restart / crash does not strand agent in illegal lifecycle | P0 | **✅ Done** — [SPEC-SESSION-RECOVERY-1](./specs/SPEC-SESSION-RECOVERY-1.md) attested 2026-06-12; §4.3 six-scenario matrix + async verify job reconcile + R6 auto-heal |
| **AR-2** | **Async verify** — long suites do not hold MCP connection; poll `verify_status` | P0 | **✅ Done** — [SPEC-VERIFY-ASYNC-1](./specs/SPEC-VERIFY-ASYNC-1.md) attested 2026-06-12 |
| **AR-3** | **Failure taxonomy v2** — `recommended_action` matches *cause* (fix_forward vs rollback) | P0 | **✅ Done** — [SPEC-FAILURE-TAXONOMY-2](./specs/SPEC-FAILURE-TAXONOMY-2.md) attested 2026-06-12 |
| **AR-4** | **Baseline-aware verify** — pre-existing red must not block attest | P0 | **✅ Done** (2026-06-11) |
| **AR-5** | **Agent contract hardening** — argv verify, needles file, sandbox fd rules | P1 | **✅ Done** (2026-06-11) |
| **AR-6** | **Supervisor visibility** — human sees *why* agent blocked without reading JSONL | P1 | **✅ Done** — [SPEC-STATUS-BLOCKED-1](./specs/SPEC-STATUS-BLOCKED-1.md) attested 2026-06-12 |
| **AR-7** | **Design partner playbook** — one doc: setup, success criteria, escalation | P1 | **✅ Done** — [SPEC-DESIGN-PARTNER-1](./specs/SPEC-DESIGN-PARTNER-1.md) attested 2026-06-12 |

---

## 4. AR-1 — Session recovery after MCP disconnect

### 4.1 Contract

On **every** governed tool entry (`MutationRuntime._finish` or shared `touch_session`):

1. **Reconcile lifecycle** from durable fields: `session_id`, `intent`, `phase`, `checkpoint`, `failure`, `ended_at`.
2. If `phase ∈ {verify, complete}` and active session exists → lifecycle **must not** be `draft`.
3. If MCP died during `apply_session` with `continue=true` → resume hint in `next_action`, not `blocked`.
4. **Idempotent resume:** repeating `apatch_verify_run` / `apatch_apply_session` / `apatch_attest` / `apatch_session_end` after reconnect must not require noop mutations.
5. **Authoritative sources (order):** TrustChain ledger op_ids → apply checkpoint → `session_state.json`. Reconcile derives lifecycle from facts on disk, not from stale phase alone.

### 4.2 Anti-patterns (today)

- `verify_run` returns `operation 'verify' not allowed in lifecycle 'draft'` while `session.phase=verify`.
- Agent told `rollback` when apply succeeded and only lifecycle metadata is wrong.

### 4.3 Acceptance

| Scenario | Expected after MCP restart |
|----------|----------------------------|
| Mid-`apply_session` (chunk 2/5, `continue=true`) | `apatch_apply_session(logs_path=…)` resumes next chunk; no human JSON edit |
| Mid-`verify_run` (sync) | `apatch_verify_run` succeeds or returns AR-2 job handle — not `draft` block |
| After `verify_run` ok, before `attest` | `apatch_attest` runs once; **no** duplicate ledger commit |
| During `attest` (Ed25519 in flight) | Reconcile from ledger: if attestation op exists for `session_id` → `attested=true`, `next_action=session_end`; else idempotent `apatch_attest` |
| After `attest` ok, before `session_end` | `apatch_session_end` completes registry purge / lease release; **no** re-attest |
| During `session_end` (partial purge) | Idempotent `session_end` finishes EPHEMERAL cleanup; session marked `ended` |

- Test: `tests/test_session_recovery_mcp.py` (new) — all six rows + stale `session_state.json` simulation.

---

## 5. AR-2 — Async verify (MCP-safe long suites)

### 5.1 Contract

```text
apatch_verify_run(verify=…, async=true)
  → { ok: true, verify_job_id, poll: "apatch_verify_status(job_id=…)" }
  → subprocess runs in background; MCP returns in < 30s

apatch_verify_status(job_id=…)
  → { state: running | passed | failed, … baseline fields from AR-4 … }
```

- Default `async=false` preserves today’s sync behaviour for **short** verifies (L1 conservative).
- `doctor.recommended_verify` may advertise `async_recommended: true` when suite historically > 60s — **hint only**; agents on rigid scripts may ignore it.
- **Runtime promotion (required):** if sync verify exceeds `APATCH_VERIFY_SYNC_MAX_SEC` (default **45**, env override) **or** estimated duration from prior runs / toolchain profile exceeds threshold, runtime **forces async** even when `async=false`. Response includes `async_forced: true`, `reason`, `poll`.
- Job state under `.apatch/verify_jobs/<id>.json`; GC via RFP-016 rotate.

**L2 note:** revisit default `async=true` for consumer profiles once forced-promotion metrics show >90% of long suites already async.

### 5.2 Acceptance

- MCP tool call for 917-test suite returns within 30s (forced async if agent omitted flag); agent polls until terminal state.
- Agent calls `verify_run(async=false)` on known 10-minute suite → runtime promotes to async before MCP timeout.
- Baseline compare (AR-4) works on async job completion.
- No `Connection closed` in stdio MCP for verify-only workloads in CI simulation.

---

## 6. AR-3 — Failure taxonomy v2

### 6.1 Principle

**Rollback** when **mutation integrity** is at risk.  
**Fix forward** when **verification** failed but apply/notarization succeeded and failure is **attributable** to tests, env, or agent mistake — not corrupt tree.

### 6.2 Mapping (target)

| Condition | `error_type` | `recommended_action` |
|-----------|--------------|----------------------|
| Chunk apply failed / patch conflict | `APPLY_FAILED` | `rollback` |
| Verify failed, `baseline.new_failures` non-empty | `VERIFY_FAILED` | `fix_forward` (default) or `rollback` if `verify_rollback=true` |
| Verify failed, only pre-existing / allow-listed (AR-4) | — | `ok: true`, `pre_existing_only` (no failure) |
| Verify failed, unparsed output, no baseline | `VERIFY_FAILED` | `fix_forward` |
| TrustChain / notarization | `NOTARIZATION_FAILED` | `rollback` |
| Illegal lifecycle (should be AR-1 auto-heal) | `RUNTIME_TRANSITION` | `resume_session` (new action) |
| Sandbox / lease | `LEASE_*` / `DIRECT_WRITE_BLOCKED` | `retry_chunk` |

Implement in `failure_taxonomy.py` + `classify_failure()`; surface `fix_forward` in `AGENTS.template.md` failure table.

### 6.3 Gray zones — explicit rules (avoid exception sprawl)

`classify_failure()` must **not** grow ad-hoc branches. Ambiguous cases resolve via this **decision order**:

```text
1. TrustChain / checkpoint facts on disk (AR-1 §4.1 rule 5)
2. Table in §6.2
3. Default: fix_forward if tree matches last good checkpoint AND verify/notarization is the only red signal
```

| Gray scenario | Facts to check | `recommended_action` | Notes |
|---------------|----------------|----------------------|-------|
| Chunk N applied + notarized; chunk N+1 not started; MCP died | `apply_session.json` `continue=true`, checkpoint exists, ledger ops for chunk N | `resume_session` → `apply_session` | **Not** rollback — partial progress is attested |
| Chunk apply returned `failed>0` or `verify_rollback=true` | `chunk_result` | `rollback` | Integrity risk — unchanged |
| All chunks applied; sync verify running; MCP died | Files + checkpoint stable; no `verify_rollback` | `resume_session` → verify (AR-2 job if long) | fix_forward after verify passes |
| `attest` interrupted mid-sign | Ledger: attestation op for `session_id`? | yes → `session_end`; no → idempotent `attest` | Never rollback applied+verified work |
| Verify red + **new** failures (AR-4) | `baseline.new_failures` non-empty | `fix_forward` | Agent fixes tests or reverts own code — not auto rollback |
| Verify red + unparsed output, no baseline | Cannot attribute | `fix_forward` + `details.unparsed_output=true` | Supervisor may intervene (AR-6) |
| Notarization failed **after** file write | `trustchain_committed=false`, files changed | `rollback` | Existing `NOTARIZATION_FAILED` |
| Lifecycle illegal after reconnect | Reconcile failed | `resume_session` (AR-1) | **Not** rollback |

**Invariant:** rollback requires **provable** mutation corruption, explicit `verify_rollback`, or failed chunk — not «verify red» alone.

### 6.4 Acceptance

- Power-user scenario: old test fails after innocent patch → agent gets `fix_forward` or `pre_existing_only`, **not** rollback.
- Missing MCP param description test fails → `fix_forward`, not rollback of unrelated `apatch/**` edits.
- Partial apply (3/5 chunks, all notarized) + MCP kill → resume chunk 4, not rollback.
- Attest interrupted → ledger reconcile → single attestation, no duplicate op.
- Regression: real apply corruption still → `rollback`.

**L2 note:** expand gray-zone fixtures from partner repos before pip default `async=true`.

---

## 7. AR-4 / AR-5 — Closed in 2026-06-11 feedback sprint ✅

Documented here so L1 checklist stays in one place.

| Item | Delivery |
|------|----------|
| Baseline capture/compare | `apatch_verify_run(baseline=capture\|compare)`, `.apatch/verify_baseline.json`, `allowed_failures[]` |
| Verify argv list | `verify=["pytest", "-k", "not slow", …]` — no shell quoting |
| Needles file | `apatch_generate_batch(needles_path=…)` — parity CLI `--needles` |
| Sandbox | `2>/dev/null`, `>/dev/null`, `&>/dev/null` allowed; real `> file` still blocked |
| Guidance noise | `APATCH_MCP_GUIDANCE=doctor_only` default — hot path slim |

Attested: `ticket:FEEDBACK-2026-06-VERIFY-SANDBOX`.

---

## 8. AR-6 — Supervisor visibility (human in the loop, not in the loop)

Agents operate autonomously; **humans approve policy and outcomes**, not every chunk.

| Surface | Audience | Shows |
|---------|----------|-------|
| `apatch status` / `apatch_project_status` | Tech lead | phase, blocked reason, hygiene, last checkpoint |
| `apatch report --html` | Architect | spec interference, attestation coverage |
| `apatch report --format md` | Manager | plain-language progress from ledger facts |

**L1 gap (closed 2026-06-12):** `apatch status --json` exposes `session.blocker`, `session.baseline`, and `extensions: ["session_blocker_v1"]` via SPEC-STATUS-BLOCKED-1. Reports (`apatch report`) remain RFP-020 scope.

---

## 9. AR-7 — Design Partner Program playbook

Single consumer-facing doc (English primary):

1. **Prerequisites:** Python 3.11+, git, optional TrustChain enroll, Cursor/Antigravity MCP.
2. **One-time setup:** `apatch init-consumer --with-sandbox --with-enforcement --with-mcp`.
3. **Agent contract:** `apatch_doctor` once → governed cycle; prefer `apatch_spec_run` for whole specs.
4. **Success criteria:** agent attests without human editing `.apatch/*` state files.
5. **Escalation:** when to rollback vs fix_forward (links AR-3 table).
6. **Parallel agents:** git worktree + lane per agent ([RFP-019 §2.3](./RFP-019-mcp-scale-lifecycle.md)).

---

## 10. L1 acceptance — «ready for people» (design partners)

A repo is **Design Partner Ready** when all pass:

| # | Gate | Verification |
|---|------|--------------|
| G1 | Agent completes mass refactor cycle (session → generate → apply → verify → attest → session_end) | Scripted agent run or recorded MCP transcript |
| G2 | MCP restart mid-cycle (apply, verify, attest, session_end) → agent resumes without human state edit | AR-1 test suite green (all §4.3 rows) |
| G3 | Consumer with 5 pre-existing failing tests → agent patch + baseline compare → attest | AR-4 integration fixture |
| G4 | 10-minute verify → async job completes; MCP stays connected | AR-2 load test |
| G5 | Wrong verify failure → `fix_forward`, not rollback of good patch | AR-3 regression |
| G6 | Human supervisor reads `apatch status` and understands blockers | AR-6 manual review |
| G7 | `pytest tests/` green in apatch repo | CI |

**Not required for L1:** PyPI publish, hosted MCP, Russian/English doc parity, reducing MCP tool count.

---

## 11. Implementation chain (proposed specs)

Partners need **supervisor visibility (AR-6) from day one** — otherwise humans read JSONL while P0 reliability work lands. Order reflects **parallel tracks** + dependency hints:

| Track | Order | Spec | Scope | Status |
|-------|-------|------|-------|--------|
| **A — visibility** | **1** | **SPEC-STATUS-BLOCKED-1** | AR-6: structured `failure`, `recommended_action`, AR-4 baseline summary in `project_status` DTO | **✅ attested** 2026-06-12 |
| **B — recovery** | 2 | **SPEC-SESSION-RECOVERY-1** | AR-1 lifecycle reconcile + §4.3 six scenarios | **✅ attested** 2026-06-12 |
| **C — verify scale** | 3 | **SPEC-VERIFY-ASYNC-1** | AR-2 jobs + runtime forced async | **✅ attested** 2026-06-12 |
| **D — taxonomy** | 4 | **SPEC-FAILURE-TAXONOMY-2** | AR-3 §6.2 table + §6.3 gray zones | **✅ attested** 2026-06-12 |
| **E — program** | 5 | **SPEC-DESIGN-PARTNER-1** | AR-7 playbook + G1–G7 checklist | **✅ attested** 2026-06-12 |

**D** follows **B** (gray zones need ledger/checkpoint reconcile). **C** can parallel **B** — job schema defined in RFP-021 §5.

| Spec doc | Path | Attestation |
|----------|------|-------------|
| SPEC-STATUS-BLOCKED-1 | [docs/specs/SPEC-STATUS-BLOCKED-1.md](./specs/SPEC-STATUS-BLOCKED-1.md) | 6/6 Rk |
| SPEC-SESSION-RECOVERY-1 | [docs/specs/SPEC-SESSION-RECOVERY-1.md](./specs/SPEC-SESSION-RECOVERY-1.md) | 7/7 Rk |
| SPEC-VERIFY-ASYNC-1 | [docs/specs/SPEC-VERIFY-ASYNC-1.md](./specs/SPEC-VERIFY-ASYNC-1.md) | 7/7 Rk |
| SPEC-FAILURE-TAXONOMY-2 | [docs/specs/SPEC-FAILURE-TAXONOMY-2.md](./specs/SPEC-FAILURE-TAXONOMY-2.md) | 6/6 Rk |
| SPEC-DESIGN-PARTNER-1 | [docs/specs/SPEC-DESIGN-PARTNER-1.md](./specs/SPEC-DESIGN-PARTNER-1.md) | 8/8 Rk |

AR-4/AR-5 require no further spec work unless baseline parsing gaps appear in partner repos.

---

## 12. Non-goals

- «Simple mode» with ~10 MCP tools for human beginners
- Replacing MCP with CLI for agent workflows
- Auto-fixing failing tests without agent intent
- PyPI / marketplace packaging (RFP-019 L2)
- Hosted multi-tenant MCP (RFP-019 L3)
- Contribution economy / HC scoring (RFP-020 §3)

---

## 13. References

- [RFP-019 — MCP scale & lifecycle](./RFP-019-mcp-scale-lifecycle.md)
- [RFP-020 — Three views](./RFP-020-three-views.md)
- [RFP-016 — Runtime hygiene](./RFP-016-runtime-hygiene.md)
- [mcp_performance.md](./mcp_performance.md) — hot path, benchmarks
- [AGENTS.template.md](./AGENTS.template.md) — deployed agent runtime
- Power-user feedback: `ticket:FEEDBACK-2026-06-VERIFY-SANDBOX` (attested 2026-06-11)
