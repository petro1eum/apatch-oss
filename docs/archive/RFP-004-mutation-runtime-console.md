# RFP-004: Mutation Operating Model, Runtime and Console

* **Статус**: **Реализовано 100%** — GTM + Engineering треки закрыты (400 тестов)
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-05 · **Обновлено**: 2026-06-05 (журнал реализации §3.1)

> **Архивный снимок.** Числа MCP tools (38→43) и тестов — на дату RFP. Актуально: [../README.md](../README.md), `apatch_doctor`. Backlog после RFP: `governed_phase_run`, `mutation replay`, auto-governance.
* **Зависимости**: RFP-001 (Semantic ID / adraft), RFP-002 (Strip / Native Pipeline), RFP-003 (MCP-транспорт — реализован)

---

## 1. Проблема

`apatch` вырос из инструмента восстановления сессий агента в **полноценный транзакционный слой** с matcher, backup, TrustChain, sandbox, enforcement, orchestration pipeline (R41–R52) и **38 MCP-tools**. Но с точки зрения архитектуры продукт всё ещё выглядит как набор разрозненных CLI-команд и MCP-обёрток, а не как **единая среда управляемых мутаций**.

| Симптом | Почему это мешает |
|:---|:---|
| Нет явной доменной модели | Разработчик и агент не видят единый жизненный цикл: `Intent → Session → Mutation → Verification → Attestation \| Rollback` |
| Грамматика команд историческая | `apply`, `strip`, `plan`, `pipeline run` — порцелан над внутренностями, а не глаголы предметной области |
| Runtime размазан по модулям | `workflows.py`, `apply_session.py`, `session_state.py`, `sandbox.py` — нет единого фасада |
| Нет потока доменных событий | MCP, CI и Console читают состояние опросом, а не из append-only журнала |
| Нет операционной консоли | Агент и инженер работают через сырой терминал или IDE; нет fullscreen-интерфейса уровня `lazygit` / `k9s` для мутаций |

**Ключевой сдвиг позиционирования:**

```text
Было:  apatch = recovery tool + набор утилит для патчей
Стало: apatch = Governed Mutation System
       (Operating Model → Runtime → Console)
```

Документ назван по **предметной области**, а не по реализации: через три года Runtime и Console могут быть переписаны, а сущности `Intent`, `Session`, `Mutation`, `Verification`, `Attestation` — останутся.

**Core** — транзакционный runtime: мутации в репозитории под политикой с криптографическим свидетельством.  
**Face** — Mutation Console (`apatch`): референсная проекция доменной модели для человека и агента, **не** эмулятор терминала и **не** конкурент iTerm.

---

## 2. Решение: Domain-first design

Главный инсайт: **сначала определяем язык Session / Mutation / Attestation, потом строим Runtime**. Домен и грамматика диктуют реализацию, а не наоборот.

```text
┌─────────────────────────────────────────────────────────────┐
│  1. Domain Model  ← долгоживущий контракт                   │
│     Session (aggregate root), Intent, Mutation, Verification, │
│     Attestation, Policy                                       │
├─────────────────────────────────────────────────────────────┤
│  2. Command Grammar                                          │
│     apatch session | mutation | verify | attestation | …    │
├─────────────────────────────────────────────────────────────┤
│  3. Runtime API                                              │
│     операции над Session как над агрегатным корнем           │
├─────────────────────────────────────────────────────────────┤
│  4. Domain Event Stream  (append-only, не event bus)         │
│     .apatch/events.jsonl — достаточно на несколько релизов   │
├─────────────────────────────────────────────────────────────┤
│  5. Mutation Console  (проекция домена, не «просто UI»)      │
│     fullscreen TUI; те же глаголы, что CLI                   │
└─────────────────────────────────────────────────────────────┘
         ▲                              ▲
         │ MCP (38 tools)               │ CI / hooks / sandbox
         └──────── adapters ────────────┘
```

### 2.1. Доменная модель

