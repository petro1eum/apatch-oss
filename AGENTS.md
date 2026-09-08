# apatch — инструкции для ИИ-агентов

Репозиторий **самого инструмента** apatch (Python CLI + MCP). При работе с кодом apatch **и** при dogfooding на этом репо — используй MCP tools, не `search_replace` для массовых правок.

> **ПОЛИТИКА:** изменения production-кода (`apatch/**`) — через тот же MCP-пайплайн, что и в consumer: `apatch_generate` → `apatch_apply_session`. Не коммить `.olang_cache/`, `extracted/`, runtime `.apatch/*` (lease, violations, inclusion) — **кроме** закоммиченных `.apatch/{sandbox,enforcement}.json` (dogfood gate).

Шаблон для **чужих** репозиториев (deployed runtime, копируется `init-consumer`): [docs/AGENTS.template.md](docs/AGENTS.template.md).  
При изменении MCP/CLI — обновляй шаблон + `consumer_profiles.agents_section` (MCP-only); sync: `init-consumer --refresh-agents`.

> **Онтология и границы слоёв — [Avatar Architecture Canon](docs/AVATAR-ARCHITECTURE-CANON.md).** Точка правды по сущностям `Avatar`, `ContributionEvent`, `Identity`, `Agent` и слоям L0→L4 (apatch = Слой 1 + компилятор аватара Слоя 2). Локальные определения, противоречащие канону, недействительны — правь их ссылкой на канон, а не дублированием.

**Онбординг нового агента:** [docs/agent-onboarding.md](docs/agent-onboarding.md) — `apatch_doctor` → `apatch_spec_lint` (один вызов = format + RFP + plan_scaffold). IDE tool count может отставать от `mcp_tool_catalog.count` — это нормально.

---

## Разработка apatch (только этот репозиторий)

| Правило | Где |
|---------|-----|
| Бизнес-логика | `apatch/workflows.py` — меняй здесь |
| CLI | `apatch/cli.py` — тонкая обёртка |
| MCP | `apatch/mcp/server.py` — tool на каждую `workflows.*` функцию |
| Оркестрация | `graph_execute.py`, `orchestrate.py`, `pipeline_run.py`, `apply_session.py` |
| Sandbox | `sandbox.py`, `sandbox_watch.py` |
| Enforcement | `enforcement.py`, `trustchain_helper.py` |
| State machine | `session_state.py`, `failure_taxonomy.py` |

**Новая команда:** `workflows.py` → CLI → MCP tool → `tests/test_mcp.py` (`expected` ⊆ registered; сейчас **124** — `apatch_doctor` → `mcp_health.tool_count`, он и есть источник, а не эта строка).

**Тесты:**

```bash
pip install -e ".[mcp,dev,yaml]"
pytest tests/ -q
pytest tests/test_mcp.py tests/test_mcp_stdio_live.py -q
```

**Документация при изменении CLI/MCP:** `docs/README.md` (навигация), `docs/mcp_setup.md`, `docs/AGENTS.template.md`, `docs/orchestration.md`, `docs/sandbox.md`, `docs/cookbook.md` (только новые рецепты).

**MCP сервер:** `apatch-mcp` / `python -m apatch.mcp.launcher` (stdio). Stderr → `.apatch/mcp_stderr.log`; в `mcp.json` задай `PYTHONIOENCODING=utf-8` (см. `docs/mcp_setup.md`).

**Parity:** [docs/mcp_setup.md](docs/mcp_setup.md) — полная таблица CLI ↔ MCP.

### Runtime hygiene (dogfood + consumer)

Patch JSONL — **EPHEMERAL**, не source code. Не создавай `patches-*.jsonl` в корне вручную.

```text
session_start → generate_batch → apply_session → attest → session_end
```

