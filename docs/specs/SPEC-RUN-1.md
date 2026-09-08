# SPEC-RUN-1 — Spec Run (batch execution всей спеки)

> **Status:** Implemented v1 · **Owner:** apatch core · **Ledger:** 8/8 attested  
> **Anchors:** [RFP-009-spec-run.md](../RFP-009-spec-run.md) · [RFP-008-spec-executor.md](../RFP-008-spec-executor.md) · [SPEC-EXECUTOR-1](./SPEC-EXECUTOR-1.md)  
> **apatch artifact:** `spec:SPEC-RUN-1`  
> **Зависимость:** все требования [SPEC-EXECUTOR-1](./SPEC-EXECUTOR-1.md) attested (`apatch_spec_status` → `done: true`)

## 0. Motivation

`apatch_execute_next` (RFP-008) сократил ручную склейку MCP-вызовов, но **единица вызова —
одно требование `Rk`**. На спеке из 7–15 требований агент делает 30–60 MCP round-trips и
легко теряет фазу (`finalize` без `needles`, stale `apply_session`, пропуск Rk).

**Spec run** — следующий слой: агент (или CI) передаёт **run manifest** с needles на все
pending `Rk`, executor выполняет полный governed cycle **последовательно** и возвращает
`continue` до `done: true`.

**Критерий успеха:**

```text
apatch_spec_run(spec='SPEC-X', dry_run=true)   # gaps + manifest_template в ответе
apatch_spec_run(spec='SPEC-X', requirements={R1: {needles: [...]}, ...})
# повторять пока continue=true
# → spec_status.done: true без ручного spec_next / execute_next per Rk
```

**Non-goals (этой спеки):** автоматический gap analysis; codegen из Markdown; параллельные Rk.

---

## R1 Manifest schema and lint

Run manifest — JSON `schema_version: 1` с полями `spec`, `requirements` (map `Rk` →
`{needles[], skip_if_attested?, verify_override?}`), опционально `options`.

`apatch_spec_run_manifest_lint(manifest_path=…)` (или `spec_run(..., dry_run=true)` с
вложенным `manifest_lint`) проверяет:

* `spec` совпадает с H1 в `docs/specs/SPEC-<ID>.md`
* каждый ключ `R1`… существует в спеке (lint spec)
* `needles[]` — валидные mutation objects (`action` ∈ replace|create|delete|rename|chmod или legacy find/replace)
* нет лишних `Rk`, не описанных в SPEC.md (`warn`)
* pending Rk без ключа `needles` → `ok: false`, `error_type: MANIFEST_GAP`; явное `needles: []` разрешено только как verify-only Rk и аттестуется лишь при зелёном `(verify: …)`

(verify: python3 -m pytest tests/test_spec_run.py::test_manifest_lint_valid_and_gap -q)

## R2 Dry-run — full plan for all pending requirements

`apatch_spec_run(spec=…, manifest_path=…, dry_run=true)` после lint spec + manifest + deps
возвращает `ok: true`, `execution_phase: discover`, `dry_run: true`, без мутаций и без
`session_start`:

* `pending[]` — ordered list `{id, title, verify, has_needles}`
* `skipped_attested[]` — attested Rk без maintenance needles либо с явным `skip_if_attested: true`; непустые needles без этого флага открывают maintenance-rerun
* `execution_plan[]` — шаги на **всю** спеку (не один Rk)
* `manifest_sha256` — hash для drift detection
* если `spec_status.done` — `done: true`, `hint` как в execute_next

При `SPEC_DEPENDENCY_UNMET` — `ok: false` до плана.

(verify: python3 -m pytest tests/test_spec_run.py::test_spec_run_dry_run_full_plan -q)

## R3 Spec run state — continue semantics

Состояние в `.apatch/spec_run.json`: `spec`, `manifest_sha256`, `rk_order[]`, `rk_index`,
`per_rk` (status: pending|running|attested|failed|skipped), `last_checkpoint`,
`spec_run_id`, timestamps.

Первый `spec_run` без state создаёт файл. Повторный вызов с тем же spec+manifest
продолжает с `rk_index` (**`continue: true`** в ответе, пока не все Rk обработаны).

`spec_run(reset=true)` удаляет state. `spec_run(abort=true)` rollback last checkpoint +
clear state (как apply_session abort).

Завершённый run (`done: true`) архивирует или удаляет state (configurable; default delete).

(verify: python3 -m pytest tests/test_spec_run.py::test_spec_run_state_continue_reset -q)

## R4 Multi-Rk loop — one manifest drives governed cycles

`apatch_spec_run(spec=…, manifest_path=…)` без `dry_run` обрабатывает до
`chunk_rk_per_call` pending Rk (default **1** за MCP-вызов). На каждое Rk внутри одного
тика:

1. `session_start(requirement=SPEC#Rk)`
2. `generate_batch(needles)` → `simulate` → `apply_session` (until `continue=false`)
3. `verify_run` с verify из спеки (или `verify_override`)
4. `attest` → `session_end`

