# RFP-024 — Needles scaffold (lint → spec_run bridge)

> **Status:** MVP shipped · **Owner:** apatch core  
> **Anchors:** [Plan contract](specs/SPEC-PLAN-ARTIFACT-1.md) · [RFP-009](RFP-009-spec-run.md) · [transformation matrix](apatch-transformation-matrix.md)

## Problem

The governed pipeline had a process gap between **format lint** and **spec_run**:

```text
RFP acceptance     embedded in apatch_spec_lint (rfp_lint + rfp_coverage)   ✅
SPEC (Rk + verify) apatch_spec_lint (format + plan_scaffold when passed)     ✅
needles            agent fills plan_scaffold.execution_plan.{Rk}.needles      ✅
spec_run           session → apply → verify → attest                        ✅
```

Agents improvised: hand-staged JSONL, ad-hoc `generate_batch` needles, or reading RFP docs outside MCP.

## Principle (unchanged from RFP-009 / RFP-011)

apatch **does not LLM-generate** mutation content from SPEC prose. Agent cognition still fills `find_text` / `replace_text` / `content`.

RFP-024 closes the **workflow** gap with a **deterministic scaffold** — **not** semantic codegen. Agents often misread scaffold as “autonomous implementation”; that is a documentation/product clarity issue, not a missing LLM feature.

### Autonomy boundary

| Layer | Autonomous? | Tool |
|-------|-------------|------|
| **Orchestration** (session → apply → verify → attest) | ✅ After needles exist | `apatch_spec_run` |
| **Cognition** (find_text from source) | ❌ Agent/human | `apatch_execute_next` per Rk |
| **Structure** (files, plan skeleton, templates) | ✅ Deterministic | `apatch_spec_lint` → `plan_scaffold` |

```text
21 pending Rk, needles unknown for R2…R21:
  apatch_execute_next(spec='SPEC-X', requirement='SPEC-X#R2', needles=[…])

All pending Rk have needles in requirements/manifest:
  apatch_spec_run(spec='SPEC-X', requirements={…}, chunk_rk_per_call=0)

Gaps only (no needles):
  apatch_spec_run(spec='SPEC-X', dry_run=true)
```

**`MANIFEST_GAP`** = needles entry missing for ≥1 pending Rk — **expected** until filled. Explicit `needles: []` is verify-only and must already pass `(verify: …)`. Response includes `recommended_tool: apatch_execute_next` and `autonomy_boundary` (same JSON as `apatch_doctor.agent_onboarding.autonomy_boundary`).

See [agent-onboarding.md §3a](./agent-onboarding.md#3a-autonomy-boundary-rfp-024).

| Output | Purpose |
|--------|---------|
| `target_files[]` | Inferred from `(verify:)` pytest nodes + paths in Rk body |
| `needle_templates[]` | Structural examples per Rk (not auto-applied) |
| `plan_scaffold` | RFP-011 schema v2 skeleton (`execution_plan.{Rk}.needles: []`) |
| `manifest_scaffold` | Inline `requirements` map for compact spec_run |
| Attested `needles[]` | Reused from ledger for stale / reference Rk |

## Tooling

| Surface | Command |
|---------|---------|
| **Primary** | `apatch_spec_lint(spec='SPEC-X')` → `needles_scaffold` + `plan_scaffold` when `passed` |
| MCP (optional) | `apatch_spec_needles_scaffold(spec='SPEC-X')` |
| CLI | `apatch spec needles-scaffold --spec SPEC-X [--json]` |
| Embedded | `apatch_spec_run(dry_run=true)` → same scaffold fields |

### Recommended flow

```text
apatch_doctor(target_dir='.')
apatch_spec_lint(spec='SPEC-X')   # passed → rfp_coverage + plan_scaffold + agent_next
# agent: fill decision_plan + execution_plan.{Rk}.needles
apatch_spec_plan_register(spec='SPEC-X', plan=<plan_scaffold>)   # optional
apatch_spec_run(spec='SPEC-X', requirements={…}) OR manifest_path=…
```

See [agent-onboarding.md](./agent-onboarding.md). Optional standalone `apatch_spec_needles_scaffold` for CLI; `apatch_spec_run(dry_run=true)` repeats scaffold on resume.

## Non-goals

- Semantic / LLM needle synthesis from Rk prose
- Auto-apply of scaffold templates
- Replacing signed execution plans (RFP-011)

## Acceptance

| Id | Criterion |
|----|-----------|
| N24-A | `infer_target_files_from_verify` extracts pytest file paths |
| N24-B | `spec_needles_scaffold` returns schema v2 `plan_scaffold` with per-Rk `target_files` |
| N24-C | MCP tool `apatch_spec_needles_scaffold` registered |
| N24-D | `apatch_spec_run(dry_run=true)` embeds `needles_scaffold` when pending Rk exist |
| N24-E | `autonomy_boundary` in doctor, spec_lint, needles_scaffold; `MANIFEST_GAP` routes to `execute_next` |
| N24-F | Path inference includes `o_lang/**`, `packages/**`, C++ extensions |

(verify: `pytest tests/test_spec_needles_scaffold.py -q`)
