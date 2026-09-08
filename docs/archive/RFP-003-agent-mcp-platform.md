# RFP-003: MCP-транспорт для транзакционного слоя исполнения apatch

* **Статус**: Draft
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-01
* **Зависимости**: RFP-001 (adraft / Semantic ID), RFP-002 (Strip / Native Pipeline)

---

## 1. Проблема

Любой ИИ-агент (Cursor, Claude Code, Gemini, Cline) при редактировании кода выполняет одну и ту же последовательность:

```
Прочитать файл → Найти фрагмент → Заменить → Записать
```

Эта последовательность ломается в трёх точках:

1. **Поиск ломается при дрейфе контекста.** Между чтением и записью файл меняется (другой агент, параллельная ветка, ручная правка). Позиционный `str_replace` не находит строку — агент падает или повторяет цикл, тратя токены.

2. **Запись не атомарна.** Многофайловый рефакторинг из 5 шагов падает на шаге 3 — файлы 1 и 2 уже перезаписаны, откат невозможен. Агент оставляет кодовую базу в несогласованном состоянии.

3. **Действия агента не подписаны.** Ни один агент не фиксирует криптографический факт "кто, когда и что именно изменил". Нет аудита, нет доказуемости, нет возможности верифицировать историю изменений независимо от сервера.

`apatch` уже решает все три проблемы:

| Проблема | Механизм apatch |
|:---|:---|
| Дрейф контекста | `matcher.py`: exact → whitespace-fuzzy → AST (tree-sitter) → semantic-sid → Jaccard |
| Неатомарная запись | `backup.py`: физический бэкап + checkpoint + step-level rollback |
| Нет аудита | `trustchain_helper.py`: Ed25519-подпись каждого шага → локальный хэш-чейн → push на TrustChain Platform |

**Но сейчас агент может использовать apatch только через CLI** (`apatch apply --logs ...`), что требует ручного экспорта логов и bash-команд. Это непригодно для realtime-интеграции.

---

## 2. Решение: MCP-сервер как транспорт к существующему стеку

MCP-сервер — это **не новый продукт**. Это стандартный транспортный слой (stdio / SSE), который открывает уже реализованный стек `apatch` для внешних агентов по протоколу MCP:

```
Внешний агент (Cursor / Claude Code / Cline / Windsurf)
        │
        │  MCP (stdio / SSE)
        ▼
┌─────────────────────────────────────────────────┐
│  apatch MCP Server                               │
│                                                   │
│  Tool: apply_patch                                │
│    → matcher.py (4 уровня поиска)                │
│    → backup.py  (физический бэкап + checkpoint)  │
│    → trustchain_helper.py (Ed25519 подпись)       │
│    → platform_client.py (push на Platform)       │
│                                                   │
│  Tool: rollback                                   │
│    → backup.py  (restore files)                  │
│    → trustchain_helper.py (HEAD reset + revert)  │
│                                                   │
│  Tool: compile_doc                                │
│    → semantic_parser.py (sid injection)           │
│    → knowledge_map.json (граф знаний)            │
│                                                   │
│  Tool: plan                                       │
│    → matcher.py (evaluate без записи)            │
│    → confidence + strategy + warnings            │
│                                                   │
│  Resource: diagnostics                            │
│    → tree-sitter parse → error nodes             │
│    → TrustChain status (HEAD, checkpoint list)   │
└─────────────────────────────────────────────────┘
```

### 2.1. Архитектурный принцип

Сервер **не содержит бизнес-логики**. Каждый MCP-tool — это тонкая обёртка над существующим Python API:

| MCP Tool | Внутренний вызов | Что возвращает |
|:---|:---|:---|
| `apply_patch` | `ASTMatcher(path).evaluate(old, new, action)` + `BackupManager` + `TrustChainHelper.commit_action()` | `{success, strategy, confidence, warnings, trustchain_signature}` |
| `rollback` | `TrustChainHelper.rollback_session(checkpoint)` | `{restored_files, new_head}` |
| `plan` | `ASTMatcher(path).evaluate(old, new, action)` (без записи) | `{would_apply, strategy, confidence, diff_preview}` |
| `compile_doc` | `semantic_parser.compile_markdown(content)` | `{compiled_path, knowledge_map, blocks_count}` |
| `diagnostics` | Tree-sitter parse + TrustChain HEAD read | `{syntax_errors[], trustchain_status}` |

### 2.2. Пример сессии (с точки зрения агента)

