# SPEC-NEEDLES-HINT-1 — needles_hint v2 + scaffold clarity

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-NEEDLES-HINT-1`  
> **Anchors:** [RFP-022](../RFP-022-unified-diagnostics-knowledge-graph.md) · [RFP-024](../RFP-024-needles-scaffold.md)

## 0. Motivation

Assist agent cognition at the autonomy boundary: partial `needles_hint` dicts when
`apatch_simulate` detects anchor drift, and clearer messaging that RFP-024
`needle_templates` are structural examples — not auto-applied mutations.

## R1 needles_hints_from_plan

`needles_hints_from_plan(plan, candidates, target_dir)` returns advisory partial
mutation dicts when simulate detects anchor drift (fuzzy recovery or unresolved).

(verify: python3 -m pytest tests/test_needles_hint.py::test_r1_recovered_drift_hint -q)

## R2 simulate embeds needles_hints

`simulate_from_logs` includes `needles_hints[]` when drift entries exist; hints are
advisory (`partial: true`, `advisory: true`) — never auto-applied.

(verify: python3 -m pytest tests/test_needles_hint.py::test_r2_simulate_embeds_hints -q)

## R3 scaffold templates vs needles clarity

`spec_needles_scaffold` response includes `scaffold_vs_templates` and per-Rk
`needle_templates_note` distinguishing examples from `execution_plan.{Rk}.needles`.

(verify: python3 -m pytest tests/test_needles_hint.py::test_r3_scaffold_templates_note -q)

## R4 agent_guidance on simulate and scaffold tools

`enrich_tool_response` for `apatch_simulate` (when hints present) and
`apatch_spec_needles_scaffold` embed `templates_vs_needles` guidance.

(verify: python3 -m pytest tests/test_needles_hint.py::test_r4_agent_guidance_scaffold_simulate -q)

## Non-goals

- Auto-apply `needles_hint`
- LLM synthesis from diagnostic suggestions