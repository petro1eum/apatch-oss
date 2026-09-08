# ADR-001: Enterprise Scale Gaps — обход диска и AST-парсинг

| Поле | Значение |
|------|----------|
| **Статус** | Accepted |
| **Дата** | 2026-06-01 |
| **Область** | `apatch.resolver`, `apatch.matcher`, `apatch.tui`, `apatch.cli`, `apatch.trustchain_helper` |

> [!NOTE]
> Этот ADR фиксирует **осознанные** ограничения масштабирования текущей архитектуры и дорожную карту улучшений. Он не отменяет принцип «работает из коробки без бинарных зависимостей» — enterprise-ускорители предлагаются как **опциональные** backends.

---

## 1. Контекст

`apatch` применяет патчи из agent-транскриптов к локальному дереву файлов. Типичный сценарий — десятки–сотни правок на десятки файлов после одной сессии агента. Для этого профиля текущая реализация достаточна.

Enterprise-сценарии добавляют другие нагрузки:

- **Монорепозитории** с сотнями тысяч файлов и дрейфующими абсолютными путями в логах.
- **Автогенерированные артефакты** размером в мегабайты (protobuf, OpenAPI, bundled JS).
- **Batch dry-run** (`apatch plan --json`) на сотнях кандидатов без параллелизма.

Два главных узких места — синхронный обход файловой системы и полная загрузка AST в память.

---

## 2. Решение (принятое состояние)

1. **Path resolution** — каскад из четырёх уровней в `resolve_smart_path`: exact → relative → strip prefix → **recursive basename search** через `os.walk`.
2. **Matching** — многоуровневый matcher: exact → whitespace-fuzzy → AST-fuzzy (tree-sitter). AST-fuzzy парсит **весь** target-файл и блоки `old_str` / `new_str` целиком.
3. **Сессия apply/plan** — последовательный цикл по кандидатам; каждый патч создаёт новый `ASTMatcher` и при необходимости заново читает файл с диска.
4. **Zero native deps** — только Python stdlib + pip-пакеты tree-sitter; без `fd`, Watchman, git-index как обязательных компонентов.

Это trade-off **простота и портируемость** против **throughput на гигантских деревьях**.

---

## 3. Текущие ограничения (as-is)

### 3.1. Однопоточный синхронный обход диска

**Fallback-поиск по basename** (`apatch/resolver.py`, шаг 4):

```python
for root, _dirs, files in os.walk(target_dir):
    if any(p in root for p in (".git", ".apatch", ".venv", "__pycache__")):
        continue
    if filename in files:
        ...
```

| Проблема | Эффект |
|----------|--------|
| `os.walk` на каждый нерезолвленный путь | O(файлы × кандидаты) stat/readdir |
| «Игнор» через `continue`, не prune `dirs` | всё равно заходим в `.git/objects`, `node_modules` и т.д. |
| Нет индекса `basename → [paths]` | повторный full-tree walk на каждый патч |
| Нет `git ls-files` / `.gitignore`-aware обхода | обход untracked и vendor-артефактов |

**TrustChain lookup** (`apatch/trustchain_helper.py`, `_find_op_id_by_signature`):

- Линейный `os.walk` по `.trustchain/objects/` с `json.load` каждого файла при rollback checkpoint.

**Discovery транскриптов** (`apatch/discovery.py`):

- `glob.glob(..., recursive=True)` по `~/.cursor`, `~/.claude`, `~/.gemini` — синхронно, но масштаб обычно умеренный.

**Enterprise-практики, которых нет:**

| Подход | Назначение |
|--------|------------|
| `git ls-files` | O(tracked files), без vendor/node_modules |
| Watchman / IDE index | инкрементальный индекс путей |
| `fd` / ripgrep `--files` | параллельный обход на Rust |
| Session-scoped path index | один walk → много resolve |

### 3.2. Полная загрузка AST в память

**Чтение файла целиком** (`apatch/matcher.py`):

```python
with open(path, "r", encoding=enc, newline="") as f:
    content = f.read()
```

`ASTMatcher._load_target_content` хранит весь текст в `self.content`.

**AST-fuzzy — три полных parse на один патч** (`_apply_ast_fuzzy`):