**Session — агрегатный корень доменной модели.** Всё исполнение происходит внутри сессии; остальные сущности — её части или ссылки на внешний контекст (Policy).

```text
Session                          Policy (вне агрегата)
 ├─ Intent
 ├─ Mutation[]
 ├─ Checkpoint[]
 ├─ Verification[]
 └─ Attestation
```

| Сущность | Смысл | Существующий код (Phase 0) |
|:---|:---|:---|
| **Session** | Агрегатный корень: единица работы агента/инженера над репо | `session_state.py`, `apply_session.py` |
| **Intent** | Зачем меняем: ADR, задача, манифест pipeline | `manifests/`, `trustchain intent`, pipeline metadata |
| **Mutation** | Атомарное изменение: plan → apply / strip | `matcher.py`, `backup.py`, `strip` pipeline |
| **Checkpoint** | Точка отката внутри сессии | `BackupManager`, TrustChain checkpoint |
| **Verification** | Проверка «мутация не сломала систему» (тесты, arch, sandbox) | `verify semantic`, sandbox `ci-gate`, arch/db checks |
| **Attestation** | Криптографическое свидетельство факта изменений (TrustChain) | `trustchain_helper.py`, `doctor` → `trustchain.mode` |
| **Policy** | Что разрешено до/после мутации | `sandbox.py`, `.apatch/enforcement.json`, MCP whitelist |

**Verification vs Attestation:** Verification отвечает на вопрос «работает ли система после правки?». Attestation фиксирует «кто, когда и что изменил» — независимо от результата verify. Путаница с термином *proof* устраняется: в домене — **Attestation**; porcelain `apatch proof` — алиас на переходный период.

### 2.2. Core Invariant

Любое изменение, произведённое через apatch, должно быть:

1. Связано с **Intent**.
2. Выполнено внутри **Session**.
3. Представлено как одна или более **Mutation**.
4. Проверено через **Verification** (если политика требует).
5. Завершено либо **Attestation**, либо **Rollback**.

```text
Intent → Session → Mutation → Verification → Attestation | Rollback
```

Это эквивалент того, чем **ACID** является для базы данных: независимо от CLI, MCP или Console, инвариант не нарушается. Runtime — исполнитель инварианта; Console — его наблюдаемая проекция.

### 2.3. State Machine сессии

Жизненный цикл сессии и мутации задаётся **допустимыми переходами**, а не свободным вызовом команд.

```text
                    ┌─────────┐
                    │  Draft  │  session start, intent задан
                    └────┬────┘
                         │ plan
                         ▼
                    ┌─────────┐
                    │ Planned │  mutation plan готов, файлы не тронуты
                    └────┬────┘
                         │ apply / strip
                         ▼
                    ┌──────────┐
                    │ Applying │  matcher + backup в процессе
                    └────┬─────┘
                         │ success
                         ▼
                    ┌───────────┐
                    │ Verifying │  pipeline, ci-gate, semantic verify
                    └─────┬─────┘
              pass      │      │ fail
         ┌──────────────┴──────┴──────────────┐
         ▼                                      ▼
    ┌───────────┐                          ┌────────┐
    │ Committed │  мутация на диске       │ Failed │
    └─────┬─────┘                          └───┬────┘
          │ attestation                       │ rollback
          ▼                                     ▼
    ┌─────────────┐                      ┌──────────┐
    │ Attested    │  TrustChain commit   │ Rolled   │
    └─────────────┘                      │ Back     │
                                         └──────────┘
```

| Из состояния | Событие | В состояние | Запрещено |
|:---|:---|:---|:---|
| Draft | `plan` | Planned | apply без plan (в enforce) |
| Planned | `apply` | Applying | — |
| Applying | success | Verifying | skip verify при enforce |
| Applying | matcher/backup error | Failed | — |
| Verifying | pass | Committed | attestation до commit |
| Verifying | fail | Failed | — |
| Committed | `attest` | Attested | — |
| Failed | `rollback` | Rolled Back | — |
| * | `replay` | Draft → … | перезапись активной сессии без policy |

