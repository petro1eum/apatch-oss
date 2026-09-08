# RFP-016: Artifact Lifecycle Governance (Runtime Hygiene)

> **Status:** Implemented MVP (2026-06-11) — registry, gc reconcile/safe/rotate, session EPHEMERAL routing + purge; RFP-017 APG query deferred  
> **Layer:** Artifact Governance Layer (AGL) — cross-cutting between Execution and Storage  
> **Depends on:** RFP-004 (session), RFP-009 (spec_run)  
> **Blocks:** Product readiness, consumer onboarding at scale, reproducibility  
> **Executable specs:** [RFP-016-IMPLEMENTATION](./specs/RFP-016-IMPLEMENTATION.md) (chain) · [SPEC-HYGIENE-CORE](./specs/SPEC-HYGIENE-CORE.md) → [SPEC-HYGIENE-1](./specs/SPEC-HYGIENE-1.md) → [SPEC-HYGIENE-2](./specs/SPEC-HYGIENE-2.md) → [SPEC-GC-1](./specs/SPEC-GC-1.md) → [SPEC-SESSION-LIFECYCLE-1](./specs/SPEC-SESSION-LIFECYCLE-1.md) → [SPEC-LAYOUT-1](./specs/SPEC-LAYOUT-1.md) (opt)
> **Date:** 2026-06-10 (v2 reframing: lifecycle invariant, not cleanup utility)

---

## 0. Problem Statement

apatch promises **engineering truth**: governed execution, signed evidence, institutional memory. But the runtime that produces this truth **does not govern its own artifacts**.

After a typical dogfood session the worktree accumulates orphaned JSONL, unbounded backups, benchmark debris mixed with state, and a flat `.apatch/` directory with no lifecycle contract. This is not cosmetic clutter — it is a **missing system invariant**.

### Product framing

RFP-016 is **not** “a GC feature”. It introduces **artifact lifecycle governance** as a mandatory runtime contract:

> Every file in the workspace that apatch creates or tracks must have: **class**, **owner**, **lifetime contract**, and **deletion pathway**.

If any of these is missing for a runtime-produced artifact → **runtime bug**, not “mess to clean up”.

### Root cause

The system is **event-sourced without GC** — a classical failure mode. No artifact has a defined:

- **Origin** (what created it)
- **Purpose** (what it is for)
- **Lifetime** (when it expires)
- **Garbage policy** (how it leaves the system)

Today `session_end()` stamps `ended_at` but does not release leases, delete session-scoped JSONL, or rotate history — entropy only grows.

### Structural vs semantic resolution (RFP-014)

Coordination boundaries (reorder, split, annotate — not “UI wins over API”) belong in [RFP-014](./RFP-014-spec-interference-detection.md) and [domain.md](./domain.md). They are **not** part of this RFP.

---

## 1. Artifact Governance Layer (AGL)

AGL sits between governed execution and filesystem storage:

```text
Execution (RFP-004)
    ↓  writes via register_artifact()
Artifact Governance Layer (RFP-016)
    ↓  class + contract + lifetime
Storage (filesystem / ledger / registry / .trustchain)
```

### 1.1 AGL guarantees

| Invariant | Statement |
|-----------|-----------|
| **A — bounded entropy** | The system cannot only create artifacts — it must classify them and retire them per policy. |
| **B — no orphan class** | No registered runtime artifact exists without `class` + policy. Legacy/inferred artifacts are flagged until write-path registration covers them. |
| **C — deterministic cleanup** | Every artifact has a defined deletion, rotation, or archive pathway — not ad-hoc heuristics. |

AGL is **safety-critical** (like notarization): if GC corrupts replay or evidence, the platform loses trust. GC is a **compiler pass over the artifact registry**, not a cleanup script.

### 1.2 AGL purity constraint (non-negotiable)

AGL is a **deterministic policy engine**, not a semantic inference layer.

| AGL **may** | AGL **must not** |
|-------------|------------------|
| Classify from registry + closed path rules | Interpret execution intent |
| Allow / deny deletion from contract fields | Decide replay importance by reading file contents heuristically |
| Validate lifecycle against static policy | Become the source of truth for “is run active?” |