```python
target_tree = parser.parse(bytes(self.content, "utf-8"))
old_tree = parser.parse(bytes(old_str, "utf-8"))
# ...
new_tree = parser.parse(bytes(new_str, "utf-8"))
```

**Рекурсивный обход всего дерева** (`find_structural_nodes`):

- Собирает все `function_*`, `class_*`, control-flow узлы по **всему** файлу, даже если патч затрагивает одну функцию.

| Проблема | Эффект |
|----------|--------|
| Строка + 3× tree-sitter tree | RAM ∝ размер файла на мегабайтных автогенератах |
| Нет windowed / incremental parse | парсится файл целиком при любом дрейфе |
| Нет кэша parse tree между патчами к одному файлу | повторный I/O + parse в batch-сессии |
| Нет streaming / lazy AST | tree-sitter API используется in-memory |

**Enterprise-практики, которых нет:**

- Incremental re-parse только изменённого byte-range.
- Parse tree cache per file на время сессии `plan` / `apply`.
- Lazy materialization structural nodes (query по имени без полного collect).

### 3.3. Последовательный pipeline

```
для каждого кандидата:
  resolve_smart_path()     → возможен os.walk
  ASTMatcher(path)         → f.read() + optional 3× parse
  evaluate() → write
```

- Нет `ThreadPool`, `ProcessPool`, `asyncio` в hot path.
- `plan` и `apply` используют один и тот же последовательный цикл (`cli.py`, `tui.py`).

---

## 4. Где текущая модель достаточна

| Профиль | Почему OK |
|---------|-----------|
| Agent session, 10–200 патчей | Path resolution чаще срабатывает на шагах 1–3 без walk |
| Файлы < 100 KB | tree-sitter + строка укладываются в RAM |
| Level 1–2 matching | AST не вызывается при exact / whitespace-fuzzy |
| Локальная машина разработчика | latency приемлема без индексов |

---

## 5. Дорожная карта (proposed improvements)

Улучшения **не ломают** default zero-deps: каждый backend — opt-in через env или флаг CLI.

| ID | Приоритет | Изменение | Модуль | Эффект | Статус |
|----|-----------|-----------|--------|--------|--------|
| SCALE-1 | P0 | Session path index: `basename → [abs paths]`, построение один раз при старте `plan`/`apply` | `path_index.py`, `resolver.py`, `tui.py`, `cli.py` | убирает повторные full-tree walk | **Done** |
| SCALE-2 | P0 | `git ls-files` если `target_dir` в git repo; fallback на один walk | `path_index.py` | O(tracked), без node_modules | **Done** |
| SCALE-3 | P0 | Prune в walk: `dirs[:] = [d for d in dirs if d not in SKIP]` | `path_index.py`, `resolver.py`, `trustchain_helper.py` | не заходить в `.git`, `node_modules` | **Done** |
| SCALE-4 | P1 | `MatchSession`: reuse `ASTMatcher` per path в рамках одной CLI-сессии | `match_session.py`, `tui.py`, `cli.py` | меньше I/O на batch | **Done** |
| SCALE-5 | P1 | Windowed parse: byte-range ± N KB вокруг `old_str` для AST-fuzzy на файлах > threshold | `matcher.py`, `ast_window.py`, `scale_config.py` | RAM на автогенератах | **Done** |
| SCALE-6 | P2 | Параллельный read-only `plan --json` (ProcessPool, N workers) | `cli.py`, `plan_worker.py` | ускорение dry-run | **Done** |
| SCALE-7 | P2 | TrustChain signature index: map `signature → op_id` при init, не walk на rollback | `trustchain_helper.py` | быстрый rollback | **Done** |
| SCALE-8 | P3 | Optional backends: `APATCH_PATH_BACKEND=fd\|watchman\|git\|walk` | `path_index.py`, `scale_config.py` | enterprise speed без breaking default | **Done** |

### 5.1. Контракт session path index (SCALE-1)

```python
class PathIndex:
    def build(target_dir: str) -> PathIndex: ...
    def resolve_basename(name: str) -> list[str]: ...
```

- Строится **один раз** в начале `plan` / `apply` / `InteractiveTUI.__init__`.
- `resolve_smart_path(..., index: PathIndex | None = None)` использует index на шаге 4 вместо `os.walk`.
- При `index is None` — текущее поведение (backward compatible).

