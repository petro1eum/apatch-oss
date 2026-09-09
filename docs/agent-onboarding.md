# Agent onboarding — apatch MCP playbook

> **Audience:** fresh agent in consumer or apatch dogfood repo.  
> **Bound workspace:** first tool is `apatch_doctor(target_dir=".")`.  
> **Human-registered sibling:** inspect its pinned contract first, then use the returned `@alias` on every MCP call.

## 0. Resolve the workspace contract

For the MCP-bound repository, `target_dir="."` is the only normal entry point. For a sibling that a human registered with `apatch workspace add`, use this read-only first hop:

```text
apatch_workspace_list(target_dir=".")
apatch_workspace_inspect(alias="crm", include_contract=true, target_dir=".")
apatch_doctor(target_dir="@crm")
```

Read the full `AGENTS.md` returned by `workspace_inspect` before acting. Root identity or contract SHA-256 drift is a hard stop until a human reviews and re-pins the alias. After inspection, pass `target_dir="@crm"` explicitly on every call; routing is stateless per request. Never pass a raw absolute cross-workspace path.

## 1. Do not guess — read doctor once

```text
apatch_doctor(target_dir=".")
```

| Field | Use |
|-------|-----|
| `protocol_contract` | Task routing, never-do list |
| `spec_authoring` | SPEC.md format rules |
| `spec_run` | Whole-spec batch workflow (§3K) |
| `mcp_health.mcp_tool_catalog` | **Authoritative tool count** (17 compact default; 124 full profile) |
| `mcp_bound_workspace` | Which repo `.` resolves to |

**Distinguish the three layers.** The IDE interpreter selects a workspace's
canonical `.apatch/mcp.json`; its isolated child must actually serve MCP; the
host must then expose the needed tools to this task. A successful doctor in
another task proves neither your tool availability nor your bootstrap selection.
Do not substitute a venv's binary realpath for its interpreter path.
Use `apatch mcp check --target-dir . --json` for configured-command readiness;
a running doctor reports `not_checked_in_stdio` for this independent probe.
Configured-command readiness is not host tool availability.
Check both runtime pointers before suggesting reconnect. Doctor never repairs
configuration; explicit sync preserves env/profile. Full profile has 124 tools.

## 2. Governed invariant

```text
Intent → Session → Mutation → Verification → Attestation | Rollback
```

- Mutations: **`apatch_generate_batch` → `apatch_apply_session`** (not Write/StrReplace on protected paths).
- Patch JSONL is **ephemeral** → `.apatch/tmp/<session_id>/` — never commit hand-staged `patches*.jsonl`.
- **`apatch_session_end`** after mass refactor or spec batch chunk.

## 3. Spec-driven pipeline (RFP-007 → RFP-024)

One MCP call replaces three separate lint/scaffold steps:

```text
apatch_spec_lint(spec='SPEC-X')
```

When `passed: true`, response includes:

| Field | Meaning |
|-------|---------|
| `passed` | Format lint OK (no errors) |
| `rfp_lint` | Present when SPEC has `## RFP traceability` — RFP Acceptance table lint (RFP-023) |
| `rfp_coverage` | RFP Acceptance ids vs SPEC traceability (`gaps[]`, `waivers[]`) |
| `plan_scaffold` | RFP-011 schema v2 skeleton — fill `execution_plan.{Rk}.needles` |
| `needles_scaffold` | Full scaffold DTO (`target_files`, `needle_templates` per Rk) |
| `agent_next` | What to do next (usually edit needles → plan_register or spec_run) |

**apatch does not LLM-generate needle content** — you fill `{action, target_file, find_text/replace_text | content}` from source.  
RFP-024 scaffold gives **structure only** (`target_files`, empty `needles[]`, templates) — not autonomy over cognition.

## 3a. Autonomy boundary (RFP-024)

Agents often expect scaffold = auto-codegen. It is not. This table is product truth:

| Layer | Autonomous? | Tool |
|-------|-------------|------|
| **Orchestration** (session → apply → verify → attest) | ✅ Yes — after needles exist | `apatch_spec_run` |
| **Cognition** (find_text from source) | ❌ Agent/human | `apatch_execute_next` per Rk |
| **Structure** (which files, plan skeleton) | ✅ Deterministic | `apatch_spec_lint` → `plan_scaffold` |

