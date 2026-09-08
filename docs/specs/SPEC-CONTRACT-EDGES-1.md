# SPEC-CONTRACT-EDGES-1 — Symbol and contract edges on diagnostics

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-CONTRACT-EDGES-1`  
> **Anchors:** [RFP-022 Phase 2](../RFP-022-unified-diagnostics-knowledge-graph.md) · [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md)

## 0. Motivation

Unified diagnostics ([SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md)) normalize
*what failed*. Phase 2 links failures to **contracts**: symbols, declaration files,
callers — so agents fix API drift without re-discovering structure from stderr alone.

Example: `SparseOmega::get_concept_name` → edges to header declaration + referencing files.

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| P1-A | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-B | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-C | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-D | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-E | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P2-A | R2 | covered |
| P2-B | R3 | covered |
| P2-C | R2 | covered |
| P3-A | — | waiver: implemented in [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md) |
| P3-B | — | waiver: implemented in [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md) |
| P3-C | — | waiver: implemented in [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md) |

## R1 resolve_symbol_edges

`apatch/diagnostics/edges.py` exposes `resolve_symbol_edges(diagnostic, target_dir) → dict`
with keys `symbols`, `files`, `requirements`, `artifacts`. No-op when diagnostic lacks
locatable symbol (returns empty lists, not error).

(verify: python3 -m pytest tests/test_contract_edges.py::test_r1_resolve_empty_when_no_symbol -q)

## R2 C++ missing_member edges

For `type: missing_member` clang diagnostics, `edges.symbols` includes the qualified
member; `edges.files` includes declaration header (via RFP-018 header scan) and at least
one referencing source file when found in workspace.

(verify: python3 -m pytest tests/test_contract_edges.py::test_r2_sparse_omega_missing_member_edges -q)

## R3 Impact subgraph link

When symbol resolves in `symbol_index`, diagnostic `edges` includes `impact_ref`:
`{target, kind, affected_files_count}` from `apatch_impact` (depth=1). MCP/CLI diagnostic
responses expose this under `diagnostics[].edges.impact_ref`.

(verify: python3 -m pytest tests/test_contract_edges.py::test_r3_impact_ref_on_python_symbol -q)

## R4 collect_diagnostics merges edges

`collect_diagnostics` calls `resolve_symbol_edges` for each diagnostic before return /
artifact write. Phase 1 diagnostics without edges remain valid.

(verify: python3 -m pytest tests/test_contract_edges.py::test_r4_collect_attaches_edges -q)

## R5 Markdown @sid edges (optional path)

When diagnostic `location.symbol` matches `@sid:claim_N` from compiled markdown,
`edges.artifacts` may include block hash from `knowledge_map.json` when map present.

(verify: python3 -m pytest tests/test_contract_edges.py::test_r5_sid_block_edge_when_map_present -q)

## Non-goals

- libclang / `compile_commands.json` (future)
- Pre-apply simulate blocking on impact (advisory warnings only, not in this spec)
- Auto `needles_hint` synthesis (suggestions stay advisory)

## Depends on

- [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) attested
- [SPEC-BUILD-DIAGNOSE-1](./SPEC-BUILD-DIAGNOSE-1.md) (C++ header scan)

## Blocks

- [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md)
