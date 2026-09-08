# UX Specialist Methodology (RFP-015)

Five-layer audit stack for governed UX/UI review. Each layer answers a distinct
question; together they produce a machine-verifiable artifact
(`manifests/ux-audit.schema.json`).

> **Reference instance:** [Sales Pipeline golden fixture](../../../tests/fixtures/ux_audit/sales_pipeline/reference.json)

## Agent workflow

1. Run L1→L5 audits (or parallelize L1+L5 for quick signal scan).
2. Emit JSON matching `ux-audit.schema.json`; tag with `domain:ux` and `layer:L*`.
3. Validate: `python -m apatch.ux_audit` or `pytest tests/test_ux_specialist.py`.
4. Anchor needles in a consumer spec; interference tags route via RFP-014 Phase 4.

## L1 — Foundation (Nielsen / IA clarity)

**Question:** Can each role complete core tasks without navigation debt?

- Role × task matrix (≥3 tasks per primary role)
- UX Clarity Index (0–100)
- Navigation item count and IA violations

## L2 — Workspace architecture

**Question:** Does the canonical object model match how users think about work?

- Canonical entity (e.g. Deal) and child objects
- Cross-surface consistency score (CSC)
- Intent distance between related actions

## L3 — Workspace ontology

**Question:** Are features/modules aligned with engagement dimensions?

- Engagement 3D mapping (who / when / why)
- Feature-module conflict inventory
- Duplicate capability detection

## L4 — Verification roots

**Question:** Does each role have a clear "done" signal?

- Role-dependent verification roots (Director / AE / SA)
- Missing or ambiguous completion criteria
- Testability of success states

## L5 — Attention flow

**Question:** Do signals reach the right surface at the right time?

- Signal inventory (source → target, bridge cost, urgency)
- Bridge cost distribution (direct / broken / dead-end %)
- Ranked high-impact fixes (Signal → Bridge → Target)

## Links

- [SPEC-UX-SPECIALIST-1](../../specs/SPEC-UX-SPECIALIST-1.md)
- [RFP-015 UX specialist domain](../../RFP-015-ux-specialist.md) (R6)
- [RFP-014 Phase 4 domain tags](../../RFP-014-spec-interference-detection.md)
