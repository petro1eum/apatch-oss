# apatch strip — Руководство по вырезанию и декомпозиции кода ✂️📦

Утилита `apatch strip` предназначена для **проактивной подготовки кодовой базы** и декомпозиции монолитов. Она позволяет вырезать крупные куски логики из монолитных файлов (например, длинные ветки `if-else` или тела функций), заменять их компактными заглушками (stubs), экспортировать извлечённый код в отдельные файлы и автоматически собирать карту зависимостей и импортов.

Это решает огромную проблему ИИ-агентов: ИИ гораздо лучше работает с небольшими, изолированными модулями, чем с файлами на 10 000 строк.

---

## 🚀 Быстрый старт с CLI

Вы можете вырезать блок напрямую через аргументы командной строки:

```bash
apatch strip --file src/eval_visitor.cpp \
  --start 'else if (call->callee == "head_dim")' \
  --until '} else if (call->callee == "resonance_derivative")' \
  --replace '        // Stubbed: native_eval_omega_head.cpp\n' \
  --out-dir src/extracted \
  --export-filename native_eval_omega_head.cpp
```

**Что произойдёт:**
1. Файл `src/eval_visitor.cpp` будет просканирован. Блок кода от строки, содержащей маркер `--start` (включительно), до строки с маркером `--until` (исключительно), будет вырезан.
2. Вместо вырезанного кода запишется строка из `--replace`.
3. Сам вырезанный фрагмент сохранится в `src/extracted/native_eval_omega_head.cpp`.

---

## 📑 Пакетный рефакторинг по манифесту

Для вырезания сразу нескольких блоков опишите их структуру в манифесте JSON или YAML (например, `manifests/eval_visitor_strips.json`).

### Пример манифеста:
```json
[
  {
    "label": "Legacy solver block",
    "start": "else if (solver_type == \"legacy\")",
    "until": "} else if (solver_type == \"ricci\")",
    "replace": "        // Stubbed: native_eval_legacy.cpp\n",
    "export": "native_eval_legacy.cpp"
  },
  {
    "label": "Reservoir block",
    "start": "else if (solver_type == \"reservoir\")",
    "until": "} else if (solver_type == \"mrv\")",
    "replace": "        // Stubbed: native_eval_reservoir.cpp\n",
    "export": "native_eval_reservoir.cpp"
  }
]
```

### Запуск пакетного вырезания:
```bash
apatch strip --file src/eval_visitor.cpp --manifest manifests/strips.json --out-dir src/extracted
```

---

## Границы strip (`start` / `until` / `end_before`)

Семантика: вырезается `[start … until)` — **строка `until` / `end_before` остаётся в parent**.

| Уровень | Манифест | Надёжность |
|---------|----------|------------|
| 1 | `"until": "    };"` | Низкая — десятки ложных совпадений |
| 2 | `"end_before": "  return ("` | **Рекомендуется для React hooks** — логика до root JSX return |
| 3 | `"end_before": "// UI rendering"` | Высокая — уникальный комментарий-якорь |
| 4 | `apatch_suggest_until` + `boundary_assessment` | R35: **AST-first** (`return_statement` guard/root), `boundary_source: ast`; heuristic только fallback |

**React hook extract (типовой шаблон):**

```json
{
  "start": "  const dispatch: AppDispatch = useDispatch();",
  "end_before": "  return (",
  "replace": "  const { ... } = useClientsPage();\n\n"
}
```

`replace` **не должен** содержать `return (` — эта строка уже в parent (после `end_before`).  
Dry-run: смотри `boundary_assessment.confidence` и `boundary_warnings`.

### AST-first boundary analysis (R35, `boundary_ast.py`)

С **2026-06** suggest/assess для `.tsx` / `.ts` / `.jsx` / `.js` идут через tree-sitter, не regex-окна.

| Компонент | Поведение |
|-----------|-----------|
| Grammar | `.tsx` → **`tsx`**, `.ts` → `typescript`, `.jsx` → `jsx` (не путать tsx с typescript — JSX ломает AST) |
| Scope | enclosing `arrow_function` / `function_declaration` body (без `min(start+N)` окон) |
| Кандидаты | все `return_statement` в body; nested handler returns отфильтрованы |
| Классификация | `guard_return` (внутри `if` / `switch` / …) vs `root_return` (top-level JSX return) |
| Confidence | `root_return` ≈ **0.95** (AST), `guard_return` ≈ 0.12; heuristic fallback ≈ 0.88 |
| Ответ MCP | `source: "ast"` в `apatch_suggest_until` и `boundary_assessment.boundary_source` |

