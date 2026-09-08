
---

## Быстрый старт: executable specs (~10 мин)

Один **data layer** (`project_status_workspace`, MCP `apatch_project_status`) — три представления для разных ролей ([RFP-020](docs/RFP-020-three-views.md)):

| Роль | Вопрос | Команда |
|------|--------|---------|
| Developer | Что сломано / заблокировано? | `apatch status` · `--json` |
| Architect | Где конфликты спек? | `apatch report --html` |
| Manager | Мы в графике? | `apatch report --format md` |

```bash
# 1. Scaffold consumer (sandbox + enforcement — для governed apply)
apatch init-consumer --target-dir . --with-sandbox --with-enforcement

# 2. Диагностика окружения
apatch doctor

# 3. Executable spec: lint → dry-run → run (inline requirements или manifest)
apatch spec lint --spec SPEC-YOUR-1
apatch spec run --spec SPEC-YOUR-1 --dry-run
# apatch spec run --spec SPEC-YOUR-1   # resume / MCP: apatch_spec_run

# 4. Три представления одного DTO
apatch status
apatch spec list
apatch report --html --out .apatch/report.html
apatch report --format md --locale ru --out -
```

TrustChain фиксирует **кто / когда / что** attested — substrate для attribution ([RFP-020 §3](docs/RFP-020-three-views.md)).  
Плейбук агента: [docs/AGENTS.template.md](./docs/AGENTS.template.md) · индекс спек: [docs/specs/README.md](./docs/specs/README.md).