**Execution** (RFP-004 / RFP-009) owns runtime semantics: active chunk, resume, lease hold. It **writes** `replay_critical`, `run_lease_id`, and `gc_allowed` into the registry at registration or on state transition. **GC reads only registry fields** — never parses `apply_session.json` or `spec_run.json` to infer guards.

Formal boundary:

```text
AGL = deterministic policy engine
NOT = semantic inference engine
NOT = runtime reasoning system
```

Registry write failure on apatch write-paths is a **hard error** (Phase 1+). Inference is a **migration aid with a sunset**, not a permanent gray layer (§3.4).

---

## 2. Artifact Lifecycle Model

### 2.1 Artifact classes

Every apatch-tracked file belongs to exactly one class:

| Class | Examples | Semantics |
|-------|----------|-----------|
| **STATE** | `session_state.json`, `enforcement.json`, `sandbox.json`, `policy.lock.json` | Current truth. Overwritten in place, never deleted by GC. |
| **LEDGER** | `events.jsonl`, `.trustchain/*`, `inclusion.jsonl` | Append-only evidence. Segment rotation only; never delete content. |
| **REGISTRY** | `.apatch/specs/*.json`, `notarized_index.json`, `artifacts.jsonl` (index) | Derived / index state. Rebuildable from ledger; refresh on corruption. |
| **RUN_STATE** | `spec_run.json`, `apply_session.json` (active resume) | Resume checkpoints. Not EPHEMERAL — lifetime tied to active run/chunk. |
| **HISTORY** | `backups/*`, closed `history/runs/*` | Replay / rollback support. Bounded retention (rotate oldest). |
| **EPHEMERAL** | active `patches.jsonl` in session scope, `write_lease.json` | Session-scoped. Must not survive `session_end` **when no resume guard applies**. |
| **DEBUG** | `sandbox_violations.jsonl`, `mcp_stderr.log` | Operational telemetry. Rotated (7 days / 10MB). |
| **GARBAGE** | `_big.jsonl`, `_bench*.jsonl`, `_cold.jsonl` | Should never have been persisted. Immediate purge when GC is allowed to delete. |
| **UNREGISTERED** | hand-written `patches-*.jsonl` in repo root | Owner `user` / `agent`. Explicit class — chaos is observable. Delete only via `gc_allowed` (§3.5). |

#### 2.1.1 Operational tiers (product grouping)

Nine classes for policy; three tiers for operator reasoning:

| Tier | Classes | Operator meaning |
|------|---------|------------------|
| **CORE** | STATE, LEDGER, RUN_STATE | Never delete casually; replay / evidence |
| **MANAGED** | REGISTRY, HISTORY, DEBUG | Bounded, rotate, rebuild |
| **TRANSIENT** | EPHEMERAL, GARBAGE, UNREGISTERED | Retire per contract |

### 2.2 Lifecycle policies

| Class | TTL | GC policy | Committed to git? |
|-------|-----|-----------|-------------------|
| STATE | persistent | never delete / move by GC | `enforcement.json`, `sandbox.json` only (flat or `state/`) |
| LEDGER | permanent | segment append-only rotate; never delete | no |
| REGISTRY | persistent | rebuild on corruption | no |
| RUN_STATE | **lease-bound** (`run_lease_id`) | never delete while lease active; GC reads `gc_allowed` only | no |
| HISTORY | bounded (N=50 runs) | rotate oldest | no |
| EPHEMERAL | session-bound | delete on `session_end` **after** execution clears lease + sets `gc_allowed=true` | **never** |
| DEBUG | bounded (7d / 10MB) | prune | never |
| GARBAGE | zero | delete when GC enforcement enabled | never |
| UNREGISTERED | policy / orphan | report always; delete only in `--safe` with guards | never |

---

## 3. Artifact Contract & Registry

Without a contract, GC remains heuristic and will eventually break system truth. With it, GC becomes deterministic.

### 3.1 Contract schema (logical)

Each **registered** write records one entry (not one JSON file per artifact):

