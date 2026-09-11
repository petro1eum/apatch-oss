# ИИ-агенты и apatch MCP

Этот проект использует [apatch](https://pypi.org/project/apatch/) для управляемых мутаций кода.

> **Ты в consumer-репозитории.** Этот файл (`AGENTS.md`) — **единственный** операционный контракт. Пути `docs/…` репозитория apatch **недоступны**. Всё нужное — здесь, в `manifests/PROFILE.*.md`, `manifests/*.json`.  
> **Только MCP tools** (`apatch_*`). Не `search_replace`, не `sed`, не терминал `apatch` при доступном MCP. Для bound-репозитория каждый вызов: `target_dir="."`. Зарегистрированный человеком sibling разрешён только как `target_dir="@alias"` после `apatch_workspace_inspect(alias=..., include_contract=true)`; сырой абсолютный cross-workspace путь запрещён.

---

## 0. Политика (не обсуждается)

Любое изменение исходного кода в protected-зонах обязано пройти **governed lifecycle** и иметь **Ed25519-подпись** в `.trustchain/` (при `enforce`). Правка в обход → `DIRECT_WRITE_BLOCKED`, `TRUSTCHAIN_REJECTED`, `NOTARIZATION_FAILED`; git hook отклонит commit.

**Core invariant:** `Intent → Session → Mutation → Verification → Attestation | Rollback`

Читай в **каждом** MCP-ответе: `state_update.next_action`, `invariant.satisfied`, `error_type`, `recommended_action`.

---

## 1. Каждая сессия начинается здесь

```text
apatch_doctor(target_dir=".")
```

**Протокол (не обсуждается):** `apatch_doctor` → **`protocol_contract`**, **`agent_onboarding`**
(если есть в ответе) — там же, почему нельзя ручной staging `patches.jsonl` и N× `spec_next`.
**Spec gate:** один вызов `apatch_spec_lint(spec='SPEC-X')` → format + RFP coverage + `plan_scaffold`
(standalone `apatch_rfp_*` / `apatch_spec_needles_scaffold` могут отсутствовать в IDE tool list).
**Вся спека за один проход:** **`spec_run`** (RFP-009) → `apatch_spec_run(spec, requirements={Rk: {needles}})`.

**Executable specs:** также читай **`spec_authoring`**, **`spec_execution`** (§3I),
**`spec_run`** (§3K). Спеки: `docs/specs/SPEC-*.md`. Cursor rule `apatch-protocol.mdc`
(`alwaysApply`) после `init-consumer --with-mcp`.

**Design Partner Program (L1):** `docs/design-partner-playbook.md` — one-time setup, G1–G7 gates,
escalation (`fix_forward` vs `rollback`), async verify, parallel lanes (English).

| Поле doctor | Что делать |
|-------------|------------|
| `recommended_workflow` | Канонический порядок шагов (ниже) |
| `recommended_verify` | Человекочитаемая verify-цепочка |
| `recommended_verify_resolved` | Shell verify с абсолютными путями — передавай в `verify=` и default `apatch_verify_run` |
| `agent_protocol` | `mass_refactor`, `preflight_tool`, `checkpoint_after_every_chunk`, sandbox |
| `trustchain.mode` | `audit_pending` → `audit` → **`enforce`** (цель consumer) |
| `trustchain.behaviors` | `no_trustchain_allowed`, `incremental_notarization`, … |
| `sandbox` | Если `enabled` — protected зоны только через lease |
| `enforcement.active` | Если true — `no_trustchain` на apply **запрещён** |
| `warnings` | Устрани до массового рефакторинга |
| `mcp_health.tool_count` | Актуальное число MCP tools (справочник §8 синхронизирован) |
| `spec_authoring` | **Формат SPEC.md** — rules, lint_tool, template_minimal |
| `spec_execution` | §3I loop, workflow_choice (3I/3J/3K) |
| `spec_run` | §3K batch, needles, verify_deferred, lessons |
| `agent_protocol.executable_specs_entrypoint` | Краткая отсылка: doctor → spec_* playbooks |
| **`protocol_contract`** | **Антипаттерны** (ручной jsonl, обход session) + task_routing + RFP-009 summary |

**Жёсткий протокол (spec):**

0. **Вся спека** → `apatch_spec_run` (RFP-009), не N× §3I и не hand-staged jsonl.
0b. **≥2 спеки с interference** → `apatch_spec_run_multi` (§3L Phase 3), не N× ручной `spec_run` между спеками.
0c. **SPEC-owned files** require `spec_run/execute_next` with the exact `SPEC#Rk`; generic `generate_batch/remote_task` receives `SPEC_WORKFLOW_REQUIRED` before JSONL generation. Ownership is opt-in: a SPEC file owns itself; an existing slug contract protects bounded legacy category paths and explicitly declared category files (RFP-039), not shared dependencies or a slug word in a filename. Strict `> **ownership mode:** strict` plus an `owns:` line declares exact files or bounded prefixes and takes precedence. Files outside these surfaces are unowned, but still require the normal governed, sandboxed, signed workflow.
0d. **Независимое массовое обслуживание нескольких SPEC** → `spec_run_multi(execution_mode="shared_maintenance")`: один физический apply, точные native verify каждого Rk параллельно и одна attestation с подписанным `SPEC#Rk → files`. Каждый файл принадлежит ровно одному Rk; shared target блокируется до мутации.
1. **Одно Rk** → `apatch_execute_next(needles=[…])` или §3I вручную.
2. Цикл §3I на Rk: `spec_next` → `session_start(requirement)` → **`apatch_generate_batch`** → `simulate` → `apply_session` → **`verify_run` (из спеки)** → `attest` → `session_end`.

**Жёсткий протокол (общий):**

1. Массовые правки (>15 кандидатов) — только **`apatch_apply_session`**, не `apatch_apply`.
2. После **каждого** chunk сохраняй `checkpoint`; при сбое — `apatch_rollback(session_id=checkpoint)`.
3. Повторяй apply_session пока `continue=true` (`agent_next` / `state_update.next_action`).
4. Перед массовым apply: **`apatch_simulate`** → оцени `risk_level`, `rollback_probability`.
5. При ошибке apatch — чини apatch, не обходи regex/sed.

### 1.1 Установка apatch (consumer — без путей разработчика)

```bash
python3 -m pip install apatch[mcp]
cd /path/to/your-project
apatch init-consumer --target-dir . --with-sandbox --with-enforcement --with-mcp
```

`apatch doctor` → `mcp_health.recommended_install` для **consumer** — `pip install apatch[mcp]` (PyPI), не editable path.  
Editable `-e /path/to/apatch` — только при разработке самого инструмента apatch.

После `init-consumer --with-mcp`: Cursor → MCP → Restart **apatch**.

Keep the exact venv interpreter in both the IDE stub and canonical
`.apatch/mcp.json`. A shared binary realpath does not mean a shared environment.
`apatch mcp sync --force` preserves existing env/profile; use `--profile` to
change it explicitly. Doctor does not repair configuration.
`apatch mcp check --target-dir . --json` checks the configured bootstrap and
stdio catalog in a disposable workspace without copying user sessions or keys.
A running MCP doctor reports `not_checked_in_stdio` for that independent check:
current-process readiness is not configured-child readiness or host tool availability.

### 1.2 Runtime hygiene (patch JSONL — RFP-016)

| Правило | Почему |
|---------|--------|
| Не создавай `patches-*.jsonl` в корне вручную | EPHEMERAL; мусор и `ORPHAN_EPHEMERAL` в doctor |
| `session_start` → `generate_batch` → `apply_session` → `attest` → **`session_end`** | JSONL уходит в `.apatch/tmp/<session>/` и удаляется на session_end |
| Для SPEC — **`apatch_spec_run`** (§3K) | закрывает сессии per Rk автоматически |
| `doctor.hygiene.orphan_count > 0` | `apatch_gc(mode="safe")` |
| `doctor.hygiene.inferred_count > 0` или `status: critical` | `apatch_gc(mode="reconcile")` — регистрация inferred (HISTORY backups после apply и т.д.) |
| `HISTORY_OVERFLOW` в doctor | `apatch_gc(mode="rotate")` |
| Параллельные чаты | `APATCH_LANE=auto` + разные spec id (§ lanes) |

---

## 2. Что делаешь? (decision tree)

```text
Задача?
├─ Strip / декомпозиция монолита     → §3A  (session → dry-run → strip/phase → verify → attest)
├─ Мутации (replace / create / …)   → §3B  (session → generate_batch → simulate → apply_session)
├─ Replay транскрипта IDE             → §3C  (scan → plan_batch → apply, only_drifted)
├─ Pipeline / orchestrate манифест  → §3D  (orchestrate или plan_graph → execute_graph)
├─ Рефакторинг с БД                 → §3E  (generate → apply_session → db_check → db_revision)
├─ Один–несколько точечных патчей   → §3F  (≤15 канд.: plan → apply)
├─ Spec-driven: вся спека (≥2 Rk)   → §3K  (spec_run + inline requirements)
├─ Spec-driven: ≥2 спеки (interference) → §3L Phase 3 (spec_run_multi)
├─ Spec-driven: одно Rk / debug     → §3J  (execute_next)
├─ Spec-driven: inspection / dogfood → §3I  (spec_next → session per Rk)
└─ Post-mortem / откат              → §3G  (replay, rollback, sandbox_audit)
```

**Всегда:** `apatch_session_start` перед мутацией, `apatch_session_end` после успешной verify+attest (или `rollback` при провале).  
При `governed_mode=auto_session` (дефолт `--with-enforcement`) сессия создаётся автоматически; при `strict` — явный `session_start` обязателен.

**Artifact-anchored intent (RFP-006):** при работе по спеке/ADR/тикету передай `artifacts` в `apatch_session_start` — тогда TrustChain доказуемо связывает решение с мутациями и attestation. После `apatch_attest` проверь `apatch_trustchain_coverage(artifact="spec:SPEC-42")` (`coverage.complete`).

**Executable specifications (RFP-007):** спеки живут в `docs/specs/SPEC-<ID>.md`. Формат: `# SPEC-<ID>` + `## R1 …` + `(verify: pytest …)` на каждое требование. Цикл — §3I; `apatch_doctor` → `spec_execution`.

---

## 3. Workflows (MCP)

Подставь `verify` из `doctor.recommended_verify_resolved`, если не указано иное.

### 3A. Strip / phase (React, C++, multi-file)

```text
apatch_session_start(target_dir=".", intent="strip <Component> → hook|native")
apatch_suggest_until(file_path="src/pages/Page.tsx",
  start_marker="  const dispatch: AppDispatch = useDispatch();", target_dir=".")
# Ожидай: kind=root_return, confidence≥0.9, source=ast (.tsx → grammar tsx)
# Манифест: end_before "  return (" (отступ важен; не `};`) — strip_guide § AST-first
apatch_strip_dry_run(file_path="src/pages/Page.tsx", manifest_path="manifests/<phase>.json",
  strict_overlap=true, out_dir="src/pages/hooks", target_dir=".")
# dry-run: boundary_assessment (boundary_source, unstable), dangling_references

apatch_strip(file_path="src/pages/Page.tsx", manifest_path="manifests/<phase>.json",
  to_module="hook", module_out_dir="src/hooks", auto_wire=true, target_dir=".")
# или bundle + verify на фазу:
apatch_phase_run(manifest_path="manifests/<phase>.json", profile="frontend", to_module="hook",
  module_out_dir="src/hooks", verify=<resolved>, auto_wire=true, target_dir=".")
# governed (enforce consumer, governed_mode=strict): сначала session_start, затем:
apatch_governed_phase_run(manifest_path="manifests/<phase>.json", profile="frontend", to_module="hook",
  module_out_dir="src/hooks", verify=<resolved>, auto_wire=true, target_dir=".")

apatch_verify_run(target_dir=".")
apatch_attest(target_dir=".")
apatch_attestation_export(target_dir=".", out_path=".apatch/audit_bundle.json")
apatch_session_end(target_dir=".")
```

