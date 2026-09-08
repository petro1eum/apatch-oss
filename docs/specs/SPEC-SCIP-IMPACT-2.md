# SPEC-SCIP-IMPACT-2 — SCIP Phase 2: produce the index + surface advisory impact

> **apatch artifact:** `spec:SPEC-SCIP-IMPACT-2`  
> **Anchors:** [RFP-033](../RFP-033-scip-reference-impact.md) (A33-G) · [SPEC-SCIP-IMPACT-1](./SPEC-SCIP-IMPACT-1.md) (Phase 1)

## 0. Motivation

Phase 1 (SPEC-SCIP-IMPACT-1) ingests a `.scip` and computes cross-file reference impact —
but nothing produced the index or surfaced the impact, so it was a tested library nobody
called. Phase 2 (A33-G) adds the out-of-band producer (`scip-python`) and the end-to-end
glue that feeds the impact engine (changed symbols from the diff + the attested
requirements' guarded-file symbols), surfaced via `apatch_scip` and `verify_run`. Stays
advisory: no hard runtime dependency, never marks a requirement stale, never blocks.

## R1 Producer is graceful (verify: python3 -m pytest tests/test_scip_phase2.py::test_producer_graceful_without_scip_python -q)

`produce_scip_index` runs `scip-python` to write `.apatch/scip/index.scip`, and degrades to
`ok: False` with a note (never raises) when scip-python is not installed — no hard runtime
dependency on the verify path (A33-B).

## R2 Advisory impact glue (verify: python3 -m pytest tests/test_scip_phase2.py::test_impact_flags_referencing_requirement_only -q)

`changed_symbols_since` reads the symbols changed in a `git diff`, and `scip_impact_workspace`
reports which ATTESTED requirements reference them cross-file via the `.scip` graph — only
the referencing requirement, never the unrelated one, and never as a staleness flag (A33-E).

## R3 Surfaced + safe fallback (verify: python3 -m pytest tests/test_scip_phase2.py::test_impact_fallback_without_index tests/test_scip_phase2.py::test_apatch_scip_tool_registered tests/test_scip_phase2.py::test_verify_run_no_index_no_scip_field -q)

The `apatch_scip` MCP tool (`action='index'|'impact'`) and `apatch scip` CLI expose Phase 2,
and `verify_run` attaches an advisory `scip_impact` only when an index is present. With no
index (or no scip-python) impact is `index_present: False` with no warnings and `verify_run`
is unaffected (A33-E).
