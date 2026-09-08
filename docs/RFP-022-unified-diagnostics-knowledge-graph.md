# RFP-022 — Unified Diagnostics & Knowledge Graph

> **Status:** **Phases 1–3 attested** (2026-06-12) · **Owner:** apatch product  
> **Date:** 2026-06-12  
> **Package context:** 0.7.0 — spine + **Understanding layer (UD-1…UD-6)** dogfood-attested; **UD-7 dogfood smoke ✅** (2026-06-12); **UD-7 on live consumer** — G8 evidence in pilot scope (partner already active)  
> **Depends on:** [RFP-004](./archive/RFP-004-mutation-runtime-console.md) (MutationRuntime), [RFP-016](./RFP-016-runtime-hygiene.md) (artifacts), [RFP-018](./RFP-018-build-diagnose.md) (build diagnose MVP), [RFP-020](./RFP-020-three-views.md) (project status / reports), [RFP-021](./RFP-021-agent-reliability-design-partner.md) (failure taxonomy, partner gates)  
> **Blocks:** Closed-loop agent fix cycles; platform-runtime narrative ([transformation matrix](./apatch-transformation-matrix.md)); L2 semantic dependency model  
> **Related:** `apatch/impact.py`, `symbol_index.py`, `trustchain_coverage`, `spec_coverage`, `failure_taxonomy.py`

### Implementation status (dogfood, 2026-06-12)

