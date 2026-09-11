# RFP-008: Spec Executor — от patch management к requirement execution

* **Статус**: **Implemented** — [SPEC-EXECUTOR-1](./specs/SPEC-EXECUTOR-1.md) **7/7 attested**; MCP **17 compact / 129 full**
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-09
* **Зависимости**: RFP-004 (governed session), RFP-006 (artifacts), RFP-007 (`spec_*`, `session_start --requirement`), `apatch_generate_batch` (patch orchestration)

> **Где что лежит:** этот файл — **канонический RFP**. Исполняемая спека реализации:
> [docs/specs/SPEC-EXECUTOR-1.md](./specs/SPEC-EXECUTOR-1.md). После lint — цикл §3I по каждому `Rk`.

---

## 1. Тезис

RFP-007 дал **очередь требований** (`apatch_spec_next`, `apatch_spec_status`) и якорь сессии
(`apatch_session_start(requirement='SPEC-42#R3')`). Агент всё ещё вручную собирает цепочку из
6–8 MCP-вызовов и сам решает, когда переходить от патчей к verify.

RFP-008 вводит слой **requirement execution**: единицей управления становится **требование
спеки**, а JSONL-патчи — внутренний артефакт, не основной интерфейс агента.

```text
SPEC.md
  ↓
spec queue (spec_next / spec_status)
  ↓
apatch_execute_next(spec='SPEC-ONPREM-2')
  ↓
governed cycle per requirement
  ↓
следующее требование (без «выполни R3» от человека)
```

Человек говорит: **«продолжай SPEC-ONPREM-2»** или агент сам вызывает `apatch_execute_next`.
Человек **не** перечисляет needles — агент формирует их после gap analysis (или из manifest).

---

## 2. Уровни (что уже есть vs что добавляем)

| Уровень | Примитив | Роль |
|---------|----------|------|
| Patch orchestration | `apatch_generate_batch` | mutation needles (`replace`\|`create`\|`delete`\|`rename`) → JSONL |
| Requirement queue | `apatch_spec_next` / `apatch_spec_status` | что не attested, `verify` на Rk |
| **Requirement execution** | **`apatch_execute_next`** | оркестрация governed cycle на одно Rk |

Переход:

```text
patch management          requirement execution
─────────────────         ─────────────────────
generate_batch            execute_next
simulate                  ├─ discover + deps
apply_session             ├─ session_start(requirement)
verify (вручную)          ├─ mutate (needles | manifest)
attest (вручную)          ├─ verify_run(Rk.verify)
                          ├─ attest
                          └─ spec_status → next Rk
```

---

## 3. Что это НЕ

* **Не автогенерация кода из текста SPEC.md.** Тело `## R1` описывает *что* должно быть
  истинным; `verify:` — как проверить. *Какие* patches писать — по-прежнему агент (или
  `manifests/SPEC-<ID>-<Rk>.json` в фазе D — backlog).
* **Не новый runtime.** Тот же `MutationRuntime`, те же `generate_batch` / `apply_session` /
  `verify_run` / `attest`. Executor — **оркестратор**, не дублирует apply/notarization.
* **Не замена `apatch_orchestrate`.** Orchestrate — engineering-pipeline по manifest;
  executor — **линейный цикл по требованиям одной спеки**.
* **Не batch run всей спеки.** Это [RFP-009](./RFP-009-spec-run.md) / [SPEC-RUN-1](./specs/SPEC-RUN-1.md)
  (`apatch_spec_run` + run manifest). RFP-008 MVP: один `Rk` за `execute_next`; мутации —
  `needles[]` от агента.

---

## 4. MCP / CLI surface (план)

| MCP | CLI | Назначение |
|-----|-----|------------|
| `apatch_execute_next` | `apatch spec execute` / `apatch execute-next` | Следующее (или указанное) Rk → governed cycle |
| _(опц.)_ `apatch_execute_finalize` | `apatch spec finalize` | Только verify → attest → session_end после ручных мутаций |

### Параметры `apatch_execute_next` (черновик)

