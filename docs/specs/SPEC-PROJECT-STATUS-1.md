# SPEC-PROJECT-STATUS-1 — Unified project status data layer

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-PROJECT-STATUS-1`
> **Anchors:** [RFP-020](../RFP-020-three-views.md) · depends on SPEC-PRODUCT-STAB-1

## 0. Motivation

Clients today need 5–10 MCP/CLI calls to assemble project state (doctor, N×
spec_status, interference, coverage, history). All three product views (developer
CLI, architect HTML, manager report) must read one read-only DTO built from
ledger + spec files + runtime state — not duplicate ledger walks.

Success criterion:

```text
apatch_project_status(target_dir='.')
  → specs[], conflicts{graph,safe_order,risk_score}, active_session,
    hygiene, policy, activity[] (who/when/what from ledger)
```

Non-goals: web server UI; contribution scoring (HC Platform scope).

## R1 project_status_workspace facade

New module `apatch/project_status.py` with `project_status_workspace(target_dir)`
that resolves layout+lane once, walks ledger once (`TrustChainHelper` +
`build_traceability_index`), discovers all `docs/specs/SPEC-*.md`, merges
`.apatch/specs/*.json` planned needles, and returns a versioned DTO
(`schema_version: 1`).

(verify: python3 -m pytest tests/test_project_status.py::test_project_status_dto_shape -q)

## R2 spec_adherence reads ledger mutations

`spec_adherence_workspace` must pass real mutation payloads from the ledger into
plan-vs-fact comparison — not `mutation_payloads=[]`.

(verify: python3 -m pytest tests/test_plan_adherence.py tests/test_project_status.py::test_adherence_uses_ledger -q)

## R3 MCP and workflows wiring

Expose `apatch_project_status` MCP tool and `workflows.project_status_workspace`;
register in `tests/test_mcp.py` expected set.

(verify: python3 -m pytest tests/test_mcp.py tests/test_project_status.py -q)

## R4 Multi-spec rollup fields

DTO includes program-level `summary` (total requirements, attested count,
percent_complete, stale_count) and `conflicts` when ≥2 specs discovered (reuse
`spec_interference_workspace` internally — no second ledger pass).

(verify: python3 -m pytest tests/test_project_status.py::test_multi_spec_summary -q)

## Non-goals

- CLI/HTML rendering (SPEC-CLI-STATUS-1, SPEC-REPORT-1)
- Writing to ledger or session state
