# RFP-009: Spec Run — batch execution всей спеки

* **Статус**: **Implemented (MVP)** — [SPEC-RUN-1](./specs/SPEC-RUN-1.md) **8/8 attested**; MCP **17 compact / 129 full**
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-09
* **Зависимости**: RFP-007 (`spec_*`), RFP-008 (`apatch_execute_next`, [SPEC-EXECUTOR-1](./specs/SPEC-EXECUTOR-1.md)), `apatch_generate_batch`, governed session / TrustChain enforce

> **Где что лежит:** этот файл — **канонический RFP**. Исполняемая спека реализации:
> [docs/specs/SPEC-RUN-1.md](./specs/SPEC-RUN-1.md). Dogfood — только §3I/§3J MCP-цикл.

---

## 1. Тезис

RFP-008 дал **requirement execution** по одному `Rk` за итерацию: агент всё ещё крутит
`execute_next` 4–5 раз на каждое требование и сам следит за переходом R3 → R4.

RFP-009 вводит **spec run** — один операционный контракт на **всю спеку**:

```text
SPEC.md + inline requirements={Rk: {needles: [...]}}   # compact specs (few Rk, short needles)
  OR
manifests/SPEC-X.run.json (versioned) + manifest_path=…  # large needles / review / CI
  ↓
apatch_spec_run(spec='SPEC-ONPREM-2', requirements={...})
  OR apatch_spec_run(spec='SPEC-ONPREM-2', manifest_path='manifests/SPEC-ONPREM-2.run.json')
  ↓
governed cycle: R1 → R2 → … → Rn  (внутри — session/apply/verify/attest per Rk)
  ↓
done: true | blocked на Rk + .apatch/spec_run.json для resume
```

Человек: **«выполни SPEC-ONPREM-2»** — агент передаёт needles **inline** (компактно) или через **закоммиченный** `manifest_path`.
**Не** крутит `execute_next` × N, **не** пишет ephemeral `manifests/*.run.json` перед каждым прогоном,
**не** запускает Python driver scripts (`manifests/*-spec.py`) обходящие MCP.

**Критерий успеха:** после lint + deps executor проходит все **pending** требования с
записью в manifest, attests каждое, возвращает `spec_status.done: true` или честный
`blocked` с `requirement_token` и `recommended_action`.

---

## 2. Почему это полезно (и честные ограничения)

### Плюсы

| Эффект | Пояснение |
|--------|-----------|
| Меньше MCP round-trips | Один manifest вместо N×(dry_run → session → needles → finalize) |
| Предсказуемый CI | `apatch spec run --manifest …` в pipeline с resume |
| Traceability | Один `spec_run_id` связывает все Rk в events / replay |
| Resume | Сбой на R5 → `spec_run(resume=true)` без ручного spec_next |

### Ограничения (не обещаем в MVP)

* **Не автогенерация needles из текста `## Rk`.** Manifest — внешний артефакт (агент или human).
* **Не один бесконечный MCP-вызов.** Как `apply_session`: state в `.apatch/spec_run.json`,
  ответ `continue: true` пока не обработаны все Rk (защита от MCP timeout).
* **Не параллельные Rk** в одной governed session — строго последовательно, одна сессия на Rk.
* **Не замена `execute_next`** — остаётся для точечной работы и inspection.

---

## 3. Уровни стека

```text
RFP-007   spec queue (lint, status, next)
RFP-008   execute_next — один Rk за вызов
RFP-009   spec_run     — вся спека за один workflow (chunked MCP)
```

| Примитив | Роль |
|----------|------|
| `apatch_spec_run` | Оркестратор: manifest → loop execute_next internals |
| `apatch_execute_next` | Внутренний шаг (или прямой вызов для одного Rk) |
| `apatch_generate_batch` | Needles → JSONL на каждое Rk |
| `.apatch/spec_run.json` | Checkpoint: spec_id, manifest hash, rk_index, per-Rk status |

---

## 4. Run manifest (формат)

Файл: `manifests/SPEC-<ID>.run.json` (или inline `manifest` в MCP).