```json
{
  "id": "sha256:… or uuid",
  "path": "relative/to/workspace",
  "class": "EPHEMERAL",
  "owner": "apatch.spec_run | apatch.session | apatch.generate | user",
  "provenance": "registered | inferred",
  "session_id": "apatch_sess_…",
  "spec_id": "SPEC-HYGIENE-1",
  "replay_critical": false,
  "run_lease_id": "lease_apply_sess_… | null",
  "gc_allowed": false,
  "lifetime": {
    "type": "session | bounded | permanent | run_bound",
    "trigger": "session_end | spec_complete | manual | rotate"
  },
  "gc_policy": {
    "allowed": true,
    "strategy": "delete | rotate | archive | never"
  },
  "lineage": {
    "created_by_tool": "apatch_apply_session",
    "created_by_spec": "SPEC-HYGIENE-1",
    "requirement": "SPEC-HYGIENE-1#R2",
    "run_id": "run_884",
    "reason": "apply_batch_execution",
    "depends_on": [".apatch/session_state.json"],
    "produces": [".apatch/events.jsonl"]
  }
}
```

`lineage` is **recorded at write time in Phase 1** (see §3.6). RFP-017 adds query/explain on top — not a second registration pass.

### 3.2 Registry storage

- **Write-time:** all apatch write-paths call `register_artifact(path, contract)` (Phase 1). **Synchronous append** to local JSONL only — no network; failure fails the write-path (registry is part of the mutation contract).
- **Persistent index:** append-only `.apatch/registry/artifacts.jsonl` (flat layout: `.apatch/artifacts.jsonl` until Phase 5 migrate).
- **Provenance:** only two long-lived states — `registered` (authoritative) and `inferred` (temporary, migration-only).

### 3.3 Write-path audit (Phase 1 deliverable)

Every producer must register: `save_session_state`, `emit_domain_event`, `write_jsonl` / `generate_batch`, apply_session backups, lease, `spec_run` persist, inclusion, policy lock, etc.

### 3.4 Inference sunset (no permanent gray layer)

`provenance: inferred` is allowed **only** during migration:

| Milestone | Rule |
|-----------|------|
| Phase 1–2 | Inferred artifacts appear in report with `inference_expires_after_ops: N` (default N=100 governed ops per workspace) |
| After N ops without registration | Path becomes **forbidden write target** for apatch tools until classified or removed |
| Phase 3+ | `inferred` count > 0 **and** governed ops ≥ N → `hygiene.status: critical`; blocks new governed mutations |
| Remediation | `apatch gc --reconcile` (`register_inferred_artifacts`) — registers classified-but-unregistered paths; no delete |
| Multi-chunk apply | Before blocking, execution auto-reconciles inferred once per hygiene check (dogfood backup files from prior apply) |
| Coverage gate | When write-path audit is complete, `inferred` must be **zero** for apatch dogfood CI |

Partial governance is not acceptable at steady state: either `registered` or explicitly **UNREGISTERED** (user/agent), never unbounded inferred.

### 3.5 GC preconditions (delete gate)

GC **never** decides resume semantics. Deletion requires all of:

```text
gc_allowed(path) :=
    registry.registered(path) == true
    AND contract.replay_critical == false
    AND contract.gc_allowed == true
    AND contract.run_lease_id == null
    AND path NOT IN STATIC_REPLAY_CRITICAL_PATHS   # §10 closed set
```

`gc_allowed` is set by **execution** on transitions (`chunk complete`, `spec_run finished`, `session_end` after lease release). RUN_STATE artifacts **must** carry a `run_lease_id` while active; releasing the lease sets `gc_allowed=true` or reclassifies to HISTORY/EPHEMERAL.

Report mode may **suggest** reclaim candidates; only Phase 3+ `gc_safe` acts on paths where `gc_allowed` is already true.

Phase 3+ may also use **active lineage edges** (`depends_on` still live) as an additional block — but GC still does not infer causality; it reads recorded edges only (§3.6).

### 3.6 Minimal provenance recording (co-shipped with Phase 1 — avoids rework)

**Problem:** if registry stores only `class` + `gc_allowed`, a later APG requires re-touching every write-path twice.

**Split:**