**Поиск `end_before` с отступом:** маркер `"  return ("` матчится только на **начало строки** с тем же префиксом — guard `    return (` не перехватывается.

**Проверка перед strip:**

```text
apatch_suggest_until(file_path="src/pages/Page.tsx", start_marker="  const dispatch: AppDispatch = useDispatch();")
# top: kind=root_return, confidence≥0.9, source=ast

apatch_strip_dry_run(..., strict_overlap=true)
# boundary_assessment: unstable=false, until_line = строка root JSX return
```

Heuristic (brace-balance, regex) — только если grammar недоступна или parse failed; тогда `boundary_source: "heuristic"` и пониженный confidence.

### React hook strip: parent imports после `auto_wire`

`auto_wire` вставляет hook import + `replace`, но **не всегда** удаляет ставшие ненужными импорты parent (`salesSlice`, `useDispatch`, …) → `tsc` TS6192.

`auto_wire` после hook-strip вызывает `prune_unused_imports` на parent (без ожидания `verify`). Скан игнорирует строковые литералы — `"Product Name"` не держит `import { Product }`.

Если prune не хватило: `apatch_generate` → `apatch_apply` → `apatch_verify_run`. Пример: `Sales_Pipeline/manifests/README.md`.

---

## 🛡️ Умное разрешение коллизий имён (Collision Avoidance)

При массовом автоматическом рефакторинге ИИ-агенты часто генерируют одинаковые имена для разных блоков или перезаписывают файлы. `apatch` имеет встроенную защиту от потери кода:

1. **Неявная коллизия (динамические имена):** Если файлы выгружаются с одинаковыми именами, вычисленными динамически по заглушке или метке, `apatch` автоматически добавляет к имени файла очищенный ярлык блока: `filename__<sanitized_label>.cpp` (например, `native_eval_memory__mem_retrieve.cpp`).
2. **Явная коллизия (заданные пути):** Если происходит пересечение явно заданных путей в поле `"export"` манифеста или через `--export-filename`, утилита выведет предупреждение в консоль и автоматически добавит порядковый индекс: `filename__1.cpp`. Ваши файлы никогда не будут стёрты или переписаны.

---

## 🧠 Умный анализатор зависимостей и `extraction_report.json`

Главная «боль» декомпозиции кода — утеря контекста импортов и раздутые зависимости. При простом вырезании новый файл обрастает десятками лишних инклудов из исходного монолита или выдает сотни ошибок сборки из-за отсутствия нужных импортов. 

`apatch strip` решает эту проблему интеллектуально, формируя очищенную карту зависимостей **`extraction_report.json`**:

*   **Минимизированная карта импортов (`parent_imports`)**: `apatch` больше не копирует импорты вслепую. Он сканирует вырезанный код и **оставляет в списке только те директивы включения (`#include`, `import`), которые реально задействованы в блоке** (на основе анализа stems файлов и STL-паттернов). Полная нефильтрованная копия родительских импортов сохраняется в резервном ключе `all_parent_imports` для обратной совместимости.
*   **Очищенный список внешних связей (`accessed_external_members`)**: Находит все обращения к внешним объектам, свойствам и методам (например, `interp.strict_contracts_` или `this->parser_->parse()`). Все стандартные методы контейнеров и библиотек (такие как `size()`, `empty()`, `push_back()`, `c_str()`, `first`, `second`) **автоматически отсекаются как шум**, оставляя только действительно важные интерфейсы для геттеров/сеттеров или `friend`-деклараций.
*   **Локация (`export_path`, `line_range`)**: Сохраняет абсолютные пути экспорта и исходные номера строк.

