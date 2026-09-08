# SPEC-LAYOUT-1 — Structured .apatch layout migrate (RFP-016 Phase 5)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-LAYOUT-1`
> **Anchors:** [RFP-016](../RFP-016-runtime-hygiene.md) Phase 5 (optional)

## 0. Motivation

Optional structured `.apatch/` layout improves operator ergonomics. Migration is **separate
from GC** (`apatch layout migrate`, not `gc --migrate`). Runtime resolves paths via
`apatch_paths` with flat fallback until migrate runs.

Success criterion:

```text
apatch layout migrate --dry-run
# → plan moves flat → structured; no gc delete
```

Non-goals: changing artifact contracts; remote storage.

## R1 apatch_paths resolution

`apatch_paths.workspace_paths(root) -> LayoutPaths` exposes resolved paths for session
state, artifacts registry, provenance log, events, backups — for both `layout=flat` and
`layout=structured`. Detect layout by presence of `.apatch/state/` directory.

(verify: python3 -m pytest tests/test_layout_paths.py::test_paths_flat_and_structured -q)

## R2 layout migrate dry-run plan

`layout_migrate_plan(workspace)` returns `{ moves: [{from, to}], layout_before, layout_after }`
without mutating disk. Never deletes source files in dry-run.

(verify: python3 -m pytest tests/test_layout_paths.py::test_layout_migrate_dry_run -q)

## R3 layout migrate apply with rollback manifest

`layout_migrate(workspace, dry_run=false)` moves files per plan and writes
`.apatch/registry/layout_migrate.json` manifest for reverse migrate. GC and registry
continue to work on structured paths post-migrate.

(verify: python3 -m pytest tests/test_layout_paths.py::test_layout_migrate_apply -q)

## Non-goals

- Mandatory migrate for consumers
- Rewriting TrustChain paths
