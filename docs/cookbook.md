# apatch Cookbook — типовые сценарии

Рецепты «задача → команда». Обзор продукта — [README](../README.md). Навигация по всем докам — [README.md](./README.md).

| Аудитория | Документ |
|-----------|----------|
| CLI (эта страница) | cookbook.md |
| ИИ-агент (MCP, consumer) | [AGENTS.template.md](./AGENTS.template.md) — deployed как `AGENTS.md` (17 tools by default, §1 install/hygiene, §3 incl. §3K) |
| MCP tools | [mcp_setup.md](./mcp_setup.md) |

---

## Replay правок агента на другой ветке

```bash
apatch scan --limit 5
apatch plan --logs ~/.cursor/.../transcript.jsonl --target-dir . --diff
apatch apply --logs ~/.cursor/.../transcript.jsonl --target-dir . --only-drifted
```

`--only-drifted` пропускает exact-совпадения (помощь не нужна).

---

## Programmatic batch refactoring (JSONL как вход)

`apatch apply` принимает **любой** JSONL с `TargetContent` / `ReplacementContent`, не только логи IDE.

```bash
python3 -c '
import json
patches = [{"step_index": 1, "tool_calls": [{"name": "replace_file_content", "arguments": {
    "TargetFile": "app/db_models.py",
    "TargetContent": "default=datetime.utcnow",
    "ReplacementContent": "server_default=func.now()",
    "AllowMultiple": True
}}]}]
for p in patches:
    print(json.dumps(p))
' > refactor.jsonl

apatch plan --logs refactor.jsonl --target-dir . --diff
apatch apply --logs refactor.jsonl --target-dir . --all -y \
  --verify "python3 -m pytest tests/" --verify-deferred
```

- `AllowMultiple: true` в JSONL **или** флаг CLI `--all` — замена **всех** вхождений.
- `--verify-deferred` — один прогон verify в конце сессии (быстрее для многих файлов).

---

## Заменить паттерн во всех файлах (generate → apply)

```bash
apatch generate --find "old_pattern" --replace "new_pattern" \
  --glob "**/*.py" --target-dir . --out patches.jsonl
apatch plan --logs patches.jsonl --target-dir .
apatch apply --logs patches.jsonl --target-dir . --all -y --verify "pytest"
```

---

## Мутации в JSONL (replace / create / delete / rename / chmod) {#mutations}

Без Python-скриптов, без ручного `*** Add File:` JSONL и без shell `chmod` для governed-файлов.

**Не пишите** `scripts/build_*_patches.py`. `apatch_generate_batch` — генератор **мутаций**, не только find/replace.

```bash
# needles.json — массив мутаций
apatch generate-batch --needles needles.json --out patches.jsonl --target-dir .
apatch simulate --logs patches.jsonl --target-dir .
apatch apply-session --logs patches.jsonl --target-dir . --verify-deferred
```

| `action` | Поля | Когда |
|----------|------|--------|
| `replace` (default) | `find_text`, `replace_text`, `target_file?` | файл существует |
| `create` | `target_file`, `content` | новый файл (`apply_patch Add File`) |
| `delete` | `target_file` | удалить файл |
| `rename` | `source_file`, `target_file` | перенос (create + delete) |
| `chmod` | `target_file`, `mode` **или** `executable` | права файла без изменения content; например `mode: "755"` или `executable: true` |
| passthrough | `tool_calls: [...]` | сырой `apply_patch` envelope |

MCP (агент) — единый цикл для R5 «новые файлы + правки»:

```text
apatch_generate_batch(
  needles=[
    {action: "create", target_file: "src/domain/monitoring/alertRules.ts", content: "…"},
    {action: "create", target_file: "src/domain/monitoring/alertRules.test.ts", content: "…"},
    {action: "replace", find_text: "…", replace_text: "…", target_file: "src/domain/monitoring/index.ts"},
    {action: "chmod", target_file: "scripts/run_live_feedback_contract.sh", executable: true},
  ],
  out_path="patches.jsonl",
  target_dir=".",
)
→ apatch_simulate → apatch_apply_session → apatch_verify_run → apatch_attest
```

Legacy needles без `action` остаются find/replace. `kind: "create"` — alias для `action`.

`append=true` добавляет шаги с продолжением `step_index`.  
Для `replace` якорь должен **уже присутствовать** в файле; для `create` — файл **не** должен существовать (`require_absent`, default true).
Для `chmod` файл должен существовать (`require_match`, default true). `mode` принимает `755`, `0755`, `0o755`, `100755`; `executable: true` = `755`, `executable: false` = `644`. `plan` показывает mode diff, line-budget считает файл touched, но не добавляет insertions/deletions; rollback восстанавливает прежние права из backup.

