# SPEC-APPLY-IDEMPOTENT-1 — re-applying an anchor-preserving replace is an idempotent skip

> **apatch artifact:** `spec:SPEC-APPLY-IDEMPOTENT-1`  
> **Anchors:** [RFP-005](../RFP-005-reference-monitor.md) · discharges reality `REC-6a1d10637598`

## 0. Motivation

When a `replace_file_content` mutation's `ReplacementContent` keeps the `TargetContent`
anchor (`new` contains `old` — e.g. insert-before/after an anchor line), the anchor
survives inside the file after the first apply. A naive re-apply re-matches that anchor
and re-inserts the new block, silently DUPLICATING function/command defs (reported as
`applied:N` rather than a skip). This is the same silent-corruption class the
overlapping-needles guard already defends against, and it bit the apatch dogfood itself
(reality record `REC-6a1d10637598`).

The matcher must detect the already-applied case and skip it (no write, no duplication).

## R1 anchor-preserving re-apply is skipped not duplicated (verify: python3 -m pytest tests/test_apply_idempotent.py -q) (discharges: REC-6a1d10637598)

`ASTMatcher.evaluate` returns a non-success `already_applied` result (so the candidate is
skipped, never written) when a REPLACE is anchor-preserving (`old != new` and `old in
new`) and the post-replacement block (`new`) is already present in the file. A genuine
first apply, and non-anchor-preserving replacements, are unaffected.
