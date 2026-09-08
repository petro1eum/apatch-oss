# SPEC-PLAN-ARTIFACT-1 — Plan as artifact (RFP-011)

> **Status:** attested (2026-06-09) — **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-PLAN-ARTIFACT-1`  
> **Anchors:** RFP-006 (artifact-anchored intent), RFP-009 (spec run), RFP-011 (plan as artifact)  
> **Product frame:** Git = Code Truth · CI = Runtime Truth · **apatch = Engineering Truth**

## 0. Motivation

The chain `Spec → Plan → Mutation → Verification → Attestation` loses the middle layer
today: **why** these needles implement R7 lives in the agent's context window and
vanishes. TrustChain proves **what** changed and **who** signed; it does not record
**which engineering decision** led to the change or **which alternatives were rejected**.

**Institutional Memory** requires two coupled contours in one signed artifact:

| Contour | Question | Primary consumer |
|---------|----------|------------------|
| **Decision Plan** | Why this approach? What did we reject? What assumptions and risks? | Future agents, auditors, architects |
| **Execution Plan** | How do we implement each Rk? Which files and needles? | `spec_run`, adherence, coverage |

A registered plan is an **ADR stitched to executable mutations** — not a Jira ticket,
not chat, not a hand-staged JSON file on disk.

**Principle:** apatch does not *generate* plans (cognition belongs to the agent); apatch
makes the plan **accountable**: lint, register, sign with enrolled identity, version,
diff. One MCP call with an **inline dict** (same anti-pattern ban as RFP-009: no
hand-staged `manifests/PLAN-*.json`).

Success criterion:

```text
apatch_spec_plan_register(spec='SPEC-X', plan={
  schema_version: 2,
  spec: 'SPEC-X',
  decision_plan: { chosen_strategy, rejected_alternatives, assumptions, risks },
  execution_plan: { R1: { target_files, needles }, … },
})
# → plan:SPEC-X@v1 in ledger + .apatch/plans/SPEC-X.v1.json
apatch_spec_run(spec='SPEC-X', plan='v1')           # needles from execution_plan
apatch_spec_plan_diff(spec='SPEC-X', from_version=1, to_version=2)
apatch_trustchain_coverage(artifact='plan:SPEC-X@v1')
# → future agent: "why not use a database?" → decision_plan.rejected_alternatives
```

Non-goals (this spec): LLM plan generation; mandatory plans for every hotfix; markdown
as primary store (markdown may be **generated** from ledger JSON for humans).

---

## 1. Architecture — Engineering Truth artifact

```text
SPEC (intent)
   ↓
plan:SPEC-X@vN  ── decision_plan   → Institutional Memory (WHY)
              └── execution_plan  → Governed Mutation (HOW)
   ↓
spec_run / execute_next
   ↓