Пример: восстановить executable-bit у shell-гейта через governed lifecycle, без policy-defined maintenance action:

```json
[
  {
    "action": "chmod",
    "target_file": "scripts/run_live_feedback_contract.sh",
    "mode": "755"
  }
]
```

---

<a id="spec-run"></a>

## Spec run — вся спека (RFP-009)

Один workflow на все pending `Rk` в `docs/specs/SPEC-*.md`. Агент: [AGENTS.template.md §3K](./AGENTS.template.md).

**Primary path — inline `requirements`, не файл на диск:**

```bash
# План + gaps (без мутаций)
apatch spec run --spec SPEC-ONPREM-2 --dry-run --target-dir . --json

# Batch (CLI: requirements из JSON-файла)
apatch spec run --spec SPEC-ONPREM-2 --requirements needles-by-rk.json --target-dir . --json
# пока continue=true в ответе:
apatch spec run --spec SPEC-ONPREM-2 --target-dir . --json

apatch spec status --spec SPEC-ONPREM-2 --target-dir .
```

MCP (consumer):

```text
apatch_spec_run(spec="SPEC-ONPREM-2", dry_run=true)
apatch_spec_run(spec="SPEC-ONPREM-2", requirements={
  "R1": {"needles": [{action: "create", target_file: "src/x.ts", content: "…"}]},
  "R2": {"needles": [{action: "replace", find_text: "…", replace_text: "…", target_file: "src/x.ts"}]},
})
```

| Параметр | Default | Смысл |
|----------|---------|--------|
| `chunk_rk_per_call` | `0` | Все pending Rk за один MCP-вызов |
| `resume` | `true` | Продолжить `.apatch/spec_run.json` |
| `manifest_path` | — | Versioned run manifest (large needles / CI); inline `requirements` for compact specs |
| `verify_deferred` | `true` | Иначе pytest/npm в chunk verify откатывает патч до конца apply |

**Needles:** только mutation dicts (`action`, `target_file`, …), не prose. Пример replace с domain literal:

```json
{"action": "replace", "target_file": "backend/app/routes/work.py",
 "find_text": "/items/", "replace_text": "/api/v1/work-items/"}
```

Mode-only требование в spec-run оформляется тем же словарём:

```json
{"action": "chmod", "target_file": "scripts/run_live_feedback_contract.sh", "executable": true}
```

Lint manifest vs SPEC: `apatch_spec_run_manifest_lint` (MCP-only).

Одно `Rk` / debug — `apatch spec execute` (`execute_next`, RFP-008). Dogfood apatch — §3I в [AGENTS.md](../AGENTS.md).

---

## Применить только конкретные шаги

```bash
apatch apply --logs transcript.jsonl --target-dir . --steps 5,10,12
apatch apply --logs transcript.jsonl --target-dir . --range 10-20
```

---

## Автопилот с порогом уверенности

```bash
apatch apply --logs transcript.jsonl --target-dir . -y \
  --min-confidence 0.85 --report apatch_report.json
```

---

## Вырезать блок из монолита (C++)

```bash
apatch strip -n --file src/eval_visitor.cpp \
  --manifest manifests/phase.json --out-dir extracted

apatch phase run \
  --profile cpp \
  --manifest manifests/phase.json \
  --file src/eval_visitor.cpp \
  --native-out-dir src/interpreter \
  --out-dir extracted \
  --verify "cmake --build build --target my_target"
```

---

## Вырезать блок из React/TypeScript монолита

### Page → hook (AST boundary, MCP)

```text
apatch_session_start(intent="strip Catalog → useCatalogPage")
apatch_suggest_until(file_path="src/pages/Catalog.tsx",
  start_marker="  const dispatch: AppDispatch = useDispatch();")
# ожидай: line root return, kind=root_return, confidence≥0.9, source=ast

apatch_strip_dry_run(file_path="src/pages/Catalog.tsx",
  manifest_path="manifests/apatch_catalog_page_hook_strip.json",
  strict_overlap=true, out_dir="src/pages/hooks")
# boundary_assessment: boundary_source=ast, unstable=false

apatch_strip(file_path="src/pages/Catalog.tsx", manifest_path="...",
  to_module="hook", module_out_dir="src/pages/hooks", auto_wire=true)
apatch_verify_run(verify="npm run build && npm run test")
```

Манифест (фрагмент):

```json
{
  "start": "  const dispatch: AppDispatch = useDispatch();",
  "end_before": "  return (",
  "replace": "  const { ... } = useCatalogPage();\n\n",
  "module_kind": "hook",
  "target_module": "src/pages/hooks/useCatalogPage.ts"
}
```

`replace` без `return (`; dry-run: [strip_guide.md](./strip_guide.md) § AST-first.

