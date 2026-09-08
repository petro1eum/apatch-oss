# SPEC-INTERFERENCE-1 — Cross-spec interference detection (RFP-014 Phase 1)

> **Status:** attested (2026-06-10) · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-INTERFERENCE-1`
> **Anchors:** [RFP-014](../RFP-014-spec-interference-detection.md), RFP-010 (`requirement_file_sets`), RFP-009 (`spec_run` manifest)

## 0. Motivation

Parallel executable specs (UI, API, infra) can mutate the same files. Today conflicts surface
only when a later `verify` fails. RFP-014 Phase 1 adds **passive detection** before apply:
file overlap (L1), write-read / write-write patch conflicts (L2), conflict graph, `safe_order`,
and `risk_score`.

Success criterion:

```text
apatch_spec_interference(specs=['SPEC-LEDGER-ACTOR-1', 'SPEC-COVERAGE-1'], target_dir='.')
# → file_overlap on apatch/spec_coverage.py, safe_order[], risk_score, has_cycle: false
```

Non-goals: `apatch_spec_schedule` (Phase 1.5); Level 3 cross-verify; auto-merge needles;
registry-only JSONL discovery without ledger/manifest.

## R1 Level-1 file overlap from ledger file sets

`spec_interference` reuses `requirement_file_sets()` per spec and reports `file_overlap`
conflicts when two specs share a `target_file` in attested mutation sets. Overlap is a
**candidate** for L2, not a final conflict verdict.

(verify: python3 -m pytest tests/test_spec_interference.py::test_l1_file_overlap -q)

## R2 Write-read conflict detection (literal simulation)

For needle pairs on the same file (from ledger-attested mutations or `.apatch/spec_run.json`
planned `requirements`), detect WR: simulate A's literal replace once; B's `find_text` must
be absent afterward. Non-literal `match_mode` adds a warning, not silent pass.

(verify: python3 -m pytest tests/test_spec_interference.py::test_wr_conflict_literal -q)

## R3 Write-write mutex detection without false cycles

WW when simulated replace regions overlap or both needles share the same `find_text` on one
file. WW is reported as `write_write` with `severity: high` and **does not** add bidirectional
ordering edges to the conflict graph (mutex / refactor_needles semantics per RFP-014).

(verify: python3 -m pytest tests/test_spec_interference.py::test_ww_mutex_no_cycle_edge -q)

## R4 Conflict graph, safe_order, cycles, and risk_score

Build directed edges only from WR conflicts (A must precede B). Detect cycles; compute
topological `safe_order` when acyclic. Aggregate `risk_score` per RFP-014 §6
(0.0 none → 1.0 irreconcilable cycle).

(verify: python3 -m pytest tests/test_spec_interference.py::test_graph_safe_order_and_risk -q)

## R5 MCP tool `apatch_spec_interference`

MCP tool returns `conflicts[]`, `conflict_graph`, `has_cycle`, `cycles[]`, `safe_order`,
`risk_score`, `summary`, `data_sources`, `warnings[]`. Registered in `tests/test_mcp.py`;
`mcp_health.tool_count` becomes **67**.

(verify: python3 -m pytest tests/test_spec_interference.py::test_mcp_spec_interference_registered -q)

## R6 CLI `apatch spec interference`

CLI parity with MCP: `apatch spec interference --spec SPEC-A --spec SPEC-B` (repeatable
`--spec`) or JSON output via `--json`.

(verify: python3 -m pytest tests/test_spec_interference.py::test_cli_spec_interference -q)

## R7 Dogfood — LEDGER-ACTOR × COVERAGE on shared module

Integration test on apatch repo data: `SPEC-LEDGER-ACTOR-1` and `SPEC-COVERAGE-1` both
touch `apatch/spec_coverage.py`; interference report includes that file overlap and
returns `ok: true` with deterministic `safe_order` (length 2, no cycle).

(verify: python3 -m pytest tests/test_spec_interference.py::test_dogfood_ledger_coverage_overlap -q)

## Non-goals

- Level 3 sandbox cross-verify (Phase 2).
- `apatch_spec_schedule` and spec_run preflight gate (Phase 1.5).
- Automatic needle merge or refactor.