- JSONL маршрутизируется в `.apatch/tmp/<session_id>/` (см. [RFP-016 §5.3](docs/RFP-016-runtime-hygiene.md)); `spec_run` / `execute_next` используют тот же routing
- `session_end` удаляет released EPHEMERAL
- Legacy / overflow: `apatch_gc(mode="safe")`, `apatch_gc(mode="rotate")`, `apatch_gc(mode="reconcile")` — backfill inferred в registry
- `doctor.hygiene.status: critical` (inference sunset) → `apatch gc --reconcile` или auto-reconcile перед apply при multi-chunk сессии
- Consumer playbook: [docs/AGENTS.template.md §1.2](docs/AGENTS.template.md)

---

## Agent playbook (MCP) — используй в этом репо так же

`target_dir="."` — корень workspace. Каждый MCP tool возвращает `state_update` → читай `next_action`.

### Первый шаг

```text
apatch_doctor(target_dir=".")
```

**Executable specs:** в JSON ответа — `spec_authoring`, `spec_execution`, `spec_run` (формат
`docs/specs/SPEC-*.md`, workflows §3I–§3K). Каждый `apatch_spec_*` tool дублирует `spec_authoring`
в ответе — агенту не нужно искать docs apatch repo.

Смотри `trustchain.mode`: `audit_pending` | `audit` | `enforce` и `trustchain.behaviors` (жёсткость нотаризации).

### Master workflow

```text
1. apatch_doctor
2. apatch_generate_batch (или apatch_generate) → patches.jsonl
3. apatch_simulate
4. apatch_apply_session (loop, checkpoint)  — или apatch_orchestrate
5. apatch_verify_notarization(staged=true)
6. apatch_arch_check / apatch_db_check (если нужно)
7. pytest tests/
```

Массовые правки в `apatch/**/*.py`: **только `apatch_apply_session`**, не `apatch_apply`, не sed.

Если Codex снова спрашивает разрешения пачками, сначала проверь класс проблемы:

```bash
apatch mcp codex-doctor --target-dir /path/to/apatch --json
```

`filesystem_prompts_likely=true` означает, что Codex запущен с другим writable workspace; это лечится workspace root/writable root, а не MCP approval.

### Local workspace roaming

Для bound-репозитория используй `target_dir="."`. Человек регистрирует sibling через
`apatch workspace add <alias> <root>`; агент сначала вызывает
`apatch_workspace_inspect(alias=..., include_contract=true)`, затем использует только
`target_dir="@alias"`. Сырой абсолютный cross-workspace path запрещён.

### MCP tools

> Consumer default — 17 intent-level tools (`APATCH_MCP_PROFILE=compact`). Полная таблица и families: [docs/mcp_setup.md](docs/mcp_setup.md); `core` / `spec` / `full` включаются только когда нужны. Не дублируй полный список здесь — обновляй `mcp_setup.md`.
> В наборе по умолчанию есть два читающих инструмента для ориентации: `apatch_impact` — что зацепит правка, до выбора цели; `apatch_build_diagnose` — что именно сломалось, после красной проверки. Скрытые профилем инструменты доктор называет явно в `agent_protocol.requires_profile_upgrade`, а не молчит о них.
> **Spec-driven (RFP-007):** [docs/RFP-007-executable-specifications.md](docs/RFP-007-executable-specifications.md) · [docs/spec-authoring.md](docs/spec-authoring.md)

#### Патчи и сессия