| Параметр | Назначение |
|----------|------------|
| `spec` / `spec_path` | Как в `apatch_spec_next` |
| `requirement` | Опционально `SPEC-42#R3` вместо auto-next |
| `dry_run` | Lint + deps + next + `execution_plan`, без мутаций |
| `needles` | Если заданы — `generate_batch` → `simulate` → `apply_session`. Формат: `{action, …}` — см. [cookbook.md § mutations](./cookbook.md) |
| `manifest_path` | Backlog: `phase_run` вместо needles |
| `finalize` | Пропустить mutate; `verify_run` → `attest` → `session_end` |
| `logs_path` | JSONL (default `patches.jsonl`) |
| `skip_lint` / `check_dependencies` | Gates |

### Фазы ответа (`execution_phase`)

```text
discover → session → mutate → verify → attest → complete | blocked
```

Каждый ответ: `requirement`, `verify`, `steps_completed[]`, `agent_next`, `state_update`.

---

## 5. Зависимости между спеками

Как в consumer-спеках (`**Зависимость:** все требования [SPEC-ONPREM-1](...) attested`):

1. Parser читает строки с `Зависимость` / `Dependency` / `Depends on` / `Prerequisite`.
2. Извлекает `SPEC-<ID>` из ссылок.
3. `apatch_spec_status` на каждую зависимость — если `done: false` → `execution_phase: blocked`,
   `error_type: SPEC_DEPENDENCY_UNMET`, `recommended_action: reduce_scope`.

---

## 6. Рекомендуемый цикл агента (после RFP-008)

```text
apatch_execute_next(spec='SPEC-ONPREM-2', dry_run=true)   # план + next Rk
apatch_execute_next(spec='SPEC-ONPREM-2')                 # session_start на Rk
# gap analysis → needles
apatch_execute_next(spec='SPEC-ONPREM-2', needles=[…])    # mutate + finalize
# или отдельно:
apatch_execute_next(spec='SPEC-ONPREM-2', finalize=true)  # после apply_session
apatch_execute_next(spec='SPEC-ONPREM-2')                 # следующий Rk
```

Антипаттерн: человек «выполни R3» + `scripts/build_*_patches.py`.

---

## 7. Связь с RFP-007

| RFP-007 | RFP-008 |
|---------|---------|
| `apatch_spec_next` | внутри `execute_next` (discover) |
| `apatch_session_start(requirement=…)` | внутри `execute_next` (session) |
| `apatch_generate_batch` | внутри `execute_next` (mutate) |
| `apatch_verify_run(verify=…)` | внутри `execute_next` (finalize) |
| §3I loop в AGENTS | сжимается в §3J + `execute_next` |

`apatch_spec_*` остаются для inspection и CI; executor — **операционный entry point**.

---

## 8. Статус реализации

| Компонент | Статус |
|-----------|--------|
| RFP-008 (этот документ) | ✅ implemented |
| [SPEC-EXECUTOR-1](./specs/SPEC-EXECUTOR-1.md) | ✅ attested (7/7) |
| `apatch/spec_executor.py` | ✅ shipped |
| MCP `apatch_execute_next` | ✅ shipped |
| CLI `apatch spec execute` | ✅ shipped |
| `tests/test_spec_executor.py` | ✅ shipped |
| `manifest_path` на Rk | backlog (фаза D) |
| CI-gate все SPEC attested | backlog (RFP-007) |

**Dogfood:** реализация только через `apatch_apply_session` по [SPEC-EXECUTOR-1](./specs/SPEC-EXECUTOR-1.md).

---

## 9. Документация

| Аудитория | Документ |
|-----------|----------|
| Архитектура | этот RFP |
| Реализация | [SPEC-EXECUTOR-1](./specs/SPEC-EXECUTOR-1.md) |
| Агент consumer | [AGENTS.template.md](./AGENTS.template.md) §3J (после R1–R6) |
| MCP таблица | [mcp_setup.md](./mcp_setup.md) (17 compact / 129 full) |