### Пример отчёта `extraction_report.json` с минимизированным набором зависимостей:
```json
{
  "source_file": "o_lang/cpp/src/frontend/interpreter/eval_visitor.cpp",
  "parent_imports": [
    "#include \"o_lang/cpp/src/frontend/interpreter/eval_visitor.h\"",
    "#include <cmath>"
  ],
  "all_parent_imports": [
    "#include \"o_lang/cpp/src/frontend/interpreter/eval_visitor.h\"",
    "#include <cmath>",
    "#include <iostream>",
    "#include \"coreml.hpp\"",
    "#include \"jit.hpp\""
  ],
  "extracted_blocks": [
    {
      "filename": "native_eval_omega_head.cpp",
      "export_path": "/path/to/project/cpp/extracted/native_eval_omega_head.cpp",
      "label": "native_omega_head",
      "start_line": 125,
      "end_line": 267,
      "line_range": "125-267",
      "removed_lines_count": 142,
      "sha256": "8f9b234a1b0c9d7e5f3a2c4e6b8a0d9e1f3c5a7b9d0e2f4a6c8b0d2e4f6a8c0b",
      "stub_replacement": "        // Stubbed: native_eval_omega_head.cpp\n",
      "accessed_external_members": [
        "interp.strict_contracts_",
        "this->parser_"
      ]
    }
  ]
}
```

---

## 🌐 Мультиязычная поддержка импортов (Polyglot)

