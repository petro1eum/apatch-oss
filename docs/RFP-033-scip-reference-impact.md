# RFP-033 — SCIP cross-file reference impact

> **Status:** Draft v1 · **Date:** 2026-06-25 · **Owner:** apatch core
> **Package context:** 0.7.x — surface cross-file refactoring impact on attested requirements
> **Depends on:** [Attestation runtime](governed-runtime-invariants.md) · [RFP-032](./RFP-032-symbol-granular-anchoring.md) (symbol anchors)
> **Origin:** apatch is used heavily for refactoring. Symbol anchors (RFP-032) make a requirement stale only when *its own* symbol changes — but they see one file at a time. When you refactor `foo()` in `a.py`, a requirement attested against `bar()` in `b.py` that *calls* `foo()` stays green, because `b.py` was never touched. That is a real provenance blind spot: the attested behaviour of `bar` depends on code that changed elsewhere. SCIP (Sourcegraph Code Intelligence Protocol, Apache-2.0) indexers emit a cross-file reference graph — every place a symbol is used, resolved semantically. Ingesting a `.scip` lets apatch answer "which attested requirements reference the symbol I just changed?" This is **advisory** for refactoring, never an automatic staleness trigger — auto-cascading drift through the call graph would recreate the file_drift cascade RFP-032 just removed.

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| A33-A | apatch can build a cross-file reference graph from a `.scip` index — for any symbol, the files (and definition sites) that define it and the files that reference it | MUST |
| A33-B | `.scip` ingest is in-process and adds no hard runtime dependency: a minimal protobuf-wire decoder reads the needed subset (documents, occurrences, symbol, symbol_roles, range) and skips unknown fields, so apatch never requires the `protobuf` runtime or a generated stub on the verify path | MUST |
| A33-C | SCIP symbols bridge to apatch's `{file → symbol}` anchors — the trailing identifier of a SCIP symbol descriptor is extracted, and a `(relative_path, name)` anchor resolves to its SCIP symbol id via the definition occurrence | MUST |
| A33-D | Given a changed symbol, apatch surfaces the attested requirements whose anchored symbol references it across files — refactoring `foo` flags a requirement anchored to a `bar` that calls `foo`, while an unrelated `baz` is not flagged (the go/no-go) | MUST |
| A33-E | Reference impact is advisory: it is reported as a warning and never marks a requirement `stale` or blocks attestation by itself | MUST |
| A33-F | Safe fallback — an absent, empty, or unparsable `.scip` yields an empty impact set and never raises; the verify path keeps working exactly as it does today with no index present | MUST |
| A33-G | The `.scip` is produced from a real indexer (`scip-python`) run out of band, and impact warnings are wired into `verify_run`/`spec_status` output | SHOULD |

---

## 7. SPEC projection

| RFP id | SPEC | Disposition |
|--------|------|-------------|
| A33-A | SPEC-SCIP-IMPACT-1 (R2) | covered |
| A33-B | SPEC-SCIP-IMPACT-1 (R1) | covered |
| A33-C | SPEC-SCIP-IMPACT-1 (R3) | covered |
| A33-D | SPEC-SCIP-IMPACT-1 (R4) | covered |
| A33-E | SPEC-SCIP-IMPACT-1 (R4) | covered |
| A33-F | SPEC-SCIP-IMPACT-1 (R5) | covered |
| A33-G | SPEC-SCIP-IMPACT-2 ✅ R1-R3 | done (phase 2: `scip-python` producer `apatch scip index` + `scip_impact_workspace` glue + advisory in `apatch_scip`/`verify_run`) |
| (native) | SPEC-SCIP-IMPACT-3 ✅ R1-R3 | done — native AST import-resolved graph is the default (`model_source: native`), no `scip-python` needed; a real `.scip` wins when present |

<!-- Implemented and notarized via the apatch governed cycle. -->
