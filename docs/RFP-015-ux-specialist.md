# RFP-015 — UX Specialist Domain

> **Status:** Phase 1 (dogfood) · **Spec:** [SPEC-UX-SPECIALIST-1](specs/SPEC-UX-SPECIALIST-1.md)

## Motivation

Universal five-layer UX audit methodology for specialist agents. Outputs are
machine-verifiable via `manifests/ux-audit.schema.json` and `apatch/ux_audit.py`.

## Artifact model

| Artifact | Path |
|----------|------|
| JSON Schema | `manifests/ux-audit.schema.json` |
| Validator | `apatch/ux_audit.py` |
| Methodology index | `docs/methodology/ux-specialist/README.md` |
| Golden instance | `tests/fixtures/ux_audit/sales_pipeline/reference.json` |

Five layers: **L1** Foundation, **L2** Workspace architecture, **L3** Ontology,
**L4** Verification roots, **L5** Attention flow.

## Domain tags (RFP-014 Phase 4)

Audit artifacts and spec needles carry structured tags for interference routing:

| Tag prefix | Example | Purpose |
|------------|---------|---------|
| `domain:` | `domain:ux` | UX vs backend/infra specs |
| `layer:` | `layer:L1` … `layer:L5` | Cognitive layer scope |
| `role:` | `role:sales_director` | Persona-specific findings |
| `product:` | `product:jason` | Product instance (dogfood) |

Cross-spec scheduling: `apatch_spec_interference(peer_specs=[...])` uses tag overlap
with backend specs to detect antagonistic parallel work.

## Agent playbook (§3M)

```text
apatch_spec_lint(spec='SPEC-UX-SPECIALIST-1')
apatch_spec_run(spec='SPEC-UX-SPECIALIST-1', requirements={...})
# Consumer: emit needles tagged domain:ux + layer:L*
```

## Non-goals

- Visual regression (separate spec family)
- LLM intent embeddings (RFP-014 research beyond tags)

## Dogfood & agent lessons

**Spec:** [SPEC-UX-SPECIALIST-1](./specs/SPEC-UX-SPECIALIST-1.md) — **7/7 attested** via `apatch_spec_run` (2026-06-10).

**Placement policy** (core vs consumer, circuit breaker): [domain.md § Domain knowledge vs execution substrate](./domain.md) — not duplicated here.

**Где записываются workflow и уроки (не дублировать prose здесь):**

| Аудитория | Документ |
|-----------|----------|
| Архитектура batch-run | [RFP-009 §9.1 — уроки dogfood](./RFP-009-spec-run.md) |
| Consumer agent | [AGENTS.template.md §3K](./AGENTS.template.md) |
| MCP inline (каждый `apatch_spec_*`) | `spec_run.lessons` в ответе (`apatch/agent_guidance.py`) |

**Уроки этого dogfood:**

1. **Вся спека** — `apatch_spec_run(requirements={R1…R7: {needles}})`, не N× §3I.
2. **Shared test file** — `tests/test_ux_specialist.py`; rebind stale Rk через `tests/fixtures/ux_specialist/rebind-r*.txt` (noop), не повторный append тестов.
3. **R6 verify** — pytest ищет `L2`…`L5` по отдельности; в RFP-015 перечислять слои явно, не только «L1–L5».