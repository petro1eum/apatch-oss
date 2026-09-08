# RFP-026 — Per-Identity Contribution Ledger & Cross-Project Timesheet

> **Status:** Draft v1 · **Date:** 2026-06-18 · **Owner:** apatch product
> **Package context:** 0.7.0 — turns the already-signed governed ledger into a **per-identity, cross-project time & contribution record**
> **Depends on:** [RFP-006](./RFP-006-artifact-anchored-intent.md) (artifact-anchored session) · [RFP-007](./RFP-007-executable-specifications.md) (specs) · [RFP-005](./RFP-005-reference-monitor.md) (Ed25519 ledger, CA-issued identity) · [RFP-010–012](./specs/SPEC-COVERAGE-1.md) (coverage / plan / adherence)
> **Refines:** [RFP-025](./RFP-025-avatar-foundation.md) **AF-5** (ContributionEvent adapter) + **AF-8** (Platform CA `key_id` on events) — RFP-026 is the *concrete engine*; RFP-025 stays the avatar/asset north star
> **External consumers:** HC ADR-006 Unified Contribution Event Log (external reference; not included in this OSS snapshot) · ADR-004 Identity Semantic Namespaces (external reference; not included in this OSS snapshot) · ADR-007 Avatar (external reference; not included in this OSS snapshot)
> **Vision:** [vision.md](./vision.md)

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| C26-A | ContributionEvent schema v3 — deterministic, signed record per attested session: `event_id` (idempotency key), signed producer `created_at`, `identity.key_id`, `project.id`, `session` (intent, artifacts, `started_at`/`ended_at`/`duration_sec`), `volume` (ops/files/insertions/deletions), `proof_ref` (op_ids, ledger HEAD); v1/v2 remain readable | MUST |
| C26-B | Emit at session boundary — on `session_end`/`attest`, apatch writes one signed `contribution:<key_id>@<session_id>` leaf to TrustChain (no parallel signer; same `tc commit` path); skipped only when no mutation occurred | MUST |
| C26-C | Identity = TrustChain certificate — `identity.key_id` + `cert_fingerprint` resolved from the user's Platform-CA-issued cert (`~/.apatch/identity/**/agent.crt`); `legacy_identity` fallback (agent_id) when no CA cert, marked `trust_level: audit` not `attested` | MUST |
| C26-D | Cross-project by identity — `project.id` stable across machines (git remote URL hash, fallback normalized-root hash); records from one global ledger (`~/.trustchain`) aggregate per `key_id` across all projects | MUST |
| C26-E | `apatch timesheet` CLI — aggregate contribution events; group by `--by` identity/project/spec/day (combinable); report time (session duration) and volume; `--since`/`--until`, `--agent`, `--project`; `--format` json or md | MUST |
| C26-F | Verifiable — `apatch timesheet --verify` re-derives each event from the raw signed ledger ops and flags mismatch (`drift`), missing signature, or unknown key; exit non-zero on tamper | MUST |
| C26-G | Multi-user, privacy-bounded — per-`key_id` breakdown for teams; events carry no source code, secrets, or prompt content (paths, hashes, counts only), consistent with RFP-025 AF-3 | MUST |
| C26-H | Active-effort estimate (opt-in) — `--idle-gap <min>` splits a session span at gaps between ledger ops longer than the threshold, yielding `active_sec` alongside wall-clock `duration_sec` | SHOULD |
| C26-I | Read-only MCP `apatch_timesheet` — parity with CLI for in-chat queries; never mutates | SHOULD |
| C26-J | ADR-006 adapter shape — emitted event maps 1:1 to HC `ContributionEvent` (`source: "apatch"`, `trust_level`, `idempotency_key`) so the HC bridge (RFP-025 AF-5) consumes it without transformation | SHOULD |
| C26-K | Executable spec `SPEC-CONTRIB-TIMESHEET-1` attested with `## RFP traceability` to this table | MUST |

Canonical ids: this section.

---

## 1. Problem

apatch already produces the *raw, signed evidence* of work, but never assembles it into the artifact a contractor or team actually needs.

```text
Intent → Session → Mutation → Verification → Attestation   (each step Ed25519-signed)
```

Every governed operation is already stamped with `session_id`, `intent`, `artifacts[]`, `started_at`/`ended_at`, `op_ids`, and the agent identity, and committed to a **single global ledger** (`~/.trustchain`) that already spans every project on the machine. What is missing:

