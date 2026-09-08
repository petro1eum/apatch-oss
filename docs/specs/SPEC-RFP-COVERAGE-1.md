# SPEC-RFP-COVERAGE-1 — RFP→SPEC traceability coverage

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-RFP-COVERAGE-1`  
> **Anchors:** [RFP-023](../RFP-023-rfp-spec-coverage.md) · [rfp-authoring.md](../rfp-authoring.md)

## 0. Motivation

Close requirements leakage: deterministic check that each RFP Acceptance row is
**covered** by a SPEC Rk or an explicit **waiver** in `## RFP traceability`.

## R0 RFP traceability gate (meta)

This spec's traceability is self-referential — RFP-023 rows map here.

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| R23-A | R4 | covered |
| R23-B | R3 | covered |
| R23-C | R3 | covered |
| R23-D | R7 | covered |
| R23-E | R6 | covered |
| R23-F | R5 | covered |

(verify: python3 -m pytest tests/test_rfp_coverage.py::test_r0_self_coverage_rfp_023 -q)

## R1 Parse RFP Acceptance table

`parse_rfp_acceptance(text)` returns rows `{id, criterion, level}` from `## Acceptance`.

(verify: python3 -m pytest tests/test_rfp_coverage.py::test_r1_parse_rfp_acceptance -q)

## R2 Parse SPEC traceability table

`parse_spec_traceability(text)` returns map `rfp_id → {spec_rk, disposition}`.

(verify: python3 -m pytest tests/test_rfp_coverage.py::test_r2_parse_spec_traceability -q)

## R3 Coverage computation

`rfp_spec_coverage(...)` returns `passed`, `gaps[]` for uncovered MUST, `waivers[]`, `covered[]`.

(verify: python3 -m pytest tests/test_rfp_coverage.py::test_r3_coverage_must_gap_and_waiver -q)

## R4 RFP lint

`rfp_lint(...)` errors when Acceptance section or Id column missing.

(verify: python3 -m pytest tests/test_rfp_coverage.py::test_r4_rfp_lint_missing_acceptance -q)

## R5 Fixture pair end-to-end

Fixture RFP+SPEC under `tests/fixtures/rfp_coverage/` — coverage PASS and MUST gap detected.

(verify: python3 -m pytest tests/test_rfp_coverage.py::test_r5_fixture_pair -q)

## R6 MCP tools registered

`apatch_rfp_lint` and `apatch_rfp_spec_coverage` in MCP tool list.

(verify: python3 -m pytest tests/test_mcp.py::test_mcp_tools_registered -q)

## R7 spec_run preflight

When SPEC contains `## RFP traceability`, `apatch_spec_run` runs coverage before Rk loop; blocks on failure.

(verify: python3 -m pytest tests/test_rfp_coverage.py::test_r7_spec_run_blocks_on_coverage_gap -q)

## Non-goals

- Signed waiver artifacts (RFP-023 Phase 2)
- LLM semantic equivalence