Replay и `session continue` входят в машину как восстановление **Draft** или **Planned** из внешнего источника (transcript, log).

### 2.4. Грамматика команд

Новые **доменные** глаголы; существующий CLI остаётся как **porcelain** (алиасы, обратная совместимость).

| Домен | Предлагаемая грамматика | Porcelain | Реализовано |
|:---|:---|:---|:---|
| Сессия | `apatch session start \| continue \| status \| end` | `apply --logs` | ✅ start/continue/status/end |
| Мутация | `apatch mutation plan \| apply \| strip` | `plan`, `apply`, `strip` | ✅ aliases + strip → runtime |
| Верификация | `apatch verify run \| status` | `verify semantic`, R41–R52 | ✅ semantic + `verify run` facade |
| Свидетельство | `apatch attestation show \| export` | `proof`, `doctor` | ✅ show + export bundle |
| Откат | `apatch rollback [--to checkpoint]` | `rollback` | ✅ → `MutationRuntime.rollback` |
| Повтор | `apatch replay <transcript\|log>` | `replay`, `scan` + `apply` | ⬜ porcelain only (Phase 0) |

Пример целевой сессии (агент или человек):

```bash
apatch session start --intent "R44: fix layer violation in services/"
apatch mutation plan --from cursor-transcript.jsonl
apatch mutation apply --confirm
apatch verify run --manifest manifests/engineering-pipeline.example.json
apatch attestation show --json       # mode: audit | enforce
apatch session end
```

### 2.5. Runtime API

Тонкий модуль-фасад `apatch/runtime/` поверх существующей логики — **без дублирования** matcher/backup/trustchain. API **сессионно-ориентированный**: `Session` — единственная точка входа для мутаций и завершения.

```python
# Целевой контракт (иллюстрация)
class MutationRuntime:
    def open_session(self, intent: Intent) -> Session: ...
    def plan(self, session: Session, source: MutationSource) -> MutationPlan: ...
    def apply(self, session: Session, plan: MutationPlan) -> MutationResult: ...
    def verify(self, session: Session, spec: VerificationSpec) -> VerificationResult: ...
    def attest(self, session: Session) -> Attestation: ...
    def rollback(self, session: Session, to: Checkpoint | None) -> RollbackResult: ...
    def replay(self, source: ReplaySource) -> Session: ...
```

MCP-tools и CLI вызывают **одни и те же** методы runtime; переходы state machine проверяются внутри runtime, а не в каждом адаптере.

### 2.6. Domain Event Stream

**Не event bus и не event sourcing** — на несколько релизов достаточно **append-only журнала** доменных событий. Подписчики (Console, MCP tail, CI) читают поток; отдельная message bus может никогда не понадобиться.

| Событие | Когда | Типичный читатель |
|:---|:---|:---|
| `SessionStarted` | `session start` | Console |
| `MutationPlanned` | после `plan` | Agent (confidence preview) |
| `MutationApplied` | успешный apply/strip | TrustChain, sandbox lease |
| `MutationFailed` | matcher/backup error | Agent retry policy |
| `VerificationPassed` / `VerificationFailed` | pipeline / ci-gate | CI, enforcement |
| `AttestationCommitted` | TrustChain checkpoint + commit | export, doctor |
| `RollbackCompleted` | rollback / strip lease release | Console, MCP |

**Транспорт v0:** `.apatch/events.jsonl` (JSON Lines, schema version в записи).  
**Опционально позже:** in-process fan-out для TUI; SSE для remote observers. Полноценный pub/sub bus — только при доказанной необходимости.

### 2.7. Mutation Console

Fullscreen TUI в духе `lazygit` / `k9s` — **не** терминальный эмулятор.

> **Mutation Console** — референсная проекция доменной модели Session / Mutation / Attestation для человека и агента. Это способ **закрепить язык системы**; выкинуть Console «потому что есть MCP» значит потерять общий операционный образ сессии, а не просто UI.