C++: `profile="cpp"`, `native_out_dir=…`, затем `apatch_natives_check(directory=…)`.  
Multi-file: манифест с `files[]` → `apatch_phase_run` без `file_path`.

### 3B. Мутации (replace / create / delete / rename / chmod)

**Единый цикл** — `apatch_generate_batch` (mutation generator), не ручной JSONL с `Add File`.

```text
apatch_session_start(target_dir=".", intent="implement <feature>")
apatch_generate_batch(
  needles=[
    {action: "create", target_file: "src/domain/foo.ts", content: "…"},
    {action: "create", target_file: "src/domain/foo.test.ts", content: "…"},
    {action: "replace", find_text: "…", replace_text: "…", target_file: "src/index.ts"},
    # {action: "delete", target_file: "src/legacy.ts"},
    # {action: "rename", source_file: "src/old.ts", target_file: "src/new.ts"},
    # {action: "chmod", target_file: "scripts/run.sh", mode: "755"},
    # {action: "chmod", target_file: "scripts/run.sh", executable: true},
  ],
  out_path="patches.jsonl",
  target_dir=".",
)
apatch_simulate(logs_path="patches.jsonl", target_dir=".")
apatch_apply_session(logs_path="patches.jsonl", target_dir=".", verify=<resolved>)
# ПОВТОРЯЙ apply_session пока continue=true

apatch_verify_run(target_dir=".")
apatch_verify_notarization(target_dir=".", staged=true)
apatch_attest(target_dir=".")
apatch_session_end(target_dir=".")
```

| `action` | Поля | Примечание |
|----------|------|------------|
| `replace` (default) | `find_text`, `replace_text`, `target_file?` | файл должен существовать |
| `create` | `target_file`, `content` | файл **не** должен существовать |
| `delete` | `target_file` | |
| `rename` | `source_file`, `target_file` | create + delete в JSONL |
| **`chmod`** | `target_file`, `mode` **или** `executable` | governed изменение прав файла без shell `chmod`; `mode`: `755`/`0755`/`0o755`/`100755`; `executable: true` = `755`, `false` = `644` |
| **`shift_outline`** | `target_file`, `after`, `levels?`, `delta?` | сдвиг `3→4`, `3.1→4.1` от якоря |
| **`insert_before`** | `target_file`, `before`, `content` | вставка без renumber |
| **`insert_section`** | `target_file`, `before`, `content`, `shift_following?` | **новый §N** + сдвиг хвоста |

**Numbered markdown** (vision/spec `.md`): `insert_section` + `shift_following`, не каскад `replace` по заголовкам — [SPEC-DOC-OUTLINE-1](specs/SPEC-DOC-OUTLINE-1.md). Якорь: substring (`"## 3."`). Большой `content` → `needles_path`.

Legacy: needle без `action` = replace. `apatch_plan(old_content="")` — dry-run CREATE.

**Mode-only changes:** не делай `chmod` через shell/`exec`/temporary maintenance action. Для executable-bit используй `chmod` needle; он проходит `generate_batch → simulate → apply_session`, попадает в TrustChain/notarization, учитывается как touched file без insertions/deletions и откатывается из backup.

**Только массовый replace одним паттерном:** `apatch_generate` + тот же цикл simulate → apply_session.  
`match_mode` (replace): `literal` | `whitespace` | `regex` | `json` | `yaml`.

### 3C. Replay транскрипта IDE