| Ship with RFP-016 (Phase 1) | Ship with RFP-017 (later) |
|-----------------------------|---------------------------|
| Record `lineage` on every `register_artifact()` | `apatch provenance why <path>` |
| Append edge events to `.apatch/registry/provenance.jsonl` | Graph query, reverse deps, cross-session views |
| Pass `created_by_tool`, `session_id`, `spec_id`, `run_id`, `depends_on[]` from execution context | Doctor “explain this mess” UI, APG dashboards |

```text
Execution (RFP-004)
    ↓  register_artifact(class + gc fields + lineage)   ← Phase 1, one hook
Artifact Governance (RFP-016)
    ↓  lifecycle / gc_allowed
Provenance log (edges)                               ← Phase 1 append-only
    ↓  query & explain                               ← RFP-017 only
```

**Kitchen analogy:** Phase 1 = every dish gets a ticket (who cooked, which recipe, ingredients). RFP-017 = you can ask the system “show me the ticket chain” without opening the fridge.

GC uses lineage **only** as recorded facts:

- `depends_on` path with active `run_lease_id` → do not delete dependent EPHEMERAL
- no active edges + `gc_allowed` → safe reclaim candidate

GC does **not** rebuild the graph from disk scans or guess “which SPEC made this”.

---

## 4. Garbage Collection (`apatch gc`)

**GC mutates content** (delete / rotate / archive). It does **not** restructure directories.

| Concern | Tool | Scope |
|---------|------|--------|
| Delete / rotate / report | `apatch gc` | Content lifecycle |
| Move / rename layout | `apatch layout migrate` | Structure only (Phase 5) |

GC and migration are **independent**, **commutatively safe** (migrate then gc == gc then migrate on equivalent paths), and **tested separately**.

### 4.1 Modes (by implementation phase)

| Mode | Flag | Phase | Behavior |
|------|------|-------|----------|
| **report** | `--dry-run` (default) | 2+ | Scan, classify, report. **No changes.** |
| **reconcile** | `--reconcile` | 3+ | Register inferred paths into registry (`register_inferred_artifacts`). **No delete.** |
| **safe** | `--safe` | 3+ | Delete only where `gc_allowed` (§3.5). Never touch STATIC_REPLAY_CRITICAL or `replay_critical=true`. |
| **rotate** | `--rotate` | 3+ | Safe + rotate HISTORY, prune DEBUG. |

There is **no** `--migrate` on `apatch gc`.

### 4.2 Report output

```json
{
  "status": "degraded",
  "artifact_count": 312,
  "classified": {
    "STATE": 4,
    "LEDGER": 3,
    "REGISTRY": 16,
    "RUN_STATE": 2,
    "HISTORY": 201,
    "EPHEMERAL": 95,
    "DEBUG": 3,
    "GARBAGE": 4,
    "UNREGISTERED": 8,
    "UNCLASSIFIED": 0
  },
  "issues": [
    {"type": "ORPHAN_EPHEMERAL", "count": 92, "paths": ["patches-*.jsonl"], "severity": "high"},
    {"type": "HISTORY_OVERFLOW", "count": 201, "limit": 50, "severity": "medium"},
    {"type": "RUN_STATE_ACTIVE", "count": 1, "paths": [".apatch/apply_session.json"], "severity": "info"}
  ],
  "size_reclaimable": "8.2MB",
  "recommendation": "apatch gc --dry-run"
}
```

Phase 2 default recommendation is **report only**. Phase 3+ may recommend `--safe` when reclaim is safe.

### 4.3 MCP tool

`apatch_gc(target_dir, mode="report")` — same semantics as CLI. Returns `state_update` like all tools. Works **without write lease** (read + bounded delete only).

### 4.4 Doctor integration

`apatch_doctor` gains:

```json
{
  "hygiene": {
    "status": "clean | degraded | critical",
    "orphan_count": 0,
    "unclassified_count": 0,
    "gc_recommendation": "apatch gc --dry-run",
    "layout": "flat | structured"
  }
}
```

| `hygiene.status` | Phase | Agent action |
|------------------|-------|--------------|
| `degraded` | 2+ | Warning; run `apatch_gc(mode=report)`. |
| `critical` | 3+ only | Blocks governed mutations when: stale `inferred` after sunset, `gc_allowed` collision, disk guard, or replay-critical set would be violated — **not** merely “messy repo” without policy breach. |

