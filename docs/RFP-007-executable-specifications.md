# RFP-007: Executable Specifications — spec-driven разработка в apatch

* **Статус**: **MVP реализован** (пакет **0.2.x**, post-0.2.0) — parser, lint, ledger-derived status, MCP/CLI
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-08
* **Зависимости**: RFP-004 (governed session), RFP-005 (статус не пишет monitored party), RFP-006 (`spec:ID#Rk` как artifact)

> **Где что лежит:** этот файл — **канонический RFP** (как RFP-005/006). Детали:
> - архитектура и цикл исполнения → [executable-specs.md](./executable-specs.md)
> - стандарт авторинга `SPEC.md` → [spec-authoring.md](./spec-authoring.md)
> - код → `apatch/spec.py`

---

## 1. Тезис

Спецификация (`docs/specs/SPEC-*.md`) перестаёт быть «текстом в соседней папке» и становится **управляемым объектом**:

```text
SPEC.md  →  Requirements (spec:ID#Rk)  →  Intent  →  Session  →  Mutation  →  Verify  →  Attestation
```

Каждое требование (`## R1`, `## FR-2`, …) — артефакт `spec:<SPEC_ID>#<REQ_ID>@<content_hash>`.  
**Состояние требования выводится из TrustChain ledger**, а не хранится как редактируемое поле в markdown (инвариант RFP-005: нельзя «поставить галочку» без подписанной мутации).

«Готово» для требования = его **acceptance check** (`verify:`) зелёный **и** есть attestation, привязанная к `spec:ID#Rk`.

---

## 2. Что это НЕ

* **Не редактор ТЗ** — apatch парсит и трекает; пишут люди/агент.
* **Не отдельный runtime** — нет `apatch spec run` / `engineering_run`; тот же governed pipeline (RFP-004).
* **Не Jira/wiki storage** — в ledger только `kind:id@hash`, не тело тикета.
* **Не под-пункты** — гранулярность = одно требование `Rk`, не «§3.2.1».

---

## 3. Файлы в consumer-репозитории

| Путь | Назначение |
|------|------------|
| `docs/specs/SPEC-<ID>.md` | Исполняемая спека (формат — [spec-authoring.md](./spec-authoring.md)) |
| `.apatch/specs/<id>.json` | Запомненный путь к файлу (не статус) |

**Discovery:** `spec_path=` → `docs/specs/<id>.md` → `.apatch/specs/<id>.json`.

Пример в продакшене: `TrustChain_Agent/docs/specs/SPEC-TENANT-1.md`.

---

## 4. MCP / CLI surface (3 tools + shortcut)

| MCP | CLI | Назначение |
|-----|-----|------------|
| `apatch_spec_lint` | `apatch spec lint` | Стандарт авторинга **до** трекинга |
| `apatch_spec_status` | `apatch spec status` | Покрытие всех требований из ledger |
| `apatch_spec_next` | `apatch spec next` | Следующее открытое требование + его `verify` |
| `apatch_session_start(requirement='SPEC-42#R3')` | `session start --requirement` | Авто `intent` + artifact `spec:SPEC-42#R3@<hash>` |

Полная таблица MCP: [mcp_setup.md](./mcp_setup.md). Счётчик tools: `apatch_doctor` → `mcp_health.tool_count` (**17 compact / 129 full**).

---

## 5. Рекомендуемый цикл (агент)

```text
apatch_spec_lint(spec_path="docs/specs/SPEC-<ID>.md")
apatch_spec_next(spec="SPEC-<ID>")
apatch_session_start(requirement="SPEC-<ID>#R1")
apatch_generate_batch(needles=[…]) → apatch_simulate → apatch_apply_session
apatch_verify_run(verify="<команда из R1 verify>")
apatch_attest
apatch_spec_status(spec="SPEC-<ID>")
apatch_session_end
```

Повторять `spec_next` → session → mutate → verify → attest, пока все требования `attested`.

**Практика:** один `apply_session` на чанк → `verify_run` → `attest`; не смешивать второй apply в lifecycle `verifying` (см. [executable-specs.md](./executable-specs.md)).

---

## 6. Связь с RFP-006

RFP-006 ввёл `artifacts[]` на session (`spec:SPEC-42@sha256:…`).  
RFP-007 уточняет **под-артефакты требований** (`spec:SPEC-42#R1@hash`) и **per-requirement verify**.

`apatch_trustchain_coverage(artifact="spec:SPEC-42")` — уровень всей спеки.  
`apatch_spec_status` — уровень каждого `Rk`.

---

## 7. Статус реализации

| Компонент | Статус |
|-----------|--------|
| `apatch/spec.py` — parse, lint, status, next | ✅ |
| MCP `apatch_spec_*` | ✅ |
| CLI `apatch spec` | ✅ |
| `session start --requirement` | ✅ |
| `apatch_doctor.spec_execution` playbook | ✅ |
| CI-gate «все SPEC в репо attested» | backlog |
| Spec executor (`apatch_execute_next`) | см. [RFP-008](./RFP-008-spec-executor.md) |
| Sub-clause granularity | out of scope |

Тесты: `tests/test_spec.py`.

---

## 8. Документация для агентов

| Аудитория | Документ |
|-----------|----------|
| Разработчик apatch | этот RFP + [executable-specs.md](./executable-specs.md) |
| Автор SPEC.md | [spec-authoring.md](./spec-authoring.md) |
| ИИ-агент в consumer | [AGENTS.template.md](./AGENTS.template.md) §3I (self-contained, без путей `docs/…` apatch) |

Sync consumer: `apatch init-consumer --refresh-agents`.
