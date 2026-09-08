# RFP-016 — Spec execution chain

> **Parent:** [RFP-016-runtime-hygiene.md](../RFP-016-runtime-hygiene.md)  
> **Authoring:** [spec-authoring.md](../spec-authoring.md) · lint: `apatch spec lint --spec SPEC-HYGIENE-CORE`

Implement **in order**. Each spec depends on the previous wave's attested requirements unless noted.

```text
SPEC-HYGIENE-CORE  →  SPEC-HYGIENE-1  →  SPEC-HYGIENE-2  →  SPEC-GC-1  →  SPEC-SESSION-LIFECYCLE-1
                                                                                      ↓
                                                                            SPEC-LAYOUT-1 (optional)
```

| Order | Spec | RFP phase | Delivers | Depends on |
|-------|------|-----------|----------|------------|
| 1 | [SPEC-HYGIENE-CORE](./SPEC-HYGIENE-CORE.md) | cross-cutting | AGL invariant library: static replay set, `gc_allowed`, lease rules, lineage contract | — |
| 2 | [SPEC-HYGIENE-1](./SPEC-HYGIENE-1.md) | Phase 1 | Taxonomy, `register_artifact`, provenance log, infer scanner, `doctor.hygiene`, advisory `gc_report` | CORE |
| 3 | [SPEC-HYGIENE-2](./SPEC-HYGIENE-2.md) | Phase 2 | CLI `apatch gc`, MCP `apatch_gc(mode=report)` — **no delete** | HYGIENE-1 |
| 4 | [SPEC-GC-1](./SPEC-GC-1.md) | Phase 3 | `gc_safe`, `gc_rotate`, inference sunset enforcement, doctor `critical` | HYGIENE-2, CORE |
| 5 | [SPEC-SESSION-LIFECYCLE-1](./SPEC-SESSION-LIFECYCLE-1.md) | Phase 4 | `session_end` registry updates, RUN_STATE lease on apply/spec_run | HYGIENE-1, CORE |
| 6 | [SPEC-LAYOUT-1](./SPEC-LAYOUT-1.md) | Phase 5 (opt) | `apatch_paths`, `apatch layout migrate` | HYGIENE-1 (paths only) |

**RFP-017 (APG query)** is out of this chain — it reads `provenance.jsonl` written in HYGIENE-1 R3.

---

## Suggested module layout

| Module | Spec | Role |
|--------|------|------|
| `apatch/artifact_governance/` or `apatch/artifact_governance.py` | CORE + HYGIENE-1 | taxonomy, registry, provenance append, infer scan |
| `apatch/gc.py` | HYGIENE-1 R5 + HYGIENE-2 + GC-1 | `gc_report`, `register_inferred_artifacts`, `gc_safe`, `gc_rotate`, `mode=reconcile` |
| `apatch/workflows.py` | all | `gc_*`, doctor hygiene, CLI/MCP wiring |
| Write-path hooks | HYGIENE-1 R4, SESSION R2–R3 | session, generate, apply_session, spec_run, lease |

---

## Write-path registration audit (HYGIENE-1 R4)

Every row must call `register_artifact` + `lineage` before or atomically with the filesystem write.

| Write-path | Class | `created_by_tool` | Notes |
|------------|-------|-------------------|--------|
| `session_state.save` | STATE | `apatch_session_start` / state transition tool | `depends_on`: session intent |
| `emit_domain_event` → events.jsonl | LEDGER | caller tool | append-only path |
| `generate` / `write_jsonl` | EPHEMERAL | `apatch_generate` / `apatch_generate_batch` | `resolve_ephemeral_logs_path` → `.apatch/tmp/<session>/`; `register_ephemeral_logs`; purge on `session_end` |
| `apply_session` backup | HISTORY | `apatch_apply_session` | `run_lease_id` while chunk open |
| `apply_session` state file | RUN_STATE | `apatch_apply_session` | lease until `continue=false` |
| `write_lease` | EPHEMERAL | `apatch_apply_session` / strip lease | release on session_end |
| `spec_run` persist | RUN_STATE | `apatch_spec_run` | lease until run complete |
| `notarized_index` update | REGISTRY | mutation tool | rebuildable |
| `inclusion` append | LEDGER | trustchain helper | static replay critical |
| `policy.lock` | STATE | `apatch_policy_sign` | static replay critical |

Dogfood CI gate (post HYGIENE-1): new apatch write-path without registration → test failure in `test_write_path_registry_coverage.py`.

---

## Agent workflow (dogfood apatch repo)

```text
apatch_spec_lint(spec='SPEC-HYGIENE-CORE')
apatch_spec_run(spec='SPEC-HYGIENE-CORE', requirements={...})   # or execute_next loop

apatch_spec_lint(spec='SPEC-HYGIENE-1')
# R1–R3 before R4 wiring; R4 last among HYGIENE-1 (broadest touch)

apatch_spec_lint(spec='SPEC-HYGIENE-2')
# …
```

Verify chain default for hygiene specs: `pytest tests/test_artifact_governance.py tests/test_gc_cli.py -q` (split per requirement in each SPEC).

---

## Test files (planned)

| File | Covers |
|------|--------|
| `tests/test_artifact_governance.py` | CORE + HYGIENE-1 |
| `tests/test_gc_cli.py` | HYGIENE-2 + GC-1 CLI/MCP |
| `tests/test_session_hygiene.py` | SESSION-LIFECYCLE-1 |
| `tests/test_layout_paths.py` | LAYOUT-1 |
| `tests/fixtures/hygiene/` | fixture workspace with legacy + registered artifacts |

---

## Version target

Ship CORE + HYGIENE-1 + HYGIENE-2 in **0.4.0** (report-only, no delete).  
GC-1 + SESSION in **0.3.2**. LAYOUT optional **0.4.0** with path abstraction.