| Need | Gap today |
|------|-----------|
| «How many hours did I spend on project X this week?» | Session spans live in the ledger but there is no duration rollup |
| «Who contributed what across our N projects?» | No per-identity, cross-project aggregation keyed by certificate |
| «Prove this timesheet is real» | Evidence is signed, but no derived, re-verifiable report over it |
| «Feed real work into HC Tracker» | apatch ledger ≠ ADR-006 `ContributionEvent` (RFP-025 AF-5 names the adapter; nothing emits it) |

The user works **across many projects** and needs **automatic** time/contribution tracking keyed to **their own TrustChain certificate**. This is not new instrumentation — it is a **derivation + receipt + report** layer over data that is already cryptographically captured.

---

## 2. Solution — three layers over the signed ledger

```text
(already exist) signed ops: apply / attest, with session spans + identity
        │
        ▼
[L1] Contribution receipt  — one signed ContributionEvent leaf per attested session   (C26-A/B)
        │
        ▼
[L2] Per-identity index    — events keyed by key_id, project.id stable cross-machine   (C26-C/D)
        │
        ▼
[L3] Timesheet + verify    — apatch timesheet (CLI/MCP), --verify re-derives from ops  (C26-E/F/I)
```

**Principle (consistent with RFP-024/025):** apatch does **not** invent contribution facts. The receipt is a *deterministic projection of already-signed primitives*; `--verify` re-computes it from the raw ledger, so a timesheet cannot be forged without forging the underlying Ed25519 chain.

**Why a receipt and not pure on-the-fly derivation** (answers «sign at checkpoint?»): re-aggregating raw ops every query is possible but (a) loses a stable billing unit, (b) needs full ledger walks. One compact **signed leaf per session** pins duration + volume + identity at the natural boundary (`session_end`/`attest`), is O(1) to read, and is itself the ADR-006 `ContributionEvent`. Raw-op re-derivation is retained as the **audit/verify path** (C26-F), not the hot path.

---

## 3. Identity model — contribution belongs to a certificate

Each user has a Platform-CA-issued certificate (`~/.apatch/identity/<profile>/agent.crt`, chained to `trustchain_platform_ca.crt`). RFP-026 keys every contribution to that certificate:

| Field | Source | Purpose |
|-------|--------|---------|
| `identity.key_id` | SPKI hash of the cert public key | Stable, machine-independent user key (the «who») |
| `identity.cert_fingerprint` | SHA-256 of `agent.crt` (DER) | Pin the exact credential used |
| `identity.agent_id` | enrollment (`apatch-edcher-prod`) | Human-readable label |
| `identity.ca` | `platform` \| `legacy` | `legacy` (no CA cert) ⇒ `trust_level: audit`, excluded from billable totals unless `--include-audit` |

Aligns with HC ADR-004 (external reference; not included in this OSS snapshot)/ADR-005 (external reference; not included in this OSS snapshot) and RFP-025 **AF-8**. Multi-user teams: each member signs with their own cert; the timesheet groups by `key_id` (C26-G).

### Cross-project key — `project.id`

Stable across clones/machines so the same project aggregates everywhere:

```text
project.id = sha256(git_remote_url_normalized)[:16]      # primary
           ↳ fallback: sha256(realpath(repo_root))[:16]   # no remote (e.g. this repo)
```

Stored on each event so `apatch timesheet --by project` works over the global ledger without per-project config.

---

## 4. Data model — `ContributionEvent` schema v3

```json
{
  "schema_version": 3,
  "kind": "fact",
  "event_id": "sha256(key_id|session_id|ledger_head)[:32]",
  "source": "apatch",
  "trust_level": "attested",
  "idempotency_key": "<event_id>",
  "identity": {
    "key_id": "…", "cert_fingerprint": "sha256:…",
    "agent_id": "apatch-edcher-prod", "ca": "platform"
  },
  "project": { "id": "…", "name": "apatch", "remote": "git@…|null" },
  "session": {
    "session_id": "apatch_sess_…", "intent": "…",
    "artifacts": ["spec:SPEC-X#R3", "ticket:…"],
    "started_at": 0.0, "ended_at": 0.0,
    "duration_sec": 0.0, "active_sec": null
  },
  "volume": { "ops": 0, "files_touched": 0, "insertions": 0, "deletions": 0 },
  "proof_ref": { "op_ids": ["op_…"], "head": "…", "committed_at": 0.0 },
  "created_at": "2026-07-18T12:00:00+00:00",
  "signature": "ed25519(canonical_json_without_signature)"
}
```