mutation + verify + attest  → TrustChain ledger (EVIDENCE)
```

**Decision Plan** fields (all under `decision_plan`):

| Field | Required | Purpose |
|-------|----------|---------|
| `chosen_strategy` | yes | One-sentence selected approach |
| `rejected_alternatives` | yes (≥1) | Options considered and discarded — **core anti-regression signal** |
| `assumptions` | no | Preconditions the plan relies on |
| `risks` | no | Known failure modes if assumptions break |
| `rationale` | no | Longer narrative; supplements `chosen_strategy` |

**Execution Plan** — object keyed by `R1`, `R2`, … (must match SPEC.md requirements):

| Field | Required | Purpose |
|-------|----------|---------|
| `needles` | yes (may be `[]` for verify-only Rk) | Mutation dicts (same lint as RFP-009) |
| `target_files` | no | Declared file scope for adherence (SPEC-ADHERENCE-1); inferred from needles if omitted |
| `rationale` | no | Per-Rk execution note (HOW for this requirement) |

Top-level `rationale` (schema v1) is **deprecated** — migrate to `decision_plan.rationale`.

### Canonical example (schema v2)

```json
{
  "schema_version": 2,
  "spec": "SPEC-COVERAGE-1",
  "decision_plan": {
    "chosen_strategy": "Deterministic staleness via TrustChain ledger hashes",
    "rejected_alternatives": [
      "LLM semantic analysis (too slow, non-deterministic)",
      "Git hooks alone (brittle, bypassable without apatch sandbox)"
    ],
    "assumptions": [
      "Ledger operations map to filesystem states for attested sessions",
      "Protected branches are not force-pushed after attestation"
    ],
    "risks": [
      "Self-staleness loop if a spec modifies its own verification tooling"
    ]
  },
  "execution_plan": {
    "R1": {
      "target_files": ["apatch/spec_coverage.py"],
      "needles": []
    }
  }
}
```

---

## R1 Plan schema v2 and lint

`schema_version: 2` plan JSON:

```text
{
  schema_version: 2,
  spec: "SPEC-<ID>",           # MUST match SPEC.md H1 id; spec_id accepted as alias
  decision_plan: { … },       # required object; see §1
  execution_plan: { Rk: { target_files?, needles[], rationale? } }  # non-empty
}
```

`apatch_spec_plan_lint` validates:

- `decision_plan.chosen_strategy` — non-empty string
- `decision_plan.rejected_alternatives` — non-empty array of non-empty strings
- every `execution_plan` key ∈ SPEC.md requirement ids; unknown Rk → warn
- every `needles[]` entry — valid mutation dict (RFP-009 lint)
- empty `execution_plan` → `ok: false`

Response includes `schema_version`, `decision_plan_present`, `execution_rk_count`.

(verify: python3 -m pytest tests/test_spec_plan.py::test_plan_schema_v2_lint -q)

## R2 Schema v1 backward compatibility

Schema v1 `{schema_version: 1, spec, rationale?, requirements: {Rk: {needles, files?, rationale?}}}`
remains lintable and registerable. Lint emits **warning**: `upgrade to schema_version 2
for decision_plan (Engineering Truth)`. Internally, v1 `requirements` maps to
`execution_plan`; v1 `files` maps to `target_files`; top-level `rationale` maps to
`decision_plan.rationale` with empty `chosen_strategy` / `rejected_alternatives` → warn.

(verify: python3 -m pytest tests/test_spec_plan.py::test_plan_schema_v1_backward_compat -q)

## R3 Register — one call, signed, spec-hash bound

`apatch_spec_plan_register(spec=..., plan={...})` (inline dict):

lint → bind sha256 of current SPEC.md text → signed ledger op `plan:SPEC-X@v<N>` with
enrolled identity (RFP-005) → canonical copy under `.apatch/plans/SPEC-X.v<N>.json`.
Response: `plan_id`, `version`, `plan_sha256`, `op_id`, `decision_plan_summary`
(chosen_strategy truncated, rejected_count).

Full plan body (including `decision_plan`) is content-addressed by `plan_sha256`; ledger
op carries hash + metadata, not a lossy summary only.

(verify: python3 -m pytest tests/test_spec_plan.py::test_plan_register_signed_ledger_op -q)

## R4 Supersession and diff (decision + execution)

Registering another plan for the same spec creates `v(N+1)` with `supersedes: v(N)`.
`apatch_spec_plan_diff` returns:

- `decision_plan_delta` — field-level diff of chosen_strategy, rejected_alternatives,
  assumptions, risks
- `execution_plan_delta` — per-Rk diff of target_files, needles, rationale

Evolution of **thinking** and **implementation** are both visible without reading git.

(verify: python3 -m pytest tests/test_spec_plan.py::test_plan_supersession_and_diff -q)

## R5 spec_run consumes execution_plan

`apatch_spec_run(spec=..., plan='v<N>'|'latest')` reads needles from registered plan
`execution_plan` (v1 fallback: `requirements`). `plan_id` + `plan_sha256` written to
`.apatch/spec_run.json` and stamped on mutation/attestation ops per Rk. Inline
`requirements` on `spec_run` remains for ad-hoc runs without a registered plan.
Plan change mid-run without `reset` → `PLAN_DRIFT`.

(verify: python3 -m pytest tests/test_spec_plan.py::test_spec_run_with_registered_plan -q)

## R6 Traceability and decision lookup

`apatch_trustchain_coverage(artifact='plan:SPEC-X@v1')` links mutations and
attestations. `apatch_trustchain_history(query='rejected_alternatives')` finds plans
by substring in stored plan JSON.

`apatch_spec_plan_show(spec=..., version='latest'|'vN')` (new MCP tool): returns full
registered plan from `.apatch/plans/` including `decision_plan` — the machine-readable
answer to *"why does this file exist?"* when combined with coverage reverse lookup.

Chain reconstructible from ledger + local plan store:
`spec → plan → session → mutation → attestation`.

(verify: python3 -m pytest tests/test_spec_plan.py::test_plan_coverage_and_show -q)

## R7 Optionality guard — no new ceremony

Existing paths unchanged without a plan: `spec_run(requirements=...)`,
`execute_next(needles=...)`, manual §3I. Missing plan → no warnings, never blocks attest.
MCP tool count and `tests/test_mcp.py` expected updated when R6 tool lands.

(verify: python3 -m pytest tests/test_spec_plan.py::test_plan_optional_paths_unchanged -q)

---

## Non-goals

- Forcing a plan for every change (operator threshold).
- Decomposing a spec into Rk — agent/architect work.
- Plan-vs-fact deviation — [SPEC-ADHERENCE-1](./SPEC-ADHERENCE-1.md) (uses
  `execution_plan.target_files`).
- Automatic LLM extraction of `decision_plan` from chat — agent must supply inline dict.

## Migration note (v1 draft → v2)

The pre-v2 draft used flat `requirements` + optional top-level `rationale`. All new
plans SHOULD use schema v2 before attestation of this spec. Implementation MUST ship
v1 compat (R2) so existing dogfood plans do not break.
