# apatch Design Partner Program — Agent Playbook

> **Audience:** Tech leads onboarding an external repo; IDE agents operating under MCP + enforce.  
> **Language:** English (primary). Human policy setup once; agents run governed cycles autonomously.  
> **Related:** [RFP-021 — Agent Reliability](./RFP-021-agent-reliability-design-partner.md) · deployed agent runtime: `AGENTS.md` (from [AGENTS.template.md](./AGENTS.template.md))

---

## 1. What “design partner ready” means

A consumer repo is **Design Partner Ready (L1)** when an IDE agent can complete:

```text
Intent → Session → Mutation → Verification → Attestation | Rollback
```

…without a human editing `.apatch/session_state.json`, and a supervisor can read **`apatch status --json`** to understand blockers.

This playbook is **agent-first**: agents receive 15 intent-level MCP tools by default, while the 110-tool full surface remains available for specialist work. The human sets sandbox, enforcement, and TrustChain policy once, then supervises outcomes.

---

## 2. Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Python 3.11+** | Same interpreter as MCP server (`apatch doctor` → `python_executable`) |
| **git** | Worktrees recommended for parallel agents |
| **apatch with MCP** | `pip install apatch[mcp,dev,yaml]` (human-only install) |
| **IDE MCP** | Cursor or Antigravity; config via `apatch mcp sync --target-dir .` |
| **TrustChain (recommended)** | `apatch trust enroll` — enforce mode blocks unnotarized commits |
| **Optional** | Node/npm, pytest, stack-specific tools (detected by `apatch doctor`) |

---

## 3. One-time setup (human)

Run from the **consumer repo root**:

```bash
apatch init-consumer --target-dir . \
  --with-sandbox \
  --with-enforcement \
  --with-mcp

apatch mcp sync --target-dir . --force
# Restart apatch MCP in IDE after apatch upgrades
```

Verify:

```bash
apatch doctor --json   # trustchain.mode, sandbox.mode, mcp_health
```

Scaffold includes:

- `AGENTS.md` — full agent runtime (from `AGENTS.template.md`)
- `docs/design-partner-playbook.md` — this document
- `.apatch/sandbox.json`, `.apatch/enforcement.json`, `.apatch/mcp.json`
- `docs/specs/SPEC-TEMPLATE.md` — executable spec format

**Default guidance:** set `APATCH_MCP_GUIDANCE=doctor_only` in MCP env (slim hot-path responses; read `apatch_doctor` once per session).

---

## 4. Agent contract

### Governed markdown (numbered outline)

Prefer structural needles over N× heading `replace`:

```json
{
  "action": "insert_section",
  "target_file": "docs/VISION.md",
  "before": "## 3.",
  "content": "## 3. New section\n\n…",
  "shift_following": {"levels": [2, 3], "delta": 1}
}
```

See [SPEC-DOC-OUTLINE-1](./specs/SPEC-DOC-OUTLINE-1.md) (`shift_outline`, `insert_before`, `insert_section`).

In **audit** mode with markdown on the allow list, direct edits are OK when you do not need TrustChain attest.

### First call every session

```text
apatch_doctor(target_dir=".")
```

Read from the response (once): `protocol_contract`, `spec_run`, `runtime_hygiene`, `sandbox_agent_protocol`.

### Governed mass refactor (non-spec)

```text
apatch_session_start(intent="…", artifacts=["ticket:…"])
apatch_generate_batch(needles=[{action, target_file, …}])
apatch_simulate(logs_path=<out_path from generate>)
apatch_apply_session(logs_path=<same>, verify_deferred=true)  # repeat until continue=false
apatch_verify_run(...)   # or async_mode=true for long suites — see §6
apatch_attest()
apatch_session_end()
```

### Whole executable spec (preferred when `docs/specs/SPEC-*.md` exists)

```text
apatch_spec_lint(spec="SPEC-X")
apatch_spec_run(spec="SPEC-X", requirements={Rk: {needles: [...]}})  # repeat while continue=true
apatch_spec_status(spec="SPEC-X")
```

**Never:** hand-staged `patches-*.jsonl` in repo root; `Write`/`StrReplace` on protected paths; `pip install` from the agent under sandbox enforce.

---

## 5. Success criteria (partner program)

The program succeeds when **all** are true for your repo:

1. Agent completes at least one full governed cycle (§4) with **`apatch_attest`** and **`apatch_session_end`**.
2. After MCP restart mid-cycle, agent **resumes** via `apatch_apply_session` / `apatch_verify_run` / `apatch_attest` — no manual JSON edits ([SPEC-SESSION-RECOVERY-1](./specs/SPEC-SESSION-RECOVERY-1.md)).
3. Pre-existing failing tests do **not** block innocent patches (`baseline=capture` before apply, `baseline=compare` after — AR-4).
4. Long verify uses **async jobs**; MCP stays connected ([SPEC-VERIFY-ASYNC-1](./specs/SPEC-VERIFY-ASYNC-1.md)).
5. Verify failures on good patches yield **`fix_forward`**, not auto-rollback ([SPEC-FAILURE-TAXONOMY-2](./specs/SPEC-FAILURE-TAXONOMY-2.md)).
6. Human supervisor reads **`apatch status --json`** and understands `session.blocker` without opening pytest logs ([SPEC-STATUS-BLOCKED-1](./specs/SPEC-STATUS-BLOCKED-1.md)).

---

## 6. Long verify (async)

When `doctor.recommended_verify` runs longer than ~45s (or prior runs exceeded `APATCH_VERIFY_SYNC_MAX_SEC`):

```text
apatch_verify_run(verify=[...], async_mode=True)
  → verify_job_id, verify_job_state: "running"

apatch_verify_status(job_id="vjob_…")
  → poll until state: passed | failed
```

CLI: `apatch verify run --async …` · `apatch verify status --job-id vjob_…`

Baseline compare works on async job completion (same fields as sync verify).

---

## 7. Escalation — rollback vs fix_forward

| Situation | `recommended_action` | Agent should |
|-----------|---------------------|--------------|
| Verify red, mutations + notarization OK | **`fix_forward`** | Fix tests or revert own code; do **not** rollback good patches |
| `verify_rollback=true`, `rollback_performed!=true` | **`rollback`** | Perform the requested rollback; reduce scope or fix verify |
| `rollback_performed=true` (chunk files already restored) | **`fix_forward`** | Resume the same governed session with corrected needles; do not roll back twice |
| Apply chunk failed / patch conflict | **`rollback`** | `apatch_rollback(session_id=checkpoint)` |
| Notarization failed after write | **`rollback`** | Rollback; fix TrustChain |
| MCP disconnect / illegal lifecycle | **`resume_session`** | Retry same tool per `next_action`; reconcile is automatic |
| Sandbox / lease blocked | **`retry_chunk`** | Use MCP tools only under lease |

Supervisor view: `apatch status --json` → `session.blocker{error_type, recommended_action, summary}` and `session.baseline`.

---

## 8. Parallel agents

Use **git worktree** + **lane** per agent ([RFP-019 §2.3](./RFP-019-mcp-scale-lifecycle.md)):

- One MCP process / lane per worktree (`APATCH_LANE` or lane manifest)
- Do not share one governed session across agents
- Merge attested work via normal git flow after `apatch_verify_notarization(staged=true)`

Hygiene: `apatch_gc(mode="reconcile")` if `doctor.hygiene.status: critical`.

---

## 9. L1 readiness checklist (G1–G7)

| Gate | Criterion | How to verify |
|------|-----------|---------------|
| **G1** | Mass refactor cycle completes | Record MCP transcript or run scripted agent session → attest |
| **G2** | MCP restart mid-cycle → resume | `pytest tests/test_session_recovery_mcp.py` in apatch repo; replay in partner repo |
| **G3** | Pre-existing red + baseline compare | Agent: `verify_run(baseline="capture")` → apply → `verify_run(baseline="compare")` → attest |
| **G4** | Long verify async | `verify_run(async_mode=True)` returns in <30s; poll until passed |
| **G5** | Innocent patch + verify red → fix_forward | `recommended_action` ≠ rollback when `verify_rollback` absent |
| **G6** | Supervisor understands blockers | Manual: `apatch status --json` shows `session.blocker` |
| **G7** | apatch core CI green | `pytest tests/` in apatch repository |

**Not required for L1:** PyPI publish, hosted MCP, reducing MCP tool count, Russian/English doc parity.

---

## 10. When to escalate to apatch maintainers

- Reconcile loops (`RUNTIME_TRANSITION` after MCP restart) that do not clear after `resume_session`
- Baseline compare false positives on your test output format
- Sandbox false blocks on legitimate shell (e.g. quoted redirects)
- Inference sunset / hygiene `critical` after `apatch_gc(mode="reconcile")`

Include: `apatch doctor --json`, `apatch status --json`, relevant `session_id`, and MCP stderr (`.apatch/mcp_stderr.log`).

**Re-apply same `patches.jsonl` in a new governed session:** call `apatch_apply_session(..., reset=true)` so apply-session state does not treat the log as already complete ([RFP-016](./RFP-016-runtime-hygiene.md)).