Failure taxonomy additions: `HYGIENE_DEGRADED`, `HYGIENE_CRITICAL`, `GC_INVARIANT_VIOLATION` (internal — must never surface as silent data loss).

---

## 5. Session lifecycle (Phase 4)

### 5.1 Problem (historical)

`session_end` used to persist `ended_at` only — EPHEMERAL patch JSONL accumulated in repo root without registry wiring.

### 5.2 `session_end` behavior (implemented)

When `session_end` is called (or session times out), **execution layer** (not full GC scan):

1. **Release** run leases — clear `run_lease_id`, set `gc_allowed=true` on session EPHEMERAL entries in registry.
2. **Delete** on-disk session EPHEMERAL files that were released (`delete_gc_allowed_session_ephemerals`).
3. **Release** `write_lease.json` (registry update; file removed on next `gc --safe` if still present).
4. **Rotate** `history/backups/` — deferred to `apatch gc --rotate` (nested session dirs supported).

**Not in session_end:** ledger segment rotation; parsing JSON to infer guards; full-workspace GC scan.

### 5.3 Generate routing (2026-06-11)

`apatch_generate` / `apatch_generate_batch` inside an active session:

- Default `out_path` (`patches.jsonl`, `patches-*.jsonl` at repo root) → **`.apatch/tmp/<session_id>/`** automatically.
- Calls `register_ephemeral_logs` (class EPHEMERAL, session lease).
- Explicit paths under `patches/` (staging manifests) are kept as-is but still registered.

Agents must **not** hand-create `patches-*.jsonl` in repo root. Prefer `apatch_spec_run` (§3K) or governed `session_start` → `generate_batch` → `session_end`.

`spec_run` / `execute_next` resolve the same path via `resolve_ephemeral_logs_path` before `simulate` and `apply_session` (logs land under `.apatch/tmp/<session_id>/`, not repo root).

### 5.4 Invariants

> **No EPHEMERAL artifact survives `session_end` with `gc_allowed=false` or active `run_lease_id`.**

If released EPHEMERAL files remain on disk → `doctor.hygiene` reports `ORPHAN_EPHEMERAL`. Remediation: `apatch_gc(mode="safe")`. Spec: [SPEC-SESSION-LIFECYCLE-1](./specs/SPEC-SESSION-LIFECYCLE-1.md) (attested).

---

## 6. Layout migration (Phase 5 — optional, separate command)

Structured layout improves ergonomics but is **not** required for lifecycle governance on flat paths.

### 6.1 Target layout

```text
.apatch/
  state/           ← STATE
  ledger/          ← LEDGER (+ rotated segments)
  registry/        ← REGISTRY (+ artifacts.jsonl index)
  history/         ← HISTORY (backups/, runs/)
  tmp/             ← EPHEMERAL (session workspace)
  debug/           ← DEBUG
```

Backward-compatible: runtime detects `layout: flat | structured` and resolves paths via `apatch_paths.py`.

### 6.2 Migration command

```bash
apatch layout migrate [--dry-run]
```

One-time structural rewrite. **Not** invoked from `apatch gc`. Spec: SPEC-LAYOUT-1 (optional).

---

## 7. `.gitignore` contract

### 7.1 Committed (dogfood / consumer gate)

```text
.apatch/enforcement.json
.apatch/sandbox.json
# after migrate:
.apatch/state/enforcement.json
.apatch/state/sandbox.json
.apatch/state/agent-identity.json
```

### 7.2 Never committed

```text
.apatch/ledger/ .apatch/registry/ .apatch/history/ .apatch/tmp/ .apatch/debug/
patches-*.jsonl patches.jsonl
```

`init-consumer` generates correct `.gitignore`. `apatch layout migrate` may rewrite ignore hints; GC does not.

---

## 8. Position in the Engineering Truth stack

