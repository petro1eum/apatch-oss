# Оркестрация инженерных изменений

> **Статус:** R41–R52 и P4 реализованы (CLI + MCP).  
> Рецепты команд: [cookbook.md](./cookbook.md) · MCP: [mcp_setup.md](./mcp_setup.md) · Индекс: [README.md](./README.md)

apatch проверяет не только «патч лёг», но и **контекст, архитектуру, базу, объём правок** — до и после изменений.

---

## Remote finalization

`fix_forward_current` с `defer_finalize=true` заканчивается после apply и сохраняет сессию открытой. После нужного service action вызывается `finalize_current`: worker сам читает обязательные проверки из привязанных требований SPEC. Переданная короткая проверка лишь дополнительная. Старый worker без подтверждения обязательных проверок не допускается к attest. См. [MCP finalization](./mcp_setup.md#remote-finalization).

## Целевой pipeline

```text
trustchain history     # что уже меняли, какой ADR (P4)
  ↓
plan → apply (+ budget R49)
  ↓
verify shell + verify semantic (R48)
  ↓
db check → db safety → revision (R41–R43, R46)
  ↓
impact (R45) → arch check (R44)
  ↓
index build (R50) → trustchain intent (R51)
```

Один запуск: `apatch pipeline run --manifest manifests/engineering-pipeline.example.json`  
Быстрый старт манифестов: `apatch init-consumer --with-arch-rules`

| # | Команда | MCP |
|---|---------|-----|
| R41 | `apatch db check` | `apatch_db_check` |
| R42 | `apatch db revision` | `apatch_db_revision` |
| R43 | `apatch db run` | `apatch_db_run` |
| R44 | `apatch arch check` | `apatch_arch_check` |
| R45 | `apatch impact` | `apatch_impact` |
| R46 | `apatch db safety` | `apatch_db_safety` |
| R47 | `apatch refactor run` | `apatch_refactor_run` |
| R48 | `apatch verify semantic` | `apatch_verify_semantic` |
| R49 | `apatch apply --budget` | `apatch_apply` + budget |
| R50 | `apatch index build/query` | `apatch_index_*` |
| R51–R52 | pipeline + trustchain | `apatch_pipeline_run`, `apatch_trustchain_history`, `apatch_trustchain_coverage` |

---

## Impact (R45)

**Задача:** агент меняет файл или символ — какие тесты и модули ещё затронуты?

```bash
apatch impact UserModel --json
apatch impact app/models/user.py --depth 2
```

Граф: импорты (Python/TS), symbol index, эвристики `tests/**`.  
Сценарий: `impact` → `plan`/`apply` → `verify` на `affected_tests` → `arch check`.

---

## Architecture check (R44)

**Задача:** патч применился, но слои/импорты нарушены?

```bash
apatch arch check --rules manifests/arch-rules.yaml --json
apatch arch check --since HEAD~1   # только новые нарушения в diff
```

Правила: `manifests/arch-rules.yaml` (пример: [manifests/arch-rules.example.yaml](./manifests/arch-rules.example.yaml)):

- `forbidden_imports` — запрет рёбер в графе импортов
- `layer_rules` — frontend/backend/shared
- `service_rules` — grep/AST по pattern в scope

CI: exit 1 при `ok: false`. `init-consumer --with-arch-rules` копирует шаблон.

---

## Database workflow (R41–R43, R46)

apatch **не подключается к боевой БД** — только файлы в репо и локальные CLI стека (Alembic, Django, Prisma).

### db check (R41)

Модели изменились, а миграции нет?

```bash
apatch db check --profile sqlalchemy --json
```

Профили: `sqlalchemy` | `django` | `prisma`. Ответ: `ok`, `reason: missing_migration`, `hint`.

### db revision (R42)

Черновик миграции через стек:

```bash
apatch db revision --profile sqlalchemy -m "describe change" --dry-run
```

Агент/человек **дописывает data migration** в файл версии.

### db safety (R46)

Опасный SQL в миграциях: `DROP`, `TRUNCATE`, `ALTER TYPE`, …

```bash
apatch db safety --profile sqlalchemy --json
```

### db run (R43)

Сценарий из манифеста: [manifests/db-refactor.example.json](./manifests/db-refactor.example.json)

```bash
apatch db run --manifest manifests/db-refactor.example.json --dry-run
```

### Рекомендуемая цепочка

```text
generate → apply (verify_deferred) → db check → db revision → db safety → db check → upgrade head
```

Подробный профиль SQLAlchemy: [profiles/sqlalchemy-alembic.md](./profiles/sqlalchemy-alembic.md).

---

## Semantic verify (R48)

Маршруты, экспорты, OpenAPI не исчезли в diff:

```bash
apatch verify semantic --json
```

Правила: `manifests/semantic-verify.yaml` (копируется с `--with-arch-rules`).

---

## Refactor bundle (R47)

`rename_symbol`: impact → generate → apply — [manifests/refactor-bundle.example.json](./manifests/refactor-bundle.example.json)

```bash
apatch refactor run --manifest manifests/refactor-bundle.example.json --dry-run
```

---

## Change budget (R49)

```bash
apatch apply --logs patches.jsonl -y --budget medium
```

Лимиты: `max_files`, `max_insertions`, `max_deletions` — отклонение до записи на диск.

---

## Project index (R50)

Память проекта: символы, маршруты, миграции, ADR.

```bash
apatch index build && apatch index query UserModel --json
```

---

## TrustChain context (P4)

```bash
apatch trustchain history --query "refactor user model"
```

Фаза `trustchain_history` в pipeline — **до** apply. Фаза `trustchain_intent` — **после**, записывает intent/ADR.

---

## Write sandbox (v1)

Capability lease + Cursor hooks. Спека: [sandbox.md](./sandbox.md).

```bash
apatch init-consumer --with-sandbox --with-enforcement
apatch sandbox status --json
```

---

## Local workspace routing

Любой локальный workflow остаётся одно-workspace операцией. Bound root задаётся
`target_dir="."`; авторизованный sibling — `target_dir="@alias"`. Алиас разрешается до
lane/session/sandbox routing, поэтому state, checkpoints, leases и TrustChain никогда не
смешиваются между репозиториями. Stateful `workspace use` намеренно отсутствует.


## Attested Git handoff

A completed remote mutation lifecycle can be committed without rebuilding scope from
`git status`:

```text
apatch_remote_task_run(
  remote_target="prod",
  intent="Commit exact attested search changes",
  plan={"commit_attested": true, "session_ids": ["apatch_sess_..."], "push": true},
  dry_run=false,
)
```

The broker runs only `doctor → commit_attested`; it opens no mutation session. The
worker verifies a later signed attestation and exact on-disk hashes, rejects a dirty
index or drift, stages only that set, commits, and pushes only when explicitly asked.
## Dependency graph + orchestrator (R54–R57)

Линейный `apatch pipeline run` выполняет фазы по порядку в манифесте. Для **dependency-driven** порядка:

```bash
apatch graph plan --manifest manifests/engineering-pipeline.example.json
apatch graph execute --manifest manifests/engineering-pipeline.example.json
# или единый вход:
apatch orchestrate --manifest manifests/engineering-pipeline.example.json
```

| MCP | CLI | Назначение |
|-----|-----|------------|
| `apatch_plan_graph` | `apatch graph plan` | граф → `.apatch/execution_graph.json` |
| `apatch_execute_graph` | `apatch graph execute` | topo-порядок узлов (apply_session внутри) |
| `apatch_orchestrate` | `apatch orchestrate` | simulate → plan graph → execute |
| `apatch_replay` | `apatch replay` | timeline chunk'ов по `session_id` (checkpoint) |

`apatch_simulate` возвращает `execution_graph` как preview. После сбоя: `apatch_replay(session_id=<checkpoint>)` → `.apatch/replay_log.json`.

---

## Архив backlog

[archive/RECOMMENDATIONS.md](./archive/RECOMMENDATIONS.md)