```text
apatch_session_start(target_dir=".", intent="replay transcript")
apatch_scan(limit=5, target_dir=".")
apatch_plan_batch(logs_path="<from scan>", target_dir=".")
apatch_apply(logs_path="...", target_dir=".", only_drifted=true, verify=<resolved>, dry_run_first=true)
apatch_verify_run(target_dir=".")
apatch_session_end(target_dir=".")
```

### 3D. Orchestrate / pipeline

```text
apatch_session_start(target_dir=".", intent="orchestrate <manifest>",
  artifacts=["adr:ADR-000", "spec:SPEC-user-model"])
apatch_trustchain_history(target_dir=".", artifact="adr:ADR-000")
apatch_orchestrate(manifest_path="manifests/engineering-pipeline.example.json", target_dir=".", dry_run=true)
apatch_orchestrate(manifest_path="manifests/engineering-pipeline.example.json", target_dir=".")
# альтернатива: apatch_pipeline_run(manifest_path=..., dry_run=true) → без graph

apatch_verify_run(target_dir=".")
apatch_attest(target_dir=".")
apatch_trustchain_coverage(target_dir=".", artifact="adr:ADR-000")
apatch_session_end(target_dir=".")
```

По шагам: `apatch_plan_graph` → `apatch_execute_graph`.

### 3H. Artifact traceability (post-mortem / compliance)

```text
apatch_trustchain_history(target_dir=".", artifact="spec:SPEC-42")
apatch_trustchain_coverage(target_dir=".", artifact="spec:SPEC-42")
# coverage.complete → есть и mutation, и attestation

apatch_trustchain_coverage(target_dir=".", op_id="<op_id from ledger or inclusion>")
# обратный маппинг: op_id → artifacts[], role (mutation|attestation|intent)
```

### 3I. Spec-driven (RFP-007, `docs/specs/SPEC-*.md`)

**Формат (lint проверяет):** H1 `# SPEC-<ID>`; в блоке `> **apatch artifact:** \`spec:SPEC-<ID>\`` (тот же id); каждое требование `## R1 <title>` с уникальным `(verify: <cmd>)`.

```text
# RFP-governed spec? scaffold the Rk skeleton from the contract first (RFP-023) — не пиши чеклист руками:
apatch_spec_scaffold(target_dir=".", rfp="RFP-<ID>", spec="SPEC-<ID>", out_path="docs/specs/SPEC-<ID>.md")  # contract-complete (0 gaps); заполни (verify:)
apatch_spec_lint(target_dir=".", spec_path="docs/specs/SPEC-<ID>.md")
apatch_spec_next(target_dir=".", spec="SPEC-<ID>")
apatch_session_start(target_dir=".", requirement="SPEC-<ID>#R1")
apatch_generate_batch(needles=[…]) → apatch_simulate → apatch_apply_session(...)
apatch_verify_run(target_dir=".", verify="<verify из spec_next>")
apatch_attest(target_dir=".")
apatch_spec_status(target_dir=".", spec="SPEC-<ID>")
apatch_session_end(target_dir=".")
```

Повторяй `spec_next` → session(requirement) → mutate → verify → attest, пока все `Rk` = `attested`.  
Не делай второй `apply_session` в lifecycle `verifying` — сначала `verify_run`, потом следующий чанк или новая session.

### 3J. Spec executor (RFP-008) — requirement execution

**Единица работы — требование `Rk`, не JSONL.** Не пиши `scripts/build_*_patches.py` — мутации через `apatch_generate_batch` (`action`: replace | create | delete | rename | chmod) или inline в `execute_next`.

```text
apatch_execute_next(target_dir=".", spec="SPEC-<ID>", dry_run=true)   # план + next Rk
apatch_execute_next(target_dir=".", spec="SPEC-<ID>")                  # session_start(Rk)
# gap analysis → needles
apatch_execute_next(target_dir=".", spec="SPEC-<ID>", needles=[
  {action: "create", target_file: "…", content: "…"},
  {action: "replace", find_text: "…", replace_text: "…", target_file: "…"},
])
apatch_execute_next(target_dir=".", spec="SPEC-<ID>", finalize=true)  # verify → attest → session_end
apatch_execute_next(target_dir=".", spec="SPEC-<ID>")                  # следующий Rk
```

Человек говорит «продолжай SPEC-ONPREM-2» — агент вызывает `apatch_execute_next`, не «выполни R3».
Зависимости между спеками (`**Зависимость:** [SPEC-PARENT]`) блокируют старт при `SPEC_DEPENDENCY_UNMET`.

§3I (ручной цикл `spec_next` + …) — dogfood / inspection. **Одно Rk** — §3J. **Вся спека** — §3K.

### 3K. Spec run (RFP-009) — вся спека одним workflow

**Когда:** спека из нескольких `Rk`, needles на все pending требования уже известны (или после dry-run).
**Единица вызова:** вся спека (chunked), не одно `Rk` как в §3J.

**Primary path — inline `requirements` для компактных спек; `manifest_path` для больших needles.**

| Размер | Путь |
|--------|------|
| ≤2 Rk, короткие needles | `requirements={Rk: {needles: [...]}}` inline в MCP |
| Много Rk / длинные `find_text` / общий test-файл | Закоммить `manifests/SPEC-<ID>.run.json` → `manifest_path=…` |
| Долгий прогон | `chunk_rk_per_call=1` + resume |

**Не делать:** Python driver (`manifests/*-spec.py`) вызывающий `spec_run_enriched()` — обход MCP; ephemeral `.run.json` перед каждым прогоном.

```text
apatch_doctor(target_dir=".")
apatch_spec_lint(target_dir=".", spec="SPEC-<ID>")   # passed → plan_scaffold, rfp_coverage, agent_next

# 1) Resume / gaps (optional)
apatch_spec_run(target_dir=".", spec="SPEC-<ID>", dry_run=true)
# → pending[], gaps[], manifest_template (в ответе — не пиши файл)

# 2) Batch run (один или несколько тиков)
apatch_spec_run(target_dir=".", spec="SPEC-<ID>", requirements={
  "R1": {"needles": [{action: "create", target_file: "…", content: "…"}]},
  "R2": {"needles": [{action: "replace", find_text: "…", replace_text: "…", target_file: "…"}]},
})
# chunk_rk_per_call=0 (default) — все pending Rk за один тик
# пока continue=true: apatch_spec_run(target_dir=".", spec="SPEC-<ID>")  # resume

apatch_spec_status(target_dir=".", spec="SPEC-<ID>")   # done: true
apatch_spec_coverage(target_dir=".", spec="SPEC-<ID>")  # stale / drifted files

# Optional plan-as-artifact (RFP-011) before spec_run:
apatch_spec_plan_lint(target_dir=".", spec="SPEC-<ID>", plan={...})
apatch_spec_plan_register(target_dir=".", spec="SPEC-<ID>", plan={...})
apatch_spec_run(target_dir=".", spec="SPEC-<ID>", plan="latest", requirements={...})
apatch_spec_adherence(target_dir=".", spec="SPEC-<ID>")  # plan-vs-fact (RFP-012)
```

**Внутри каждого Rk** (автоматически): `session_start` → `generate_batch` → `simulate` →
`apply_session` → `verify_run` (из `(verify: …)` в SPEC.md) → `attest` → `session_end`.
Если verify уже проходит **и needles пуст** — мутации пропускаются (`verify_precheck_passed`).
Если needles заданы — **всегда** `generate_batch` → `apply_session`, даже когда verify зелёный. Для уже attested Rk непустые needles автоматически открывают maintenance-rerun; только явный `skip_if_attested: true` разрешает пропуск. Если postcondition каждого needle уже доказан на диске, ответ честно возвращает `applied=0` и `already_satisfied_requirements`.