```text
Institutional Memory          ← outcome
────────────────────────────
Evidence                      ← coverage, adherence
────────────────────────────
Attestation                   ← TrustChain
────────────────────────────
Verification
────────────────────────────
Execution (RFP-004)
────────────────────────────
Coordination (RFP-014)
────────────────────────────
… Spec / Plan / Design / Intent …
═══════════════════════════════════════════════════════
Artifact Governance (RFP-016)  ← between Execution and Storage
═══════════════════════════════════════════════════════
Storage (filesystem, .trustchain)
```

In the truth stack, AGL is **cross-cutting discipline** (like RFP-005 monitor), not a “truth layer” above Spec. Every layer above produces artifacts; AGL governs their lifecycle.

### Product formula

```text
Before RFP-014:  mutation execution system
After  RFP-014:  interference-aware execution system
After  RFP-016:  self-regulating runtime with bounded entropy
```

Formal one-liner:

> **RFP-016 introduces Artifact Lifecycle Governance — deterministic classification, bounded lifetime, and controlled entropy for all runtime-generated artifacts.**

---

## 9. Implementation phases

**Principle:** law before enforcement — classify before delete; GC ⊥ migration.

### Phase 1 — Classification lock + provenance recording (no deletion)

- [ ] `register_artifact()` API + append-only registry index
- [ ] **Same hook:** `lineage` fields + `provenance.jsonl` edge append (§3.6)
- [ ] Wire registration into all apatch write-paths (audit table in SPEC-HYGIENE-1)
- [ ] Retroactive scanner (`provenance: inferred`) with **sunset counter** (§3.4)
- [ ] `doctor.hygiene` — warnings Phase 1; inference sunset → critical Phase 3+
- [ ] SPEC: [SPEC-HYGIENE-CORE](./specs/SPEC-HYGIENE-CORE.md) + [SPEC-HYGIENE-1](./specs/SPEC-HYGIENE-1.md)

### Phase 2 — GC as advisory system

- [ ] `gc_report()` / `apatch gc --dry-run` / `apatch_gc(mode=report)`
- [ ] Report is source of truth; **GC has no delete permission**
- [ ] Zero `UNCLASSIFIED` for all **registered** write-paths; inferred count tracked with sunset

### Phase 3 — Safe deletion boundary

- [ ] `gc_safe()`, `gc_rotate()` — EPHEMERAL, DEBUG, GARBAGE only
- [ ] Replay-equivalence invariant enforced (§10)
- [ ] `doctor` may set `critical` and block spec_run (narrow guards)
- [x] SPEC: SPEC-GC-1 (attested)

### Phase 4 — Session lifecycle enforcement

- [ ] `session_end` updates registry (`run_lease_id`, `gc_allowed`) — execution owns semantics
- [ ] RUN_STATE lease acquire/release on apply_session / spec_run transitions
- [x] SPEC: SPEC-SESSION-LIFECYCLE-1 (attested)

### Phase 5 — Layout migration (optional, semver minor)

- [ ] `apatch_paths.py` abstraction
- [ ] `apatch layout migrate` (not `gc --migrate`)
- [ ] Flat fallback until consumer opts in
- [x] SPEC: SPEC-LAYOUT-1 (attested)

### Appendix A — Dogfood one-off (not product code)

Manual cleanup on the apatch repo after Phase 2 report exists: remove known GARBAGE benchmarks, orphan root JSONL, run `apatch gc --dry-run` to verify classification. Do not encode repo-specific counts in the RFP body.

---

## 10. GC safety invariant (machine-enforced)

### Primary invariant

> **GC may not modify anything that can affect replay equivalence.**

### 10.1 Static closure (no dynamic “smart” set)

`REPLAY_CRITICAL` is **not** computed by reading runtime JSON. It is the union of:

1. **STATIC_REPLAY_CRITICAL_PATHS** — closed, versioned list in code (path globs + layout variants).
2. **Registry flags** — any artifact with `replay_critical=true` or `gc_allowed=false` or non-null `run_lease_id`.

```python
# gc_safe() / gc_rotate() — deterministic, no execution semantics
STATIC_REPLAY_CRITICAL_PATHS = frozenset({...})  # versioned in SPEC-HYGIENE-CORE

def gc_delete_candidates(registry, paths) -> set[Path]:
    blocked = STATIC_REPLAY_CRITICAL_PATHS | registry.replay_blocked_paths()
    return {p for p in paths if p not in blocked and registry.gc_allowed(p)}

assert gc_targets <= gc_delete_candidates(...)
```

