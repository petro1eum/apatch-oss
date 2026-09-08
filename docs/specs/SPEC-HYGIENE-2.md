# SPEC-HYGIENE-2 — GC report CLI & MCP (RFP-016 Phase 2)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-HYGIENE-2`
> **Anchors:** [RFP-016](../RFP-016-runtime-hygiene.md) Phase 2 · [SPEC-HYGIENE-1](./SPEC-HYGIENE-1.md)

## 0. Motivation

Phase 2 exposes Phase 1 `gc_report` to agents and humans. **GC still has no delete
permission** — report is the source of truth before SPEC-GC-1 enables `--safe`.

Success criterion:

```text
apatch gc --dry-run --json
apatch_gc(target_dir='.', mode='report')
# → same classified payload; no files removed
```

Non-goals: `--safe`, `--rotate` (SPEC-GC-1); hygiene `critical` blocking (SPEC-GC-1).

## R1 CLI apatch gc default dry-run

`apatch gc` with no destructive flags calls `gc_report` and prints human summary.
`--json` emits full §4.2 dict. `--safe` and `--rotate` must error with message pointing
to SPEC-GC-1 / future version until implemented.

(verify: python3 -m pytest tests/test_gc_cli.py::test_gc_cli_dry_run_default -q)

## R2 MCP apatch_gc report mode

MCP tool `apatch_gc(target_dir, mode="report")` returns `ok`, report body, and
`state_update`. Only `mode=report` is registered in Phase 2; other modes return
`error_type=RUNTIME_TRANSITION` or explicit not-implemented with `use_tool` hint.

(verify: python3 -m pytest tests/test_gc_cli.py::test_mcp_gc_report -q)

## R3 Report parity CLI and MCP

Same fixture workspace: CLI `--json` and MCP `apatch_gc` produce byte-identical
`classified` and `issues` keys (timestamps may differ).

(verify: python3 -m pytest tests/test_gc_cli.py::test_gc_cli_mcp_parity -q)

## R4 Registered write-path zero UNCLASSIFIED

On apatch dogfood fixture after HYGIENE-1 write-path coverage, `gc_report` reports
`UNCLASSIFIED: 0` for paths under `apatch/` produced by registered writers. Legacy
inferred paths may remain non-zero `inferred_count`.

(verify: python3 -m pytest tests/test_gc_cli.py::test_gc_report_zero_unclassified_apatch_writes -q)

## Non-goals

- Filesystem deletion
- Inference sunset block (SPEC-GC-1)
