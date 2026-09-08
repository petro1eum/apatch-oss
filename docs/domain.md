# Engineering Truth — Domain Model

> **Простыми словами (без кода):** [engineering-truth-overview.md](./engineering-truth-overview.md)  
> RFP-004 (mutation runtime) · Engineering Truth stack (2026-06) · `schema_version: 2`

## Product frame

Three truths, three layers — each **adds** a type of truth; none replaces the previous:

| Layer | Question | Typical tooling |
|-------|----------|-----------------|
| **Code Truth** | What is in the tree? | Git |
| **Runtime Truth** | Does it work? | CI, tests |
| **Engineering Truth** | What did we intend, why, and can we prove it? | apatch + TrustChain |

**Kernel:**

```text
trust_chain  = truth substrate       (cryptographic memory — WHO / WHEN / WHAT signed)
apatch       = intent preservation engine  (formal Spec / Design / Plan → governed execution → evidence)
```

Platform PKI (CA, enroll, agent identity) is **optional for local dev**, **core for org-scale proof** —
it anchors *who* may sign ledger operations, not *what* engineering semantics mean (that is apatch).

Consumer surfaces (Agent chat, Platform UI/inbox, panel) are **adapters** into the kernel, not substitutes.

---

## Domain knowledge vs execution substrate (placement policy)

**Execution substrate** (apatch core) owns: spec/plan/run lifecycle, sandbox, interference **machinery**, tag **grammar**, and how domain artifacts become executable specs with attestation.

**Domain knowledge** (methodologies, audit models, product golden instances) defaults to **consumer repos** — each product/org ships `docs/specs/SPEC-*.md`, schemas, validators, and fixtures under its own tree (RFP-007 §3).

**Reference domains in apatch** (dogfood) exist to prove platform primitives end-to-end — not to accumulate every future methodology in core. Today: [SPEC-UX-SPECIALIST-1](./specs/SPEC-UX-SPECIALIST-1.md) (RFP-015) validates Phase 4 tags + executable audit artifacts. Product fixes (e.g. Sales Pipeline code) stay in consumer specs; apatch holds the reference schema, thin validator, and JASON test fixture only.

| Lives in apatch core | Lives in consumer (default) |
|----------------------|-----------------------------|
| RFP-007 spec engine, RFP-009 `spec_run`, RFP-014 interference | `docs/specs/SPEC-<DOMAIN>-*.md` per product |
| Tag vocabulary (`domain:`, `layer:`, `role:`, `product:`) | Domain JSON schemas (`manifests/*.schema.json`) |
| Interference L0–L3 stack (see RFP-014 Phase 4) | Product audit instances, tenant fixtures |
| **One** reference domain package (UX today) | Security / perf / a11y methodologies (unless extending platform grammar) |

### Circuit breaker: domain-validator extraction

> **Rule:** When a **second** domain-validator module is proposed for `apatch/` (e.g. `security_audit.py` alongside `ux_audit.py`), **both** the incumbent and the new validator are extracted into optional packages (`apatch-domains-ux`, `apatch-domains-security`, …). Core keeps only interference machinery and generic hooks — not a growing domain monolith.

| Count in `apatch/*_audit.py` (or equivalent) | Action |
|-----------------------------------------------|--------|
| **1** (current: `ux_audit.py`) | OK — reference dogfood in core |
| **≥ 2** | **Mandatory** extraction before merge; no second in-tree domain validator |

This is an automatic packaging boundary, not a deferred “maybe later.” Without it, core accumulates `ux_audit.py`, `security_audit.py`, `perf_audit.py`, … and apatch becomes a domain knowledge monolith instead of an execution kernel.

Reference: [RFP-015](./RFP-015-ux-specialist.md) (UX reference domain) · [RFP-014 Phase 4](./RFP-014-spec-interference-detection.md) (L0 tags).

---

## Engineering Truth Stack

Each RFP **formalizes a layer**; it does not create a parallel product branch. Layers **accumulate** —
there is no false choice of Spec *or* Plan *or* Design *or* Evidence.

```text
Institutional Memory          ← outcome: reconstruct the full stack years later
────────────────────────────
Evidence                      ← coverage, adherence, inclusion, receipts
────────────────────────────
Attestation                   ← TrustChain commit, Ed25519, enrolled identity
────────────────────────────
Verification                  ← pytest, arch-check, semantic verify, ci-gate
────────────────────────────
Execution                     ← governed mutation (apply_session, strip, …)
────────────────────────────
Coordination                  ← interference detection, safe ordering (RFP-014)
────────────────────────────
Plan                          ← executable change contract per Rk
────────────────────────────
Design                        ← architectural structure and boundaries (RFP-013, planned)
────────────────────────────
Spec                          ← formal requirements with per-Rk verify
────────────────────────────
Intent                        ← session goal, artifact anchors
════════════════════════════
Runtime Hygiene               ← CROSS-CUTTING: artifact lifecycle, GC (RFP-016)
```

### Layer questions