```text
Many pending Rk, needles unknown (e.g. 21/21):
  → apatch_execute_next(spec, requirement='SPEC-X#R2', needles=[…])  # one Rk at a time

All pending Rk have needles in manifest/requirements:
  → apatch_spec_run(spec, requirements={…}, chunk_rk_per_call=0)     # batch until done

Discover gaps only (no needles needed):
  → apatch_spec_run(spec, dry_run=true)
```

**`MANIFEST_GAP`** = a needles entry is missing for one or more pending Rk — **expected**, not broken autonomy.
Fix: fill needles for that Rk (`execute_next`) or for all pending then `spec_run`; explicit `needles: []` is allowed only for verify-only Rk that are already green.

Every `apatch_spec_lint` / `needles_scaffold` response includes **`autonomy_boundary`** (same JSON as `apatch_doctor.agent_onboarding.autonomy_boundary`).

### After lint passes

```text
# A) Compact spec (few Rk, small needles)
apatch_spec_run(spec='SPEC-X', requirements={
  R1: {needles: [{action: "create", target_file: "…", content: "…"}]},
  R2: {needles: [{action: "replace", find_text: "…", replace_text: "…", target_file: "…"}]},
})

# B) Large needles — versioned manifest
# Edit plan_scaffold → save manifests/SPEC-X.run.json → lint manifest
apatch_spec_plan_register(spec='SPEC-X', plan=<plan_scaffold>)   # optional signed plan
apatch_spec_run(spec='SPEC-X', manifest_path='manifests/SPEC-X.run.json')

# C) Resume / discover gaps
apatch_spec_run(spec='SPEC-X', dry_run=true)   # pending[], manifest_template, needles_scaffold
while continue: apatch_spec_run(spec='SPEC-X')
apatch_spec_status(spec='SPEC-X')
```

### Doc gate before code (RFP → SPEC)

```text
apatch_rfp_lint(rfp='RFP-NNN')                    # optional — also inside spec_lint
apatch_rfp_spec_coverage(rfp='RFP-NNN', spec='…') # optional — also inside spec_lint
apatch_spec_lint(spec='SPEC-X')                   # format + rfp + plan_scaffold
```

Multi-spec RFP: `apatch_rfp_spec_coverage(rfp='RFP-022', specs='SPEC-A,SPEC-B,SPEC-C')`.

See [rfp-authoring.md](./rfp-authoring.md) · [spec-authoring.md](./spec-authoring.md).

## 4. Choose workflow by task