---

## 11. UD-7 diagnostics smoke (apatch dogfood)

Run once with a **clean MCP agent** (no chat history, no RFP-022) to validate [RFP-022 UD-7](RFP-022-unified-diagnostics-knowledge-graph.md) on the apatch repo. Live consumer pilots use §12 + G8 on their stack.

**Prereq:** MCP restarted after apatch upgrade; `apatch_doctor` → `mcp_health.tool_count` ≥ 75; `trustchain.mode` known.

**Agent prompt (minimal):** `target_dir` = apatch repo root · only `apatch_*` MCP · first tool `apatch_doctor` · read `protocol_contract` + `apatch://playbook/diagnose` · governed session → failing pytest in `tests/` → `apatch_verify_run` → use **`diagnostics[]` only** (not stderr) → `apatch_project_status` · `apatch_knowledge_graph(session_id=…)` → fix → re-verify → `apatch_attest` → `apatch_session_end`.

**Pass table (all PASS required):**

| Check | Evidence field |
|-------|----------------|
| Doctor + diagnose playbook without human hint | `apatch_doctor` → tool_usage / mcp_resources |
| `diagnostics[]` on verify fail | `apatch_verify_run` → `diagnostics` |
| `recommended_action: fix_forward` (no spurious rollback) | `diagnostics[0].recommended_action` |
| `diagnostics_summary` | `apatch_project_status` → `diagnostics_summary` |
| Intent → Diagnostic graph | `apatch_knowledge_graph` → `edges` with `DIAGNOSED_AS` |
| Full cycle | `apatch_attest` → `committed`; `apatch_session_end` → `session.lifecycle: ended` |

**2026-06-12 dogfood result:** PASS (partner agent transcript). Feedback:

1. **Structured diagnostics** — `diagnostics[]` with `recommended_action` is reliably easier for agents than pytest stderr.
2. **Governed lifecycle** — phase guards prevent invalid transitions; document `apply_session(reset=true)` when reusing JSONL.
3. **Knowledge graph** — `Intent → Diagnostic → File` restores verify context by session id (artifacts under `.apatch/diagnostics/<session_id>.json`; governed session id bound from `session_state` since 2026-06-12).

**Not covered by this smoke:** consumer-repo adapter formats (G8), G1–G4, G6.

---

## 12. Partner pilot kickoff (consumer repo)

**Status (2026-06-12):** at least one **live consumer** already runs governed cycles and files feedback (`ticket:FEEDBACK-*` → attest). §12 is the formal G1–G8 checklist — run (or re-run) to produce attested evidence, not to «start» a pilot from zero.

Run per consumer repo (human setup, agent executes cycles):

| Step | Who | Action |
|------|-----|--------|
| 1 | Human | `pip install apatch[mcp,dev,yaml]` · `apatch init-consumer --with-sandbox --with-enforcement --with-mcp` · `apatch mcp sync --force` · restart MCP |
| 2 | Human | `apatch trust enroll` (if enforce) · branch protection with `apatch-gate` optional |
| 3 | Agent | `apatch_doctor` → read `protocol_contract` + this playbook §4 |
| 4 | Agent | **G1:** one mass-refactor or `apatch_spec_run` cycle → attest → `session_end` |
| 5 | Human | **G2:** restart MCP mid-apply or mid-verify; agent resumes without JSON edits |
| 6 | Agent | **G3:** `verify_run(baseline="capture")` before apply → patch → `verify_run(baseline="compare")` |
| 7 | Agent | **G4:** `verify_run(async_mode=True)` on `doctor.recommended_verify`; poll `verify_status` |
| 8 | Human | **G6:** read `apatch status --json` during a deliberate blocker — confirm `session.blocker` |
| 9 | Human | File feedback as `ticket:FEEDBACK-YYYY-MM-*` artifact; attested if apatch changes needed |

**Success:** all G1–G6 pass on the consumer repo; G7 remains apatch CI (`pytest tests/`).

---

## 13. References

| Doc | Topic |
|-----|--------|
| [AGENTS.template.md](./AGENTS.template.md) | Full MCP tool playbook (consumer `AGENTS.md`) |
| [mcp_setup.md](./mcp_setup.md) | MCP install, 15-tool default, 110-tool full parity table |
| [sandbox.md](./sandbox.md) | Protected paths, hooks, CI gate |
| [RFP-016](./RFP-016-runtime-hygiene.md) | EPHEMERAL JSONL, `session_end`, GC |
| [RFP-021](./RFP-021-agent-reliability-design-partner.md) | L1 gates, AR-1–AR-7 map |
