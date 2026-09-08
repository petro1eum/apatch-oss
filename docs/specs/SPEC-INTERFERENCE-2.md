# SPEC-INTERFERENCE-2 — Cross-spec semantic cross-verify (RFP-014 Phase 2)

> **Status:** attested (2026-06-10) · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-INTERFERENCE-2`
> **Anchors:** [RFP-014](../RFP-014-spec-interference-detection.md), [SPEC-INTERFERENCE-1](./SPEC-INTERFERENCE-1.md)

## 0. Motivation

Level 1/2 interference (file overlap, WR/WW literal simulation) misses semantic breaks:
renamed symbols, deleted API surface, schema drift. Phase 2 adds **empirical cross-verify**:
backup workspace → apply source spec needles → run victim spec `(verify:)` commands → rollback.

Success criterion:

```text
apatch_spec_cross_verify(
  specs=['SPEC-CV-SOURCE', 'SPEC-CV-VICTIM'],
  target_dir='tests/fixtures/cross_verify',
)
# → semantic_conflicts[] when apply(A) breaks verify(B); workspace restored
```

Non-goals: `apatch_spec_run_multi`; LLM intent analysis; attested-ledger needles as apply source
(default: registry / inline planned needles only).

## R1 Sandbox apply with BackupManager

`apply_needles_sandboxed` backs up each touched file, applies literal `replace` needles from
registry/planned manifest, and returns `{session_id, applied[], failed[]}`. Non-literal needles
are skipped with `failed[]` entries, not silent pass.

(verify: python3 -m pytest tests/test_spec_cross_verify.py::test_apply_needles_sandboxed -q)

## R2 Run victim spec verify commands

After apply, `run_spec_verifies` loads victim SPEC markdown and runs each requirement's
verify command via `run_shell_verify`. Returns per-Rk `{requirement, verify, ok, error}`.

(verify: python3 -m pytest tests/test_spec_cross_verify.py::test_run_spec_verifies -q)

## R3 Rollback always restores workspace

Every cross-verify pair uses a dedicated backup session; `finally` block calls
`BackupManager.rollback_session` even when verify fails. Post-check: touched files match
pre-apply content.

(verify: python3 -m pytest tests/test_spec_cross_verify.py::test_cross_verify_rollback -q)

## R4 Semantic conflict report

`spec_cross_verify_workspace` runs ordered pairs `(source, victim)` for ≥2 specs. When a
victim verify fails after source apply, emit `semantic_cross_verify` conflict with
`error_type: SPEC_CROSS_VERIFY_FAILED` and `recommended_action: refactor_needles`.

(verify: python3 -m pytest tests/test_spec_cross_verify.py::test_semantic_conflict_detected -q)

## R5 MCP tool `apatch_spec_cross_verify`

MCP tool returns `checks[]`, `semantic_conflicts[]`, `all_passed`, `pairs_tested`.
Registered in `tests/test_mcp.py`; `mcp_health.tool_count` becomes **69**.

(verify: python3 -m pytest tests/test_spec_cross_verify.py::test_mcp_spec_cross_verify_registered -q)

## R6 CLI `apatch spec cross-verify`

CLI parity: `apatch spec cross-verify --spec SPEC-A --spec SPEC-B --json`.

(verify: python3 -m pytest tests/test_spec_cross_verify.py::test_cli_spec_cross_verify -q)

## R7 Fixture integration — apply breaks victim verify

Fixture specs under `tests/fixtures/cross_verify/`: source needle renames shared token;
victim verify asserts original token. Cross-verify reports exactly one semantic conflict;
rollback leaves fixture file unchanged.

(verify: python3 -m pytest tests/test_spec_cross_verify.py::test_fixture_integration -q)

## Non-goals

- Multi-spec orchestrator (`apatch_spec_run_multi`, Phase 3).
- Using attested ledger needles as default apply source (registry/planned only).
- Level 4 domain-tag heuristics.