| Layer | Question | Formal artifact / mechanism |
|-------|----------|----------------------------|
| **Intent** | What do we want overall? | `apatch_session_start`, ledger intent op, `artifacts[]` |
| **Spec** | What must exist? | `docs/specs/SPEC-*.md`, `spec:SPEC-X`, Rk + `(verify: …)` |
| **Design** | How / why is the system structured this way? | `design:SPEC-X@vN` (planned); components, boundaries |
| **Plan** | What exactly will we change? | `plan:SPEC-X@vN`; `decision_plan` + `execution_plan` |
| **Coordination** | Are specs compatible? Safe order? | `apatch_spec_interference`, `spec_schedule`, conflict graph |
| **Execution** | What actually changed on disk? | mutations, `.apatch/notarized_index.json` |
| **Verification** | Does it work? | `apatch_verify_run`, spec `(verify:)`, arch/db checks |
| **Attestation** | Who confirms this step? | `apatch_attest` → `.trustchain/`; enrolled **named** identity per op |
| **Evidence** | Provable years later? | coverage, adherence, history, Merkle inclusion |
| **Institutional Memory** | Full reconstructible story? | ledger + plan/design stores + spec hash binding |
| *Runtime Hygiene* | *Is the system clean?* | *`apatch gc`, artifact classification, EPHEMERAL invariant (RFP-016)* |

**Marketing shorthand** (elevator): `Spec → Plan → Execution → Evidence`.  
**Architecture contract** (full stack): table above — Design, Verification, and Attestation are first-class, not folded away.

### Analogy

```text
Filesystem  →  code on disk
Git         →  history of code changes          (+ layer)
CI          →  “does this commit work?”         (+ layer)
trust_chain →  “who signed what, in what order?” (+ layer)
apatch      →  “under which spec/plan, with what deviation?” (+ layer)
Platform PKI→  “under which org identity?”      (+ layer, enterprise)
```

---

## RFP mapping (layers → specs)

| RFP / spec | Layer(s) | Role |
|------------|----------|------|
| RFP-004 | Execution, Session | Mutation runtime, lifecycle, sandbox hooks |
| RFP-005 | Attestation | Trust anchor, enrolled agent identity |
| RFP-006 | Intent, Evidence | Artifact-anchored intent, `trustchain_coverage` |
| RFP-007 | Spec | Executable specifications, `apatch_spec_*` |
| RFP-008 | Execution | `apatch_execute_next` per Rk |
| RFP-009 | Spec → Execution | `apatch_spec_run` batch orchestrator |
| RFP-010 | Evidence | [SPEC-COVERAGE-1](./specs/SPEC-COVERAGE-1.md) staleness |
| RFP-011 | Plan | [SPEC-PLAN-ARTIFACT-1](./specs/SPEC-PLAN-ARTIFACT-1.md) decision + execution |
| RFP-012 | Evidence | [SPEC-ADHERENCE-1](./specs/SPEC-ADHERENCE-1.md) plan vs fact |
| RFP-013 (planned) | Design | Architectural design artifact |
| RFP-014 ✅ Phase 1/1.5/2/4 | Coordination | [SPEC-INTERFERENCE-1](./specs/SPEC-INTERFERENCE-1.md) … Phase 3: [SPEC-INTERFERENCE-3](./specs/SPEC-INTERFERENCE-3.md); Phase 4 tags (L0) via [SPEC-UX-SPECIALIST-1](./specs/SPEC-UX-SPECIALIST-1.md) |
| RFP-015 (reference) | Spec (domain dogfood) | [SPEC-UX-SPECIALIST-1](./specs/SPEC-UX-SPECIALIST-1.md) — UX audit methodology; see placement policy above |
| RFP-016 (✅ MVP) | Artifact Governance (AGL) | [RFP-016](./RFP-016-runtime-hygiene.md) — registry, gc reconcile/safe/rotate, EPHEMERAL tmp routing + session_end purge; chain attested |
| RFP-018 ✅ | Diagnose (MVP) | [SPEC-BUILD-DIAGNOSE-1](./specs/SPEC-BUILD-DIAGNOSE-1.md) · extended by RFP-022 |
| RFP-019 L1 ✅ | MCP scale | [SPEC-MCP-SCALE-1](./specs/SPEC-MCP-SCALE-1.md) lanes, hygiene, profiles |
| RFP-020 ✅ | Product views | [SPEC-PROJECT-STATUS-1](./specs/SPEC-PROJECT-STATUS-1.md) … [SPEC-REPORT-1](./specs/SPEC-REPORT-1.md) |
| RFP-021 ✅ | Agent reliability | [SPEC-SESSION-RECOVERY-1](./specs/SPEC-SESSION-RECOVERY-1.md) … [SPEC-DESIGN-PARTNER-1](./specs/SPEC-DESIGN-PARTNER-1.md) |
| RFP-022 ✅ Ph1–3 | Understanding / diagnostics | [SPEC-DIAGNOSTIC-GRAPH-1](./specs/SPEC-DIAGNOSTIC-GRAPH-1.md) … [SPEC-KNOWLEDGE-GRAPH-1](./specs/SPEC-KNOWLEDGE-GRAPH-1.md) |
| RFP-023 ✅ | Doc governance + authoring | [SPEC-RFP-COVERAGE-1](./specs/SPEC-RFP-COVERAGE-1.md) RFP→SPEC traceability lint; `apatch spec scaffold --from-contract` / `apatch_spec_scaffold` emits a contract-complete SPEC skeleton from an RFP Acceptance table |
| RFP-033 ✅ | Cross-file refactor impact | SPEC-SCIP-IMPACT-1/2/3 — which **attested** requirements reference a changed symbol across files; native (Python AST, no external tool) or `.scip`; `apatch scip impact` / `apatch_scip`; advisory, never blocks |
| RFP-005 ✅ (gate quality + reality) | Reference monitor | `apatch probe` falsify/regress/ratify (a gate must be able to go RED) + `apatch reality` observed-reality ledger (reality is the source of truth; requirements *discharge* records, `uncovered` = derived debt) |

