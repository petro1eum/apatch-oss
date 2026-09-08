# SPEC-DIAGNOSTIC-GRAPH-1 — Unified Diagnostic schema & adapters

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-DIAGNOSTIC-GRAPH-1`  
> **Anchors:** [RFP-022 Phase 1](../RFP-022-unified-diagnostics-knowledge-graph.md) · extends [SPEC-BUILD-DIAGNOSE-1](./SPEC-BUILD-DIAGNOSE-1.md) · [transformation matrix](../apatch-transformation-matrix.md)

## 0. Motivation

Verify failures today surface as opaque stderr (clang, pytest, grep) or separate taxonomy
fields. Agents re-parse logs each cycle. Phase 1 introduces a versioned **`Diagnostic[]`**
model and adapters so the spine exposes structured findings with `recommended_action`
aligned to [failure taxonomy v2](./SPEC-FAILURE-TAXONOMY-2.md).

```text
Apply → Verify (fail) → collect_diagnostics → diagnostics[]
```

Suggestions remain **advisory** — no auto-apply ([RFP-018](../RFP-018-build-diagnose.md)).

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| P1-A | R2 | covered |
| P1-B | R3 | covered |
| P1-C | R5 | covered |
| P1-D | R7 | covered |
| P1-E | R8 | covered |
| P2-A | — | waiver: implemented in [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) |
| P2-B | — | waiver: implemented in [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) |
| P2-C | — | waiver: implemented in [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) |
| P3-A | — | waiver: implemented in [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md) |
| P3-B | — | waiver: implemented in [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md) |
| P3-C | — | waiver: implemented in [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md) |

## R1 Diagnostic schema

Module `apatch/diagnostics/schema.py` defines `schema_version=1` and required fields:
`id`, `source`, `type`, `severity`, `message`, `recommended_action`. Optional:
`location`, `session_id`, `verify_command`, `evidence`, `suggestions`, `edges`.
`validate_diagnostic(dict)` rejects unknown required omissions.

(verify: python3 -m pytest tests/test_diagnostic_graph.py::test_r1_schema_validate_required_fields -q)

## R2 Clang adapter

`apatch/diagnostics/adapters/clang.py` wraps `build_diagnose.parse_compiler_output` and
emits normalized diagnostics with `source: clang`, preserving `missing_member` type and
RFP-018 suggestions.

(verify: python3 -m pytest tests/test_diagnostic_graph.py::test_r2_clang_adapter_missing_member -q)

## R3 Pytest adapter

`apatch/diagnostics/adapters/pytest.py` parses pytest failure lines
(`FAILED path::node - …`) into diagnostics with `source: pytest`, `type: test_failure`,
and `location.file` / `location.symbol` (node id).

(verify: python3 -m pytest tests/test_diagnostic_graph.py::test_r3_pytest_adapter_failed_node -q)

## R4 Spec and trust adapters

`spec_adapter` maps spec lint / coverage violations to `source: spec`,
`type: spec_violation`. `trust_adapter` maps `classify_failure` results to
`source: trust|sandbox` with taxonomy-aligned `recommended_action`.

(verify: python3 -m pytest tests/test_diagnostic_graph.py::test_r4_spec_adapter_violation tests/test_diagnostic_graph.py::test_r4_trust_adapter_notarization -q)

## R5 collect_diagnostics spine integration

`apatch/diagnostics/collect.py` exposes `collect_diagnostics(verify_result, …) → list`.
`enrich_verify_failure` delegates here; `verify_run` / `apply_session` failure responses
include top-level `diagnostics[]`. Backward-compatible `build_diagnose` block during
deprecation window.

(verify: python3 -m pytest tests/test_diagnostic_graph.py::test_r5_collect_from_clang_log tests/test_diagnostic_graph.py::test_r5_verify_run_response_has_diagnostics -q)

## R6 Session diagnostic artifacts

`write_session_diagnostics` persists `.apatch/diagnostics/<session_id>.json` (array of
validated diagnostics) when `write_artifacts=true`.

(verify: python3 -m pytest tests/test_diagnostic_graph.py::test_r6_session_artifact_written -q)

## R7 Agent discoverability

`diagnose_playbook()` in `agent_playbooks.py` registered as MCP resource
`apatch://playbook/diagnose`; listed in `mcp_playbook_index`. Documents closed loop:
read `diagnostics[0].recommended_action` — do not parse stderr.

(verify: python3 -m pytest tests/test_diagnostic_graph.py::test_r7_diagnose_playbook_resource tests/test_agent_playbooks.py::test_diagnose_playbook_in_index -q)

## R8 Phase 1 integration suite

RFP-022 P1-E: full Phase 1 module `tests/test_diagnostic_graph.py` green (schema, adapters, spine, playbook).

(verify: python3 -m pytest tests/test_diagnostic_graph.py -q)

## Non-goals

- MSVC / rustc / pyright adapters (Phase 1)
- Auto needle synthesis from suggestions
- Populating `edges.symbols` (see [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md))
- AGL `DIAGNOSTIC` registry class (RFP-016 follow-up)

## Depends on

- [SPEC-BUILD-DIAGNOSE-1](./SPEC-BUILD-DIAGNOSE-1.md) attested (clang reference)
- [SPEC-FAILURE-TAXONOMY-2](./SPEC-FAILURE-TAXONOMY-2.md) attested

## Blocks

- [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md)
- [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md)