```json
{
  "schema_version": 1,
  "spec": "SPEC-ONPREM-2",
  "requirements": {
    "R1": {
      "needles": [
        {"action": "create", "target_file": "src/monitor.ts", "content": "…"}
      ],
      "verify_override": null,
      "skip_if_attested": true
    },
    "R2": {
      "needles": [
        {"action": "replace", "target_file": "src/monitor.ts", "find_text": "…", "replace_text": "…"}
      ]
    }
  },
  "options": {
    "chunk_rk_per_call": 1,
    "verify_deferred_per_rk": true,
    "stop_on_first_failure": true
  }
}
```

Правила:

1. Ключи `requirements` — только `Rk`, существующие в `SPEC-<ID>.md`.
2. Отсутствующий ключ `needles` при pending Rk → `blocked`, `error_type: MANIFEST_GAP` (агент должен заполнить). Явный `needles: []` — verify-only режим: можно аттестовать только если `(verify: …)` уже зелёный.
3. Для attested Rk непустые `needles` по умолчанию открывают maintenance-rerun. Только явный `skip_if_attested: true` пропускает такое Rk; если все postconditions уже доказаны на диске, run возвращает `applied=0` и `already_satisfied_requirements`.
4. `verify_override` — только для emergency; default берётся из `(verify: …)` в спеке.
5. SHA-256 manifest фиксируется в `spec_run.json` — смена manifest без `reset=true` → ошибка.

CLI/MCP lint: `apatch_spec_run_manifest_lint(manifest_path=…)` (R1 SPEC-RUN-1).

---

## 5. MCP / CLI surface

| MCP | CLI | Назначение |
|-----|-----|------------|
| `apatch_spec_run` | `apatch spec run` | Batch run всей спеки по manifest |
| `apatch_spec_run_manifest_lint` | `apatch spec run lint-manifest` | Валидация manifest vs SPEC.md |

### Параметры `apatch_spec_run`

| Параметр | Назначение |
|----------|------------|
| `spec` / `spec_path` | Как в `apatch_spec_next` |
| `requirements` | **Primary:** inline `{Rk: {needles: [...]}}` — один MCP-вызов |
| `manifest_path` | JSON manifest (versioned in repo; large needles / CI) |
| `manifest` | Inline dict (same schema as file) |
| `dry_run` | Lint spec + manifest + deps + полный план всех pending Rk, без мутаций |
| `resume` | Продолжить `.apatch/spec_run.json` (default true если state matches spec+manifest) |
| `reset` | Сбросить spec_run state |
| `abort` | Откат last checkpoint + clear state |
| `chunk_rk_per_call` | Сколько Rk за один MCP-вызов (**default 0** = все pending за тик) |
| `skip_lint` / `check_dependencies` | Gates (как execute_next) |

### Фазы ответа (`execution_phase`)

```text
discover → running → rk_session → rk_mutate → rk_verify → rk_complete
         → complete | blocked
```

Ответ всегда включает:

* `continue` — повторить `spec_run` без новых аргументов
* `current_requirement` / `requirement_token`
* `progress`: `{ rk_done, rk_total, percent, attested[] }`
* `steps_completed[]`, `agent_next`, `state_update`
* при `blocked`: `error_type`, `recommended_action`, `resume_hint`

---

## 6. Внутренний алгоритм (один «тик»)

```text
1. lint(spec) + check_dependencies(spec)
2. load/create spec_run.json
3. manifest_lint(manifest vs spec requirements)
4. pending = spec_status.pending ∪ stale (document order)
5. FOR up to chunk_rk_per_call pending Rk:
     a. if attested: run non-empty needles by default; skip only on explicit skip_if_attested=true; return proven noop when every postcondition is already satisfied
     b. session_start(requirement=SPEC#Rk)
     c. generate_batch(needles) → simulate → apply_session (loop until continue=false)
     d. verify_run(Rk.verify)
     e. attest → session_end
     f. on failure: stop_on_first_failure → blocked + rollback hint
6. save spec_run.json; continue = pending_remain > 0
7. if all attested: done=true, clear or archive spec_run.json
```

Каждый Rk — **отдельная** governed session + отдельная attestation (TrustChain invariant сохранён).