| User intent | Tool | Section |
|-------------|------|---------|
| One Rk / discovering needles (many pending) | `apatch_execute_next` | §3J — **default for N/21 specs** |
| All pending Rk have needles ready | `apatch_spec_run` | §3K — batch orchestration |
| Inspect / dogfood one Rk | `spec_next` → `session_start(requirement)` → … | §3I |
| Mass refactor (not SPEC) | `session_start` → `generate_batch` → `apply_session` | §3B |
| Doc + SPEC shape only | `apatch_spec_lint` | — |
| Is a green gate trustworthy? (real / regressed / still green) | `apatch_probe` (mode=falsify\|regress\|ratify) | [probe.md](./probe.md) |
| Track / review observed reality (bug, incident, feedback) | `apatch_reality` (action=add\|status) | [reality.md](./reality.md) |
| Start a SPEC from an RFP/contract (don't hand-author the Rk checklist) | `apatch_spec_scaffold` (rfp=, spec=) | RFP-023 |
| Cross-file refactor impact (which attested Rk reference a changed symbol) | `apatch_scip` (action=index\|impact) | [RFP-033](./RFP-033-scip-reference-impact.md) |

**Never:** hand-staged JSONL for SPEC workflows; Python driver scripts bypassing MCP.  
**Do not** call `spec_run` with missing needles entries for pending Rk (you get `MANIFEST_GAP` — use `execute_next` instead). Use explicit `needles: []` only for verify-only green Rk.
**Do not** call `spec_run` when only one Rk’s needles are ready but 20 remain — use `execute_next` until batch-ready.

## 5. Needles (mutation dicts)

```json
{"action": "replace", "target_file": "apatch/foo.py", "find_text": "<literal>", "replace_text": "…"}
{"action": "create", "target_file": "tests/test_x.py", "content": "…"}
```

Numbered markdown: `insert_section` + `shift_following` — not N× heading replace ([SPEC-DOC-OUTLINE-1](./specs/SPEC-DOC-OUTLINE-1.md)).

## 6. Failure taxonomy & recovery (read `state_update`)

| `error_type` | `recommended_action` |
|--------------|----------------------|
| `VERIFY_FAILED` / `NOTARIZATION_FAILED` | `fix_forward` (edit + re-verify) or `rollback` |
| `RUNTIME_TRANSITION` (op not allowed in lifecycle `failed`/`verifying`) | **`apatch_resume_session`** — clears failure, re-enables verify/attest (RFP-027) |
| `BACKUPS_PRUNED` | backups gc'd — start a fresh session; pruned backups cannot be restored (RFP-027) |
| `MANIFEST_GAP` | Needles entry missing — `apatch_execute_next` for current Rk, or fill all pending then `spec_run`; explicit `needles: []` is verify-only green precheck |
| `MASS_APPLY_BLOCKED` | `reduce_scope` |
| `DIRECT_WRITE_BLOCKED` | Use `apply_session` with active lease |

**A red test does not brick the session.** On `verify` failure the lifecycle goes `failed`;
call **`apatch_resume_session`**, then fix the cause and re-`verify` → `attest`. Do **not**
`session_end` + restart for an ordinary failure (RFP-027 R1/R2).

**Shared file across Rk:** when one module satisfies several Rk, the earlier Rk go `stale`
(file_drift). Fastest: **`apatch_rebind_stale(spec="SPEC-X")`** re-verifies + re-attests every file_drift-stale Rk in one call. Manual fallback: **`apatch_noop_attest(covered_by="R1,R4")`** — no fabricated
marker file (RFP-027 U27-F).

**Closing a slug spec:** use **`apatch_slug_ratify(slug="...")`**. It executes each
unique verify command once and binds every green pending/in-progress/stale Rk in one
governed session and one signed batch-attestation. An actual test failure or a broken
verify on an open Rk blocks that Rk; an already-closed stale test-node reference is
reported as advisory and does not block green open work. The conformance verdict reuses
the same evidence. Do not create marker fixtures or run one `spec_run` lifecycle per Rk.

**Notarize new docs before commit.** An RFP/SPEC.md created with Write/Edit is `never_notarized`, so `apatch_verify_notarization(staged=true)` blocks the commit. Route it through a governed mutation (a real edit — e.g. a footer — via `generate_batch` → `apply_session` → `attest`); only files touched by an attested mutation enter the notarized index.

**Trailing edits stale the last Rk.** The last requirement's `content_hash` spans to EOF, so a footer after the final `## Non-goals` trips `spec_text_changed` (which `rebind_stale` skips). Re-anchor that one: `session_start(requirement="SPEC-X#Rk")` → `apatch_noop_attest(covered_by="Rk")`. Keep trailing edits above the last `## Rk`.

**False `SPEC_DEPENDENCY_UNMET`.** A `dependency`/`requires`/`depends on` keyword on the same line as another `SPEC-id` makes that spec a phantom upstream dependency. Keep dep keywords off lines that mention other spec ids.

**`apply_session` returned `status: SESSION_ALREADY_COMPLETE`?** Nothing was applied —
pass `reset=true` to re-apply a changed patch set, or proceed to verify/attest (RFP-027 U27-E).

**After editing apatch's own source:** an `apply_session` response may carry
`restart_required` / `tooling_refresh` — copy the steps to the human and wait for the MCP
restart before relying on the changed code (RFP-027 U27-H).

## 7. Links

| Doc | Topic |
|-----|-------|
| [spec-authoring.md](./spec-authoring.md) | SPEC.md rules |
| [RFP-024-needles-scaffold.md](./RFP-024-needles-scaffold.md) | Needles scaffold |
| [RFP-023-rfp-spec-coverage.md](./RFP-023-rfp-spec-coverage.md) | RFP traceability |
| [RFP-027-agent-ux-recovery.md](./RFP-027-agent-ux-recovery.md) | Recovery: `resume_session`, `noop_attest`, gc-safe backups |
| [RFP-026-contribution-timesheet.md](./RFP-026-contribution-timesheet.md) | `apatch_timesheet` — per-identity contribution ledger |
| [mcp_setup.md](./mcp_setup.md) | Full MCP tool table |
| [AGENTS.template.md](./AGENTS.template.md) | Consumer copy-paste playbook |
