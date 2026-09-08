# SPEC-UX-SPECIALIST-1 — Universal UX/UI audit methodology (RFP-015)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-UX-SPECIALIST-1`
> **Anchors:** [RFP-015](../RFP-015-ux-specialist.md), [methodology](../methodology/ux-specialist/README.md), [RFP-014 Phase 4](../RFP-014-spec-interference-detection.md)

## 0. Motivation

Five-layer UX audit stack (L1 Foundation → L5 Attention Flow) for specialist agents.
Machine-verifiable audit artifacts (`manifests/ux-audit.schema.json`) enable governed
implementation without hand-staged JSONL. Domain tags (`domain:ux`, `layer:L*`) extend
RFP-014 Phase 4 interference routing.

Reference dogfood: JASON Sales Pipeline (brain audit 2026-06-10).

## R1 UX audit JSON schema

`manifests/ux-audit.schema.json` defines required layers L1–L5, signal inventory,
bridge_cost distribution, and domain tags.

(verify: python3 -m pytest tests/test_ux_specialist.py::test_r1_schema_file -q)

## R2 UX audit validator module

`apatch/ux_audit.py` exposes `validate_ux_audit_artifact`, per-layer validators,
and `ux_audit_lint_workspace` for CI and future MCP lint.

(verify: python3 -m pytest tests/test_ux_specialist.py::test_r2_ux_audit_module -q)

## R3 Methodology index (five layers)

`docs/methodology/ux-specialist/README.md` documents L1–L5 questions, agent workflow,
and link to reference instance.

(verify: python3 -m pytest tests/test_ux_specialist.py::test_r3_methodology_index -q)

## R4 Golden reference artifact (Sales Pipeline)

`tests/fixtures/ux_audit/sales_pipeline/reference.json` validates against schema
and encodes key metrics from the JASON audit (31 signals, bridge cost distribution).

(verify: python3 -m pytest tests/test_ux_specialist.py::test_r4_reference_artifact -q)

## R5 Per-layer validator tests

Pytest nodes exercise L1–L5 structural validation independently on the reference artifact.

(verify: python3 -m pytest tests/test_ux_specialist.py::test_r5_layer_validators -q)

## R6 RFP-015 UX specialist domain

`docs/RFP-015-ux-specialist.md` describes artifact model, Phase 4 tags, and agent playbook §3M.

(verify: python3 -m pytest tests/test_ux_specialist.py::test_r6_rfp015 -q)

## R7 RFP-014 Phase 4 domain tags

RFP-014 Phase 4 section documents `domain:ux`, `layer:L1`…`L5`, `role:*`, `product:*`
for cross-spec interference with backend specs.

(verify: python3 -m pytest tests/test_ux_specialist.py::test_r7_phase4_tags -q)

## Non-goals

- Playwright/visual regression in this spec (separate SPEC-UX-VISUAL-*)
- LLM intent analyzer (RFP-014 Phase 4 research beyond tags)
- Sales Pipeline code fixes (consumer repo; needles are separate specs)