Ответ: `execution_phase: running`, `current_requirement`, `progress{r k_done, rk_total}`,
`steps_completed` включает подшаги. Attested Rk с непустыми needles автоматически переоткрывается для maintenance; только явный `skip_if_attested: true` даёт `skipped` без мутаций, а уже выполненные postconditions возвращают `applied=0`.

(verify: python3 -m pytest tests/test_spec_run.py::test_spec_run_executes_two_rk_sequence -q)

## R5 Failure, rollback, and resume

При `VERIFY_FAILED` / `NOTARIZATION_FAILED` / `APPLY_FAILED` на Rk с
`stop_on_first_failure: true` (default):

* `ok: false`, `execution_phase: blocked`, `error_type: SPEC_RUN_BLOCKED`
* `requirement_token`, `recommended_action: rollback`
* `resume_hint`: `apatch_spec_run(resume=true)` после fix + rollback
* state сохраняет `rk_index` на failed Rk (не advance)

`apatch_rollback(session_id=…)` + `spec_run(resume=true)` продолжает тот же Rk.

Смена manifest hash при active state — `MANIFEST_DRIFT` без `reset=true`.

(verify: python3 -m pytest tests/test_spec_run.py::test_spec_run_blocked_and_resume -q)

## R6 MCP, CLI, and example manifest

* MCP `apatch_spec_run` зарегистрирован; `apatch_spec_run_manifest_lint` или lint внутри dry_run.
* CLI: `apatch spec run --spec SPEC-RUN-1 --manifest manifests/SPEC-RUN-1.example.run.json`
* `manifests/SPEC-RUN-1.example.run.json` — рабочий пример для dogfood (2 Rk, fixtures only).
* `tests/test_mcp.py` — tool(s) в `expected`; `mcp_health.tool_count` ≥ 59.

(verify: python3 -m pytest tests/test_mcp.py tests/test_spec_run.py::test_mcp_spec_run_registered -q)

## R7 Dependency gate before batch start

Если в SPEC.md строка `Зависимость` / `Dependency` ссылается на upstream `SPEC-<ID>`,
`spec_run` вызывает ту же логику, что `execute_next` (`check_spec_dependencies`).
Любой upstream с `done: false` блокирует **весь** run до `ok: true` на deps — без
`session_start` на первом Rk.

(verify: python3 -m pytest tests/test_spec_run.py::test_spec_run_dependency_blocked -q)

## R8 Agent playbook — §3K in AGENTS.template

[AGENTS.template.md](../AGENTS.template.md) содержит §3K «Spec run»:

```text
apatch_spec_run(dry_run=true) → заполнить manifest → apatch_spec_run() until continue=false
```

Антипаттерн: N× `execute_next` loop когда manifest уже известен. §3J остаётся для single-Rk /
debug. Таблица MCP tools обновлена (59+ tools).

(verify: python3 -m pytest tests/test_spec_run.py::test_agents_template_has_section_3k -q)

## Non-goals

* Автогенерация manifest из текста `## Rk` (отдельная спека / RFP-010).
* `chunk_rk_per_call > 1` в enforce без явного `options.allow_multi_rk_per_call`.
* Параллельное исполнение Rk.
* Замена `apatch_orchestrate` для engineering-pipeline.

## Appendix A — Dogfood lessons (agent pitfalls)

| Pitfall | Fix |
|---------|-----|
| Needles as prose hints | Mutation dicts only: `{action, target_file, find_text/replace_text}` or `{action, target_file, content}` or `{action, target_file, mode/executable}` |
| Attest outside mutate session | One governed session per Rk: mutate → verify → attest (internal to `spec_run`) |
| Chunk verify rolls back patch | `verify_deferred=true` (default); verify after all `apply_session` chunks |
| Wrong API literals in `find_text` | Copy from source (e.g. `/api/v1/work-items/`, not `/items/`) |

## Appendix B — Agent workflow (§3K preview)

```text
apatch_spec_lint(spec='SPEC-ONPREM-2')
apatch_spec_run(spec='SPEC-ONPREM-2', dry_run=true,
                manifest_path='manifests/SPEC-ONPREM-2.run.json')
# → pending[], manifest gaps → agent fills needles

apatch_spec_run(spec='SPEC-ONPREM-2',
                manifest_path='manifests/SPEC-ONPREM-2.run.json')
# while continue: apatch_spec_run(spec='SPEC-ONPREM-2')  # resume

apatch_spec_status(spec='SPEC-ONPREM-2')  # done: true
```

## Appendix C — Ответ `apatch_spec_run` (schema sketch)

```json
{
  "ok": true,
  "continue": true,
  "done": false,
  "execution_phase": "running",
  "spec": "SPEC-ONPREM-2",
  "spec_run_id": "spec_run_…",
  "current_requirement": {"id": "R3", "token": "SPEC-ONPREM-2#R3"},
  "progress": {"rk_done": 2, "rk_total": 7, "percent": 28.6, "attested": ["R1", "R2"]},
  "manifest_sha256": "sha256:…",
  "agent_next": "apatch_spec_run(spec='SPEC-ONPREM-2')",
  "state_update": {"phase": "apply", "next_action": "…"}
}
```
