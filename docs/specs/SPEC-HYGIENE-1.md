# SPEC-HYGIENE-1 — Artifact registry & advisory report (RFP-016 Phase 1)

> **Status:** Draft v2 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-HYGIENE-1`
> **Anchors:** [RFP-016](../RFP-016-runtime-hygiene.md) Phase 1 · [SPEC-HYGIENE-CORE](./SPEC-HYGIENE-CORE.md) · [RFP-016-IMPLEMENTATION](./RFP-016-IMPLEMENTATION.md)

## 0. Motivation

Phase 1 establishes **law before enforcement**: every apatch write-path registers class,
lease fields, and **lineage** (APG seed) in one hook. `gc_report` classifies the workspace
and returns RFP-016 §4.2 JSON **without deleting or moving files**.

Success criterion:

```text
apatch_doctor → hygiene.status, orphan_count, unclassified_count
apatch gc --dry-run → classified report, zero filesystem mutations
```

Non-goals: CLI/MCP surface beyond doctor (SPEC-HYGIENE-2); delete/rotate (SPEC-GC-1);
`session_end` lease release (SPEC-SESSION-LIFECYCLE-1).

## R1 Artifact taxonomy module

`apatch.artifact_governance` exposes class enum (`STATE`, `LEDGER`, `REGISTRY`,
`RUN_STATE`, `HISTORY`, `EPHEMERAL`, `DEBUG`, `GARBAGE`, `UNREGISTERED`), default
`gc_policy` per class, and `tier(class) -> CORE | MANAGED | TRANSIENT`.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_taxonomy_classes -q)

## R2 register_artifact persistence

`register_artifact(workspace, path, contract)` appends JSON lines to
`.apatch/registry/artifacts.jsonl` (flat fallback: `.apatch/artifacts.jsonl`). Validates
contract via SPEC-HYGIENE-CORE lineage + class rules. Failure raises and must abort the
calling write-path.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_register_persists_jsonl -q)

## R3 Provenance edge log co-append

Each successful registration appends one line to `.apatch/registry/provenance.jsonl`
(flat: `.apatch/provenance.jsonl`) with `artifact_id`, `path`, ISO timestamp, and full
`lineage` object. Append-only; no in-process graph index.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_provenance_edge_append -q)

## R4 Write-path registration coverage

Minimum wired write-paths (see [RFP-016-IMPLEMENTATION](./RFP-016-IMPLEMENTATION.md) audit
table): session state save, events ledger path touch, generate/write_jsonl, apply_session
backup + apply_session state, write lease, spec_run persist, and the project memory
index. Integration test creates artifacts through each path and asserts registry + provenance lines exist with
`provenance: registered`.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_write_path_registry_coverage -q)

## R5 Retroactive infer scanner

`classify_unregistered(workspace)` assigns `provenance: inferred` using closed path rules
(e.g. `patches-*.jsonl` → EPHEMERAL, `.apatch/_bench*.jsonl` → GARBAGE). Does not write
registry; merged into `gc_report` output only. Unknown paths → `UNCLASSIFIED` count.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_infer_scanner_closed_rules -q)

## R6 gc_report advisory engine

`gc_report(workspace) -> dict` matches RFP-016 §4.2 shape (`status`, `classified`,
`issues[]`, `size_reclaimable`, `recommendation`). **Must not** delete, move, or truncate
any file (assert mtime/size unchanged in test). Phase 1 recommendation is always report-only
(`apatch gc --dry-run`).

(verify: python3 -m pytest tests/test_artifact_governance.py::test_gc_report_dry_run_no_mutation -q)

## R7 doctor hygiene field

`run_doctor` / `build_doctor_payload` includes `hygiene: { status, orphan_count,
unclassified_count, inferred_count, gc_recommendation, layout }`. Phase 1:
`status=degraded` when `orphan_count + unclassified_count + inferred_count > 0`, else
`clean`. Does not block `apatch_spec_run`.

(verify: python3 -m pytest tests/test_artifact_governance.py::test_doctor_hygiene_field -q)

## Non-goals

- `apatch gc` CLI subcommand (SPEC-HYGIENE-2)
- `apatch_gc` MCP tool (SPEC-HYGIENE-2)
- Filesystem delete (SPEC-GC-1)
