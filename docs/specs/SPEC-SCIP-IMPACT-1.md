# SPEC-SCIP-IMPACT-1 — SCIP cross-file reference impact

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-SCIP-IMPACT-1`
> **Anchors:** [RFP-033](../RFP-033-scip-reference-impact.md)

## 0. Motivation

Symbol anchors (SPEC-SYMBOL-ANCHOR-1) make a requirement stale only when its own symbol changes, but they see one file at a time. Refactoring `foo()` in `a.py` leaves a requirement attested against `bar()` in `b.py` green even when `bar` calls `foo` — a cross-file provenance blind spot. This spec ingests a SCIP index (`.scip`, Apache-2.0) to build a cross-file reference graph and surface, for a changed symbol, the attested requirements whose anchored symbol references it. Impact is **advisory** (a warning), never an automatic staleness trigger: auto-cascading drift through the call graph would recreate the file_drift cascade RFP-032 removed. Ingest is a minimal in-process protobuf-wire decoder — the `protobuf` runtime is not needed, with graceful fallback when no index is present. Running the indexer and wiring warnings into verify output is phase 2 (the follow-on spec).

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A33-A | R2 | covered |
| A33-B | R1 | covered |
| A33-C | R3 | covered |
| A33-D | R4 | covered |
| A33-E | R4 | covered |
| A33-F | R5 | covered |
| A33-G | — | waiver: SPEC-SCIP-IMPACT-2 (phase 2 scip-python producer + verify/spec_status wiring) |

(verify: python3 -m pytest tests/test_scip_impact.py::test_r0_scip_impact_rfp_coverage -q)

## R1 Zero-dependency .scip decode

`decode_scip(blob)` parses the SCIP protobuf-wire subset we need — `Index.documents` (field 2), `Document.relative_path` (1) and `occurrences` (2), `Occurrence.range` (1), `symbol` (2), `symbol_roles` (3) — using a minimal in-process varint/length-delimited reader that skips unknown fields. It imports no `protobuf` runtime and no generated stub. Malformed input raises no exception to callers of `load_scip_model`; `decode_scip` returns the documents it could read. Validated against a real `.scip` blob produced by a SCIP indexer (committed fixture).

(verify: python3 -m pytest tests/test_scip_impact.py::test_r1_decode_scip -q)

## R2 Cross-file reference graph

`ScipModel` built from `decode_scip` answers, for any SCIP symbol id, `definitions(symbol)` (files whose occurrence carries the Definition role bit `0x1`) and `references(symbol)` (files with a non-definition occurrence), plus `references_in_file(rel_path)` → `[(symbol, start_line)]`. For the fixture, `foo` is defined in `a.py` and referenced from `b.py`.

(verify: python3 -m pytest tests/test_scip_impact.py::test_r2_reference_graph -q)

## R3 Bridge SCIP symbols to apatch anchors

`symbol_local_name(scip_symbol)` returns the trailing identifier of a SCIP descriptor (`scip-python python repo 1.0 \`a\`/foo().` → `foo`; type `Beta#` → `Beta`; method `Beta#m().` → `m`). `resolve_symbol(model, rel_path, name)` returns the SCIP symbol id whose Definition occurrence is in `rel_path` and whose local name is `name`, or `None`. This bridges apatch's `{file → symbol}` anchors (RFP-032) to SCIP symbol ids.

(verify: python3 -m pytest tests/test_scip_impact.py::test_r3_bridge -q)

## R4 Cross-file refactor impact (go/no-go)

`scip_impacted_requirements(changed_anchors, requirement_anchors, model, root)` takes changed `(file, name)` anchors and other requirements' `{req: {file: {name: hash}}}` anchors. For each changed symbol it resolves the SCIP id, finds reference occurrences, and reports a requirement when its anchored symbol's body (tree-sitter span from RFP-032) encloses a reference to the changed symbol. Editing `foo` (defined in `a.py`) flags the requirement anchored to `bar` in `b.py` (which calls `foo`) and does **not** flag the requirement anchored to the unrelated `baz`. Each report is a warning; the function never sets `stale` and never mutates ledger state.

(verify: python3 -m pytest tests/test_scip_impact.py::test_r4_refactor_impact_go_no_go -q)

## R5 Safe fallback without an index

`load_scip_model(root)` returns `None` when no `.scip` exists, is empty, or cannot be parsed — never raising. `scip_impacted_requirements` with `model=None` returns `[]`. The verify path therefore keeps its exact current behaviour when SCIP is not in use; reference impact is purely additive.

(verify: python3 -m pytest tests/test_scip_impact.py::test_r5_safe_fallback -q)

## Non-goals

- Running the `scip-python` indexer to produce the `.scip`, and wiring impact warnings into `verify_run`/`spec_status` (SPEC-SCIP-IMPACT-2).
- Auto-marking requirements stale from reference impact — it is advisory by design.
- Type/dataflow-level impact or non-SCIP indexers (Kythe, SCIP variants for other languages are supported only insofar as the same `.scip` format is emitted).

<!-- R0-R5 attested via the apatch governed cycle (RFP-033). -->