| Phase | Spec | Status | Key modules |
|-------|------|--------|-------------|
| **1** — Unified Diagnostic[] | [SPEC-DIAGNOSTIC-GRAPH-1](./specs/SPEC-DIAGNOSTIC-GRAPH-1.md) | **Attested** | `apatch/diagnostics/` · `collect_diagnostics` · `.apatch/diagnostics/<session_id>.json` · `apatch://playbook/diagnose` |
| **2** — Contract edges | [SPEC-CONTRACT-EDGES-1](./specs/SPEC-CONTRACT-EDGES-1.md) | **Attested** | `apatch/diagnostics/edges.py` · `resolve_symbol_edges` · merged in `collect_diagnostics` |
| **3** — Knowledge graph & views | [SPEC-KNOWLEDGE-GRAPH-1](./specs/SPEC-KNOWLEDGE-GRAPH-1.md) | **Attested** | `apatch/knowledge_graph.py` · MCP `apatch_knowledge_graph` · `diagnostics_summary` in `project_status` · manager report section |
| **Parallel** — Partner validation | UD-7 | **Dogfood smoke ✅** · **live consumer pilot** | See [§7.2](#72-partner-validation-ud-7); [design-partner-playbook §11–§12](./design-partner-playbook.md) |

Tests: `tests/test_diagnostic_graph.py` · `tests/test_contract_edges.py` · `tests/test_knowledge_graph.py`

**Still open (non-blocking):** standalone MCP `apatch_diagnose_collect`, CLI `apatch diagnose collect`, AGL class `DIAGNOSTIC`, simulate pre-verify impact warnings, architect HTML graph nodes, `graph_ref` in status DTO.

**Follow-up from UD-7 smoke (fixed 2026-06-12):** `collect_diagnostics` binds artifact `session_id` from active `session_state` when verify result omits it (no more default `.apatch/diagnostics/unknown.json` under governed session). Re-apply same JSONL in a new session → `apatch_apply_session(reset=true)`.

## Acceptance

Canonical acceptance rows for [RFP-023](./RFP-023-rfp-spec-coverage.md) coverage lint. Phase detail and status narrative remain in §4.5 / §5.2 / §6.2.

| Id | Criterion | Level |
|----|-----------|-------|
| P1-A | One verify failure with clang log → `diagnostics[]` length ≥ 1 with stable `schema_version` | MUST |
| P1-B | One pytest failure → same schema, `source: pytest` | MUST |
| P1-C | `enrich_verify_failure` delegates to `collect_diagnostics` (legacy `build_diagnose` block retained) | MUST |
| P1-D | Agent playbook documents read `diagnostics[0].recommended_action` — no stderr parsing required | MUST |
| P1-E | `pytest tests/test_diagnostic_graph.py` green | MUST |
| P2-A | `missing_member` diagnostic includes `edges.symbols` + `edges.files` (caller + decl) | MUST |
| P2-B | `apatch_impact` linked via `edges.impact_ref` on resolvable Python symbols | MUST |
| P2-C | Dogfood: SparseOmega fixture produces ≥1 edge to declaration file | MUST |
| P3-A | From `session_id`, graph returns Intent linked to ≥1 Diagnostic and Attestation or Rollback | MUST |
| P3-B | `apatch status --json` includes last diagnostic count + top `type` | MUST |
| P3-C | Manager report mentions verify/diagnostic fact, not raw stderr | MUST |

**Executable specs:** [SPEC-DIAGNOSTIC-GRAPH-1](./specs/SPEC-DIAGNOSTIC-GRAPH-1.md) (P1) · [SPEC-CONTRACT-EDGES-1](./specs/SPEC-CONTRACT-EDGES-1.md) (P2) · [SPEC-KNOWLEDGE-GRAPH-1](./specs/SPEC-KNOWLEDGE-GRAPH-1.md) (P3)

---

## 0. Positioning — after L1 spine, before platform L2

[RFP-021](./RFP-021-agent-reliability-design-partner.md) closed **operational reliability** (session recovery, async verify, baseline, taxonomy, supervisor visibility). The governed spine is **4–5/5** in dogfood.

This RFP closes the **Understanding gap**:

| apatch knows well (accountability) | apatch knows weakly (comprehension) |
|-----------------------------------|-------------------------------------|
| What was intended (`intent`, needles, SPEC) | How the domain is structured |
| What changed (patches, ledger ops) | Which **contracts** exist (APIs, symbols) |
| What was verified (`verify_run`, baseline) | Which **invariants** link subsystems |
| What was signed (`attest`, TrustChain) | Why a failure is **fixable vs structural** |

**Strategic framing** ([transformation matrix](./apatch-transformation-matrix.md)):

> apatch relates **agent intent** to the repo the way a **build system** relates **source** to **artifacts** — but today **Diagnostics** are fragmented and there is no unified **knowledge graph**.

apatch is **not** replacing gcc/clang/rust-analyzer (~40–50% compiler metaphor). It **orchestrates** external domain tools and attaches **governed provenance**. This RFP makes that orchestration **structurally honest**.

---

## 1. Problem statement

### 1.1 Fragmented diagnostics (Diagnose layer)

Today the spine ends in practice as:

```text
Apply → Verify → opaque failure text
```

Failures live in separate silos:

| Source | Today | Structured? |
|--------|-------|-------------|
| clang/gcc stderr | [RFP-018](./RFP-018-build-diagnose.md) MVP → `diagnostics.json` | Partial (`missing_member`) |
| pytest output | stderr in `verify_run` result | No |
| grep / spec `(verify:)` | exit code only | No |
| `apatch_spec_lint` | separate tool | No |
| TrustChain / notarization | `failure_taxonomy` | Partial (`error_type`) |
| Sandbox / lease | `failure_taxonomy` | Partial |

For a build metaphor this is like a compiler that **prints** errors but does not maintain an **internal diagnostic model**. Agents cannot reliably loop:

```text
failure → structured finding → governed needles → re-verify
```

without re-parsing stderr each time.

**Example:** `SparseOmega::get_concept_name` — RFP-018 parses the clang line and suggests members; pytest and spec violations on the same session are unrelated JSON fields.

### 1.2 File-level mutations, not contract-level understanding

apatch excels at:

```text
file → mutation → verify
```

It weakly models:

```text
symbol → declaration → references → dependent contracts
```

Existing **fragments** (not wired to spine):

- `apatch_impact` + `symbol_index` (py/ts/js regex index)
- `import_graph`
- `verify_semantic`, `arch_check`
- `@sid` blocks in markdown (`apatch compile`)

Contract drift is discovered **after** `verify_run`, not during `simulate` / plan review. gcc understands callers; apatch mostly understands **artifacts**.

### 1.3 No unified knowledge graph

Durable facts exist but are not linked:

```text
Intent ↔ Spec ↔ Files ↔ Symbols ↔ Diagnostics ↔ Evidence ↔ Attestation
```

TrustChain coverage, spec coverage, plan artifacts, diagnostics files, and session state are **separate queries**. The product remains a **governed mutation runtime**, not yet a **platform runtime** for attributable change.

### 1.4 Discoverability lag

Spine discoverability ~4/5 (`apatch_doctor`, nine `apatch://playbook/*` resources including `doc_outline`). Domain tools (`build_diagnose`, `impact`, `compile`, `verify_semantic`) are **not** on the unfamiliar-agent hot path.

### 1.5 External validation still open

RFP-021 AR-1…AR-7 ✅ in apatch repo. Live consumer pilot already exercises G1–G5 patterns; **G8** (clang + pytest adapters on partner stack) and attested §12 checklist remain the evidence gap — not «find a partner».

---

## 2. Goals

| ID | Goal | Priority | Phase | Status |
|----|------|----------|-------|--------|
| **UD-1** | **Unified `Diagnostic` schema** — one JSON model for all failure sources | P0 | 1 | **Done** — `apatch/diagnostics/schema.py` (`schema_version=1`) |
| **UD-2** | **Adapters** — clang (extend RFP-018), pytest, spec lint, trust/sandbox taxonomy | P0 | 1 | **Done** — `adapters/{clang,pytest,spec,trust}.py` |
| **UD-3** | **Spine integration** — `verify_run`, `apply_session`, `failure_taxonomy` emit `diagnostics[]`; `recommended_action` derived consistently | P0 | 1 | **Done** — `collect_diagnostics`; `build_diagnose.enrich_verify_failure` delegates |
| **UD-4** | **Symbol / contract edges** — link diagnostics to symbols, files, optional `impact` subgraph | P1 | 2 | **Done** — `edges.py`; `impact_ref` on Python symbols; C++ `missing_member` + `@sid` |
| **UD-5** | **Knowledge graph query** — read-only join Intent↔Spec↔Files↔Diagnostics↔Attest for session / artifact | P1 | 3 | **Done** — `knowledge_graph_for_session`; MCP `apatch_knowledge_graph` |
| **UD-6** | **Discoverability** — `apatch://playbook/diagnose`, doctor field, status/report surfacing | P1 | 1–3 (parallel) | **Done** — playbook resource; `diagnostics_summary`; report MD section |
| **UD-7** | **Partner validation** — diagnostic loop on external repo (extends G5 fix_forward) | P0 | parallel | **Dogfood smoke ✅** · consumer pilot open |

**Non-goal:** autonomous patch synthesis from diagnostics (agents craft needles; apatch governs apply).

---

## 3. Target architecture

### 3.1 Spine with Diagnose (canonical)

```text
Needles / SPEC        source intent
       ↓
Execution plan        mutation plan (JSONL)
       ↓
Apply                 emit under lease
       ↓
Diagnose              Diagnostic[]  ← THIS RFP (Phase 1–2)
       ↓
Verify                external gate (exit code)
       ↓
Attest                signed provenance
```

`Diagnose` runs:

- **after** verify failure (primary),
- optionally **after** simulate / spec_lint (advisory, Phase 2).

### 3.2 Unified Diagnostic schema (UD-1)

Minimal contract (versioned `schema_version`):

```json
{
  "schema_version": 1,
  "id": "diag_…",
  "source": "clang|pytest|spec|trust|sandbox|arch",
  "type": "missing_member|test_failure|spec_violation|notarization_failed|…",
  "severity": "error|warning",
  "message": "human-readable",
  "location": {
    "file": "path/to/file.cpp",
    "line": 42,
    "column": null,
    "symbol": "SparseOmega::get_concept_name"
  },
  "session_id": "apatch_sess_…",
  "verify_command": "make",
  "recommended_action": "fix_forward|rollback|reduce_scope|retry_chunk",
  "evidence": {
    "raw_excerpt": "…",
    "artifact_paths": [".apatch/diagnostics.json"]
  },
  "suggestions": [
    {
      "strategy": "rename_call_site|restore_api|adapter_or_literal",
      "hint": "…",
      "needles_hint": null
    }
  ],
  "edges": {
    "symbols": ["SparseOmega::get_concept_name"],
    "files": ["src/sparse_omega.h", "src/caller.cpp"],
    "requirements": ["SPEC-FOO#R2"],
    "artifacts": ["spec:SPEC-FOO#R2"]
  }
}
```

**Rules:**

- `recommended_action` MUST align with [failure taxonomy v2](./specs/SPEC-FAILURE-TAXONOMY-2.md) — no contradictory top-level vs diagnostic-level actions.
- `suggestions` are **advisory** (RFP-018 §5); never auto-applied.
- `edges` populated in Phase 2 when resolvable; optional in Phase 1. **Implemented** — see [SPEC-CONTRACT-EDGES-1](./specs/SPEC-CONTRACT-EDGES-1.md).

### 3.3 Knowledge graph (UD-5, Phase 3)

Read-only logical graph — **not** a new database server. Implemented as:

```text
project_status_workspace() + graph query API
  nodes: Intent, Requirement, File, Symbol, Diagnostic, Attestation, LedgerOp
  edges: MUTATED, VERIFIED_BY, DIAGNOSED_AS, ATTESTED_IN, COVERS, REFERENCES
```

Sources (existing):

| Node/edge | Source module |
|-----------|---------------|
| Intent, session | `session_state`, ledger |
| Requirement | `docs/specs/SPEC-*.md`, `spec_coverage` |
| File mutations | TrustChain, apply reports |
| Symbol | `symbol_index`, `@sid` map |
| Diagnostic | `.apatch/diagnostics/` (Phase 1 artifacts) |
| Attestation | ledger, `trustchain_coverage` |

**MCP:** **Resolved** — dedicated `apatch_knowledge_graph(session_id=…)` plus `diagnostics_summary` on `apatch_project_status` / `apatch status --json` ([SPEC-KNOWLEDGE-GRAPH-1](./specs/SPEC-KNOWLEDGE-GRAPH-1.md)).

---

## 4. Phase 1 — Unified Diagnostic Graph (UD-1, UD-2, UD-3) ✅ attested

> **Spec:** [SPEC-DIAGNOSTIC-GRAPH-1](./specs/SPEC-DIAGNOSTIC-GRAPH-1.md) · **Tests:** `tests/test_diagnostic_graph.py`

### 4.1 Deliverables

| Item | Description | Status |
|------|-------------|--------|
| `apatch/diagnostics/` package | `schema.py`, adapters, `collect.py`, `artifacts.py` | **Done** |
| Adapters | `clang` (wrap `build_diagnose`), `pytest`, `spec`, `trust` | **Done** |
| `collect_diagnostics(…)` | Single entry from runtime / verify failure | **Done** |
| Artifact | `.apatch/diagnostics/<session_id>.json` | **Done** |
| MCP | `verify_run` / apply failure responses include `diagnostics[]` | **Done** |
| MCP | optional `apatch_diagnose_collect` | **Not implemented** (collect is internal + playbook) |
| CLI | `apatch diagnose collect --session …` | **Not implemented** |
| Discoverability | `apatch://playbook/diagnose` (UD-6) | **Done** — `diagnose_playbook()` |
| Tests | clang / pytest / spec fixtures | **Done** |

### 4.2 Pytest adapter (sketch)

Parse:

- `FAILED tests/test_foo.py::test_bar - AssertionError: …`
- short test summary info

Emit `type: test_failure`, `location.file`, optional `location.symbol` = node name, `recommended_action: fix_forward` when patch unrelated to session scope (align G5).

### 4.3 Spec adapter (sketch)

Input: `apatch_spec_lint` / coverage staleness / missing `(verify:)`.

Emit `type: spec_violation`, link `edges.requirements`.

### 4.4 Trust / sandbox adapter (sketch)

Input: `classify_failure()` output.

Map `NOTARIZATION_FAILED`, `DIRECT_WRITE_BLOCKED`, etc. to unified diagnostics with `source: trust|sandbox`.

### 4.5 Acceptance (Phase 1)

> **Canonical ids:** [## Acceptance](#acceptance) — table below is status narrative only; do not edit ids here.

| # | Criterion | Status |
|---|-----------|--------|
| P1-A | One verify failure with clang log → `diagnostics[]` length ≥ 1 with stable `schema_version` | **Met** |
| P1-B | One pytest failure → same schema, `source: pytest` | **Met** |
| P1-C | `enrich_verify_failure` delegates to `collect_diagnostics` (legacy `build_diagnose` block retained) | **Met** |
| P1-D | Agent playbook documents read `diagnostics[0].recommended_action` — no stderr parsing required | **Met** — `apatch://playbook/diagnose` |
| P1-E | `pytest tests/test_diagnostic_graph.py` green | **Met** |

**Executable specs:** [SPEC-DIAGNOSTIC-GRAPH-1](./specs/SPEC-DIAGNOSTIC-GRAPH-1.md) · [SPEC-CONTRACT-EDGES-1](./specs/SPEC-CONTRACT-EDGES-1.md) · [SPEC-KNOWLEDGE-GRAPH-1](./specs/SPEC-KNOWLEDGE-GRAPH-1.md)

---

## 5. Phase 2 — Symbol & contract edges (UD-4) ✅ attested

> **Spec:** [SPEC-CONTRACT-EDGES-1](./specs/SPEC-CONTRACT-EDGES-1.md) · **Tests:** `tests/test_contract_edges.py`

### 5.1 Deliverables

| Item | Description | Status |
|------|-------------|--------|
| `resolve_symbol_edges(diagnostic) → edges` | Uses `symbol_index`, `impact`, C++ header scan (RFP-018) | **Done** — `apatch/diagnostics/edges.py` |
| `@sid` linkage | Markdown `claim_N` → `knowledge_map.json` hash in `edges.artifacts` | **Done** |
| Pre-verify advisory | `apatch_simulate` MAY attach warnings from `impact` | **Not implemented** (non-goal in spec) |
| Suggestions v2 | `needles_hint` partial dicts where confidence high | **Not implemented** (suggestions remain advisory only) |

### 5.2 Acceptance (Phase 2)

> **Canonical ids:** [## Acceptance](#acceptance) — status narrative only.

| # | Criterion | Status |
|---|-----------|--------|
| P2-A | `missing_member` diagnostic includes `edges.symbols` + `edges.files` (caller + decl) | **Met** — SparseOmega fixture |
| P2-B | `apatch_impact` linked via `edges.impact_ref` on resolvable Python symbols | **Met** |
| P2-C | Dogfood: SparseOmega fixture produces ≥1 edge to declaration file | **Met** |

**Executable spec:** [SPEC-CONTRACT-EDGES-1](./specs/SPEC-CONTRACT-EDGES-1.md)

---

## 6. Phase 3 — Knowledge graph & views (UD-5) ✅ attested

> **Spec:** [SPEC-KNOWLEDGE-GRAPH-1](./specs/SPEC-KNOWLEDGE-GRAPH-1.md) · **Tests:** `tests/test_knowledge_graph.py`

### 6.1 Deliverables

| Item | Description | Status |
|------|-------------|--------|
| `knowledge_graph_for_session(session_id)` | Read-only JSON graph `{nodes[], edges[]}` | **Done** — `apatch/knowledge_graph.py` |
| `apatch_project_status` extension | `diagnostics_summary` on unified DTO | **Done** |
| `apatch report` | Manager MD: **Verification & diagnostics** section | **Done** |
| `apatch report --html` | Diagnostic nodes on architect HTML | **Not implemented** (optional follow-up) |
| MCP tool | `apatch_knowledge_graph(session_id=…)` | **Done** — available in the 110-tool full profile |
| MCP resource | `apatch://playbook/diagnose` (UD-6) | **Done** (Phase 1 R7) |

### 6.2 Acceptance (Phase 3)

> **Canonical ids:** [## Acceptance](#acceptance) — status narrative only.

| # | Criterion | Status |
|---|-----------|--------|
| P3-A | From `session_id`, graph returns Intent linked to ≥1 Diagnostic and Attestation or Rollback | **Met** |
| P3-B | `apatch status --json` includes last diagnostic count + top `type` | **Met** — `diagnostics_summary` |
| P3-C | Manager report mentions verify/diagnostic fact, not raw stderr | **Met** |

**Executable spec:** [SPEC-KNOWLEDGE-GRAPH-1](./specs/SPEC-KNOWLEDGE-GRAPH-1.md)

---

## 7. Parallel tracks

### 7.1 Discoverability (UD-6) — ✅ shipped

| Item | Effort | Status |
|------|--------|--------|
| `apatch://playbook/diagnose` | 1 session | **Done** |
| `doctor.diagnostics` summary + read_order entry | 1 session | **Done** — `tool_usage.by_intent.fix_verify_failure` |
| `tool_usage.by_intent.fix_verify_failure` | doc only | **Done** |
| Link from [transformation matrix](./apatch-transformation-matrix.md) Gaps → RFP-022 | done | **Done** |

### 7.2 Partner validation (UD-7)

**Dogfood smoke (apatch repo, 2026-06-12):** clean MCP agent, no RFP/spec hints — **PASS** on all checks: `diagnostics[]`, `fix_forward`, `diagnostics_summary`, `apatch_knowledge_graph`, attest + `session_end`. Checklist and agent prompt: [design-partner-playbook §11](./design-partner-playbook.md#11-ud-7-diagnostics-smoke-apatch-dogfood).

**Live consumer pilot:** already active (governed cycles + `ticket:FEEDBACK-*` → attest). Formalize with [playbook §12](./design-partner-playbook.md#12-partner-pilot-kickoff-consumer-repo) + **G8** below — evidence gap, not greenfield pilot.

Extend [design-partner playbook §9](./design-partner-playbook.md):

| Gate | Extension |
|------|-----------|
| G5+ | Verify failure → agent uses `diagnostics[]` → fix_forward cycle without rollback |
| G8 (new, optional) | Consumer repo: one clang + one pytest diagnostic parsed end-to-end |

Runs **in parallel** with Phase 1 — informs adapter priority (Python vs C++ consumer).

---

## 8. Agent workflow (closed loop)

```text
apatch_session_start(intent="fix API drift …")
apatch_generate_batch(needles=[…])
apatch_apply_session(…, verify_deferred=true)
apatch_verify_run(verify="make")          # ok:false
  → diagnostics[]                         # unified
  → diagnostics[0].recommended_action     # fix_forward
  → diagnostics[0].suggestions            # advisory
apatch_generate_batch(needles=[…])        # agent-crafted
apatch_apply_session → apatch_verify_run  # ok:true
apatch_attest → apatch_session_end
```

**Never:** auto-apply from suggestions without new governed session intent.

---

## 9. Relationship to existing work

| Existing | RFP-022 relationship |
|----------|---------------------|
| [RFP-018](./RFP-018-build-diagnose.md) | Phase 1 **extends** — clang adapter wraps `parse_compiler_output`; deprecate duplicate shapes over time |
| [RFP-021](./RFP-021-agent-reliability-design-partner.md) AR-3 | Diagnostics carry `recommended_action`; taxonomy remains authoritative |
| [RFP-020](./RFP-020-three-views.md) | Phase 3 surfaces graph in status/report |
| `apatch_impact` / `symbol_index` | Phase 2 edges — not replaced |
| `apatch compile` / `@sid` | Phase 2 markdown symbol edges |
| [RFP-014](./RFP-014-spec-interference-detection.md) | Orthogonal — interference graph vs session knowledge graph; may share visualization later |

---

## 10. Non-goals

| Non-goal | Reason |
|----------|--------|
| Replace gcc/clang/pyright | apatch orchestrates; domain tools compile |
| Auto-fix without agent intent | Governance / attest chain |
| Full semantic equivalence checking | L3 research — not product scope |
| Hosted graph database (Neo4j service) | Read-only join over `.apatch/` + ledger |
| MSVC/rustc adapters in Phase 1 | Follow clang/pytest MVP |
| HC contribution scoring | [RFP-020 §5](./RFP-020-three-views.md) |
| Reduce MCP tool count | [RFP-021 §12](./RFP-021-agent-reliability-design-partner.md) |

---

## 11. Priority stack (recommended execution)

```text
Done (dogfood attested):
  Phase 1  UD-1…UD-3  Unified Diagnostic[]
  Phase 2  UD-4       Symbol/contract edges
  Phase 3  UD-5       Knowledge graph + views
  UD-6    playbook/diagnose + status/report surfacing

Still open:
  UD-7    consumer pilot (validates adapters on external repo)
  Optional: CLI diagnose collect, simulate impact warnings, HTML graph nodes
```

---

## 12. Success metrics

| Metric | Target |
|--------|--------|
| Verify failure → structured diagnostic | ≥95% for clang + pytest in partner repo |
| Agent stderr parsing in dogfood transcripts | → 0 (use `diagnostics[]`) |
| fix_forward cycles without rollback | ↑ vs RFP-021 baseline (G5) |
| Time to first governed retry after verify fail | ↓ (agent reads JSON, not logs) |
| `project_status` answers "why blocked?" | diagnostic `type` + `message` without JSONL |

---

## 13. Open questions

1. **Single MCP tool** `apatch_diagnose` vs enrich existing tools only? → **Resolved:** enrich `verify_run` / failure responses; no standalone collect tool yet.
2. **AGL registration** for `.apatch/diagnostics/*` — Phase 1 or with RFP-016 Phase 2? → **Open.**
3. **Graph API** — extend `apatch_project_status` vs new `apatch_knowledge_graph`? → **Resolved:** both — `diagnostics_summary` on status DTO + **`apatch_knowledge_graph`** for full session graph.
4. **C++ depth** — stay regex MVP vs `compile_commands.json` in Phase 2? → **Resolved for MVP:** regex + header scan (RFP-018); libclang deferred.
5. **Partner consumer profile** — Python-first vs C++-first adapter ordering? → **Informed by live pilot**; formal G8 attestation on partner stack remains open. Dogfood smoke on apatch repo: **done** (2026-06-12).

---

## 14. References

- [apatch-transformation-matrix.md](./apatch-transformation-matrix.md) — build metaphor, Diagnose layer, gaps
- [RFP-018-build-diagnose.md](./RFP-018-build-diagnose.md) — clang MVP
- [RFP-021-agent-reliability-design-partner.md](./RFP-021-agent-reliability-design-partner.md) — L1 reliability
- [design-partner-playbook.md](./design-partner-playbook.md) — G1–G7
- [engineering-truth-overview.md](./engineering-truth-overview.md) — HC attribution bridge
- Code: `apatch/diagnostics/`, `apatch/knowledge_graph.py`, `apatch/build_diagnose.py`, `failure_taxonomy.py`, `impact.py`, `symbol_index.py`, `project_status.py`, `report_render.py`

---

## Changelog

| Date | Change |
|------|--------|
| 2026-06-12 | **RFP-023 polish** — Rk validation, multi-spec `--specs` aggregate, sibling waiver pattern in phase SPECs |
| 2026-06-12 | **RFP-023 backfill** — canonical `## Acceptance` (P1-A…P3-C); traceability in phase SPECs |
| 2026-06-12 | **UD-7 dogfood smoke PASS** — design-partner-playbook §11; follow-up: bind `session_id` in `collect_diagnostics` from `session_state` |
| 2026-06-12 | **Phases 1–3 attested** — SPEC-DIAGNOSTIC-GRAPH-1, SPEC-CONTRACT-EDGES-1, SPEC-KNOWLEDGE-GRAPH-1; implementation status table; acceptance criteria marked met |
| 2026-06-12 | Draft v1 — problem, phases UD-1…UD-7, Diagnostic schema, acceptance, non-goals |
