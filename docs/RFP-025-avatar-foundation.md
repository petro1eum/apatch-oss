# RFP-025 — Avatar Foundation & Asset Pipeline

> **Status:** Draft v1 · **Date:** 2026-06-13 · **Owner:** apatch product  
> **Package context:** 0.7.0 — closes the **last foundation brick** between governed apatch runtime and Human Capital Avatar (ADR-007)  
> **Depends on:** [RFP-006](./RFP-006-artifact-anchored-intent.md) (artifact session) · [RFP-007](./RFP-007-executable-specifications.md) (specs) · [RFP-010–012](./specs/SPEC-COVERAGE-1.md) (coverage, plan, adherence) · [RFP-023](./RFP-023-rfp-spec-coverage.md) (doc gate) · [RFP-024](./RFP-024-needles-scaffold.md) (agent onboarding scaffold)  
> **Blocks:** GTM Stage 2–3 (GTM §3–6 (external reference; not included in this OSS snapshot)) · ADR-006 Phase D (external reference; not included in this OSS snapshot) (apatch → ContributionEvent) · honest «asset-first» narrative  
> **Vision:** [vision.md](./vision.md) (Avatar Compiler) · **External:** HC ADR-007 (external reference; not included in this OSS snapshot) · technical_debt § TD-010 (external reference; not included in this OSS snapshot)

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| AF-1 | **Avatar Compiler (read model):** deterministic `asset_summary` from TrustChain + spec coverage + attested artifacts — artifact counts, methodology ids, no LLM scoring | MUST |
| AF-2 | **Discovery not upload:** asset forms from governed usage; no manual «build my profile» flow in apatch core | MUST |
| AF-3 | **Opt-in economic boundary:** apatch core responses MUST NOT expose PI, GPI, Creator Bonus, or clearing math by default (HC layer only, explicit consent) | MUST |
| AF-4 | **Portable export bundle:** signed, content-safe export (specs metadata + attestation refs + dependency graph — not employer code/secrets) per ADR-007 non-invasiveness | MUST |
| AF-5 | **ContributionEvent adapter (ADR-006-D):** attested session lifecycle → `ContributionEvent` fact (`source: apatch`, `trust_level: attested`) with `idempotency_key` | MUST |
| AF-6 | **Company lens (GTM §5):** aggregate DTO for org — spec coverage by team/repo, attestation velocity, bus-factor hints from interference/coverage graphs | SHOULD |
| AF-7 | **Aha surface:** `apatch_project_status` / report embeds `asset_summary` + human-readable «N attested artifacts» without opening Tracker | SHOULD |
| AF-8 | **Identity hook:** events carry Platform CA `key_id`; `legacy_identity` fallback until HC IRS (ADR-004) lands | MUST |
| AF-9 | **Executable spec:** `SPEC-AVATAR-FOUNDATION-1` attested with traceability to this table | MUST |

Canonical ids: this section. Narrative status below mirrors these rows.

---

## 1. Problem

### Engineering spine ≠ economic asset

apatch today reliably runs:

```text
Intent → Session → Mutation → Verification → Attestation
```

TrustChain, executable specs, MCP, doc gate (RFP-023/024), and diagnostics (RFP-022) close **operational reliability** and **agent onboarding**.

They do **not** yet close **GTM Stage 2–3**:

| GTM promise | Gap today |
|-------------|-----------|
| «173 attested artifacts — they belong to you» | Counts scattered across ledger, coverage, reports — no unified **asset_summary** |
| Avatar = portable methodology | Repo is portable; **no standard export bundle** for HC / Tracker |
| Tracker timeline from real events | apatch ledger ≠ HC `ContributionEvent` log (ADR-006 (external reference; not included in this OSS snapshot) four islands, TD-010 (external reference; not included in this OSS snapshot)) |
| Company analytics without KM project | Partial (`spec_coverage`, `interference`) — no **org lens** product DTO |

**Human Capital built the market and UI; apatch is the missing engine that turns daily work into a defensible, portable asset.** Without RFP-025, ADR-007 reads as product definition without a shipping bridge.

### Why now (after RFP-021–024)

| Prior wave | What it unlocked |
|------------|------------------|
| RFP-021 | Partner-grade governed cycles |
| RFP-022 | Diagnose / understanding layer |
| RFP-023 | RFP→SPEC fidelity (no fake «phase done») |
| RFP-024 | Agent stays in MCP from lint → spec_run |

Next bottleneck is **asset formation**, not another lint rule.

---

## 2. Solution — five-stage asset pipeline

From [vision.md](./vision.md):

```text
Event sources (sessions, attestations, spec graph)
   ↓
TrustChain attestation (proof)
   ↓
Contribution graph (ADR-006 — history of formation)
   ↓
Avatar Compiler (read-only aggregation → asset_summary)   ← RFP-025 core
   ↓
Surfaces: CLI/MCP status · export bundle · HC Tracker (opt-in)
```

**Avatar Compiler** properties:

| Property | Rule |
|----------|------|
| Read-only for user | No extra forms; aggregates existing attestations |
| Write-creating for market | Produces portable asset DTO HC can consume |
| Non-invasive | Metadata + methodology + proofs — not code content, not LLM prompts |
| Deterministic | Same ledger → same summary (reproducible for audit) |

---

## 3. GTM alignment

| GTM Stage | RFP-025 deliverable | Acceptance ids |
|-----------|---------------------|----------------|
| **1** apatch + TrustChain | Company lens DTO (reuse coverage/interference) | AF-6 |
| **2** Avatar | `asset_summary` + portable export bundle | AF-1, AF-2, AF-4 |
| **3** Tracker mirror | ContributionEvent adapter + Aha in status/report | AF-5, AF-7, AF-8 |
| **4** HC economy | **Out of scope** — HC consumers read event log; apatch does not implement PI/clearing | AF-3 |