**State:** `.apatch/spec_run.json` — `rk_index`, `manifest_sha256`, `per_rk`, `last_checkpoint`.
`reset=true` — сброс; `abort=true` — rollback + clear; смена manifest без reset → `MANIFEST_DRIFT`.

**Ответ:** `continue`, `done`, `progress{rk_done,rk_total}`, `current_requirement`, `agent_next`.
При `blocked`: `error_type` (`SPEC_RUN_BLOCKED`, `MANIFEST_GAP`, …), `requirement_token`, `resume_hint`.

| Выбор | §3K spec_run | §3J execute_next | §3I manual |
|-------|--------------|------------------|------------|
| Много Rk, needles известны | ✅ | ❌ N× round-trips | ❌ |
| Одно Rk / hotfix | overkill | ✅ | ✅ |
| Dogfood apatch/SPEC-*.md | ✅ если batch | ✅ | ✅ эталон attest |

**Антипаттерн:** ephemeral `manifests/SPEC-X.run.json` перед каждым прогоном; Python driver scripts (`manifests/*-spec.py`) обходящие MCP; N× `execute_next`
когда `requirements` уже собран; ручной `session_start` на каждом Rk при известном manifest.

**Уроки dogfood (не повторять):**

| Ошибка | Как надо |
|--------|----------|
| Needles как текстовые подсказки | Только mutation dicts: `{action, target_file, find_text/replace_text}` или `{action, target_file, content}` или `{action, target_file, mode/executable}` |
| **Doc-only Rk: needle только в `attestation.md`** | Needle в **целевом артефакте**, который проверяет `(verify:)` — playbook, manifest `_readme`, тестовый файл. `attestation.md` — зеркало, не единственная мутация. Иначе `verify_precheck` зелёный → `skipped_mutations` → attest не биндится |
| `attest` без мутаций в той же session | `spec_run` держит invariant: mutate + verify + attest + `session_end` per Rk; не вызывай `finalize` отдельно |
| Chunk verify откатывает патч | `verify_deferred=true` на mutate (default в `spec_run` / `execute_next`); verify — после всех chunk'ов `apply_session` |
| Неверные literal в `find_text` | Бери строки из реального кода (пример: API **`/api/v1/work-items/`**, не `/items/`) |
| **Shared file: R4/R5+ → ping-pong `stale`** | Несколько Rk патчат один файл — после batch-run ранние Rk часто `stale`. **Rebind:** последним — Rk с **последней** мутацией shared-файла; остальные stale — **noop** (marker fixture `tests/fixtures/<spec>/rebind-rN.txt`, **без** правки shared-файла). Примеры: SPEC-LEDGER-ACTOR-1 → `test_ledger_actor.py`; SPEC-UX-SPECIALIST-1 → `test_ux_specialist.py` | Повторно mutating shared-файл на нижних Rk после верхних |
| **≥2 pending Rk** | **`apatch_spec_run`** (§3K) — inline `requirements` или `manifest_path` | N× §3I / hand-staged JSONL / Python driver scripts |
| **Verify vs markdown prose** | Подстроки в `(verify:)` должны буквально встречаться в целевом doc (не `L1–L5` вместо отдельных `L2`…) | SPEC-UX-SPECIALIST-1 R6: `test_r6_rfp015` vs RFP-015 |

### 3L. Cross-spec interference (RFP-014 Phase 1)

Перед batch исполнением **нескольких** спек (параллельные команды / домены):

```text
apatch_spec_interference(
  target_dir=".",
  specs=["SPEC-COVERAGE-1", "SPEC-LEDGER-ACTOR-1"],
  level=2,
)
# → conflicts[], conflict_graph, has_cycle, safe_order, risk_score, warnings[]

# risk_score < 0.4 — исполняй в safe_order
# has_cycle или risk_score ≥ 0.7 — refactor needles, re-run interference

# ≥2 specs с известными needles — предпочитай §3L Phase 3 (run_multi), не N× §3K вручную
apatch_spec_run_multi(
  target_dir=".",
  specs=["SPEC-A", "SPEC-B"],
  requirements={"SPEC-A": {...}, "SPEC-B": {...}},
  cross_verify=true,
)
# или по одной: apatch_spec_run(spec="SPEC-A", requirements={...})  # §3K
apatch_spec_status / apatch_spec_coverage per spec
```

CLI: `apatch spec interference --spec SPEC-A --spec SPEC-B --json`.

**Phase 1.5 — schedule + spec_run gate:**

```text
apatch_spec_schedule(target_dir=".", specs=["SPEC-A", "SPEC-B"])
# → safe_order, risk_per_step[], interference summary

apatch_spec_run(
  spec="SPEC-A",
  requirements={...},
  peer_specs=["SPEC-B"],
)
# Gate ON when peer_specs non-empty (interference_check optional override).
# Blocks: SPEC_INTERFERENCE_CYCLE | SPEC_INTERFERENCE_STALE | SPEC_RUN_ORDER_BLOCKED
# (predecessor in safe_order not fully attested)
```

Registry needles: after manifest lint, `spec_run` writes `.apatch/specs/<SPEC-ID>.json`.

**Phase 2 — Level-3 cross-verify:**

```text
apatch_spec_cross_verify(target_dir=".", specs=["SPEC-A", "SPEC-B"])
# → checks[], semantic_conflicts[], all_passed; workspace always rolled back
```

**Phase 3 — multi-spec orchestration (default for N specs):**

```text
apatch_spec_run_multi(
  target_dir=".",
  specs=["SPEC-A", "SPEC-B"],
  requirements={
    "SPEC-A": {"R1": {"needles": [{action, target_file, ...}]}},
    "SPEC-B": {"R1": {"needles": [...]}},
  },
  cross_verify=true,      # exact source plan is sandboxed before real apply; every later spec verifies it
  re_interference=true,   # L1/L2 refresh + peer_specs on nested spec_run
)
# → completed_specs[], order, steps[], final_interference
# Explicit requirements per spec are the bounded work list: unrelated open Rk are
# skipped_pending and do not cause MANIFEST_GAP; omitted specs keep strict registry behavior.
# On failure: failed_at, error_type, and workspace_restored=true only after every checkpoint restored
# Repeat NOT needed — each supplied work list runs to completion internally (while continue on spec_run)

# Антипаттерн: N× ручной spec_run + hand-staged patches.jsonl между спеками
# CLI: apatch spec run-multi --spec SPEC-A --spec SPEC-B
```

**Intra-spec** shared file (одна спека) → §3K rebind lesson; **inter-spec** → §3L.

### 3E. Рефакторинг с БД

```text
apatch_session_start(target_dir=".", intent="db refactor <models>")
apatch_generate(...) → patches.jsonl
apatch_apply_session(logs_path="patches.jsonl", verify_deferred=true, verify=<resolved>, target_dir=".")
apatch_impact(target="ModelName", target_dir=".")
apatch_db_check(profile="sqlalchemy", target_dir=".")
apatch_db_revision(profile="sqlalchemy", message="...", dry_run=true, target_dir=".")
apatch_db_safety(profile="sqlalchemy", target_dir=".")
apatch_arch_check(rules_path="manifests/arch-rules.yaml", target_dir=".")
apatch_db_check(profile="sqlalchemy", target_dir=".")   # ok: true
apatch_verify_run(target_dir=".")
apatch_session_end(target_dir=".")
```

Или: `apatch_db_run(manifest_path="manifests/db-refactor.example.json")`.  
Миграции Alembic/Prisma/Django — **отдельно** после apply. Профиль: `manifests/PROFILE.<stack>.md`.

### 3F. Точечные патчи (≤15 кандидатов)

```text
apatch_plan(target_file="...", old_content="...", new_content="...", target_dir=".")
apatch_apply(logs_path="...", steps="5,10", budget="medium", min_confidence=0.85, verify=<resolved>, target_dir=".")
```