```json
// Агент вызывает plan перед правкой
→ {"tool": "plan", "arguments": {
    "target_file": "src/engine.cpp",
    "old_content": "void Engine::tick() {\n  update();\n}",
    "new_content": "void Engine::tick() {\n  pre_update();\n  update();\n  post_update();\n}"
  }}

← {"would_apply": true, "strategy": "ast-fuzzy", "confidence": 0.92,
   "warnings": ["signature change ignored (AST-fuzzy replaces body only)"]}

// Агент видит confidence 0.92, решает применить
→ {"tool": "apply_patch", "arguments": {
    "target_file": "src/engine.cpp",
    "old_content": "void Engine::tick() {\n  update();\n}",
    "new_content": "void Engine::tick() {\n  pre_update();\n  update();\n  post_update();\n}"
  }}

← {"success": true, "strategy": "ast-fuzzy", "confidence": 0.92,
   "checkpoint": "apatch_sess_1748736000",
   "trustchain_signature": "ed25519:a3f8c1..."}
```

Агент получает **структурированный ответ**, а не сырой терминальный вывод. Если `success: false` — агент знает, что файл не тронут.

---

## 3. Связь с TrustChain Platform

Когда переменные окружения `APATCH_PLATFORM_*` настроены, каждый `apply_patch` автоматически:

1. Подписывает действие Ed25519-ключом агента.
2. Пушит подписанный пакет в Verifiable Log на TrustChain Platform (`log_service.append()`).
3. Platform проверяет nonce (replay-защита), записывает в Merkle-лог, обновляет `chain_head`.

При `rollback` — пушится `revert`-пакет (`push_revert()`), и Platform фиксирует факт отката.

Это означает, что **внешний аудитор может независимо проверить** каждое изменение, сделанное ИИ-агентом через apatch:

```bash
# Скачать историю от владельца
# Получить сертификат агента с Platform
curl https://keys.trust-chain.ai/api/pub/agents/cursor-agent-42/cert > agent.crt
# Проверить подпись локально
tc-verify ./trustchain_chain.jsonl.gz --pubkey <из сертификата>
```

Без доверия к серверу. Чистая криптография.

---

## 4. Реализация

### 4.1. Новый файл: `apatch/mcp_server.py`

Минимальный MCP-сервер на `stdio` (стандартный транспорт для Cursor, Claude Code, Cline):

- Читает JSON-RPC из stdin, пишет в stdout.
- Регистрирует tools: `apply_patch`, `rollback`, `plan`, `compile_doc`, `diagnostics`.
- Каждый tool вызывает существующий Python API (ноль дублирования логики).
- Поддержка `--workspace` для привязки к конкретному проекту.

### 4.2. CLI-команда: `apatch mcp`

```bash
apatch mcp                    # stdio (для MCP-клиентов)
apatch mcp --transport sse    # SSE (для HTTP-клиентов / TrustChain Agent)
```

### 4.3. Конфигурация MCP-клиентов

Пользователь добавляет в конфиг своего агента:

```json
{
  "mcpServers": {
    "apatch": {
      "command": "apatch",
      "args": ["mcp"],
      "env": {
        "APATCH_PLATFORM_URL": "https://app.trust-chain.ai",
        "APATCH_PLATFORM_AGENT_ID": "my-agent",
        "APATCH_PLATFORM_KEY_PATH": "~/.trustchain/agent.key"
      }
    }
  }
}
```

Platform env — опциональны. Без них apatch работает полностью локально (подпись в `.trustchain/`, бэкапы в `.apatch/backups/`).

### 4.4. Интеграция с TrustChain Agent

`TrustChain_Agent` уже имеет MCP-интеграции (Playwright MCP, etc.). apatch MCP-сервер встраивается аналогично — как ещё один MCP-инструмент в Docker-контейнере агента:

```yaml
# docker-compose.yml фрагмент
services:
  apatch-mcp:
    image: apatch:latest
    command: ["apatch", "mcp", "--transport", "sse", "--port", "4444"]
    volumes:
      - ./workspace:/workspace
```

---

## 5. Этапы

### Этап 1: MCP-сервер (stdio) + tools `apply_patch`, `plan`, `rollback`
- Файл: `apatch/mcp_server.py`
- CLI: `apatch mcp`
- Тесты: интеграционные (stdin/stdout JSON-RPC round-trip)
- **Критерий готовности**: агент в Cursor может вызвать `apply_patch` и получить структурированный ответ

### Этап 2: `compile_doc` + `diagnostics` tools
- Подключение `semantic_parser.py` и tree-sitter валидации
- Ресурс `diagnostics` для чтения состояния TrustChain

### Этап 3: TrustChain Platform push
- Автоматический push при наличии `APATCH_PLATFORM_*` env
- Revert push при rollback
- Интеграция с `ca_service.py` для получения X.509-сертификата агента

---

## 6. Вне рамок

- **Собственный IDE или форк VS Code.** apatch встраивается в существующие агенты через MCP.
- **Облачное хранение кода.** apatch работает локально. Platform хранит только подписанные хэши действий, не код.
- **Генерация `.cursorrules` / перехват инструментов агента.** Агент сам решает, какие инструменты использовать. apatch не навязывается — он предлагает лучший инструмент.
- **Синтетические бенчмарки.** Ценность доказывается архитектурно: 4-уровневый matcher, атомарные транзакции, криптографический аудит. Не нуждается в маркетинговых демо.