| Панель | Содержимое | Статус |
|:---|:---|:---|
| Session | intent, lifecycle, phase, checkpoint, risk | ✅ |
| Attestation | TrustChain mode, HEAD, notarized_index | ✅ |
| Verification / Policy | sandbox, enforcement, recommended_verify | ✅ G5 |
| Events | tail `events.jsonl` | ✅ |
| Mutations (очередь plan/apply) | strategy, confidence per step | ✅ G5+ (после `p`) |

Запуск: `apatch` (TTY) или `apatch console`. **G5:** `i` intent · `l` logs · `p` plan · `a` apply-session chunk · `v` verify · `r`/`q`.

### 2.8. Маппинг «bash-мышление → apatch-мышление»

| Привычка в shell | Эквивалент в apatch |
|:---|:---|
| файл | mutation |
| процесс | session |
| exit code | verification |
| history | replay |
| undo | rollback |
| audit log | attestation |

### 2.9. Relationship to Git

Новый читатель неизбежно спросит: *почему это не `git commit`?*

| Git | apatch |
|:---|:---|
| История изменений (VCS) | Управляемое **исполнение** изменений |
| Commit | Mutation |
| Branch | Session |
| Revert | Rollback |
| Signed commit | Attestation (TrustChain) |
| `git apply` / patch | Matcher + checkpoint + policy |

Git остаётся системой контроля версий и долгосрочной истории репозитория. apatch — **слой governed execution** поверх рабочей копии: атомарные мутации, verify до/после, криптографическое свидетельство и sandbox для агентов. Коммит в Git — следствие успешной сессии, а не замена ей.

---

## 3. Связь с уже реализованным (Phase 0)

RFP-003 и orchestration R41–P4 **закрыли транспорт и тактику**. RFP-004 не переписывает matcher/TrustChain — он **именует и связывает** их в operating model и runtime.

| Область | Статус | Артефакты |
|:---|:---|:---|
| Matcher + backup | ✅ | `matcher.py`, `backup.py` |
| TrustChain + enforcement | ✅ | `trustchain_helper.py`, `doctor` → `trustchain.mode` |
| MCP stdio / 38 tools | ✅ | `apatch/mcp/`, `stdio_guard.py` |
| Session state | ✅ | `session_state.py`, `apply_session.py` |
| Sandbox + CI gate | ✅ | `sandbox.py`, `ci-gate`, MCP whitelist hook |
| Replay + failure taxonomy | ✅ | `replay.py`, `failure_taxonomy.py` |
| Orchestration pipeline | ✅ | R41–R52, `workflows.py` |
| Domain docs + invariant | ✅ G1 | `docs/domain.md`, `docs/grammar.md`, `docs/security-one-pager.md` |
| Command grammar facade | ✅ G2 | `apatch session start\|status\|end`, `apatch attestation show`, `apatch proof` |
| Runtime module | ✅ E1 | `apatch/runtime/` — `MutationRuntime`, `state_machine.py`, `errors.py` |
| Domain event stream | ✅ G4 v0 | `.apatch/events.jsonl`, `runtime/events.py` → `emit_for_tool_result` |
| Mutation Console | ✅ G3+G5 | `apatch/console/mvp.py`, `console/state.py`; TTY + интерактив |
| MCP domain tools | ✅ Phase 0 | read-only: `apatch_attestation_show`, `apatch_session_state` |
| MCP domain tools | 🚧→✅ Phase 1 | write/facade: `apatch_session_start/end`, `apatch_verify_run`, `apatch_attestation_export`, `apatch_events_tail` |
| State machine enforce | ✅ E1 | `RUNTIME_TRANSITION`; governed session в enforce |
| Tests RFP-004 | ✅ | `tests/test_runtime_rfp004.py`, `test_runtime_state_machine.py`, `test_console_g5.py` (381 total) |

### 3.1. Журнал реализации (2026-06-05)