При >15 кандидатах ответ укажет `use_tool: apatch_apply_session`.

### 3G. Post-mortem / откат

```text
apatch_replay(target_dir=".", session_id="<checkpoint>")   # → .apatch/replay_log.json
apatch_rollback(session_id="<checkpoint>", target_dir=".")
apatch_sandbox_audit(target_dir=".", auto_revert=true)
apatch_verify_notarization(target_dir=".", staged=true)
```

При `ok: false` + `VERIFY_FAILED` / `NOTARIZATION_FAILED`: **`apatch_rollback`**, не чини файлы вручную.

---

### 3M. Качество гейта и реальность (CLI + MCP, RFP-005)

Зелёный verify ничего не доказывает, пока не знаешь, что он **умеет** краснеть и **всё ещё** краснеет где должен. Есть и CLI (shell), и MCP-тулы.

```bash
# Гейт реально тестирует то, что охраняет? (must_diverge — обязан покраснеть при порче)
apatch probe falsify --verify "pytest tests/test_auth.py" --files src/auth.py
#   real  — корраптим охраняемое → verify КРАСНЫЙ → restored → ЗЕЛЁНЫЙ
#   false — verify остался зелёным → ЛОЖНЫЙ гейт (тест не проверяет требование) → чини тест

# Аттестованный гейт всё ещё зелёный? (must_hold)   ratified | stale
apatch probe ratify  --verify "pytest tests/test_billing.py"
# Нет новых падений против корпуса? (baseline-aware) stable | regressed
apatch probe regress --verify "pytest -q" --allow "test_flaky_xyz"

# Наблюдаемая реальность как источник истины: факт → запись; требование её дискорджит
apatch reality add    --summary "checkout 500 на купоне" --source linear --kind bug
apatch reality status            # 0 covered · 0 pending · 1 uncovered (долг)  (--json → exit 1)
```

Требование закрывает запись строкой `(discharges: REC-…)` в `## Rk …`. `uncovered` — выводимый долг (наблюдаемое ∧ не закрыто аттестованным зелёным гейтом), не ручной ярлык. `--verify` — любая shell-команда, отсюда доменная независимость.

**MCP-эквиваленты** (governed, через MCP-клиент): `apatch_probe(mode='falsify'|'regress'|'ratify', verify=…, files=…)` и `apatch_reality(action='add'|'status', summary=…, source=…, kind=…)` — те же три режима / две операции. Подробно: [probe.md](./probe.md) · [reality.md](./reality.md).

### 3N. Кросс-файловый refactor-impact (SCIP, RFP-033 — advisory)

При рефакторинге символ в `a.py` может ломать аттестованное требование, которое его *вызывает* из `b.py` (а `b.py` не трогали — символьные якоря это не видят). SCIP закрывает слепое пятно — **advisory, никогда не stale и не блок**:

```bash
apatch scip index                 # MCP: apatch_scip(action='index')  — произвести .scip через scip-python (out of band)
apatch scip impact --since HEAD   # MCP: apatch_scip(action='impact') — какие аттестованные Rk ссылаются на изменённый символ
```

Работает **из коробки без scip-python**: `apatch scip impact` строит граф ссылок нативно из Python AST (import-resolved — `model_source: native`). Реальный `.scip` (если есть, от `apatch scip index` / `scip-python`) точнее и выигрывает (`model_source: scip`). `verify_run` доцепляет `scip_impact` только из готового `.scip` (на hot-path нативный граф не пересобирается).

### 3O. Continuous conformance: не смешивай регистрацию и доказательство

`used_for_spec`, `conformance: true` или наличие `docs/specs/SPEC-X.md` — только проектные декларации. Они **не** означают, что SPEC реально включён в `.apatch/conformance.json`, что его live verify зелёный или что доменный evidence-аудит закрыт.

Отчитывай отдельно: (1) декларации, (2) фактический enrollment, (3) live buckets `conformant|drifted|broken|stale|unproven`, (4) доменную evidence qualification. `apatch conformance status --spec SPEC-X --no-live --json` подтверждает bounded enrollment/state без рекурсии; `apatch conformance gate --live` измеряет текущее выполнение. По умолчанию блокирует только `drifted`; `stale` — долг переаттестации, не runtime-регрессия.

The CLI honors configured `mode: blocking` without an extra flag, for both text
and JSON output. `--blocking` strengthens advisory mode; it does not enable an
unconfigured gate.

После `apatch_resume_session` используй только свежую `session_capability` из его ответа: она заменяет token от `session_start`. `apatch_remote_task_run` 0.8.11+ переносит её автоматически.

## 4. Failure taxonomy

| `error_type` | `recommended_action` | Когда |
|--------------|----------------------|-------|
| `TRUSTCHAIN_REJECTED` | `retry_chunk` | нет подписи / policy hook |
| `VERIFY_FAILED` | `fix_forward` | verify red, mutations intact (default AR-3); см. `baseline.new_failures` |
| `VERIFY_FAILED` | `rollback` | только когда `verify_rollback=true`, но `rollback_performed!=true` |
| `VERIFY_FAILED` | `fix_forward` | когда `rollback_performed=true`: файлы уже восстановлены; `resume_session` возвращает `resume_mode=reapply`, затем исправленные needles + `apply_session(reset=true)` в той же сессии |
| `NOTARIZATION_FAILED` | `rollback` | файлы записаны, block не создан |
| `ARCH_VIOLATION` | `reduce_scope` | arch-rules |
| `DB_RISK` | `reduce_scope` | опасные миграции |
| `BUDGET_EXCEEDED` | `reduce_scope` | change budget |
| `APPLY_FAILED` | `rollback` | chunk apply failed |
| `MASS_APPLY_BLOCKED` | `reduce_scope` | >15 канд. без session |
| `DIRECT_WRITE_BLOCKED` | `retry_chunk` | sandbox: Write без lease |
| `LEASE_EXPIRED` | `retry_chunk` | lease истёк |
| `LEASE_CONFLICT` | `retry_chunk` | другой pid держит lease |
| `SESSION_ABORTED` | `retry_chunk` | `abort=true` |
| `RUNTIME_TRANSITION` | `resume_session` | lifecycle desync после MCP disconnect; reconcile (AR-1) |
| `REMOTE_TIMEOUT` | `reconcile_remote_state` | transport timeout не доказывает провал: сначала сверить remote session state; не rollback вслепую |
| `MANIFEST_GAP` | `reduce_scope` | pending Rk без ключа `needles` в requirements/manifest; явное `needles: []` только для verify-only зелёных Rk |
| `MANIFEST_DRIFT` | `retry_chunk` | manifest hash ≠ active `.apatch/spec_run.json`; `reset=true` |
| `SPEC_RUN_BLOCKED` | `rollback` / resume | verify/apply failed на Rk в spec_run |
| `SPEC_DEPENDENCY_UNMET` | `reduce_scope` | upstream SPEC не `done` |
| `SPEC_INTERFERENCE_CYCLE` | `resolve_conflicts` | L2 cycle в peer_specs |
| `SPEC_INTERFERENCE_STALE` | `re_run_interference` | registry snapshot устарел |
| `SPEC_RUN_ORDER_BLOCKED` | `complete_predecessor_first` | predecessor не fully attested |
| `SPEC_SCHEDULE_BLOCKED` | `resolve_conflicts` | schedule not schedulable |
| `SPEC_CROSS_VERIFY_FAILED` | `refactor_needles` | Level-3 cross-verify failed |

Поля: `error_type`, `recoverable`, `recommended_action`, `failure`, `rejection_prompt`.

Явная проверка фазы: `apatch_session_state(target_dir=".")`.

---

## 5. State machine

Каждый MCP tool пишет **`state_update`** в `.apatch/session_state.json`:

