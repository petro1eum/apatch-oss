# SPEC-SYMBOL-ANCHOR-1 — Symbol-granular attestation drift

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-SYMBOL-ANCHOR-1`
> **Anchors:** [RFP-032](../RFP-032-symbol-granular-anchoring.md)

## 0. Motivation

apatch anchors attestation to whole-file sha256, so any edit to a shared file drifts every requirement that touched it (file_drift), even when each requirement's own function/class is unchanged. This spec adds the symbol-granular machine — extract, map, and diff named symbols — so a requirement goes stale only when its own symbol changes. apatch already loads tree-sitter grammars and finds function boundaries (`boundary_ast`); this builds the named-symbol layer on top. Wiring it into the live attest payload and spec coverage is phase 2 (SPEC-SYMBOL-ANCHOR-2).

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A32-A | R3 | covered |
| A32-B | R1 | covered |
| A32-C | R2 | covered |
| A32-D | R3 | covered |
| A32-E | R4 | covered |
| A32-F | R5 | covered |
| A32-G | — | waiver: SPEC-SYMBOL-ANCHOR-2 (phase 2 attest payload + coverage wiring) |

(verify: python3 -m pytest tests/test_symbol_anchor.py::test_r0_symbol_anchor_rfp_coverage -q)

## R1 Deterministic symbol extraction

`extract_file_symbols(path)` returns `{name: {start_line, end_line, hash}}` for top-level functions and classes, parsed with the tree-sitter grammar apatch already loads for the file's language. `hash` is the sha256 of the symbol's source text. Unsupported languages or unparsable files return `{}` (the signal to use file-level behaviour). Extraction is deterministic for identical input.

(verify: python3 -m pytest tests/test_symbol_anchor.py::test_r1_extract_symbols -q)

## R2 Line-range to enclosing symbol

`symbols_for_line_range(path, start_line, end_line)` returns the names of the top-level symbols whose span overlaps `[start_line, end_line]` — the symbols a mutation touching that range belongs to. Empty when the range hits no symbol or the file is unparsable.

(verify: python3 -m pytest tests/test_symbol_anchor.py::test_r2_symbols_for_range -q)

## R3 Symbol-granular drift

`symbol_drift(anchors, root)` takes recorded anchors `{rel_path: {symbol: hash}}` and returns the sorted list of `rel::symbol` whose current extracted hash differs from the recorded hash (or whose symbol no longer exists). A symbol whose hash still matches is not reported. This is the symbol-level analogue of `compute_drift`.

(verify: python3 -m pytest tests/test_symbol_anchor.py::test_r3_symbol_drift -q)

## R4 Shared-file symbol isolation

Two requirements anchored to different symbols in one shared file do not drift each other: after editing symbol `foo`, `symbol_drift` for an anchor on `bar` returns empty while an anchor on `foo` reports `foo`. This is the go/no-go — the file_drift cascade is gone at symbol granularity.

(verify: python3 -m pytest tests/test_symbol_anchor.py::test_r4_shared_file_symbol_isolation -q)

## R5 Backward-compatible fallback

`requirement_stale_with_symbols(file_hashes, symbol_anchors, root)` returns stale exactly as today when `symbol_anchors` is absent/empty (pure file-hash `compute_drift`), and otherwise is stale only when an anchored symbol drifted. A previously-attested requirement with no symbol anchors keeps its exact current behaviour — symbol anchoring never makes it falsely stale.

(verify: python3 -m pytest tests/test_symbol_anchor.py::test_r5_file_hash_fallback_compat -q)

## Non-goals

- Wiring symbol anchors into the live attest payload and spec_coverage (SPEC-SYMBOL-ANCHOR-2).
- Cross-repo symbol resolution / SCIP ingest (separate RFP).
- Sub-symbol (statement / control-flow / data-flow) granularity.