Если verify: unused imports в parent → `apatch_generate` + `apatch_apply` (см. consumer `manifests/README.md`).

### CLI (Planning-style phase)

```bash
apatch strip -n --strict-overlap \
  --file src/pages/Planning.tsx \
  --manifest manifests/phase.json \
  --out-dir src/features/extracted \
  --json

apatch phase run \
  --profile frontend \
  --manifest manifests/phase.json \
  --file src/pages/Planning.tsx \
  --out-dir src/features/extracted \
  --module-out-dir src/features/hooks \
  --to-module hook \
  --verify "npm run build" \
  --emit-wiring manifests/wiring.md
```

---

## Откат сессии

```bash
apatch rollback --target-dir .
apatch rollback --target-dir . --session apatch_strip_20260605_120000
```

---

## Local workspace roaming

Человек один раз авторизует sibling-репозитории:

```bash
apatch workspace add agent /path/to/TrustChain_Agent
apatch workspace add crm /path/to/Sales_Pipeline
apatch workspace inspect crm --include-contract --json
```

Агент вызывает `apatch_workspace_inspect(include_contract=true)` и затем передаёт
`target_dir="@crm"` в каждом MCP-вызове. `.` остаётся исходно bound workspace;
произвольные абсолютные пути между репозиториями не используются.

## Consumer install (PyPI — без путей разработчика)

```bash
python3 -m pip install 'apatch[mcp]'
cd /path/to/your-app
apatch init-consumer --target-dir . --with-sandbox --with-enforcement --with-mcp
apatch mcp sync --target-dir . --force   # writes .apatch/mcp.json
apatch doctor --json
```

`doctor.mcp_health.recommended_install` для consumer: `pip install apatch[mcp]` (не editable path).  
Editable `-e` — только при разработке самого репозитория apatch.

---

## Runtime hygiene (patch JSONL, RFP-016)

Governed workflow — JSONL **не** остаётся в корне:

```bash
apatch session start --intent "refactor billing" --target-dir .
apatch generate-batch --needles needles.json --target-dir .
# → .apatch/tmp/<session_id>/patches.jsonl (registered EPHEMERAL)
apatch simulate --logs .apatch/tmp/.../patches.jsonl --target-dir .
apatch apply-session --logs .apatch/tmp/.../patches.jsonl --target-dir . --verify-deferred
apatch session end --target-dir .
# → tmp JSONL удалён
```

MCP: `apatch_session_start` → `apatch_generate_batch` → `apatch_apply_session` → `apatch_attest` → `apatch_session_end`.

| Symptom | Fix |
|---------|-----|
| `doctor.hygiene.orphan_count > 0` | `apatch gc --safe` or `apatch_gc(mode="safe")` |
| `doctor.hygiene.inferred_count > 0` / `status: critical` | `apatch gc --reconcile` or `apatch_gc(mode="reconcile")` |
| `HISTORY_OVERFLOW` | `apatch gc --rotate` |
| Legacy root `patches-*.jsonl` | `apatch gc --safe` once; then use session workflow |
| `inference sunset` on apply | `apatch gc --reconcile` (backups from prior apply often cause this in dogfood) |

Для SPEC — **`apatch spec run`** / `apatch_spec_run` (закрывает сессии per Rk; JSONL routing через `.apatch/tmp/<session>/` внутри executor). Playbook: [AGENTS.template.md §1.2](./AGENTS.template.md).

---


## Commit/push only exact attested sessions

After verify, attestation, and `session_end`, hand Git only the explicit session ids:

```bash
apatch commit-attested --session apatch_sess_A --session apatch_sess_B \
  -m "Fix canonical search filters" --dry-run
apatch commit-attested --session apatch_sess_A --session apatch_sess_B \
  -m "Fix canonical search filters" --push
```

MCP: `apatch_commit_attested(session_ids=[...], message="...", push=true)`. For a
remote alias, use `apatch_remote_task_run(...,
plan={"commit_attested": true, "session_ids": [...], "push": true}, dry_run=false)`.
The operation rejects pre-staged files, unsigned or post-attestation drift, and never
adds unrelated dirty files.
## Проверка окружения consumer-проекта

```bash
apatch doctor
apatch doctor --json   # trustchain.mode: audit_pending | audit | enforce
# init only on fresh scaffold:
apatch init-consumer --target-dir /path/to/your-app --with-sandbox --with-enforcement --with-mcp
```

`trustchain.mode`:

| mode | Смысл |
|------|--------|
| `audit_pending` | `.trustchain/` ещё нет — появится при первом apply |
| `audit` | ledger есть, enforcement выкл (`no_trustchain` можно) |
| `enforce` | strict: нотаризация каждого шага, commit только с proof |