```json
{
  "phase": "idle | plan | apply | verify | arch | db | rollback | complete | blocked",
  "checkpoint": "apatch_sess_…",
  "last_tool": "apatch_apply_session",
  "budget_remaining": 3,
  "trustchain": true,
  "risk_level": "low | medium | high",
  "next_action": "…"
}
```

---

## 6. Локальные файлы

| Путь | Назначение |
|------|------------|
| `manifests/*.json` | strip / phase / pipeline манифесты |
| `manifests/arch-rules.yaml` | `apatch_arch_check` |
| `manifests/semantic-verify.yaml` | `apatch_verify_semantic` |
| `manifests/PROFILE.*.md` | verify и цепочки стека |
| `patches.jsonl` | сгенерированные / replay патчи |
| `.trustchain/` | Ed25519 ledger |
| `.apatch/enforcement.json` | notarization enforcement |
| `.apatch/notarized_index.json` | sha256 нотаризованных файлов |
| `.apatch/sandbox.json` | write sandbox |
| `.apatch/write_lease.json` | capability lease (во время apply) |
| `.apatch/session_state.json` | фаза lifecycle |
| `.apatch/spec_run.json` | spec_run checkpoint (RFP-009): rk_index, manifest hash |
| `.apatch/events.jsonl` | domain events (`apatch_events_tail`) |
| `.apatch/backups/` | бэкапы для rollback |
| `.cursor/hooks.json` | Cursor sandbox hooks |

**Не коммитить:** `extracted/`, `**/extraction_report.json`, `.apatch/*` (кроме `sandbox.json`, `enforcement.json`).

---

## 7. Sandbox и TrustChain

**Sandbox** (`apatch_init_consumer(with_sandbox=true)`):

| Зона | Правило |
|------|---------|
| Protected | `src/**`, `app/**`, `services/**`, `packages/**` — только через `apatch_apply_session` / strip lease |
| Allow | `docs/**`, `manifests/**`, `tests/**`, `scripts/**`, `**/*.md` |
| MCP mutations | Только `apatch_*` tools |
| MCP inspection | `cursor-ide-browser` (default) — navigate/snapshot, read-only |

```text
apatch_sandbox_status(target_dir=".")
apatch_sandbox_audit(target_dir=".", auto_revert=true)
apatch_sandbox_ci_gate(target_dir=".")   # PR gate
```

**TrustChain** (`with_enforcement=true`): `no_trustchain` запрещён; после успешного apply-chunk — одна нотаризация всех surviving files; провал → rollback всего chunk. Обычный write/verify использует O(1) receipt + cached index; полный ledger scan только для явного audit/recovery. Перед commit: `apatch_verify_notarization(staged=true)`.

---

### 7.1 Инварианты, которые агент не меняет локальным workaround

- capability бери из `session_capability` и передавай exact id+token во все manual hot-path calls;
- один target file никогда не делится между apply chunks, даже если лимит меньше числа его needles;
- не возвращай per-step notarization или полный ledger scan в normal apply/verify;
- не нормализуй пути через `lstrip("./")`: `.apatch/**` и `.github/**` обязаны сохранить точку;
- verify subprocess не должен наследовать server-only `APATCH_MCP_TARGET_POLICY`;
- при ошибке этих механизмов чини APatch и добавляй regression gate, не обходи lifecycle.

## 8. MCP tools — полный справочник (123; compact default: 15)

CLI = то же имя без префикса `apatch_` / `apatch ` (`apatch --help`).  
**Только MCP:** `apatch_plan`. **Только CLI (TUI):** интерактивный `apatch apply` без `-y`.

### Session и lifecycle

| MCP | Назначение |
|-----|------------|
| `apatch_doctor` | Окружение, verify, trustchain, sandbox, `recommended_workflow` |
| `apatch_workspace_list` | Read-only список зарегистрированных локальных workspace-алиасов и pin/drift status |
| `apatch_workspace_inspect` | Read-only проверка алиаса; `include_contract=true` возвращает закреплённый `AGENTS.md` |
| `apatch_extension_list` | Список явно закреплённых локальных расширений |
| `apatch_extension_inspect` | Метаданные, права и digest одного расширения без запуска |
| `apatch_extension_validate` | Повторная проверка lock/manifest/artifact digests |
| `apatch_extension_run` | Bounded JSON-запуск `read_only`/`proposal`; предложения сами не применяются |
| `apatch_build_diagnose` | RFP-018: parse compiler log → diagnostics + suggestions; auto-linked on `VERIFY_FAILED` |
| `apatch_remote_task_run` | RFP-029: one intent-level remote task boundary; `dry_run=true` plans, `dry_run=false` executes via SSH transport; `plan={"execute_next": true, "spec": "SPEC-X", "requirement": "SPEC-X#Rk", "needles": [...]}` mutates and re-attests one exact already-attested requirement; add `"defer_finalize": true` when a service restart must precede live verify, then restart through the service broker and use `plan={"finalize_current": true}` with a fresh verify; `plan={"specs": [...], "requirements": {...}}` routes cross-SPEC work through remote `spec_run_multi`; `plan={"slug_ratify": true, "slug": "slug", "spec": "SPEC-X"}` runs remote verify-once, batch re-attestation, and conformance gating; `plan={"rebind_stale": true, "spec": "SPEC-X"}` verifies and rebinds shared-file stale Rk without fake source mutations; `plan={"finalize_current": true}` resumes/verifies/attests/closes after transient verify recovery; `plan={"fix_forward_current": true, "needles": [...]}` repairs a failed active session in place before verify/attest/session_end; `defer_finalize=true` also works for fix-forward and leaves the session open after apply; finalization executes the exact hash-bound SPEC checks, treats supplied verify as supplementary, and fails closed on unresolved/drifted requirements or an older worker; `plan={"commit_attested": true, "session_ids": [...], "push": true}` commits only exact signed session files without opening a mutation session; an unrelated active session fails closed as `REMOTE_SESSION_BUSY` and is never auto-closed |
| `apatch_remote_source_handoff` | RFP-030: opaque local-source handoff when the remote machine cannot fetch repo credentials |
| `apatch_remote_service_action` | RFP-030: policy-approved remote service lifecycle broker (`status`/`restart`/`logs`/`healthcheck`) |
| `apatch_gc` | RFP-016: artifact lifecycle GC (`mode=report\|safe\|rotate\|reconcile`) |
| `apatch_mcp_hygiene` | RFP-019 L1-8: ghost MCP processes + lease health (`sweep_leases`) |
| `apatch_session_start` | Intent + governed session; `artifacts` — `kind:id@hash` или dict (RFP-006); `requirement='SPEC-42#R3'` (RFP-007) |
| `apatch_session_end` | Закрыть session |
| `apatch_session_state` | lifecycle, intent, `artifacts[]`, invariant (read) |
| `apatch_recover` | RFP-041: one-call recovery exact session по `governed_session_id`; завершает cleanup или возвращает новую capability, не забирая foreign live lease |
| `apatch_resume_session` | RFP-027: восстановить сессию из `failed`/`verifying` → снова `verify`/`attest` доступны |
| `apatch_spec_lint` | RFP-007: стандарт авторинга `docs/specs/SPEC-*.md` |
| `apatch_spec_status` | RFP-007: статус каждого `Rk` из ledger |
| `apatch_spec_next` | RFP-007: следующее открытое требование + `verify` |
| `apatch_rfp_lint` | RFP-023: lint `docs/RFP-*.md` (Acceptance таблица) |
| `apatch_rfp_spec_coverage` | RFP-023: gate RFP Acceptance id ↔ SPEC `Rk` (covered/waiver) |
| `apatch_knowledge_graph` | RFP-022: session/diagnostics knowledge graph (status/report views) |

### Патчи и apply

