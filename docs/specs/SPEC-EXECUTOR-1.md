# SPEC-EXECUTOR-1 — Spec Executor (requirement execution layer)

> **Status:** Draft v1 · **Owner:** apatch core  
> **Anchors:** [RFP-008-spec-executor.md](../RFP-008-spec-executor.md) · [RFP-007-executable-specifications.md](../RFP-007-executable-specifications.md) · `apatch_generate_batch`  
> **apatch artifact:** `spec:SPEC-EXECUTOR-1`

## 0. Motivation

Сегодня агент вручную собирает цепочку `spec_next` → `session_start` → `generate_batch` →
`simulate` → `apply_session` → `verify_run` → `attest` → `session_end`. RFP-008 вводит
**единую точку входа** `apatch_execute_next`, где единицей планирования является требование
`Rk`, а не JSONL-патч.

**Критерий успеха:** агент говорит `apatch_execute_next(spec='SPEC-ONPREM-2')` и получает
управляемый governed cycle без «выполни R3» от человека и без consumer-скриптов `build_*_patches.py`.

**Зависимость:** RFP-007 MVP (`apatch_spec_*`, `session_start --requirement`) и
`apatch_generate_batch` (mutation generator: create/delete/rename/replace/chmod) и `apatch_execute_next` в MCP (tool_count ≥ 58).

---

## R1 Discover — lint, dependencies, next requirement

`apatch_execute_next(spec=…, dry_run=true)` возвращает `ok: true`, `execution_phase: discover`,
поля `requirement` (первое открытое Rk в порядке документа), `verify`, `remaining[]`,
`dependencies` (список upstream SPEC с `done: true|false`), `execution_plan[]` (упорядоченные
шаги MCP без побочных мутаций). Если спека полностью attested — `done: true`, `next: null`.

При `lint.passed: false` (errors) — `ok: false`, `execution_phase: blocked`, без `session_start`.

(verify: python3 -m pytest tests/test_spec_executor.py::test_execute_next_discover_dry_run -q)

## R2 Dependency gate — block when upstream spec incomplete

Если в теле спеки есть строка зависимости (`Зависимость` / `Dependency` / `Depends on`) со
ссылкой на `SPEC-<ID>`, executor проверяет `apatch_spec_status` для каждого upstream. Если
хотя бы один `done: false` — `ok: false`, `error_type: SPEC_DEPENDENCY_UNMET`,
`recommended_action: reduce_scope`, без открытия сессии.

(verify: python3 -m pytest tests/test_spec_executor.py::test_execute_next_dependency_blocked -q)

## R3 Session — auto session_start for requirement

`apatch_execute_next(spec=…, dry_run=false)` без `needles` и без `finalize` открывает governed
session через `resolve_requirement` + `MutationRuntime.open_session`: `intent` и artifact
`spec:<ID>#<Rk>@<hash>` соответствуют выбранному требованию. Ответ: `execution_phase: session`,
`session.session_id` присутствует.

Повторный вызов при уже активной сессии на том же `requirement` не создаёт вторую сессию
(`ok: true`, `session_reused: true`).

(verify: python3 -m pytest tests/test_spec_executor.py::test_execute_next_starts_session -q)

## R4 Mutate — needles drive generate_batch → simulate → apply_session

`apatch_execute_next(..., needles=[{action, …}, …])` после R3 — мутации `replace` | `create` |
`delete` | `rename` | `chmod` (legacy: `{find_text, replace_text}` = replace). Пример:

```json
{"action": "create", "target_file": "src/foo.ts", "content": "…"}
```

Mode-only example:

```json
{"action": "chmod", "target_file": "scripts/run.sh", "executable": true}
```

После R3
выполняет: `generate_patch_jsonl_batch` → `simulate_workspace` → `apply_session`
(`verify_deferred=true`). Ответ включает `steps_completed` с `generate_batch`, `simulate`,
`apply_session`; при успехе `execution_phase: mutate` или `verify` (если apply завершён).

При `continue: true` от `apply_session` — `ok: true`, `agent_next` указывает повтор
`apply_session` до `continue=false`.

(verify: python3 -m pytest tests/test_spec_executor.py::test_execute_next_mutate_with_needles -q)

## R5 Finalize — verify, attest, session_end, updated spec status

`apatch_execute_next(..., finalize=true)` (после мутаций) запускает `verify_run` с `verify`
из требования, затем `attest`, `session_end`, `spec_status` для той же спеки. Ответ:
`execution_phase: complete`, `requirement_attested: true` если ledger связывает Rk с attestation.

При провале verify — `ok: false`, `error_type: VERIFY_FAILED`, `recommended_action: rollback`.

(verify: python3 -m pytest tests/test_spec_executor.py::test_execute_next_finalize -q)

## R6 MCP and CLI surface

MCP tool `apatch_execute_next` зарегистрирован; CLI `apatch spec execute --spec SPEC-EXECUTOR-1`
(или `apatch execute-next`) вызывает тот же workflow. `tests/test_mcp.py` включает tool в
`expected`; `mcp_health.tool_count` ≥ 58.

(verify: python3 -m pytest tests/test_mcp.py tests/test_spec_executor.py::test_mcp_execute_next_registered -q)

## R7 Agent playbook — §3J in AGENTS.template

[AGENTS.template.md](../AGENTS.template.md) содержит §3J «Spec executor» с циклом
`apatch_execute_next` (dry_run → session → needles → finalize → repeat). Антипаттерн
`scripts/build_*_patches.py` явно заменён на `execute_next` + `generate_batch`.

(verify: python3 -m pytest tests/test_spec_executor.py::test_agents_template_has_section_3j -q)

## Non-goals (MVP)

- Автоматический gap analysis / codegen из текста `## Rk` без агента.
- Batch run всей спеки одним workflow — см. [SPEC-RUN-1](./SPEC-RUN-1.md) / [RFP-009](../RFP-009-spec-run.md).
- Параллельное исполнение нескольких Rk в одной сессии.
- Sub-clause granularity ниже `Rk`.