| Tool | Назначение |
|------|------------|
| `apatch_scan` | JSONL транскрипты IDE |
| `apatch_view` | Кандидаты в логе |
| `apatch_plan` | Dry-run одного патча |
| `apatch_plan_batch` | Dry-run JSONL |
| `apatch_generate` | JSONL find/replace (`match_mode`: literal\|whitespace\|regex\|json) |
| `apatch_generate_batch` | **Mutation generator:** `action` replace\|create\|delete\|rename\|chmod\|shift_outline\|insert_before\|insert_section → JSONL |
| `apatch_execute_next` | RFP-008: governed cycle на одно требование SPEC (`needles` → batch) |
| `apatch_spec_run` | RFP-009: вся спека — inline `requirements` (default chunk=all); `peer_specs` → R4 gate |
| `apatch_spec_run_manifest_lint` | Lint run manifest vs SPEC.md |
| `apatch_spec_run_multi` | RFP-014 Phase 3: N specs in schedule order; inline `requirements` map |
| `apatch_spec_coverage` | Requirement coverage + file-drift staleness (RFP-010) |
| `apatch_rebind_stale` | RFP-027: re-verify + re-attest все file_drift-stale Rk спеки одним вызовом (shared-file cascade) |
| `apatch_spec_interference` | Cross-spec L1/L2 conflicts, `safe_order`, `risk_score` (RFP-014) |
| `apatch_spec_schedule` | Schedule view + `risk_per_step` (RFP-014 Phase 1.5) |
| `apatch_spec_cross_verify` | Level-3 cross-verify sandbox (RFP-014 Phase 2) |
| `apatch_spec_plan_lint` | RFP-011: lint inline plan dict vs SPEC.md |
| `apatch_spec_plan_register` | RFP-011: register signed `plan:SPEC-X@vN` |
| `apatch_spec_plan_diff` | RFP-011: diff plan versions per Rk |
| `apatch_spec_adherence` | RFP-012: plan-vs-fact adherence report |
| `apatch_apply` | ≤15 кандидатов |
| **`apatch_apply_session`** | **Массовый apply, чанки, checkpoint** |
| `apatch_rollback` | Откат `session_id`=checkpoint |
| `apatch_simulate` | Preflight risk map |
| `apatch_replay` | Debug timeline сессии |

#### Sandbox / state / enforcement

| Tool | Назначение |
|------|------------|
| `apatch_session_state` | Фаза агента |
| `apatch_sandbox_status` | mode, lease, hooks |
| `apatch_sandbox_audit` | Audit; `auto_revert` |
| `apatch_sandbox_ci_gate` | PR gate: audit + notarization |
| `apatch_verify_notarization` | Staged vs TrustChain |
| `apatch_trust_enroll` | Enroll агента (мост к `tc cert request`) → корневой якорь |
| `apatch_verify_anchor` | CI-гейт: подпись apatch → leaf → root (PKIX + key-binding) |
| `apatch_verify_inclusion` | CI-гейт: op_id включены во внешний append-only лог (Merkle proof) |
| `apatch_policy_sign` | Подписать конфиг монитора → `.apatch/policy.lock.json` (tamper-evident) |
| `apatch_policy_verify` | Проверить подпись политики + дрейф конфига |

#### Strip

| Tool | Назначение |
|------|------------|
| `apatch_strip_dry_run` | Preview |
| `apatch_suggest_until` | Границы until |
| `apatch_strip` | Вырезание блоков |
| `apatch_phase_run` | Strip + module/native + verify |
| `apatch_natives_check` | C++ register_native |

#### Оркестрация

| Tool | Назначение |
|------|------------|
| `apatch_impact` | Граф затронутых файлов |
| `apatch_arch_check` | arch-rules |
| `apatch_db_check` / `apatch_db_revision` / `apatch_db_safety` / `apatch_db_run` | DB workflow |
| `apatch_verify_semantic` | Routes/exports/OpenAPI |
| `apatch_refactor_run` | rename_symbol bundle |
| `apatch_pipeline_run` | Линейный pipeline |
| `apatch_plan_graph` / `apatch_execute_graph` | Dependency graph |
| `apatch_orchestrate` | simulate → graph → execute |
| `apatch_trustchain_history` | Intent/artifact history (`artifact=`, `query=`) |
| `apatch_trustchain_coverage` | Artifact traceability + `op_id` reverse map |
| `apatch_index_build` / `apatch_index_query` | Project index |

#### Прочее

| Tool | Назначение |
|------|------------|
| `apatch_doctor` | Диагностика |
| `apatch_compile` | Markdown semantic ID |
| `apatch_init_consumer` | Скаффолд consumer (`with_sandbox`, `with_enforcement`, …) |
| `apatch_spec_scaffold` | RFP-023: скелет SPEC.md из `## Acceptance`-таблицы RFP (`rfp=`, `spec=`) — не пиши чеклист руками |
| `apatch_scip` | RFP-033: кросс-файловый refactor-impact (`action=index\|impact`) — нативно, advisory, никогда не блок |