| MCP | Назначение |
|-----|------------|
| `apatch_scan` | JSONL транскрипты IDE |
| `apatch_view` | Кандидаты в логе |
| `apatch_plan` | Dry-run одного патча |
| `apatch_plan_batch` | Dry-run JSONL |
| `apatch_generate` | JSONL find/replace (`match_mode`, `glob_pattern`, `append`) |
| `apatch_generate_batch` | **Mutation generator:** `action` replace\|create\|delete\|rename\|chmod\|shift_outline\|insert_before\|insert_section → JSONL; `append`; `needles_path` |
| `apatch_execute_next` | RFP-008: governed cycle на одно `Rk` (needles → batch внутри) |
| `apatch_spec_run` | RFP-009: вся спека — inline `requirements` (default chunk=all) |
| `apatch_spec_run_manifest_lint` | Валидация run manifest vs SPEC.md |
| `apatch_spec_coverage` | Requirement coverage + file-drift staleness (RFP-010) |
| `apatch_spec_interference` | Cross-spec L1/L2 conflicts, `safe_order`, `risk_score` (RFP-014 Phase 1) |
| `apatch_spec_schedule` | Schedule view + `risk_per_step` over interference (RFP-014 Phase 1.5) |
| `apatch_spec_cross_verify` | Level-3 empirical cross-verify: apply A → verify B → rollback (RFP-014 Phase 2) |
| `apatch_spec_run_multi` | RFP-014 Phase 3: N specs in schedule order; optional cross-verify; inline `requirements` map |
| `apatch_spec_plan_lint` | Lint inline plan dict vs SPEC.md (RFP-011) |
| `apatch_spec_plan_register` | Register signed plan artifact `plan:SPEC-X@vN` |
| `apatch_spec_plan_show` | Full registered plan from `.apatch/plans/` including `decision_plan` (RFP-011 R6) |
| `apatch_spec_plan_diff` | Diff registered plan versions per Rk |
| `apatch_spec_adherence` | Plan-vs-fact adherence report per Rk (RFP-012) |
| `apatch_apply` | ≤15 кандидатов (`budget`, `steps`, `range_str`) |
| `apatch_apply_session` | **Массовый apply чанками** (`chunk_max_files`, `verify_deferred`, `checkpoint`) |
| `apatch_simulate` | Preflight: `risk_map`, `rollback_probability` |
| `apatch_rollback` | Откат по `session_id` (= checkpoint) |
| `apatch_replay` | Timeline chunk'ов |

**apply_session:** `logs_path`, `verify`, `chunk_max_files` (5), `replace_all`, `only_drifted`, `min_confidence`, `verify_deferred`, `reset`, `abort`, `tool`, `keyword`. Все шаги, затрагивающие один файл, остаются в одном атомарном чанке; лимит считается по уникальным файлам, а не по числу needles.
**Ответ:** `ok`, `continue`, `checkpoint`, `chunk_result`, `agent_next`, `verify_rollback`, `state_update`.

### Strip и декомпозиция

| MCP | Назначение |
|-----|------------|
| `apatch_strip_dry_run` | Preview (`strict_overlap`) |
| `apatch_suggest_until` | Границы `until` → `score`, `confidence` |
| `apatch_strip` | Вырезать блоки через `MutationRuntime`; session gate по `governed_mode` |
| `apatch_phase_run` | Strip + module/native + verify через `MutationRuntime`; `files[]` в манифесте |
| `apatch_governed_phase_run` | Alias `phase_run` (тот же runtime-path; отличается `finish_tool` в ответе) |
| `apatch_natives_check` | Дубликаты `register_native()` (C++) |

### Verify и attestation

| MCP | Назначение |
|-----|------------|
| `apatch_sdd_verify` | Executes only the exact session-bound frozen SDD judge plus reversible falsification; no caller-supplied command/result |
| `apatch_verify_run` | Default: shell `recommended_verify_resolved`; `verify=` (string or argv list); `baseline=capture\|compare`; **`async_mode=True`** → stdio-isolated restart-safe worker, poll `apatch_verify_status(job_id=…)` |
| `apatch_verify_status` | Unified verify status; **`job_id=`** polls async job terminal state |
| `apatch_verify_status` | Unified verification status |
| `apatch_verify_semantic` | Маршруты/экспорты (`manifests/semantic-verify.yaml`) |
| `apatch_verify_notarization` | staged/working tree vs notarized index |
| `apatch_attest` | TrustChain commit; подписанные `intent` + `artifacts[]` сессии |
| `apatch_commit_attested` | Commit/push exact current hashes from explicit attested `session_ids`; rejects drift and unrelated staged files |
| `apatch_noop_attest` | RFP-027: re-anchor `Rk` дрейфнувшего общего файла через `covered_by="Rk"` (без фиктивного marker) |
| `apatch_attestation_show` | mode, HEAD, recent events |
| `apatch_attestation_export` | audit bundle JSON |
| `apatch_events_tail` | tail `.apatch/events.jsonl` |
| `apatch_trust_enroll` | Enroll агента (мост к `tc cert request`) → корневой якорь |
| `apatch_verify_anchor` | CI-гейт: подпись apatch → leaf → root CA (PKIX + key-binding) |
| `apatch_verify_inclusion` | CI-гейт: записанные op_id включены во внешний append-only лог (Merkle proof) |

### Orchestration, DB, index

| MCP | Назначение |
|-----|------------|
| `apatch_orchestrate` | simulate → plan_graph → execute_graph |
| `apatch_plan_graph` | Dependency graph |
| `apatch_execute_graph` | Topo execute |
| `apatch_pipeline_run` | Линейный engineering-pipeline |
| `apatch_impact` | Затронутые файлы/тесты |
| `apatch_arch_check` | `manifests/arch-rules.yaml` |
| `apatch_db_check` | Модели без миграции |
| `apatch_db_revision` | Черновик миграции |
| `apatch_db_safety` | DROP/ALTER риски |
| `apatch_db_run` | Манифест db-refactor |
| `apatch_refactor_run` | Bundle `rename_symbol` |
| `apatch_index_build` | `.apatch/project_index.json` |
| `apatch_index_query` | Символы, миграции, ADR |
| `apatch_trustchain_history` | Прошлые intent/artifact; `query` (substring) или `artifact` (`kind:id`) |
| `apatch_trustchain_coverage` | Traceability matrix; `artifact` или `op_id` (reverse lookup) |
| `apatch_compile` | Semantic ID в Markdown |

### Sandbox и consumer

| MCP | Назначение |
|-----|------------|
| `apatch_sandbox_status` | mode, lease, hooks, violations |
| `apatch_sandbox_audit` | audit; `auto_revert=true` |
| `apatch_sandbox_ci_gate` | Локальная/сохранённая-state диагностика (`base` для diff PR; + policy drift); fresh checkout не может доказать ephemeral lease/ignored ledger — generated CI проверяет committed policy + tracked inclusion manifest |
| `apatch_policy_sign` | Подписать конфиг монитора → `.apatch/policy.lock.json` (tamper-evident) |
| `apatch_policy_verify` | Проверить подпись политики + дрейф конфига |
| `apatch_init_consumer` | Скаффолд (`profile`, `with_sandbox`, `with_enforcement`, `with_ci`, `refresh_agents`) |

### Product views (RFP-020)