See GTM.md §11 (external reference; not included in this OSS snapshot) execution table — RFP-025 replaces ad-hoc «roadmap in matrix» as the product north star.

---

## 4. Phase plan

### Phase 1 — Avatar Compiler (apatch-only)

**Goal:** Ship `asset_summary` without HC dependency.

| Output | Description |
|--------|-------------|
| `apatch/avatar_compiler.py` | Read ledger + `.apatch/specs/` coverage + plan registry |
| `asset_summary` schema v1 | `{ artifact_count, spec_ids[], requirement_states{}, methodology_tags[], attested_at_range, key_id }` |
| MCP `apatch_asset_summary` | Optional alias embedded in `apatch_project_status` |
| CLI `apatch asset summary --json` | Parity |

(verify: `pytest tests/test_avatar_compiler.py -q`)

### Phase 2 — Portable export (AF-4)

**Goal:** Signed bundle a professional can move between employers / AI tools.

| Output | Description |
|--------|-------------|
| `apatch export avatar --out .apatch/avatar-bundle/` | Specs (paths + hashes), attestation chain refs, dependency graph — **no** protected src blobs |
| Bundle manifest | `avatar-bundle.json` + TrustChain signature over manifest hash |
| ADR-007 alignment | Document non-invasive fields in bundle schema |

### Phase 3 — HC event bridge (AF-5, AF-8)

**Goal:** Close ADR-006 Phase D from apatch side; HC owns Postgres table + API.

| apatch side | HC side (coordination, not owned by this RFP) |
|-------------|-----------------------------------------------|
| Adapter: attest → POST ContributionEvent fact | `contribution_events` table (ADR-006 §7) |
| `idempotency_key = governed_session_id + op_id` | Append-only ingest API |
| `legacy_identity` when IRS unavailable | IRS backfill Phase G |

Until HC API exists: adapter **queues** to `.apatch/outbox/contribution_events.jsonl` (durable, replayable).

### Phase 4 — Company & Aha surfaces (AF-6, AF-7)

| Surface | Change |
|---------|--------|
| `apatch_project_status` | `asset_summary` + optional `org_lens` when `target_dir` is monorepo root |
| `apatch report --format md` | Section «Professional capital formed» |
| Notification hook | When `artifact_count` crosses threshold — message string for IDE/CLI (no push infra in v1) |

---

## 5. Tools (target)

| MCP | CLI | Purpose |
|-----|-----|---------|
| `apatch_asset_summary` | `apatch asset summary` | AF-1 compiler output |
| `apatch_avatar_export` | `apatch export avatar` | AF-4 portable bundle |
| `apatch_contribution_emit` | `apatch contribution emit` | AF-5 manual replay / debug outbox |
| *(embed)* `apatch_project_status` | `apatch status --json` | AF-7 Aha fields |

Existing tools unchanged: `apatch_attest`, `apatch_spec_coverage`, `apatch_trustchain_coverage` feed the compiler.

---

## 6. Non-goals

| Non-goal | Where it lives |
|----------|----------------|
| PI / GPI / Creator Bonus computation | HC Platform (ADR-002) |
| Tracker UI / timeline rendering | HC Tracker |
| IRS implementation | HC ADR-004 |
| Kafka / central event store | HC ADR-006 storage choice |
| LLM «skill score» or resume generation | Forbidden — conflicts with ADR-007 |
| Replacing TrustChain with HC witness | Adapter only; ledger remains SoT for apatch |
| PyPI marketplace (RFP-019 L2) | Parallel track; not blocked on AF-1 |

---

## 7. Relationship to other docs

| Document | Role |
|----------|------|
| [apatch-transformation-matrix.md](./apatch-transformation-matrix.md) | **Engineering status** appendix (RFP-021–024 shipped) |
| [agent-onboarding.md](./agent-onboarding.md) | Stage 1 adoption — feeds artifact accumulation |
| [vision.md](./vision.md) | Avatar Compiler concept — RFP-025 makes it executable |
| HC `what-is-avatar.md` | Public language — must match AF-2, AF-3, AF-4 |
| HC `start-here.md` | Points to ADR-007 — bundle export is the tangible «avatar» |

**Honest platform statement (for investors and TD-010):**

> HC built market, federation, economics simulation, and Tracker.  
> apatch built governed proof + methodology execution.  
> RFP-025 connects them — **after this, «avatar» is a file format + event stream, not a slide.**

---

## 8. Success metrics

| Metric | Stage | Target (design partner) |
|--------|-------|-------------------------|
| Attested artifacts / active user / month | 1 | ↑ (baseline from ledger) |
| Users with `asset_summary.artifact_count ≥ 10` | 2 | ≥ 1 per pilot org |
| Export bundles generated | 2 | reproducible verify |
| ContributionEvents emitted from apatch | 3 | 100% attest → outbox or HC |
| Company dashboard views | 1–2 | bus-factor + coverage exported |

---

## 9. Open questions

1. **Bundle scope:** include attested plan artifacts (`plan:SPEC-X@vN`) in export by default?
2. **Outbox vs live POST:** ship Phase 3 with outbox-only until HC ingest API is staging-ready?
3. **Monorepo org lens:** single `target_dir` vs explicit `--team` glob in v1?

Resolve in `SPEC-AVATAR-FOUNDATION-1` decision_plan before implementation needles.

---

**Version:** 1.0 draft  
**Next:** `SPEC-AVATAR-FOUNDATION-1.md` + traceability table · update [transformation-matrix](./apatch-transformation-matrix.md) north star pointer