### Качество гейта и реальность (CLI + MCP, RFP-005)

Зелёный verify ничего не доказывает, пока не знаешь, что он **умеет** краснеть и **всё ещё** краснеет где должен. Есть и CLI (shell), и MCP-тулы `apatch_probe` / `apatch_reality`.

| Намерение | Команда |
|-----------|---------|
| Гейт реально тестирует охраняемое? | `apatch probe falsify --verify "<cmd>" --files a.py,b.py` → `real`/`false` |
| Нет новых падений vs корпус? | `apatch probe regress --verify "<cmd>" [--allow ...]` → `stable`/`regressed` |
| Аттестованный гейт всё ещё зелёный? | `apatch probe ratify --verify "<cmd>"` → `ratified`/`stale` |
| Наблюдаемый факт (баг/инцидент/фидбэк) | `apatch reality add --summary "..." --source <tracker> --kind bug` |
| Что из реальности покрыто, а что долг? | `apatch reality status` (`--json`; exit 1 при долге) |

Один примитив «возмути → пере-измерь → суди дельту против полярности» (`must_diverge` falsify / `must_hold` regress·ratify). Реальность стоит над спекой: требование закрывает запись строкой `(discharges: REC-…)` в `## Rk …`; `uncovered` — выводимый долг, не ручной ярлык. Подробно: [docs/probe.md](docs/probe.md) · [docs/reality.md](docs/reality.md).

#### Язык статусов continuous conformance

Не называй декларацию доказательством. `used_for_spec`, `conformance: true` и существующий SPEC-файл не равны фактическому enrollment в `.apatch/conformance.json`, live-green bucket и доменной evidence qualification. Эти четыре слоя считаются и сообщаются отдельно. После `apatch_resume_session` свежая `session_capability` заменяет token от `session_start`; remote orchestrator 0.8.11+ делает это автоматически.

### Cookbook (краткие рецепты)

**Мутации (эталон — replace + create в одном JSONL):**

```text
apatch_generate_batch(needles=[
  {action: "create", target_file: "apatch/new_module.py", content: "…"},
  {action: "replace", find_text: "…", replace_text: "…", target_file: "apatch/workflows.py"},
  {action: "chmod", target_file: "scripts/run_live_feedback_contract.sh", executable: true},
], out_path="patches.jsonl")
apatch_simulate(logs_path="patches.jsonl")
apatch_apply_session(logs_path="patches.jsonl", verify="pytest tests/test_generate_batch.py -q")
```

Mode-only changes use `chmod` needles, not shell `chmod` or temporary remote policy actions. `mode` accepts `755`/`0755`/`0o755`/`100755`; `executable: true` means `755`. The change is backed up, notarized, shown as a mode diff, and rollback restores the prior permission bits.

**Массовый replace (один паттерн, много файлов):**

```text
apatch_generate(find_text="...", replace_text="...", glob_pattern="apatch/**/*.py", out_path="patches.jsonl", replace_all=true)
apatch_simulate(logs_path="patches.jsonl")
apatch_apply_session(logs_path="patches.jsonl", verify="pytest tests/test_mcp.py -q", replace_all=true)
# repeat apply_session until continue=false
```

**Orchestrate:**

```text
apatch_orchestrate(manifest_path="docs/manifests/engineering-pipeline.example.json", dry_run=true)
```

**Post-mortem:**

```text
apatch_replay(session_id="<checkpoint>")
apatch_sandbox_audit(auto_revert=false)
```

**Strip FE:**

```text
apatch_phase_run(profile="frontend", manifest_path="...", verify="npm run build")
```

**DB chain:**

```text
apatch_apply_session(...) → apatch_db_check → apatch_db_revision → apatch_db_safety → apatch_arch_check
```

**Artifact-anchored session (RFP-006):**

```text
apatch_session_start(intent="…", artifacts=["spec:SPEC-42@sha256:…"])
apatch_apply_session(...)
apatch_attest()
apatch_trustchain_coverage(artifact="spec:SPEC-42")
```