Minimum static paths (flat layout):

| Glob / path | Reason |
|-------------|--------|
| `.trustchain/**` | attestation evidence |
| `.apatch/events.jsonl`, `.apatch/ledger/**` | domain event replay |
| `.apatch/session_state.json`, `.apatch/enforcement.json`, `.apatch/sandbox.json`, `.apatch/policy.lock.json` | STATE |
| `.apatch/inclusion.jsonl` | У3 inclusion evidence |

**Active resume** (`apply_session.json`, `spec_run.json`, checkpoint backups) is protected via **lease + `gc_allowed` on registry entries**, not by GC parsing those files. Execution sets flags when `continue=true`; clears on chunk/run complete.

`STATE` and `LEDGER` classes ⊆ static set; RUN_STATE ⊆ lease/registry flags.

If GC violates this internally → `GC_INVARIANT_VIOLATION`; operation aborts with no partial delete.

**GC is lossless for replay-critical artifacts, and policy-bound for everything else.**

Ledger **segment rotation** (append new segment, retain old) is not deletion — separate explicit policy, never truncate bytes required for replay.

---

## 11. Closed execution cycle

```text
spec → run → interference → execution → artifacts → gc → clean state
  ↑                                                          │
  └──────────────────────────────────────────────────────────┘
                        next spec
```

| Step | RFP | Guarantee |
|------|-----|-----------|
| spec | RFP-007 | Formal requirements |
| run | RFP-009 | Batch per Rk |
| interference | RFP-014 | Safe order / feasibility |
| execution | RFP-004 | Governed mutation |
| artifacts | RFP-006, 010, 012 | Intent, coverage, adherence |
| gc | **RFP-016** | Bounded entropy |
| clean state | **RFP-016** | Deterministic baseline for next cycle |

Before RFP-016: entropy grows without bound. After: **built-in thermodynamics** — execution produces entropy; AGL consumes it under contract.

---

## 12. Effects on other subsystems

| Subsystem | Before | After |
|-----------|--------|-------|
| **Interference (RFP-014)** | Orphan JSONL inflates graph | Inputs = active specs + registered needles |
| **Doctor** | Health vs mess conflated | `hygiene.status` separates signal from entropy |
| **Reproducibility** | `.apatch/` varies run-to-run | Post-`gc --safe`, reclaimable noise bounded |
| **Consumer onboarding** | Mystery files after first session | `session_end` + report-driven cleanup |
| **Attestation trust** | Stale registry ambiguity | REGISTRY class rebuildable + freshness |

---

## 13. Non-goals

- Remote artifact storage (→ RFP-017+)
- Distributed GC across repos
- Ledger compaction format that deletes historical bytes (separate RFC)
- Changing TrustChain ledger format
- Semantic “which artifact matters more” (human / policy)
- Structural vs semantic resolution (→ RFP-014 / domain.md)

---

## 14. RFP-017 — APG query layer (not a second registration pass)

RFP-016 makes the runtime **clean** (class, lifetime, GC). RFP-017 makes it **self-explaining** (why exists, what depends on it).

| Question | RFP-016 | RFP-017 (APG) |
|----------|---------|---------------|
| What is this file? | class, tier, policy | — |
| What delete? | `gc_allowed`, GC | — |
| **Why does it exist?** | stored in `lineage` (Phase 1) | `apatch provenance why`, graph walk |
| **What breaks if removed?** | `depends_on` + lease flags | reverse dependency query |
| Cross-session story | edges in `provenance.jsonl` | timeline, explain bundle |

**Implement together with Phase 1:** recording `lineage` on every write (cheap — execution context already has session, tool, spec, run_id).

**Implement later (RFP-017):** CLI/MCP explain tools, graph index, doctor `provenance` field, cross-session analytics. No rewrite of write-paths if Phase 1 did recording correctly.

### 14.1 Observability

Dashboards and metrics consume APG queries; they are not a third parallel subsystem.