| Шаг | Что сделано | Ключевые файлы |
|:---|:---|:---|
| G1 | Core Invariant, Session aggregate, Relationship to Git в docs; security one-pager | `docs/domain.md`, `docs/grammar.md`, `docs/security-one-pager.md` |
| G2 | Доменные CLI-глаголы session / attestation | `apatch/cli.py`, `apatch/runtime/session.py`, `attestation.py` |
| G3 | Console MVP: Session · Attestation · Events | `apatch/console/mvp.py` |
| G4 | Append-only event stream + emitter | `apatch/runtime/events.py`, hook в `session_state.enrich` |
| G5 | Интерактив: `i/l/p/a/v`, панель Verification/Policy, UI state | `apatch/console/mvp.py`, `console/state.py` |
| E1 | `MutationRuntime`: plan, apply, rollback, apply_session, verify_semantic | `apatch/runtime/runtime.py`, `state_machine.py` |
| E1 | Проводка MCP/CLI через runtime; enforce transitions | `apatch/mcp/server.py`, `apatch/cli.py` |
| E2 | Единый `emit_for_tool_result`; anti double-enrich в MCP wrapper | `runtime/events.py`, `mcp/server.py` |
| — | Исправлен баг: `session` group не был зарегистрирован в CLI | `cli.add_command(session_group)` |

**Закрыто (2026-06-06):**

- Typed `Session` dataclass (`runtime/models.py`, `load_typed_session`)
- `MutationRuntime.attest` + `apatch attestation commit` + MCP `apatch_attest`
- `verify status` facade + MCP `apatch_verify_status`
- Interactive `apply` → `MutationRuntime.apply_interactive`
- Тесты: `test_rfp004_final.py`, `test_runtime_e2_completion.py` (400 total)

### 3.2. Phase 1 — MCP как first-class domain interface (2026-06-07)

**Принято (архитектурно):** закрыть разрыв «домен в runtime, но не в MCP». Первый срез — без auto-session; **доработано (2026-06-07):** `governed_mode`, `apatch_governed_phase_run`, `apatch_strip`/`apatch_phase_run` через `MutationRuntime`.

| P | Deliverable | Статус |
|:---|:---|:---|
| P1 | MCP tools: `session_start`, `session_end`, `verify_run`, `attestation_export`, `events_tail` → `MutationRuntime` only | ✅ |
| P1 | `RuntimeTransitionError` / `state_update` / `doctor` → MCP tool names в `hint` / `next_action` | ✅ |
| P2 | `invariant` + `core_invariant` в каждом enriched MCP-ответе | ✅ |
| P2 | `AGENTS.template.md` + consumer playbooks — governed workflow через MCP | ✅ |
| P3 | Event stream tail tool; Console остаётся TTY-only | ✅ tail |
| **Закрыто (2026-06-07)** | `governed_mode` (off/auto_session/strict), `apatch_governed_phase_run`, `apatch mutation replay` | ✅ |
| **Отложено** | авто-attestation / авто-verify (расширенный auto-governance) | backlog |

**Критерий успеха Phase 1:** агент проходит  
`Intent → Session → Mutation → Verification → Attestation | Rollback`  
через MCP без shell и без чтения `.apatch/*` вручную. Тест: `tests/test_mcp_rfp004_lifecycle.py`.

**Не в Phase 1 (явный anti-pattern):** composed orchestration со скрытым rollback; Session как implicit side-effect.

---

## 4. Коммерческое позиционирование (GTM)

При **продаваемом продукте** RFP-004 — не рефакторинг ради порядка, а **условие входа на enterprise-рынок** и страховка вендора от репутационного риска.

| Вопрос клиента | Ответ из operating model | Артефакт для сейлза / security |
|:---|:---|:---|
| «Как гарантировать, что агент не сделает несанкционированное?» | Core Invariant + Policy + sandbox | Схема §2.2, `trustchain.mode`, enforcement |
| «Кто что менял и когда?» | Session + Attestation | `apatch attestation show`, TrustChain HEAD |
| «Как вы предотвращаете обход проверок?» | State machine, единый runtime | Диаграмма §2.3, roadmap runtime |
| «Где audit log?» | Domain Event Stream | `.apatch/events.jsonl` (v0) |