Plan **decision_plan** partially overlaps Design until RFP-013 ships; then Design holds topology, Plan holds change contract.

### Artifact Governance Layer (RFP-016)

Cross-cutting between Execution and Storage (not a “truth layer” like Spec or Plan):

```text
Execution (RFP-004) → AGL (classify, contract, GC) → Storage
```

**Coordination boundary (RFP-014):** apatch resolves **execution feasibility** (reorder, split,
annotate conflict edges). apatch does **not** resolve **intent conflicts** (priority, merge,
redesign) — those are human or domain policy. See [RFP-014](./RFP-014-spec-interference-detection.md).

**AGL boundary (RFP-016):** Execution sets `run_lease_id` / `gc_allowed` on registry entries;
GC reads registry only. **Phase 1 also records `lineage`** (why-exists edges) — cheap,
one hook with `register_artifact`. **RFP-017 (APG)** adds query/explain only; no second
write-path pass. See [RFP-016 §3.6](./RFP-016-runtime-hygiene.md).

---

## Core invariant (session-level)

Any change through apatch in enforce/governed mode:

1. Bound to **Intent** (and optionally **Artifact[]**)
2. Executed inside a **Session**
3. Represented as **Mutation**(s)
4. Checked via **Verification** when policy requires
5. Closed with **Attestation** or **Rollback**

```text
Artifact[] → Intent → Session → Mutation → Verification → Attestation | Rollback
```

(RFP-006: **Artifact** — typed ref `kind:id@hash`; e.g. `spec:SPEC-42`, `plan:SPEC-42@v1`, `adr:ADR-12`.)

Spec / Design / Plan registration **precedes or wraps** session execution; attestation and evidence **follow** it.

---

## Session (aggregate root)

```text
Session
 ├─ Artifact[]        ← RFP-006 (spec, plan, design, adr, ticket, …)
 ├─ Intent
 ├─ Mutation[]
 ├─ Checkpoint[]
 ├─ Verification[]
 └─ Attestation
```

Policy (sandbox, enforcement) — outside the aggregate, mandatory for governed transitions.

---

## Lifecycle

| State | Meaning |
|-------|---------|
| `draft` | Session opened, intent set |
| `planned` | plan/generate done, disk untouched |
| `applying` | apply in progress |
| `verifying` | tests, arch, sandbox, pipeline |
| `committed` | mutations on disk |
| `attested` | TrustChain committed |
| `failed` | blocked / verify failed |
| `rolled_back` | rollback completed |
| `ended` | session closed |

---

## Verification vs Attestation

| | Verification | Attestation |
|---|--------------|-------------|
| Question | Does it work? | Who changed what, under which intent? |
| Examples | pytest, arch check, ci-gate | TrustChain HEAD, Ed25519, **enrolled agent id** (human or named AI agent) |
| Layer | Verification | Attestation |
| CLI | `apatch verify …` | `apatch attestation show` |

Adherence and coverage are **Evidence** — they compare Plan (expected) to Execution (fact) and spec scope over time.

---

## CLI projection

```bash
apatch session start --intent "ADR-12: extract billing module" \
  --artifact adr:ADR-12 --artifact spec:SPEC-billing@sha256:…
apatch trustchain coverage --artifact spec:SPEC-billing
apatch spec plan register --spec SPEC-billing --plan '{…}'   # MCP: apatch_spec_plan_register
apatch spec run --spec SPEC-billing --plan v1
apatch plan --logs patches.jsonl --json    # enforce: requires session
apatch apply --logs patches.jsonl -y       # enforce: requires planned phase
apatch session status --json
apatch attestation show --json
apatch console
```

**Enforce mode:** `plan` / `apply` route through `MutationRuntime` and reject orphan mutations (`RUNTIME_TRANSITION`).

---

## Persistence

| Path | Layer |
|------|-------|
| `.trustchain/` | Attestation, Evidence (ledger) |
| `.apatch/session_state.json` | Session / Execution lifecycle |
| `.apatch/events.jsonl` | Execution domain events |
| `.apatch/plans/` | Plan artifacts (local canonical copy) |
| `.apatch/notarized_index.json` | Execution (notarized file hashes) |
| `docs/specs/SPEC-*.md` | Spec (source of truth for requirements) |

Institutional Memory = ledger ops + content-addressed plan/design JSON + spec content hash binding — reconstructible without chat logs.
