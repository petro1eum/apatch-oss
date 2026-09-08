# RFP-023 — RFP→SPEC Traceability & Coverage Gate

> **Status:** Draft v1 · **Date:** 2026-06-12 · **Owner:** apatch product  
> **Package context:** 0.7.0 — closes **requirements leakage** between product intent (RFP) and executable SPEC  
> **Depends on:** [RFP-007](./RFP-007-executable-specifications.md) (`spec_lint`), [RFP-009](./RFP-009-spec-run.md) (`spec_run` preflight)  
> **Blocks:** Honest «RFP Phase done» narrative; [transformation matrix](./apatch-transformation-matrix.md) documentation product layer  
> **Executable spec:** [SPEC-RFP-COVERAGE-1](./specs/SPEC-RFP-COVERAGE-1.md)

---

## 1. Problem

`apatch_spec_lint` validates **SHAPE** (H1, Rk, verify) — not **projection fidelity** RFP → SPEC.

An agent can move RFP deliverables to `## Non-goals`, write fewer Rk, attest, and claim phase complete while **intent leaks** above the executable layer.

---

## 2. Solution

Extend the governed stack **one step earlier**:

```text
RFP.md (Acceptance table + stable ids)
  → SPEC.md (## RFP traceability)
  → apatch_spec_lint(spec='…')   # format + embedded rfp_lint + rfp_coverage + plan_scaffold
  → fill plan_scaffold.execution_plan.{Rk}.needles
  → spec_run / execute_next → … → attest
```

Standalone `apatch_rfp_lint` / `apatch_rfp_spec_coverage` remain for CLI and multi-spec aggregate (`specs='A,B,C'`). Fresh agents: [agent-onboarding.md](./agent-onboarding.md).

Coverage is **deterministic** (markdown tables) — not LLM judgment.

---

## 3. RFP authoring standard

See [rfp-authoring.md](./rfp-authoring.md). Minimum:

```markdown
# RFP-NNN — Title

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| P1-A | … | MUST |
| P1-B | … | MAY |
```

- **Id** — stable token (`P1-A`, `AR-3`, `UD-7`); reused in SPEC traceability.
- **Level** — `MUST` | `MAY` | `SHOULD`. Uncovered `MUST` without waiver → coverage **error**.

---

## 4. SPEC traceability block

```markdown
## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| P1-A | R2 | covered |
| P1-B | — | waiver: deferred to v2 (ticket:FEEDBACK-…) |
```

- **covered** — Rk implements criterion; Rk must exist in SPEC.
- **waiver:** — explicit deferral (human/product decision); allowed for MUST only with reason text.

Silent `## Non-goals` **without** traceability row → not a valid waiver.

---

## 5. Tools

| MCP | CLI | Purpose |
|-----|-----|---------|
| `apatch_rfp_lint` | `apatch rfp lint` | RFP template / Acceptance table |
| `apatch_rfp_spec_coverage` | `apatch rfp coverage` | RFP ids ↔ SPEC traceability |

**Preflight:** `apatch_spec_run` runs coverage when SPEC contains `## RFP traceability` (or `rfp=` param). Blocks on coverage errors (not warnings).

**Aggregate (multi-spec RFP):** `apatch rfp coverage --rfp RFP-NNN --specs SPEC-A,SPEC-B` — union check; every MUST id covered in at least one SPEC. See [rfp-authoring.md](./rfp-authoring.md).

---

## 6. Acceptance (RFP-023)

| Id | Criterion | Level |
|----|-----------|-------|
| R23-A | `apatch_rfp_lint` parses Acceptance table; errors on missing section | MUST |
| R23-B | `apatch_rfp_spec_coverage` reports gaps for uncovered MUST rows | MUST |
| R23-C | Waiver rows (`waiver:`) satisfy MUST without Rk | MUST |
| R23-D | `apatch_spec_run` blocks when traceability present and coverage fails | MUST |
| R23-E | Playbook / `spec_authoring` documents RFP→SPEC step | MUST |
| R23-F | Dogfood fixture RFP+SPEC pair in tests | MUST |

---

## 7. Non-goals (Phase 1)

- LLM semantic review of criterion text
- Auto-generating SPEC from RFP
- Waivers as separate signed artifact type (Phase 2 — traceability table sufficient for MVP)

---

## 8. References

- [spec-authoring.md](./spec-authoring.md) — SPEC side
- [rfp-authoring.md](./rfp-authoring.md) — RFP side
- [domain.md](./domain.md) — Engineering Truth stack
