# SPEC-ADHERENCE-1 - Plan adherence (RFP-012)

> **Status:** attested (2026-06-10) - **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-ADHERENCE-1`
> **Anchors:** RFP-010 (coverage), RFP-011 (plan as artifact), RFP-012 (plan adherence)
> **Dependency:** [SPEC-PLAN-ARTIFACT-1](./SPEC-PLAN-ARTIFACT-1.md)

## 0. Motivation

The strongest consequence of plan-as-artifact is not the v1->v2 diff but the
**plan-vs-fact diff**:

```text
PLAN: R7 -> [auth/service.py, auth/models.py]
FACT: R7 -> [auth/service.py, auth/models.py, api/routes.py]   <- deviation
```

Attestation should record: "implemented per plan v2, deviation: +1 file outside the
declared scope". This is deterministic (the session knows its mutations, the plan knows
its file set) and it is exactly the signal review and compliance need: where the agent
departed from the declared intent. The report is **report-only**: blocking on deviation
is out of scope for v1.

Non-goals (this spec): semantic equivalence of changes; auto-blocking on deviation
(report only); adherence without a registered plan.

## R1 Deviation computation per requirement

For an Rk with a registered plan: compare planned files (`execution_plan[Rk].target_files`
or needle-derived scope; SPEC-PLAN-ARTIFACT-1 R5) with the actual mutations of the
governed session ->
`{planned[], touched[], unplanned[], untouched_planned[]}`. A clean match yields
`adherent: true`.

(verify: python3 -m pytest tests/test_plan_adherence.py::test_deviation_computation -q)

## R2 Attestation embeds the adherence report

On `attest` in an Rk session that has a plan, the signed payload gains
`adherence: {plan_id, adherent, unplanned[], untouched_planned[]}`. The report is read
back from the ledger (`apatch_trustchain_coverage`) and cannot be rewritten after the
fact.

(verify: python3 -m pytest tests/test_plan_adherence.py::test_attest_embeds_adherence -q)

## R3 MCP/CLI report - spec-level adherence

`apatch_spec_adherence` (MCP) / `apatch spec adherence` (CLI): per-Rk adherence for a
spec, aggregate `{rk_total, adherent, deviated}`, and the unplanned file list. Tool
registered; `tests/test_mcp.py` expected and tool_count updated.

(verify: python3 -m pytest tests/test_plan_adherence.py::test_mcp_spec_adherence_registered -q)

## R4 Plan-aware staleness

Staleness from SPEC-COVERAGE-1 R2 uses the planned file set as the canonical Rk scope
when a plan is registered (fallback: ledger-derived set). Drift in a planned file the
session never touched also yields `stale` (the plan promised it, the code did not
change, the environment moved).

(verify: python3 -m pytest tests/test_plan_adherence.py::test_plan_aware_staleness -q)

## Non-goals

- A PR-blocking policy on deviation - separate operator decision (CI gate flag).
- Semantic diff (a refactor outside the plan is still a deviation).
- Multi-agent attribution inside a single session.