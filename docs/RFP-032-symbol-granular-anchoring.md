# RFP-032 — Symbol-granular attestation anchoring

> **Status:** Draft v1 · **Date:** 2026-06-25 · **Owner:** apatch core
> **Package context:** 0.7.x — make attestation drift track code symbols, not whole files
> **Depends on:** [Attestation runtime](governed-runtime-invariants.md) · [RFP-007](./RFP-007-executable-specifications.md) (executable specs)
> **Origin:** Field evidence — implementing a multi-Rk spec where several requirements mutate one shared test file, attesting requirement N rehashes the **whole file** and flips every earlier requirement to `stale` (file_drift), even though each requirement's own function/class is untouched. Closing a 6-Rk spec then costs ~18 extra manual rebind calls. apatch already loads tree-sitter grammars and extracts function boundaries (`boundary_ast`); it just anchors attestation at file granularity.

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| A32-A | An attestation can anchor a requirement to the specific code symbols (functions/classes) its mutation touched, recorded as a {file → symbol → content-hash} map, not only whole-file hashes | MUST |
| A32-B | Symbol extraction is deterministic per file using the tree-sitter grammars apatch already loads, and falls back safely (empty symbol set → file-level behaviour) for unsupported languages or unparsable files | MUST |
| A32-C | A mutation's touched line range maps to its enclosing top-level symbol(s) | MUST |
| A32-D | Drift for a symbol-anchored requirement is computed at symbol granularity: it is stale only when one of its anchored symbols changes; a touched file with no resolvable symbols falls back to file-level drift | MUST |
| A32-E | Two requirements that touch different symbols in the same shared file do not drift each other — editing one symbol never makes a requirement anchored to another symbol stale (the go/no-go) | MUST |
| A32-F | Backward compatible — requirements attested with file hashes only keep their exact current drift behaviour; symbol anchoring is additive and never makes a previously-attested requirement falsely stale | MUST |
| A32-G | Symbol anchors are persisted in the TrustChain attestation payload and consumed by spec coverage, so symbol drift is derived from the signed ledger, not an agent-writable field | SHOULD |

---

## 7. SPEC projection

| RFP id | SPEC | Disposition |
|--------|------|-------------|
| A32-A | SPEC-SYMBOL-ANCHOR-1 (R3) | covered |
| A32-B | SPEC-SYMBOL-ANCHOR-1 (R1) | covered |
| A32-C | SPEC-SYMBOL-ANCHOR-1 (R2) | covered |
| A32-D | SPEC-SYMBOL-ANCHOR-1 (R3) | covered |
| A32-E | SPEC-SYMBOL-ANCHOR-1 (R4) | covered |
| A32-F | SPEC-SYMBOL-ANCHOR-1 (R5) | covered |
| A32-G | SPEC-SYMBOL-ANCHOR-2 | deferred (phase 2: attest payload + spec_coverage wiring) |
