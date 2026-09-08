# SPEC-KNOWLEDGE-GRAPH-1 — Session knowledge graph & product views

> **Status:** Draft v1 · **Owner:** apatch product  
> **apatch artifact:** `spec:SPEC-KNOWLEDGE-GRAPH-1`  
> **Anchors:** [RFP-022 Phase 3](../RFP-022-unified-diagnostics-knowledge-graph.md) · [RFP-020](../RFP-020-three-views.md) · [SPEC-PROJECT-STATUS-1](./SPEC-PROJECT-STATUS-1.md)

## 0. Motivation

Diagnostics and attestation facts exist in separate modules. Phase 3 exposes a **read-only
knowledge graph** joining Intent, requirements, files, diagnostics, and attestation for a
session — turning apatch from mutation runtime into queryable platform substrate (HC
attribution narrative).

```text
Intent ↔ Spec ↔ Files ↔ Diagnostics ↔ Evidence ↔ Attestation
```

No hosted graph DB — join over `.apatch/`, ledger, and existing DTOs.

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| P1-A | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-B | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-C | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-D | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P1-E | — | waiver: implemented in [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) |
| P2-A | — | waiver: implemented in [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) |
| P2-B | — | waiver: implemented in [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) |
| P2-C | — | waiver: implemented in [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) |
| P3-A | R1 | covered |
| P3-B | R4 | covered |
| P3-C | R6 | covered |

## R1 knowledge_graph_for_session

`apatch/knowledge_graph.py` exposes `knowledge_graph_for_session(session_id, target_dir)`
returning `{schema_version, nodes[], edges[]}`. Node kinds include at minimum: `Intent`,
`Diagnostic`, `Attestation` or `Rollback`. From a completed or failed governed session
with diagnostics artifact, graph connects Intent node to ≥1 Diagnostic.

(verify: python3 -m pytest tests/test_knowledge_graph.py::test_r1_session_graph_intent_to_diagnostic -q)

## R2 Requirement and file nodes

Graph includes `Requirement` nodes when session `artifacts` bind a spec requirement
(e.g. dict `{kind: spec, id: SPEC-KNOWLEDGE-GRAPH-1, ref: R2}`).
`File` nodes appear for paths in diagnostic `edges.files` or apply report touched files.
Edge kinds: `DIAGNOSED_AS`, `COVERS`, `MUTATED`.

(verify: python3 -m pytest tests/test_knowledge_graph.py::test_r2_requirement_and_file_nodes -q)

## R3 project_status extension

`project_status_workspace()` includes `diagnostics_summary`:
`{count, top_type, last_session_id, artifact_path}` when diagnostics exist.

(verify: python3 -m pytest tests/test_knowledge_graph.py::test_r3_project_status_diagnostics_summary -q)

## R4 apatch status JSON

CLI `apatch status --json` and MCP `apatch_project_status` surface the same
`diagnostics_summary` block (parity with SPEC-PROJECT-STATUS-1 patterns).

(verify: python3 -m pytest tests/test_knowledge_graph.py::test_r4_status_json_diagnostics_summary -q)

## R5 MCP graph query

MCP tool `apatch_knowledge_graph(session_id=…)` returns the R1 graph DTO (or error if
session unknown). Registered in MCP tool table and `mcp_setup.md`.

(verify: python3 -m pytest tests/test_knowledge_graph.py::test_r5_mcp_knowledge_graph_tool tests/test_mcp.py -k knowledge_graph -q)

## R6 Manager report diagnostic fact

`apatch report --format md` includes a **Verification & diagnostics** section citing
ledger-backed facts: last verify command, diagnostic count, top `type` — not raw stderr.

(verify: python3 -m pytest tests/test_knowledge_graph.py::test_r6_report_md_mentions_diagnostic_summary -q)

## Non-goals

- Neo4j / hosted graph service
- HC contribution scoring
- Real-time graph streaming
- Architect HTML graph visualization (optional follow-up to SPEC-REPORT-1)

## Depends on

- [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) attested
- [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) attested (recommended, not hard runtime dep for R1 minimal graph)
- [SPEC-PROJECT-STATUS-1](./SPEC-PROJECT-STATUS-1.md) attested

## Execution order

```text
SPEC-DIAGNOSTIC-GRAPH-1 → SPEC-CONTRACT-EDGES-1 → SPEC-KNOWLEDGE-GRAPH-1
```

Parallel (any time): `apatch://playbook/diagnose` content — owned by SPEC-DIAGNOSTIC-GRAPH-1 R7.
