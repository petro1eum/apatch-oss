# Profile: SQLAlchemy + Alembic

apatch refactors **files in the repo** — models, migration scripts, tests. It does not connect to a live database.

## Typical workflow

```bash
apatch generate --find "default=datetime.utcnow" \
  --replace "server_default=func.now()" \
  --glob "**/db_models.py" --out patches.jsonl

apatch plan --logs patches.jsonl --target-dir . --diff
apatch apply --logs patches.jsonl --target-dir . --all -y \
  --verify "python3 -m pytest tests/ && alembic upgrade head" --verify-deferred
```

## Checklist

1. Patch ORM models with `generate` → `apply`.
2. `apatch db check --profile sqlalchemy` *(R41)* — или вручную: есть ли новый файл в `alembic/versions/`?
3. `apatch db revision --profile sqlalchemy -m "..."` *(R42)* — или `alembic revision --autogenerate`; допиши data migration при необходимости.
4. Verify: pytest + `alembic upgrade head` (or `alembic upgrade head --sql` in CI dry-run).
5. On verify failure apatch rolls back **patched model files**; migration files you added are not auto-reverted.

Полный сценарий для агента: [orchestration.md](../orchestration.md).

## AGENTS.md snippet

```bash
apatch init-consumer --target-dir . --profile sqlalchemy
apatch doctor --json   # toolchain, recommended_verify, detected_profiles
```

Шаблон для агентов: [AGENTS.template.md](../AGENTS.template.md) (копируется в `AGENTS.md` при init-consumer).

## MCP

- `apatch_generate` → `apatch_plan_batch` → `apatch_apply` with `verify` and `verify_deferred=true`
- `apatch_rollback` if verify fails