Strict: `apatch init-consumer --with-enforcement`.

---

## Verify: типичные ошибки

`--verify` выполняется через `shell=True`. Избегайте сложного inline Python с кавычками и glob `**`:

```bash
# Плохо: glob раскрывается shell до subprocess
--verify "python3 -c \"... glob('**/*.py') ...\""

# Хорошо: отдельный скрипт
--verify "./scripts/verify.sh"
--verify "npm run build"
--verify "pytest tests/"
```

При падении verify apatch откатывает шаг и печатает полный stdout/stderr команды.

---

## Domain profiles (DB, search, cloud)

Stack-specific verify chains and AGENTS snippets:

- [SQLAlchemy + Alembic](./profiles/sqlalchemy-alembic.md)
- [Prisma](./profiles/prisma.md)
- [Django](./profiles/django.md)
- [Elastic / OpenSearch](./profiles/elasticsearch-opensearch.md)
- [Cosmos SDK + IaC](./profiles/cosmos-sdk.md)
- [Frontend strip](./profiles/frontend.md)

```bash
apatch init-consumer --target-dir . --profile sqlalchemy
apatch doctor --json   # toolchain + recommended_verify
```

## Generate with match modes

```bash
apatch generate --find "default=datetime.utcnow" --replace "server_default=func.now()" \
  --match whitespace --glob "**/db_models.py" --out patches.jsonl
apatch generate --find-pattern "Column\\(.*default=datetime" --replace "NEW" \
  --match regex --glob "**/*.py" --out patches.jsonl

# Elastic/OpenSearch mappings (whitespace/key-order tolerant, JSON + YAML)
apatch generate --find '{"type": "text"}' --replace '{"type": "keyword"}' \
  --match json --glob "**/*template*.json" --out patches.jsonl
apatch generate --find "type: text" --replace "type: keyword" \
  --match json --glob "**/*template*.{yaml,yml}" --out patches.jsonl
# plan --json покажет strategy: json-semantic, если exact/whitespace не сработали
apatch plan --logs patches.jsonl --target-dir . --json
```

## Multi-file bundle + verify rollback

При `phase run` с манифестом `files[]` и общим `--verify`: один TrustChain checkpoint на весь bundle. Если verify падает — откат исходников **и** удаление exported artifacts (`extracted/`, `module_path`, report). См. [strip_guide.md](./strip_guide.md).

## Рефакторинг с базой данных

Краткий пример — полная цепочка в [orchestration.md](./orchestration.md#database-workflow-r41r43-r46).

```bash
apatch generate --find "old" --replace "new" --glob "**/db_models.py" --out patches.jsonl
apatch apply --logs patches.jsonl -y --verify "pytest" --verify-deferred
apatch db check --profile sqlalchemy --json
apatch db revision --profile sqlalchemy -m "describe change"
apatch db safety --profile sqlalchemy --json
```

## init-consumer profiles

```bash
apatch init-consumer --profile prisma   # or django, sqlalchemy, frontend, elastic, cosmos
```

## suggest-until (ranked boundaries)

```bash
apatch strip --suggest-until --start "// --- BLOCK START ---" --file src/Page.tsx
# MCP: apatch_suggest_until → [{line, text, score, confidence}, ...]
```

## CI template

```bash
apatch init-consumer --target-dir . --with-ci
apatch init-consumer --target-dir . --with-arch-rules
```

`--with-arch-rules` copies `arch-rules.yaml`, `semantic-verify.yaml`, and `engineering-pipeline.example.json` into `manifests/`.

See [docs/ci/github-action-apatch.yml](./ci/github-action-apatch.yml). Fresh-checkout CI validates committed policy and an optional tracked inclusion manifest; the local write lease/ignored ledger are not portable CI evidence.

## Pipeline context (trustchain history)

Before applying patches, load prior intent/ADR from the TrustChain ledger:

```bash
apatch trustchain history --query "user model"
apatch pipeline run --manifest manifests/engineering-pipeline.example.json --dry-run
```

The example pipeline starts with a `trustchain_history` phase so agents see past decisions in the same run.

---

## Engineering pipeline (R52)

```bash
apatch pipeline run --manifest docs/manifests/engineering-pipeline.example.json --dry-run --json
```

## Project index (R50)

```bash
apatch index build --target-dir .
apatch index query UserModel --json
```

## Оркестрация

Справочник, pipeline и MCP: **[orchestration.md](./orchestration.md)**.

```bash
apatch pipeline run --manifest manifests/engineering-pipeline.example.json --dry-run
```

---

*См. также: [strip_guide.md](./strip_guide.md), [AGENTS.template.md](./AGENTS.template.md), [profiles/](./profiles/)*