---

## 7. Связь с RFP-008

| RFP-008 | RFP-009 |
|---------|---------|
| `execute_next` × N | `spec_run` × M (M << N) |
| needles inline в MCP | needles в manifest (versionable, reviewable) |
| §3J agent loop | §3K «один manifest → spec_run until done» |
| `agent_next` per Rk | `agent_next`: `spec_run()` or `spec_run(resume=true)` |

`apatch_execute_next` **не удаляется** — debugging, single-Rk hotfix, dogfood SPEC-EXECUTOR-1.

---

## 8. Failure taxonomy (дополнения)

| `error_type` | `recommended_action` | Когда |
|--------------|----------------------|-------|
| `MANIFEST_GAP` | `reduce_scope` | pending Rk без ключа needles в manifest |
| `MANIFEST_DRIFT` | `retry_chunk` | manifest hash ≠ active spec_run |
| `SPEC_RUN_BLOCKED` | `rollback` / `resume` | verify failed на Rk |
| `SPEC_DEPENDENCY_UNMET` | `reduce_scope` | upstream spec не done |
| `SPEC_INTERFERENCE_CYCLE` | `resolve_conflicts` | L2 cycle в `peer_specs` |
| `SPEC_INTERFERENCE_STALE` | `re_run_interference` | registry snapshot устарел |
| `SPEC_RUN_ORDER_BLOCKED` | `complete_predecessor_first` | predecessor в `safe_order` не fully attested |
| `SPEC_SCHEDULE_BLOCKED` | `resolve_conflicts` | schedule not schedulable (`spec_run_multi`) |
| `SPEC_CROSS_VERIFY_FAILED` | `refactor_needles` | Level-3 cross-verify failed |

---

## 8. Multi-spec (RFP-014 Phase 3)

Когда нужно исполнить **≥2 спеки** с interference-aware порядком — **`apatch_spec_run_multi`**, не N× ручной `spec_run` и не staged JSONL между спеками.

```text
apatch_spec_schedule(specs=["SPEC-A", "SPEC-B"])   # optional preview
apatch_spec_run_multi(
  specs=["SPEC-A", "SPEC-B"],
  requirements={"SPEC-A": {Rk: {needles: [...]}}, "SPEC-B": {...}},
  cross_verify=true,
)
```

Внутри: schedule → (optional cross-verify) → `spec_run` per spec до `continue=false` → `close_session` между спеками.  
Consumer playbook: [AGENTS.template.md §3L Phase 3](./AGENTS.template.md). Архитектура: [RFP-014 Phase 3](./RFP-014-spec-interference-detection.md).

---

## 9. Риски и митигации

| Риск | Митигация |
|------|-----------|
| MCP timeout на большой спеке | `chunk_rk_per_call=1` default; `continue` semantics |
| Огромный manifest в MCP payload | Закоммить `manifests/SPEC-X.run.json` → `manifest_path=…`; или `chunk_rk_per_call=1` |
| Agent пишет Python driver обходя MCP | Запрещено: `manifests/*-spec.py` calling `spec_run_enriched()` — use MCP `apatch_spec_run` or CLI |
| Agent забывает manifest для R7 | `dry_run` возвращает шаблон JSON с пустыми needles |
| Partial attestation | `spec_status` после каждого Rk в `progress.attested[]` |

---

## 9.1 Уроки из dogfood (обязательно для агентов)

Проверено на consumer-прогонах; типичные ошибки агента при первом `spec_run`:

| Урок | Правильно | Неправильно |
|------|-----------|-------------|
| **Needles = mutation dicts** | `{action, target_file, find_text/replace_text}` или `{action, target_file, content}` — валидные объекты для `apatch_generate_batch` | Текстовые подсказки, prose, «сделай эндпоинт X» вместо literal patch |
| **Attestation в одной session** | На каждое Rk: `session_start` → mutate → `verify_run` → `attest` → `session_end` в **одной** governed session (так делает `spec_run` внутри) | `finalize` / `attest` в другой session или без предшествующих мутаций в той же session |
| **`verify_deferred=true` на mutate** | Default `spec_run` / `execute_next`: verify **после** всех chunk'ов `apply_session`, не в каждом chunk | `verify_deferred=false` → `npm`/`pytest` в chunk verify откатывает патч до завершения apply |
| **Domain literals в needles** | Точные строки из кода: путь API, импорт, сигнатура — как в `find_text` | Устаревший или выдуманный путь (пример: work queue **`/api/v1/work-items/`**, не `/items/`) |
| **Shared file → ping-pong `stale` (rebind)** | Несколько Rk патчат **один** файл (пример: SPEC-LEDGER-ACTOR-1 R2–R7 → `tests/test_ledger_actor.py`; [SPEC-UX-SPECIALIST-1](./specs/SPEC-UX-SPECIALIST-1.md) R1–R7 → `tests/test_ux_specialist.py`). После batch-run ранние Rk часто `stale` (`file_drift`) — нормально. **Rebind:** (1) **последним** переаттестуй Rk с **последней** мутацией shared-файла (макс. R, который append/replace в этом файле); (2) остальные stale Rk — **noop-сессия**: marker fixture в `tests/fixtures/<spec>/rebind-rN.txt`, **без** повторной мутации shared-файла (`apatch_spec_run`, markers-only needles) | Снова патчить shared-файл на R4 после R5/R7 → ping-pong stale на соседних Rk |
| **≥2 pending Rk → `spec_run`, не N× §3I** | `apatch_spec_run(dry_run=true)` → inline `requirements={Rk: {needles}}` → `while continue` | N× `spec_next` + hand-staged `patches.jsonl` или ручной `Write`/`StrReplace` в protected |
| **Verify literals = doc literals** | Если `(verify:)` ищет подстроку `L2` в markdown — в документе должно быть **`L2`**, не только диапазон `L1–L5` (en-dash). Dogfood: R6 [SPEC-UX-SPECIALIST-1](./specs/SPEC-UX-SPECIALIST-1.md) → `docs/RFP-015-ux-specialist.md` | Красивый prose «слои L1–L5» при pytest `assert "L2" in text` |

**Needle sketch (replace):**

```json
{"action": "replace", "target_file": "backend/app/routes/work.py",
 "find_text": "router.get(\"/items/\"", "replace_text": "router.get(\"/api/v1/work-items/\""}
```

**Needle sketch (create):**

```json
{"action": "create", "target_file": "src/foo.ts", "content": "export const X = 1;\n"}
```

См. также [cookbook.md § mutations](./cookbook.md) и consumer [AGENTS.template.md §3K](./AGENTS.template.md).

---

## 10. Статус реализации

| Компонент | Статус |
|-----------|--------|
| RFP-009 (этот документ) | ✅ MVP |
| [SPEC-RUN-1](./specs/SPEC-RUN-1.md) | ✅ **8/8 attested** |
| `apatch/spec_run.py` | ✅ orchestrator + `.apatch/spec_run.json` |
| MCP `apatch_spec_run` | ✅ registered (**60** tools total) |
| MCP `apatch_spec_run_manifest_lint` | ✅ registered |
| CLI `apatch spec run` | ✅ `apatch spec run lint-manifest` |
| `tests/test_spec_run.py` | ✅ R1–R8 verify |
| §3K [AGENTS.template.md](./AGENTS.template.md) | ✅ consumer playbook |
| `manifests/SPEC-RUN-1.example.run.json` | ✅ CI/review example |
| Gap analysis / auto-manifest | **Non-goal** (see [RFP-014](./RFP-014-spec-interference-detection.md) interference) |

**Зависимость:** [SPEC-EXECUTOR-1](./specs/SPEC-EXECUTOR-1.md) attested (R1–R7).

---

## 11. Документация

| Аудитория | Документ |
|-----------|----------|
| Архитектура | этот RFP |
| Реализация | [SPEC-RUN-1](./specs/SPEC-RUN-1.md) |
| Агент consumer | [AGENTS.template.md](./AGENTS.template.md) §3K |
| MCP таблица | [mcp_setup.md](./mcp_setup.md) |
| Пример manifest | `manifests/SPEC-RUN-1.example.run.json` (создать в R6) |