`idempotency_key == event_id` (C26-J) → safe re-emit; an existing receipt is returned byte-for-byte instead of being re-signed. `signature` covers the canonical event minus `signature`, including `created_at`, and uses the same key that signs the ledger. `created_at` states when the producer built the receipt; it is not an independent time authority. A consumer validates certificate authority at its own receipt time unless `proof_ref` supplies an externally verifiable inclusion timestamp.

---

## 5. Phase plan & deliverables

### Phase 1 — Receipt emission (C26-A, C26-B, C26-C)

| Output | Description |
|--------|-------------|
| `apatch/contribution.py` | `ContributionEvent` dataclass + `build_event(session, ledger)` + canonical-JSON signer |
| `runtime` hook | `session_end`/`attest` emits the signed leaf (artifact kind `contribution`); no-op when 0 mutations |
| Identity resolver | `resolve_identity()` → `key_id`/fingerprint from `agent.crt`; `legacy` fallback |

(verify: `pytest tests/test_contribution_event.py -q`)

### Phase 2 — Cross-project index + timesheet (C26-D, C26-E, C26-F, C26-G, C26-H)

| Output | Description |
|--------|-------------|
| `apatch/timesheet.py` | Read `contribution` leaves from global ledger; group/aggregate; `--verify` re-derives from raw ops |
| CLI `apatch timesheet` | `--by`, `--since/--until`, `--agent`, `--project`, `--format json\|md`, `--idle-gap`, `--verify`, `--include-audit` |
| `project.id` resolver | git-remote → root-path fallback |

(verify: `pytest tests/test_timesheet.py -q`)

### Phase 3 — Surfaces & HC alignment (C26-I, C26-J)

| Output | Description |
|--------|-------------|
| MCP `apatch_timesheet` | Read-only parity |
| ADR-006 mapping doc | Field table `ContributionEvent` ↔ HC event log; consumed by RFP-025 AF-5 bridge |

(verify: `pytest tests/test_timesheet_mcp.py -q`)

---

## 6. Relationship to RFP-025 (no duplication)

| Concern | RFP-025 | RFP-026 |
|---------|---------|---------|
| `asset_summary` (avatar read model) | ✅ owns | consumes events as one input |
| ContributionEvent **adapter** (AF-5) | names the requirement | **implements the emitter + schema** |
| Platform CA `key_id` on events (AF-8) | names the hook | **implements identity resolution + keying** |
| Timesheet / hours product | — | ✅ owns (`apatch timesheet`) |
| HC economy (PI/clearing) | out of scope | out of scope |

RFP-026 is the substrate; RFP-025's avatar/export builds on top. If RFP-025 ships first, AF-5/AF-8 should be marked `waiver: implemented in RFP-026` in its traceability.

---

## 7. Non-goals

- Payroll/billing rates, invoicing, or money math (HC layer / out of scope; apatch emits hours + proof only).
- Wall-clock keystroke/IDE-activity tracking outside governed sessions (only attested work is counted; that is the integrity guarantee, not a limitation).
- LLM estimation of «effort» or value scoring (deterministic counts only).
- Editing/backfilling historical sessions that predate receipt emission (derivable via `--verify` from raw ops, but not re-signed).
- New cryptographic primitive — reuses RFP-005 Ed25519 + Platform CA.

---

## 8. Open questions

| # | Question | Default if unanswered |
|---|----------|-----------------------|
| Q1 | Default `--idle-gap`? | Off by default; report wall-clock `duration_sec`. `active_sec` only with explicit `--idle-gap N` |
| Q2 | Receipts in global `~/.trustchain` (cross-project, recommended) or per-project `.apatch/`? | **Global** — required for cross-project per-identity rollup (C26-D); per-project mirror optional |
| Q3 | Team aggregation: local multi-cert ledger now, or defer central collection to HC? | Local per-`key_id` now; central collection is HC/ADR-006 scope |

---

## 9. References

- [rfp-authoring.md](./rfp-authoring.md) · [spec-authoring.md](./spec-authoring.md) — authoring standards
- [RFP-025](./RFP-025-avatar-foundation.md) — avatar/asset north star (AF-5/AF-8)
- [RFP-006](./RFP-006-artifact-anchored-intent.md) — artifact-anchored session (the carrier)
- [RFP-005](./RFP-005-reference-monitor.md) — Ed25519 ledger + CA identity
- HC ADR-006 (external reference; not included in this OSS snapshot) — downstream event-log consumer