**Доверие продаётся как фича:** в финансах, медицине, госсекторе прослеживаемость — не nice-to-have, а gate в procurement. Split-brain между адаптерами при инциденте у клиента — **репутационный риск вендора**, не только операционный риск клиента.

| Артефакт RFP-004 | Роль в GTM |
|:---|:---|
| Core Invariant | Security questionnaire, SOC2 narrative |
| Mutation Console | Демо на сейлз-звонке («видимая» часть продукта) |
| Domain Event Stream | Enterprise audit log |
| Runtime API + state machine | Техническая гарантия «один путь исполнения» (due diligence) |

### Классы инцидентов и порядок цены (для вендора)

| Класс | Пример | Без operating model | Оценка ущерба |
|:---|:---|:---|:---|
| Orphan mutation | MCP apply без Session/Intent | Нет ответа «кто и зачем» | 2–8 ч расследования; задержка audit |
| Split-brain | Новый адаптер минует verify | Сломанный код у клиента через ваш продукт | 0.5–3 инженеро-дня; **репутация** |
| Attestation ≠ verification | «Подписано» воспринято как «проверено» | Ложное доверие в demo/audit | 1 день – инцидент у клиента |
| Операционная слепота | 5 источников правды при разборе | Долгий support, ошибочный git reset | 30–90 мин × частота тикетов |

*Placeholder: добавить анонимизированные постмортемы команды по мере накопления.*

---

## 5. Этапы реализации: два трека

Один linear roadmap **не подходит**: для продажи важнее **видимость и язык** (Console, invariant), для зрелости продукта — **единый runtime** (страховка от split-brain). Треки идут **параллельно** после общего фундамента.

```text
GTM (видимость)          Engineering (страховка)
─────────────────        ───────────────────────
G1 docs + invariant ✅ ─┬─ E1 Runtime API ✅
G2 thin CLI ✅           │     plan/apply/rollback/apply-session
G3 Console MVP ✅      ─┘
G4 event stream ✅
G5 Console interactive ✅
                         E2 hardening ✅ (strip/pipeline/replay; contract tests)
```

### Общий фундамент — G1 ✅

- [x] `docs/domain.md`, `docs/grammar.md`, `docs/security-one-pager.md`
- [x] CLI: `session status`, `attestation show`, `proof show` (alias)
- [x] Porcelain map в README
- [x] `schema_version` в `--json` session / attestation

### GTM-трек (что показать клиенту раньше всего)

**G2 — Thin CLI ✅**  
`session start|end|status`, `attestation show`, MCP `apatch_attestation_show`.

**G3 — Mutation Console MVP ✅**  
`apatch/console/mvp.py` — Session · Attestation · Events; `apatch` / `apatch console`.

**G4 — Domain Event Stream v0 ✅**  
`.apatch/events.jsonl`; `emit_for_tool_result` — SessionStarted, MutationPlanned/Applied/Failed, Verification*, AttestationCommitted, RollbackCompleted.

**G5 — Console interactive ✅**  
Панель Verification/Policy; клавиши `i` intent · `l` logs · `p` plan · `a` apply chunk · `v` verify · `.apatch/console.json`

### Engineering-трек (параллельно, не блокирует G3)

**E1 — Runtime API + state machine ✅**  
- [x] `MutationRuntime` + `state_machine.py` — lifecycle transitions
- [x] MCP + CLI `plan` / `apply -y` / `rollback` → `MutationRuntime`
- [x] enforce: `session start --intent` обязателен для plan/apply; apply только из `planned`
- [x] `apply-session` → `MutationRuntime.apply_session` (MCP + CLI)
- [x] interactive `apply` TUI enforce gate; `strip` / `pipeline` / `replay` → runtime