Анализатор импортов умеет работать со множеством популярных языков:
*   **C / C++**: Собирает `#include <vector>`, `#include "my_header.h"`.
*   **Python**: Распознает `import os`, `from typing import List`, `from .utils import helper`.
*   **JS / TS**: ESM-импорты и CommonJS `require()`.
*   **Rust**: Парсит `use std::...`, `use crate::...`.
*   **Go**: Распознает одиночные и многострочные блоки `import ( ... )`.
*   **Fallback**: Для любых других языков (C#, Ruby, PHP, Swift) сканирует ключевые слова импорта в начале строк.

---

## 🔄 Алгоритм обратного применения (Reverse Order)

При пакетном вырезании 10-20 блоков из одного исходного файла удаление каждого блока изменяет координаты строк для последующих. 

`apatch` решает это элегантно: он находит координаты всех блоков, сортирует их и **применяет изменения строго снизу вверх (от конца файла к началу)**. Координаты верхних блоков остаются абсолютно неизменными до самого момента их вырезания!

---

## 📝 Практический опыт фаз 10-11 и ограничения (Retrospective & Limitations)

Реальный опыт декомпозиции монолитного интерпретатора `eval_visitor` в фазах 10–11 выявил сильные стороны инструмента и практические ограничения, о которых необходимо помнить при подготовке к Phase 12+:

### ✅ Что сработало на отлично:
1. **Надёжность Strip**: Механизмы поиска маркеров, поддержки стыковочных конструкций типа `} else if`, обработки обратного порядка вырезания (reverse order) и сухого запуска (`dry-run`) позволили вырезать суммарно более 1500 строк кода без поломки синтаксиса родительских монолитов.
2. **Полезность `extraction_report.json`**: Чистый список `accessed_external_members` (после автоматического удаления STL-шума вроде `size()`, `empty()`) полностью готов для генерации PR-описаний и C++ объявлений класса-друга (`friend`).
3. **Автоматизация TrustChain**: Неизменяемые чекпоинты и коммиты сэкономили часы работы на ручной откатке/коммите шагов.
4. **Конвертер `convert_extracted_to_native.py`**: Сценарий решает 90% рутинных задач трансляции вырезанного кода в C++ Native модули (замена `interp.`, аргументы вызовов, разрешение инклудов).

### ⚠️ Практические нюансы и ограничения:
* **Слишком строгий `parent_imports`**: Если в теле вырезанного блока нет явных текстовых упоминаний имени исходного файла заголовка (например, есть только префикс `omega_` или фабричные методы класса), фильтр импортов вычищает его из `parent_imports`. В таких случаях конвертер спасает fallback к полному списку `all_parent_imports` и эвристика STL.
* **Хрупкость границ в манифестах**: Конструкции ветвлений со сложными комментариями между ветками могут приводить к ложным срабатываниям парсера границ. Рекомендуется проверять dry-run перед физическим применением.
* **Шероховатости конвертера в C++**: Ключевые слова C++20 (например, `concept` в качестве переменных), вложенные типы (`Interpreter::Record` в комментариях) могут потребовать 5–10 минут ручной доводки после работы конвертера.
* **Двухэтапный запуск**: Для полного E2E используйте `apatch phase run` (strip + `--to-native auto` + опциональный `--verify` в одной команде).

---

## 🛡️ Транзакционный откат (checkpoint = backup файла)

Для apatch **checkpoint** — это одна сессия `apatch_strip_<timestamp>`:

| Слой | Где | Что хранит |
| :--- | :--- | :--- |
| **Файлы** | `.apatch/backups/<checkpoint>/` | Полные копии исходников до strip |
| **TrustChain ref** | `.trustchain/refs/checkpoints/<checkpoint>.ref` | Подпись `HEAD` на момент checkpoint |
| **Ledger** | `.trustchain/objects/` | Запись `action: checkpoint` с путями и SHA-256 файлов |

Каждый `apatch strip` / `phase run` (без `--dry-run`) перед изменениями создаёт **все три** (backup обязателен; TrustChain — если `.trustchain/` активен).

`rollback_checkpoint` / `rollback_session` / `apatch rollback` восстанавливают **и файлы, и HEAD**:

| Событие | Поведение |
| :--- | :--- |
| Ошибка маркера в манифесте | Откат исходника + удаление частичных `.raw.cpp` / native |
| `--strict` и blockers конвертера | То же (exit code 1) |
| Падение `--verify` в `phase run` | Откат последней strip-сессии |
| Ручной откат | `apatch rollback --target-dir <repo_root>` |

```bash
# Безопасный прогон с прерыванием при blockers
apatch phase run --manifest manifests/phase13.json \
  --file src/eval_visitor.cpp \
  --native-out-dir src/frontend/interpreter \
  --out-dir extracted \
  --strict

# Явный откат
apatch rollback --target-dir .
```

В манифесте для блоков «только вырезать, не конвертировать» оставьте `"register": ""` — конвертация в `register_native` будет пропущена.

Поле `"register"` обязательно при `phase run` / `--to-native auto`, иначе имя функции регистрации строится из `label` и может не совпасть с проектом (например, `register_stl_topology_...` вместо `register_stl_builtins`).

---

## TypeScript / React монолиты

Для frontend-проектов используйте профиль `frontend` и опционально `--to-module`:

```bash
# Dry-run с проверкой overlap и JSON для агентов
apatch strip -n --strict-overlap --json \
  --file src/pages/Planning.tsx \
  --manifest manifests/phase.json \
  --out-dir src/features/extracted

# E2E: strip → hook scaffold → npm verify → auto-rollback при fail
apatch phase run --profile frontend \
  --manifest manifests/phase.json \
  --file src/pages/Planning.tsx \
  --out-dir src/features/extracted \
  --module-out-dir src/features/hooks \
  --to-module hook \
  --verify "npm run build" \
  --auto-wire \
  --emit-barrel src/features/hooks/index.ts \
  --emit-wiring manifests/wiring.md
```

**Дополнительные флаги:**
- `--auto-wire` — вставить `parent_import` и заменить stub на `parent_wire` (opt-in, с `--verify`).
- `--emit-barrel` — дописать re-export в указанный `index.ts`.
- `apatch strip --suggest-until` / MCP `apatch_suggest_until` — кандидаты `until` с `score` и `confidence`.

**Multi-file манифест (v2):** один `phase run` на несколько файлов, общий verify и TrustChain checkpoint:

```json
{
  "verify_command": "npm run build",
  "files": [
    { "path": "src/pages/Planning.tsx", "strips": [ "..."] },
    { "path": "src/pages/AccountPlan.tsx", "strips": [ "..."] }
  ]
}
```

```bash
apatch phase run --manifest manifests/multi.json --profile frontend --target-dir .
```

**Multi-file bundle:** при падении общего `verify_command` apatch откатывает исходники по единому TrustChain checkpoint **и** удаляет exported artifacts всех файлов bundle (`extracted/`, сгенерированные hooks/modules, `extraction_report.json`). Ответ CLI/MCP содержит `rollback.removed_artifacts`.

**Важно:**
- По умолчанию raw-фрагменты экспортируются как `.fragment.txt` (не попадают в `tsc`).
- `extraction_report.json` содержит `integration_hints`, `wiring_hints` (import/wire) и `dangling_references`.
- `--strict-dangling` — exit 1, если родитель всё ещё ссылается на символы из вырезанного блока.
- `--strict-overlap` — валидация пересечений блоков в манифесте до apply.

Пример полей манифеста для React:

```json
{
  "label": "planning_handlers",
  "start": "// --- Handlers ---",
  "until": "// --- Gantt ---",
  "target_module": "src/features/hooks/usePlanningHandlers.ts",
  "module_kind": "hook",
  "parent_import": "import { usePlanningHandlers } from '@/features/hooks/usePlanningHandlers';",
  "verify_command": "npm run build"
}
```

См. [cookbook.md](./cookbook.md) и [PHASE_CHECKLIST.md](./PHASE_CHECKLIST.md) (секция Frontend).

