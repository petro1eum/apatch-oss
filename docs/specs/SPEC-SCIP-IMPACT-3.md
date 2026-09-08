# SPEC-SCIP-IMPACT-3 — native cross-file reference resolver (no external indexer)

> **apatch artifact:** `spec:SPEC-SCIP-IMPACT-3`  
> **Anchors:** [RFP-033](../RFP-033-scip-reference-impact.md) · [SPEC-SCIP-IMPACT-2](./SPEC-SCIP-IMPACT-2.md) (producer)

## 0. Motivation

Phase 2 produced the index via `scip-python`, but nobody installs it — so the impact
feature was wired yet dormant (`index_present: false` everywhere). apatch already parses
code; it should compute the cross-file reference graph **natively, in process**, so impact
works out of the box on any Python repo. A real `.scip` (when present) still wins as the
more precise / multi-language source.

## R1 Native import-resolved reference graph (verify: python3 -m pytest tests/test_native_refs.py::test_native_resolves_import_and_attribute -q)

`native_reference_model` builds the cross-file graph from the Python AST, import-resolved
(not bare-name): `from a import foo; foo()` and `import a; a.foo()` both resolve to `a.py`'s
`foo`. It mirrors `ScipModel`'s read interface so the Phase-1 impact engine consumes it
unchanged.

## R2 Impact works natively, no scip-python (verify: python3 -m pytest tests/test_native_refs.py::test_native_impact_flags_only_referencing -q)

With no `.scip` index and no `scip-python`, `scip_impact_workspace` builds the native model
(`model_source: "native"`, `index_present: false`) and flags only the attested requirements
that reference a changed symbol cross-file — never the unrelated ones, never as a staleness
flag (A33-E).

## R3 A real .scip wins; verify stays scip-only (verify: python3 -m pytest tests/test_native_refs.py::test_scip_index_wins_over_native tests/test_scip_phase2.py::test_verify_run_no_index_no_scip_field -q)

When a `.scip` index is present it is preferred (`model_source: "scip"`). The native resolver
is on-demand (`apatch_scip` / `apatch scip impact`); `verify_run` passes `allow_native=False`
so it never rebuilds the native graph on the hot path — it attaches `scip_impact` only from a
precomputed `.scip`.