### 5.2. Контракт file session cache (SCALE-4)

```python
class MatchSession:
    def matcher_for(path: str) -> ASTMatcher: ...
    def invalidate(path: str) -> None: ...
```

- После успешной записи файла — `invalidate(path)`.
- Parse tree кэшируется только если AST-fuzzy уже вызывался для этого файла.

### 5.3. Порог windowed parse (SCALE-5) — реализовано

| Переменная | Default | Назначение |
|------------|---------|------------|
| `APATCH_AST_WINDOW_BYTES` | `0` | Размер окна parse; `0` = всегда full file |
| `APATCH_AST_FULL_PARSE_MAX` | `524288` | Порог (512 KB): выше — пробовать windowed parse |

При неудаче windowed match — автоматический fallback на full parse.

### 5.4. Parallel plan (SCALE-6) — реализовано

```bash
apatch plan --logs session.jsonl --target-dir . --json --workers 4
# или env APATCH_PLAN_WORKERS=4
# --workers -1  → все CPU
```

Параллелизм только для read-only `plan --json`.

### 5.5. Path backends (SCALE-8) — реализовано

| `APATCH_PATH_BACKEND` | Поведение |
|-----------------------|-----------|
| `auto` (default) | `git ls-files` → pruned walk |
| `git` | только git, иначе walk |
| `walk` | pruned `os.walk` |
| `fd` | [fd](https://github.com/sharkdp/fd), иначе walk |
| `watchman` | Facebook Watchman query, иначе walk |

---

## 6. Последствия

### Плюсы текущего решения (сохраняем)

- Установка: `pip install -e .` — без компиляции, без системных демонов.
- Предсказуемое поведение на любой ОС с Python 3.9+.
- AST-fuzzy даёт высокую точность при context drift на типичных файлах.

### Минусы (принимаем до SCALE-*)

- На монорепо с mass path drift — минуты на `plan` из-за repeated walk.
- На мегабайтных файлах с AST-fuzzy — риск OOM или swap thrashing.
- Batch `--yes` на 500+ патчах — линейное время без параллелизма.

### Риски roadmap

| Риск | Mitigation |
|------|------------|
| `git ls-files` не видит untracked CREATE | fallback на walk / index для CREATE |
| Windowed parse пропускает distant duplicate nodes | full parse fallback при ambiguous match |
| Parallel `plan` и shared cache | только read-only workers, без shared mutable state |
| Optional `fd` не установлен | graceful fallback на stdlib index |

---

## 7. Критерии приёмки (SCALE-1 … SCALE-3)

Минимальный enterprise-hardening без optional backends:

1. **Монорепо fixture** (≥ 50k файлов, synthetic): `plan` на 100 кандидатов с basename drift — wall time **≤ 10×** baseline single walk (не 100×).
2. **Prune**: walk не заходит в `.git/objects` (проверка через mock/spy на `os.scandir` или интеграционный fixture).
3. **Git repo**: при `git ls-files` доступен — index строится из tracked files; untracked fallback documented.
4. **Backward compat**: без git и без index — поведение идентично текущему (regression tests green).

Автотесты: `tests/test_scale_perf.py` (маркер `@pytest.mark.slow`, по умолчанию 50k файлов). Быстрый прогон CI:

```bash
APATCH_SKIP_SLOW=1 pytest          # без perf
APATCH_MONOREPO_FILES=5000 pytest -m slow   # уменьшенный monorepo
pytest -m slow                   # полный ADR §7 (долго)
```

---

## 8. Связанные документы

- [README.md](../../README.md) — умные пути (`resolve_smart_path`), Level 3 AST-fuzzy.
- [RFP-003-agent-mcp-platform.md](../archive/RFP-003-agent-mcp-platform.md) — MCP-интеграция (архив), diagnostics через tree-sitter.
- [fuzzing.md](../fuzzing.md) — AST-валидация patched output.

---

## 9. Changelog

| Дата | Изменение |
|------|-----------|
| 2026-06-01 | **Implemented** SCALE-5 … SCALE-8: windowed AST, parallel `plan --workers`, `fd`/`watchman` path backends |