| MCP / CLI | Назначение |
|-----------|------------|
| `apatch_project_status` | Unified project DTO (`project_status_workspace`); same data as `apatch status --json`; embeds `asset_summary` (AF-7) |
| `apatch_asset_summary` | RFP-025 AF-1: deterministic avatar read model (counts/spec ids/methodology tags; no economics) |
| `apatch_avatar_episodes` | WorkEpisode v2 facts plus explicit capability-exclusion reasons |
| `apatch_avatar_capabilities` | Subject-preserving CapabilityEstimate v2 with intervals and uncertainty |
| `apatch_avatar_evidence_export` | Signed, content-safe CapabilityEvidence v2 distillate |
| `apatch_avatar_evidence_sync` | Durable evidence-first sync to HC Tracker; then authenticated timeline batches |
| `apatch_governed_work_configure` | Configure the signed-service endpoint, authorized Platform subject, enrolled APatch request identity and trusted binding-authority keys; no static token |
| `apatch_governed_work_transition_endpoint` | Signed CAS/idempotent transition of `platform_url`; preserves subject, identity and authority pins |
| `apatch_governed_work_prepare_change` | Sign an exact APatch Change and queue its ProjectSourceBinding admission |
| `apatch_governed_work_store_binding` | Verify and immutably store the exact Platform-signed ProjectSourceBinding |
| `apatch_governed_work_build_evidence` | Build source-bound metadata-only evidence plus factual timesheet draft and queue admission |
| `apatch_governed_work_read_execution_proposal` | Verify and transiently read one exact Platform-signed Work Item proposal without starting work |
| `apatch_governed_work_accept_execution_proposal` | Explicitly accept the exact proposal into a local Change and queue canonical Work Item acceptance |
| `apatch_governed_work_preview_evidence` | Preview the exact metadata-only evidence selection before any publication |
| `apatch_governed_work_publish_evidence` | Explicitly publish only the confirmed evidence selection |
| `apatch_governed_work_disconnect` | Fence further sharing while preserving local history and pending evidence |
| `apatch_governed_work_sync` | Deliver the durable governed-work outbox and require an exact signed Platform receipt as ACK |
| `apatch_governed_work_retire_outbox` | Preserve and sign retirement of a rejected request only after an exact same-scope replacement is acknowledged |
| `apatch_governed_work_status` | Read delivery state plus the four independent Platform states without copying Platform lifecycle into APatch |
| `apatch_timesheet` | RFP-026: signed contribution timesheet per identity/project/spec/day |
| `apatch_rebind_stale` | Re-anchor file_drift-stale requirements of a spec in one call |
| `apatch_work_assets` | List WorkAsset export candidates (read-only) |
| `apatch_work_asset_show` | Show one WorkAsset candidate by id (read-only) |
| `apatch_work_asset_search` | Search WorkAsset export candidates (read-only) |
| `apatch_work_asset_export` | Export a signed WorkAsset bundle |
| `apatch_work_asset_export_schema` | WorkAsset export JSON schema ($id contract) |
| `apatch_work_asset_suggest` | Avatar Recall (AUC-1/A31-E) — intent → proven recallable methods, explainable reasons (read-only) |
| `apatch_work_asset_recall` | RecallBundle — money-free method distillate, integrity-checked against the signed pin (read-only) |
| `apatch_work_asset_use` | Signed use record after verify (A31-F) — reuse counters grow only from the ledger |
| `apatch_probe` | RFP-005 differential probe — `mode=falsify\|regress\|ratify` (gate quality) |
| `apatch_reality` | RFP-005 observed-reality ledger — `action=add\|status` (`uncovered` = derived debt) |
| `apatch_slug_intake` | Slug/category cockpit — specs + reality + conformance + query-first/data gaps (read-only) |
| `apatch_slug_cockpit` | Slug work cockpit — combines intake, feedback closure, graph diagnostics, and optional `operational_status` JSON hook (read-only). With `live=true`, `runtime_work_complete=true` plus empty `reopen_reasons` means **do not rebuild the category**; `evidence_debt_codes` is proof debt only, and missing feedback replay stays yellow rather than masquerading as red runtime failure. |
| `apatch_slug_feedback_lint` | Feedback-status vocabulary lint — canonical statuses, alias normalization needles, unknown-status findings (read-only) |
| `apatch_slug_close` | Slug feedback closure — replay owner TSV via live API + decision_graph, propose closure statuses and governed needles (read-only) |
| `apatch_slug_ratify` | Single-call slug ratification — one verify run per unique command, then one governed session + one signed batch-attestation for all green pending/stale Rk, then conformance from the same evidence. No marker fixtures or N× lifecycle; `dry_run` previews |
| `apatch_spec_scaffold` | RFP-023 — SPEC.md skeleton from an RFP Acceptance table (`rfp=`, `spec=`) |
| `apatch_scip` | RFP-033 — cross-file refactor impact, `action=index\|impact` (native, advisory) |
| — | `apatch status` — Rich CLI dashboard (CLI-only) |
| — | `apatch spec list` — discover `docs/specs/SPEC-*.md` ids (CLI-only) |
| — | `apatch report --html` / `--format md` — architect HTML + manager MD (CLI-only) |
| — | `apatch asset summary [--json]` / `apatch timesheet` (CLI parity) |

---

## 9. Антипаттерны

| Нельзя | Вместо этого |
|--------|----------------|
| sed / awk / массовый `search_replace` | `apatch_generate_batch` + `apatch_apply_session` |
| `scripts/build_*_patches.py` (кастомный JSONL) | `apatch_generate_batch(needles=[{action:…}, …])` |
| Ручной JSONL с `*** Add File:` для новых файлов | `{action: "create", target_file, content}` в `generate_batch` |
| Ручной shell `chmod` / временный remote policy action для executable-bit | `{action: "chmod", target_file, mode: "755"}` или `{action: "chmod", target_file, executable: true}` в `generate_batch` |
| `apatch_apply` на весь репо | `apatch_apply_session` |
| Терминал `apatch` при MCP | MCP tools |
| Чинить полусломанное дерево руками | `apatch_rollback` |
| Игнорировать `state_update.next_action` | Следовать lifecycle |
| Commit без notarization verify | `apatch_verify_notarization(staged=true)` |
| Inline Python в `verify=` с кавычками | Простая команда: `"pytest"`, `"npm run build"` |
| Читать `docs/` apatch по URL | Этот файл + `manifests/` |

---

<!-- apatch:stack:start -->
<!-- apatch:stack:end -->

---

## 10. Проектный backlog (опционально)

Свои фазы, монолиты, манифесты — **между маркерами** (сохраняются при `apatch init-consumer --refresh-agents`):

<!-- apatch:project:start -->
(пусто — заполни: следующие strip-фазы, verify для этого репо, `manifests/REFACTOR_PLAN.md`)
<!-- apatch:project:end -->

---

## 11. Настройка MCP (для человека, не для агента)

Агент **не поднимает** MCP-сервер. Человек:

1. `pip install -e "/path/to/apatch[mcp]"`
2. `.apatch/mcp.json` — `apatch init-consumer --with-mcp` или `apatch mcp sync --target-dir .`
3. IDE MCP stub: `python -m apatch.mcp.workspace_launcher` (auto-written to `.cursor/mcp.json`, `~/.gemini/…` when they exist)
4. Stub env includes `APATCH_WORKSPACE` pointing at project root (IDE `cwd` is often `$HOME`)
5. Reload MCP Servers в IDE
6. `apatch mcp check --target-dir . --json` → configured-command health; then `apatch_doctor(target_dir=".")` → `writer_protocol.ready` for this running MCP. Host tool availability is separate. **17 intent-level tools** by default; select `core`/`spec`/`full` explicitly when needed.

7. Опциональный roaming: `apatch workspace add crm /absolute/repo/root`; агент использует только `target_dir="@crm"`

Stderr → `.apatch/mcp_stderr.log`. Env: `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`.

Обновить этот playbook из apatch: `apatch init-consumer --refresh-agents` (сохранит §10 project-блок).

Installed wheels include the same canonical consumer templates, profile docs,
CI and protection hooks as source checkouts. Initialization needs no source
repository. Missing required resources are an installation error, not a successful
partial setup; existing consumer files remain untouched unless refresh is explicit.
