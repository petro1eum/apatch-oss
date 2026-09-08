# Authoring an RFP for coverage lint (RFP-023)

> **Canonical RFP:** [RFP-023-rfp-spec-coverage.md](./RFP-023-rfp-spec-coverage.md)  
> **SPEC side:** [spec-authoring.md](./spec-authoring.md)

Product intent lives in **RFP**. Executable work lives in **SPEC**. Coverage lint ensures SPEC is a faithful projection — not a shortened rewrite.

## Template

```markdown
# RFP-NNN — Short title

> **Status:** Draft v1 · **Date:** YYYY-MM-DD · **Owner:** team

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| P1-A | First testable outcome | MUST |
| P1-B | Optional stretch goal | MAY |
```

## Rules (`apatch_rfp_lint`)

1. **H1 owns the id.** `# RFP-022 — …` → id `RFP-022`.
2. **`## Acceptance` section** (H2) with markdown table; header row must include `Id` and `Level`.
3. **Stable ids** — `[A-Z][A-Z0-9-]*` (e.g. `P1-A`, `R23-C`, `AR-6`).
4. **Level** — `MUST`, `MAY`, or `SHOULD` (case-insensitive). Default `MUST` if omitted.
5. **Single source of truth** — phase subsections (`### 4.5 Acceptance …`) may keep **Status** narrative, but ids and criteria must mirror `## Acceptance`. Example link in the RFP you are authoring: `Canonical ids: [## Acceptance](#acceptance)`.

## SPEC traceability (required when implementing from RFP)

Add to `docs/specs/SPEC-*.md`:

```markdown
## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| P1-A | R1 | covered |
| P1-B | — | waiver: out of scope for MVP |
```

- **covered** — Rk implements criterion; **`## Rk` heading must exist** in the same SPEC (`unknown_spec_rk` error otherwise).
- **waiver:** — explicit deferral or cross-spec pointer (see below). Silent `## Non-goals` without a traceability row is **not** a valid waiver.

### Multi-spec RFP (one RFP, several SPECs)

When phases map to different executable specs (e.g. RFP-022 → DIAGNOSTIC / CONTRACT / KNOWLEDGE):

| Disposition | When |
|-------------|------|
| `covered` + `Rk` | This SPEC implements the row |
| `waiver: implemented in SPEC-OTHER-1` | Row owned by sibling spec (link required) |
| `waiver: deferred …` | Product decision — row intentionally not implemented |

**Per-spec gate:** `apatch rfp coverage --rfp RFP-022 --spec SPEC-DIAGNOSTIC-GRAPH-1` — sibling waivers satisfy MUST rows (single-spec preflight before `spec_run`).

**Aggregate gate (whole RFP):**

```bash
apatch rfp coverage --rfp RFP-022 --specs SPEC-DIAGNOSTIC-GRAPH-1,SPEC-CONTRACT-EDGES-1,SPEC-KNOWLEDGE-GRAPH-1
```

Every MUST id must be **covered** (with valid Rk) in at least one listed SPEC. Sibling waivers alone do not count in aggregate — the owning SPEC must show `covered`.

Dogfood reference: [RFP-022](./RFP-022-unified-diagnostics-knowledge-graph.md) + phase specs.

## Workflow

```text
apatch_rfp_lint(rfp='RFP-NNN')                    # optional — also inside apatch_spec_lint
→ write SPEC(s) + ## RFP traceability
→ apatch_spec_lint(spec='SPEC-…')                 # format + rfp_lint + rfp_coverage + plan_scaffold
→ fill plan_scaffold.execution_plan.{Rk}.needles
→ apatch_spec_run(spec='SPEC-…', requirements=…)

# Multi-spec aggregate only (RFP-022 phases):
apatch_rfp_spec_coverage(rfp='RFP-NNN', specs='A,B,C')
```

**Agent note:** prefer one `apatch_spec_lint` over separate RFP tools — IDE MCP list may omit standalone `apatch_rfp_*` (embedded in spec_lint). See [agent-onboarding.md](./agent-onboarding.md).