**Spec run — вся спека (RFP-009, consumer §3K):**

```text
apatch_spec_run(spec="SPEC-X", dry_run=true)   # pending[], gaps[], manifest_template
apatch_spec_run(spec="SPEC-X", requirements={R1: {needles: [...]}, R2: {needles: [...]}})
# while continue: apatch_spec_run(spec="SPEC-X")
apatch_spec_status(spec="SPEC-X")
```

Dogfood `apatch/**` по SPEC-*.md — §3I (`spec_next` → session per Rk). Реализация apatch core по [SPEC-RUN-1](docs/specs/SPEC-RUN-1.md) — attested.

Полные рецепты: [docs/AGENTS.template.md](docs/AGENTS.template.md) · CLI-варианты: [docs/cookbook.md](docs/cookbook.md).

### Failure taxonomy (RFP-021 AR-3)

| `error_type` | `recommended_action` | Когда |
|--------------|----------------------|-------|
| `VERIFY_FAILED` | `fix_forward` | verify red, apply/notarization ok; **не** auto-rollback (baseline / новые тесты) |
| `VERIFY_FAILED` + `verify_rollback=true`, без `rollback_performed` | `rollback` | chunk verify запросил откат, который ещё надо выполнить |
| `VERIFY_FAILED` + `rollback_performed=true` | `fix_forward` | файлы уже восстановлены; resume той же governed session и исправленные needles |
| `NOTARIZATION_FAILED` / `APPLY_FAILED` | `rollback` | integrity risk |
| `RUNTIME_TRANSITION` | `resume_session` | lifecycle desync — reconcile (SPEC-SESSION-RECOVERY-1) |
| `MASS_APPLY_BLOCKED` / `ARCH_VIOLATION` / `DB_RISK` / `BUDGET_EXCEEDED` | `reduce_scope` | |
| `DIRECT_WRITE_BLOCKED` / `LEASE_*` | `retry_chunk` | sandbox |
| `TRUSTCHAIN_REJECTED` | `retry_chunk` | |

Pre-existing red после apply: `baseline=compare` → `ok: true`, `pre_existing_only` (AR-4). Async verify: `apatch_verify_run(async_mode=True)` → poll `apatch_verify_status(job_id=…)` (AR-2). Job выполняет отдельный stdio-isolated worker: большой output не блокирует MCP, restart не теряет terminal state; умерший worker становится persisted `failed`, а не вечным `running`.

### State machine

Фазы: `idle → plan → apply → verify → arch → db → rollback → complete | blocked`.  
Файл: `.apatch/session_state.json`. Проверка: `apatch_session_state`.

### Sandbox (dogfooding)

```text
apatch_init_consumer(target_dir=".", with_sandbox=true, with_enforcement=true, with_ci=true)
```

Protected: в **этом** репо — `apatch/**` (см. `.apatch/sandbox.json`); в consumer — `src/**`, `app/**`, `services/**`, `packages/**`. Мутации — через `apatch_apply_session` (lease). CI: job `apatch-gate` (required check в branch protection).

**Cursor hooks** (`init-consumer --with-sandbox`):

| Hook | Эффект |
|------|--------|
| `preToolUse` | блок Write/StrReplace в protected |
| `beforeShellExecution` | блок sed/tee/redirect |
| `beforeMCPExecution` | whitelist только `apatch_*` MCP |

Локальная/сохранённая-state диагностика: `apatch sandbox ci-gate`. Fresh-checkout CI не видит ephemeral lease/ignored ledger; generated workflow проверяет committed policy и tracked `manifests/apatch-inclusion.jsonl` (если он есть). Devcontainer: `--with-devcontainer`.

### Антипаттерны

- sed / search_replace для массовых правок в `apatch/`
- `apatch_apply` на большой JSONL
- Игнорировать `checkpoint` и `state_update.next_action`
- Commit без тестов после изменения workflows/MCP

---

## Грабли governed-цикла (живые, из dogfood)