**E2 — Hardening ✅**  
- [x] `emit_for_tool_result` в `runtime/events.py` (единый emitter)
- [x] MCP wrapper: skip double-enrich если `state_update` уже есть
- [x] `verify` / `pipeline` / strip через runtime
- [x] contract tests lifecycle ↔ failure_taxonomy (`test_runtime_e2_completion.py`)
- [x] `session continue`, `attestation export`, `verify run`, `mutation` subgroup

> **Почему Console раньше Runtime:** покупатель и CISO не видят `workflows.py`. Они видят Console и one-pager с инвариантом. Runtime — ваша страховка; Console — ваш storefront.

---

## 6. Адаптеры (без изменения core)

| Адаптер | Роль |
|:---|:---|
| **MCP** | Тонкие tools → Runtime API; skills/playbook в `AGENTS.template.md` |
| **CI** | `apatch sandbox ci-gate`, чтение `VerificationFailed` из event stream |
| **Hooks** | `beforeMCPExecution` whitelist → Policy enforcement |
| **IDE** | Cursor / Claude Code — клиенты MCP, не форк редактора |

Миграция MCP: **43 tools** (Phase 1 +5 domain); в документации группируются по families (`session/*`, `mutation/*`, `verify/*`, `attestation/*`). Breaking rename — только с major version bump.

---

## 7. Критерии успеха (product)

| # | Критерий | Статус |
|:---|:---|:---|
| 1 | Security narrative: invariant + attestation для enterprise questionnaire | ✅ `docs/security-one-pager.md` |
| 2 | Console demo-ready до полного runtime | ✅ G3+G5 до E1 strip/pipeline |
| 3 | Core Invariant через runtime (plan/apply/apply-session/strip/pipeline) | ✅ |
| 4 | State machine — единый источник переходов | ✅ plan/apply/strip/pipeline/replay |
| 5 | Event stream — audit log | ✅ `.apatch/events.jsonl` |
| 6 | Обратная совместимость porcelain | ✅ 381 тест |

---

## 8. Вне рамок

- **Эмулятор терминала** (iTerm, Warp, ghostty). apatch — mutation console, не shell.
- **Собственный IDE или форк VS Code.** Интеграция через MCP и hooks.
- **Облачное хранение кода.** Runtime локальный; TrustChain Platform — опциональный push подписей (как в RFP-003).
- **Привязка к внешним продуктовым линейкам** (commerce, CEI, отдельные knowledge-платформы). apatch — автономный open-source runtime.
- **Замена Git.** Git — VCS; apatch — governed execution layer (см. §2.9).
- **Event bus / event sourcing** как обязательная цель v1. Достаточно append-only stream.
- **Синтетические бенчмарки** для позиционирования. Ценность — архитектура: invariant, транзакции, policy, attestation.

---

## 9. Открытые вопросы

| # | Вопрос | Направление по умолчанию |
|:---|:---|:---|
| Q1 | Имя бинарника без аргументов: Console или help? | ✅ **Решено:** Console при TTY, help при pipe |
| Q2 | Версионирование JSON schema runtime | `schema_version` в каждом `--json` ответе |
| Q3 | Event stream: только JSONL или + in-process fan-out? | v0 JSONL; TUI читает tail + optional fan-out |
| Q4 | Группировка 38 MCP tools | Документация по families; код без rename в v1 |
| Q5 | Porcelain `proof` vs домен `attestation` | Оба CLI-глагола до major; docs — только attestation |

---

## 10. Ссылки

- [RFP-003](./RFP-003-agent-mcp-platform.md) — MCP-транспорт (реализован)
- [orchestration.md](../orchestration.md) — R41–R52 pipeline
- [mcp_setup.md](../mcp_setup.md) — конфигурация MCP-клиентов
- [sandbox.md](../sandbox.md) — policy и ci-gate
- [AGENTS.template.md](../AGENTS.template.md) — playbook для агентов