- **Доки (RFP/SPEC.md) от Write/Edit — never_notarized.** `verify_notarization(staged=true)` блокирует commit. Проведи через governed-мутацию (footer needle → `apply_session` → `attest`); в notarized-индекс попадают только файлы заверённой мутации.
- **Хвост файла за-stale-ивает последний Rk.** `content_hash` последнего требования тянется до EOF — правка после «## Non-goals» даёт `spec_text_changed` (rebind не чинит). Переаттестуй один Rk: `session_start(requirement='SPEC-X#Rk')` → `apatch_noop_attest(covered_by='Rk')`. Или держи хвостовые правки выше последнего «## Rk».
- **Ложный `SPEC_DEPENDENCY_UNMET`.** Слово `dependency`/`requires`/`depends on` на одной строке с другим `SPEC-XXX` делает его фантомной upstream-зависимостью. Держи dep-слова подальше от строк с чужими SPEC-id.
- **Shared file across Rk → file_drift.** Один модуль/тест на несколько Rk: после batch ранние Rk → stale. `apatch_rebind_stale(spec='SPEC-X')` чинит одним вызовом.
- **Неизвестный аргумент — отказ, а не тишина.** Раньше любой ключ вне схемы инструмента молча выбрасывался, и вызывающий получал `ok` за работу, которую не заказывали. Теперя такой вызов падает. Частый случай: у `apatch_execute_next` параметр называется `verify_override`, а не `verify`.
- **Собственность заявляется явно.** Файл реализации принадлежит требованию только после `> **ownership mode:** strict` и строки `owns:`. Без этого шлагбаума нет вовсе — не читай зелёный гейт как защиту, которую ты не объявлял. Один файл могут заявить несколько Rk одной спеки — разрешает любой из них; неоднозначность — только когда путь заявляют две разные спеки.
- **Строгое требование пишет только объявленное.** Из сессии, привязанной к strict-Rk, недоступен даже ничейный файл. Расширяй `owns:` отдельной сессией — новый список действует со следующей генерации, не задним числом.
- **Не называй чужой RFP-id в теле требования.** Линт переякорит спеку на эту RFP и потребует трассировку на её таблицу приёмки — та же семья, что фантомный `SPEC_DEPENDENCY_UNMET`.
- **Проверка требования должна быть про требование.** `pytest -q` на весь набор в `(verify:)` ничего не доказывает про конкретный Rk, не влезает в бюджет контрактного гейта и приходит как `broken`, а не как красное. «Весь набор зелёный» — работа conformance и CI.
- **`falsify` на документе.** Проверку «строка присутствует» нельзя сломать дописыванием — зонд докидывает удаляющий мутант и называет в ответе `mutation`. Вердикт `false_gate` на старом рантайме — повод проверить руками, а не верить.
- **Требование без собственной мутации не финализируется.** `execute_next(finalize=true)` из `draft` откажет (`RUNTIME_TRANSITION`). Путь: `session_start(requirement=...)` → прогнать его `(verify:)` → `apatch_noop_attest(covered_by='Rk')` → `session_end`.

---

## Документация

| Документ | Содержание |
|----------|------------|
| [docs/README.md](docs/README.md) | Индекс |
| [docs/AGENTS.template.md](docs/AGENTS.template.md) | **Полный agent playbook для consumer** |
| [docs/mcp_setup.md](docs/mcp_setup.md) | MCP setup + tool table |
| [docs/orchestration.md](docs/orchestration.md) | Pipeline, graph, orchestrate |
| [docs/sandbox.md](docs/sandbox.md) | Write sandbox spec |
| [docs/probe.md](docs/probe.md) | `apatch probe` — falsify/regress/ratify (качество гейта, RFP-005) |
| [docs/reality.md](docs/reality.md) | `apatch reality` — наблюдаемая реальность как источник истины (RFP-005) |
| [docs/cookbook.md](docs/cookbook.md) | CLI рецепты |

```bash
apatch init-consumer --target-dir /path/to/project --profile sqlalchemy --with-arch-rules --with-enforcement --with-sandbox
```
